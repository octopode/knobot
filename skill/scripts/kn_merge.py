#!/usr/bin/env python3
"""Merge slides from one Keynote deck into another, at the file level.

A Keynote package stores each slide in its own component (Index/Slide-N.iwa). What a
component may contain is not arbitrary, and three invariants govern a merge:

  1. A slide component's `identifier` IS its root KN.SlideArchive's object id.
  2. A slide component owns its drawables and text storages, and does NOT own the
     styles they use: those live in the document stylesheet component, declared in the
     slide component's `externalReferences`. Copying the stylesheet (or style objects)
     into a slide component inverts that ownership and Keynote refuses the file.
  3. Every archive carries its own header; `messageInfos[].type` governs payload
     decoding, so a copied archive must keep its source header, remapped.

So a merged slide copies the slide's OWN objects with fresh identifiers and keeps
references to the styles/theme where they already live in the target. That requires
the two decks to share a theme lineage (same style object ids), which is checked.

Usage: kn_merge.py TARGET SOURCE DST '[20, 21]'
"""
import json
import os
import sys
import uuid
import warnings
import zipfile

warnings.filterwarnings("ignore")
from keynote_parser.codec import IWAFile

INDEX = "Index/"


def load(path):
    """-> (archives {id: obj}, headers {id: header}, members {member: dict}, meta)"""
    arch, headers, members = {}, {}, {}
    with zipfile.ZipFile(path) as zf:
        for n in zf.namelist():
            if n.startswith(INDEX) and n.endswith(".iwa"):
                d = IWAFile.from_buffer(zf.read(n), n).to_dict()
                members[n] = d
                for ch in d.get("chunks", []):
                    for a in ch.get("archives", []):
                        i = str(a["header"]["identifier"])
                        arch[i] = (a.get("objects") or [{}])[0]
                        headers[i] = a["header"]
    meta = members[INDEX + "Metadata.iwa"]["chunks"][0]["archives"][0]["objects"][0]
    return arch, headers, members, meta


def refs(obj, out=None):
    """Every {"identifier": X} reference inside an object, in document order."""
    out = [] if out is None else out
    if isinstance(obj, dict):
        if set(obj.keys()) == {"identifier"}:
            out.append(str(obj["identifier"]))
        for v in obj.values():
            refs(v, out)
    elif isinstance(obj, list):
        for v in obj:
            refs(v, out)
    return out


def owner_map(members, meta):
    """object id -> identifier of the component that owns it."""
    by_loc = {}
    for c in meta["components"]:
        key = c.get("locator") or c.get("preferredLocator")
        if key:
            by_loc[key] = c["identifier"]
    owners = {}
    for fn, d in members.items():
        cid = by_loc.get(fn[len(INDEX):-4])
        for ch in d.get("chunks", []):
            for a in ch.get("archives", []):
                owners[str(a["header"]["identifier"])] = cid
    return owners


def slide_closure(arch, owners, slide_id, comp_id):
    """Objects the slide OWNS, and the foreign objects it references.

    The walk descends only through objects owned by the slide's own component; anything
    owned elsewhere (styles, theme) is recorded as an external reference, not copied."""
    own, ext, stack = set(), set(), [slide_id]
    while stack:
        i = stack.pop()
        if i in own or i in ext:
            continue
        if i not in arch:
            continue
        if owners.get(i) != comp_id:
            ext.add(i)
            continue
        own.add(i)
        stack.extend(refs(arch[i]))
    return own, ext


def remap(obj, mapping):
    if isinstance(obj, dict):
        if set(obj.keys()) == {"identifier"}:
            i = str(obj["identifier"])
            return {"identifier": mapping.get(i, i)}
        return {k: remap(v, mapping) for k, v in obj.items()}
    if isinstance(obj, list):
        return [remap(v, mapping) for v in obj]
    return obj


def archive_entry(ident, obj, src_header, mapping):
    """Copy an archive's OWN header, remapping its identifier and reference lists."""
    h = json.loads(json.dumps(src_header))
    h["identifier"] = ident
    for mi in h.get("messageInfos") or []:
        for key in ("objectReferences", "dataReferences"):
            if mi.get(key):
                mi[key] = [mapping.get(str(r), str(r)) for r in mi[key]]
    return {"header": h, "objects": [obj]}


def fresh_uuid():
    v = uuid.uuid4().int
    return {"lower": str(v & (2**64 - 1)), "upper": str(v >> 64)}


def main():
    target, source, dst = sys.argv[1], sys.argv[2], sys.argv[3]
    wanted = json.loads(sys.argv[4])

    t_arch, t_headers, t_members, t_meta = load(target)
    s_arch, s_headers, s_members, s_meta = load(source)
    t_owners, s_owners = owner_map(t_members, t_meta), owner_map(s_members, s_meta)

    s_show = next(o for o in s_arch.values()
                  if isinstance(o, dict) and o.get("_pbtype") == "KN.ShowArchive")
    t_doc_member = INDEX + "Document.iwa"
    t_doc = t_members[t_doc_member]
    t_show = None
    for ch in t_doc["chunks"]:
        for a in ch["archives"]:
            for o in (a.get("objects") or []):
                if isinstance(o, dict) and o.get("_pbtype") == "KN.ShowArchive":
                    t_show = o
    if t_show is None:
        raise SystemExit("no KN.ShowArchive in the target's Document.iwa")

    s_nodes = [str(r["identifier"]) for r in s_show["slideTree"]["slides"]]
    s_datas = {str(d["identifier"]): d for d in (s_meta.get("datas") or [])}
    # Keynote deduplicates media by digest. Adding a second registry entry for bytes the
    # target already holds is fatal on open (bisected: a lone duplicate-digest `datas`
    # entry crashes Keynote, while new components and archives are accepted), so identical
    # media is REFERENCED, never copied.
    t_by_digest = {d["digest"]: str(d["identifier"])
                   for d in (t_meta.get("datas") or []) if d.get("digest")}
    s_comp = {c["identifier"]: c for c in s_meta["components"]}
    doc_comp = next(c for c in t_meta["components"]
                    if (c.get("locator") or c.get("preferredLocator")) == "Document")
    comp_tmpl = next(c for c in t_meta["components"] if c.get("preferredLocator") == "Slide")

    next_id = int(t_meta["lastObjectIdentifier"])
    def alloc():
        nonlocal next_id
        next_id += 1
        return str(next_id)

    new_members, new_data_files, added = {}, [], []
    unresolved = []
    total_arch = total_media = 0

    for sn in wanted:
        if not 1 <= int(sn) <= len(s_nodes):
            raise SystemExit("slide %r outside source (1-%d)" % (sn, len(s_nodes)))
        node_id = s_nodes[int(sn) - 1]
        slide_id = str(s_arch[node_id]["slide"]["identifier"])
        own, ext = slide_closure(s_arch, s_owners, slide_id, slide_id)

        # Foreign objects must already exist in the target, with the same type: that is
        # what "shares a theme lineage" means. Without it the slide would reference
        # styles that are not there, which is a crash rather than a formatting problem.
        missing = [i for i in sorted(ext)
                   if i not in t_arch
                   or t_arch[i].get("_pbtype") != s_arch[i].get("_pbtype")]
        if missing:
            unresolved.append({"slide": int(sn), "objects": missing[:10],
                               "n": len(missing)})
            continue

        mapping = {i: i for i in ext}
        mapping.update({i: alloc() for i in sorted(own)})
        media_ids = sorted({r for i in own for r in refs(s_arch[i]) if r in s_datas})
        reused, fresh_media = [], []
        for m in media_ids:
            have = t_by_digest.get(s_datas[m].get("digest"))
            if have:
                mapping[m] = have
                reused.append(m)
            else:
                mapping[m] = alloc()
                fresh_media.append(m)

        new_slide_id = mapping[slide_id]
        # Invariant 1: the component's identifier is its slide's object id.
        locator = "Slide-%s" % new_slide_id
        member = INDEX + locator + ".iwa"
        new_members[member] = {"chunks": [{"archives": [
            archive_entry(mapping[i], remap(s_arch[i], mapping), s_headers[i], mapping)
            for i in sorted(own, key=int)]}]}
        total_arch += len(own)

        for m in fresh_media:
            d = json.loads(json.dumps(s_datas[m]))
            d["identifier"] = mapping[m]
            src_file = s_datas[m].get("fileName")
            if src_file:
                # On-disk name is `fileName` (<stem>-<id><ext>); `preferredFileName` is
                # a display name and is not unique.
                stem, ext_ = os.path.splitext(d.get("preferredFileName") or src_file)
                d["fileName"] = "%s-%s%s" % (stem, mapping[m], ext_)
                new_data_files.append((src_file, d["fileName"]))
                total_media += 1
            t_meta.setdefault("datas", []).append(d)
            if d.get("digest"):
                t_by_digest[d["digest"]] = d["identifier"]

        # Invariant 2: declare every reference that leaves this component, and carry the
        # slide's UUID map across so stable cross-references keep resolving.
        src_comp = s_comp.get(slide_id, {})
        ext_by_comp = {}
        for i in sorted(ext, key=int):
            ext_by_comp.setdefault(t_owners.get(i), []).append(i)
        external = []
        for cid, objs in ext_by_comp.items():
            if cid is None:
                continue
            external.append({"componentIdentifier": cid, "isWeak": True})
            external.extend({"componentIdentifier": cid, "objectIdentifier": i}
                            for i in objs)
        comp = {"identifier": new_slide_id,
                "preferredLocator": "Slide",
                "locator": locator,
                "documentReadVersion": comp_tmpl.get("documentReadVersion", [2, 0, 0]),
                "documentWriteVersion": comp_tmpl.get("documentWriteVersion", [2, 0, 0]),
                "externalReferences": external,
                "dataReferences": [{"dataIdentifier": mapping[m]} for m in media_ids],
                "isStoredOutsideObjectArchive": False,
                "objectUuidMapEntries": [
                    {"identifier": mapping[e["identifier"]], "uuid": e["uuid"]}
                    for e in (src_comp.get("objectUuidMapEntries") or [])
                    if str(e.get("identifier")) in mapping],
                "saveToken": comp_tmpl.get("saveToken")}
        t_meta["components"].append(comp)

        node_copy = remap(s_arch[node_id], mapping)
        node_copy["slide"] = {"identifier": new_slide_id}
        # Cached thumbnails point at the source's data registry; let Keynote rebuild them.
        node_copy.pop("thumbnails", None)
        node_copy.pop("thumbnailSizes", None)
        node_copy["thumbnailsAreDirty"] = True
        new_node_id = alloc()
        t_doc["chunks"][0]["archives"].append(
            archive_entry(new_node_id, node_copy, s_headers[node_id], mapping))
        t_show["slideTree"]["slides"].append({"identifier": new_node_id})
        # The node is owned by the Document component: register it there the way every
        # other slide node is (bare component reference + a UUID map entry).
        doc_comp.setdefault("externalReferences", []).append(
            {"componentIdentifier": new_slide_id})
        doc_comp.setdefault("objectUuidMapEntries", []).append(
            {"identifier": new_node_id, "uuid": fresh_uuid()})

        added.append({"from_slide": int(sn), "new_slide_id": new_slide_id,
                      "own_objects": len(own), "external_refs": len(ext),
                      "media_reused": len(reused), "media_added": len(fresh_media)})

    if unresolved and not added:
        raise SystemExit(json.dumps({"error": "source styles absent from target "
                                     "(decks do not share a theme lineage)",
                                     "unresolved": unresolved}))

    t_meta["lastObjectIdentifier"] = str(next_id)

    tmp = dst + ".tmp"
    not_copied = []
    with zipfile.ZipFile(target) as zt, zipfile.ZipFile(source) as zs, \
            zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as out:
        rewritten = {t_doc_member: t_doc,
                     INDEX + "Metadata.iwa": t_members[INDEX + "Metadata.iwa"]}
        for item in zt.infolist():
            if item.filename in rewritten:
                out.writestr(item, IWAFile.from_dict(rewritten[item.filename]).to_buffer())
            else:
                out.writestr(item, zt.read(item.filename))
        for member, doc in new_members.items():
            out.writestr(member, IWAFile.from_dict(doc).to_buffer())
        src_names = set(zs.namelist())
        for old, new in new_data_files:
            if "Data/" + old in src_names:
                out.writestr("Data/" + new, zs.read("Data/" + old))
            else:
                not_copied.append(old)
    os.replace(tmp, dst)

    # Validate before Keynote sees it: a bad reference is a crash on open (-609), which
    # is a slow and uninformative way to find a bug.
    v_arch, _, v_members, v_meta = load(dst)
    v_owners = owner_map(v_members, v_meta)
    data_ids = {str(d["identifier"]) for d in (v_meta.get("datas") or [])}
    broken = sum(1 for i, o in v_arch.items() for r in refs(o)
                 if r not in v_arch and r not in data_ids)
    with zipfile.ZipFile(dst) as zf:
        have = set(zf.namelist())
    missing_media = [d["fileName"] for d in (v_meta.get("datas") or [])
                     if d.get("fileName") and "Data/" + d["fileName"] not in have]
    # Undeclared cross-component references are the specific defect that broke v1.
    comp_ids = {c["identifier"] for c in v_meta["components"]}
    undeclared = 0
    for c in v_meta["components"]:
        declared = {str(e.get("objectIdentifier")) for e in (c.get("externalReferences") or [])}
        for i, o in v_arch.items():
            if v_owners.get(i) != c["identifier"]:
                continue
            for r in refs(o):
                if r in v_arch and v_owners.get(r) not in (None, c["identifier"]) \
                        and r not in declared:
                    undeclared += 1
    print(json.dumps({"added": added, "copied_archives": total_arch,
                      "copied_media": total_media,
                      "unresolved_styles": unresolved,
                      "media_not_found_in_source": not_copied,
                      "n_missing_media": len(missing_media),
                      "broken_refs_in_output": broken,
                      "undeclared_cross_component_refs": undeclared}))


if __name__ == "__main__":
    main()
