#!/usr/bin/env python3
"""Per-slide text and geometry edits for a Keynote .key, writing a NEW deck.

Unlike keynote-parser's deck-wide `replace`, edits here touch only the named
slide archives (Index/Slide-*.iwa), so duplicated template slides can be given
different text. Every run-length text table (tableParaStyle, tableCharStyle,
tableLanguage, tableParaStarts, ...) is remapped through each replacement span in
UTF-16 units, so inline styling (italic species names, coloured words) survives and
the paragraph count may change. Every other zip member is copied byte-for-byte.

Usage: kn_slide_text.py SRC DST EDITS.json
EDITS.json: list of
  {"file": "Index/Slide-123.iwa", "find": "old", "replace": "new", "expect": 1}
      literal find in that slide's text storages; `expect` = required number of
      occurrences across the slide (default: at least 1)
  {"file": ..., "geom": "<archive id>", "x": .., "y": .., "w": .., "h": ..}
      set a drawable's position/size (any of x,y,w,h; points)
Prints JSON report.
"""
import json
import os
import sys
import warnings
import zipfile

warnings.filterwarnings("ignore")
from keynote_parser.codec import IWAFile


def u16(s):
    return len(s.encode("utf-16-le")) // 2


def remap_tables(ob, A, B, L):
    def f(i):
        if i <= A:
            return i
        if i < B:
            return A + min(i - A, L)
        return i - (B - A) + L
    for k, v in list(ob.items()):
        if not k.startswith("table") or not isinstance(v, dict):
            continue
        ents = v.get("entries")
        if not isinstance(ents, list):
            continue
        for e in ents:
            if isinstance(e, dict) and "characterIndex" in e:
                e["characterIndex"] = f(int(e["characterIndex"]))


def clean_tables(ob, n):
    for k, v in list(ob.items()):
        if not k.startswith("table") or not isinstance(v, dict):
            continue
        ents = v.get("entries")
        if not isinstance(ents, list) or not ents:
            continue
        if not all(isinstance(e, dict) and "characterIndex" in e for e in ents):
            continue
        out = {}
        order = []
        for e in ents:
            ci = e["characterIndex"]
            if ci > n:
                continue
            if ci not in out:
                order.append(ci)
            out[ci] = e        # last wins at a collapsed index
        if not order:          # keep the first run, pinned to 0
            ents[0]["characterIndex"] = 0
            v["entries"] = [ents[0]]
            continue
        v["entries"] = [out[ci] for ci in sorted(order)]


def para_starts(text):
    starts = [0]
    for i, c in enumerate(text):
        if c == "\n":
            starts.append(u16(text[:i + 1]))
    return starts


def rebuild_para_style(ob):
    tps = ob.get("tableParaStyle")
    if not isinstance(tps, dict) or not tps.get("entries"):
        return
    ents = sorted(tps["entries"], key=lambda e: e["characterIndex"])
    out = []
    for s in para_starts(ob["text"][0]):
        cur = ents[0]
        for e in ents:
            if e["characterIndex"] <= s:
                cur = e
        ne = dict(cur)
        ne["characterIndex"] = s
        out.append(ne)
    tps["entries"] = out


def replace_in_storage(ob, find, repl):
    text = ob["text"][0]
    if "\ufffc" in find:
        raise SystemExit("refusing to replace across an attachment character")
    hits = []
    start = 0
    while True:
        i = text.find(find, start)
        if i < 0:
            break
        hits.append(i)
        start = i + len(find)
    for i in reversed(hits):
        A = u16(text[:i])
        B = A + u16(find)
        L = u16(repl)
        remap_tables(ob, A, B, L)
        text = text[:i] + repl + text[i + len(find):]
    if hits:
        ob["text"][0] = text
        clean_tables(ob, u16(text))
        rebuild_para_style(ob)
    return len(hits)


def storages(d):
    for ch in d["chunks"]:
        for ar in ch["archives"]:
            for ob in ar.get("objects") or []:
                if isinstance(ob, dict) and ob.get("_pbtype") == "TSWP.StorageArchive" \
                        and isinstance(ob.get("text"), list) and ob["text"]:
                    yield ob


def find_geometry(d, ident):
    for ch in d["chunks"]:
        for ar in ch["archives"]:
            if str(ar["header"].get("identifier")) != str(ident):
                continue
            ob = (ar.get("objects") or [None])[0]
            node = ob
            # walk super chain to the DrawableArchive holding geometry
            while isinstance(node, dict):
                if "geometry" in node:
                    return node["geometry"]
                node = node.get("super")
    return None


def all_objs(d):
    m = {}
    for ch in d["chunks"]:
        for ar in ch["archives"]:
            m[str(ar["header"]["identifier"])] = (ar.get("objects") or [None])[0]
    return m


def storage_by_match(d, match):
    hits = [ob for ob in storages(d) if match in ob["text"][0]]
    if len(hits) != 1:
        raise SystemExit("storage match %r found %d storages" % (match[:50], len(hits)))
    return hits[0]


def shape_by_match(d, match):
    m = all_objs(d)
    sid = None
    for i, ob in m.items():
        if ob is storage_by_match(d, match):
            sid = i
    for i, ob in m.items():
        node = ob
        while isinstance(node, dict):
            st = node.get("ownedStorage")
            if isinstance(st, dict) and str(st.get("identifier")) == sid:
                return i
            node = node.get("super")
    raise SystemExit("no shape owns storage matching %r" % match[:50])


def settext(ob, text, para_style=None, char_runs=None):
    ob["text"][0] = text
    n = u16(text)
    for k, v in list(ob.items()):
        if not k.startswith("table") or not isinstance(v, dict):
            continue
        ents = v.get("entries")
        if not isinstance(ents, list) or not ents:
            continue
        if k == "tableCharStyle":
            continue
        first = ents[0]
        if isinstance(first, dict) and "characterIndex" in first:
            first["characterIndex"] = 0
            v["entries"] = [first]
    if para_style:
        ob["tableParaStyle"]["entries"] = [{"characterIndex": 0, "object": {"identifier": str(para_style)}}]
    rebuild_para_style(ob)
    if "tableLanguage" in ob:
        ob["tableLanguage"]["entries"] = [{"characterIndex": 0, "object": "en"}]
    if char_runs is not None or "tableCharStyle" in ob:
        ents = [{"characterIndex": 0}]
        for s, e, cs in sorted(char_runs or []):
            S, E = u16(text[:s]), u16(text[:e])
            if ents and ents[-1]["characterIndex"] == S:
                ents[-1] = {"characterIndex": S, "object": {"identifier": str(cs)}}
            else:
                ents.append({"characterIndex": S, "object": {"identifier": str(cs)}})
            if E < n:
                ents.append({"characterIndex": E})
        ob["tableCharStyle"] = {"entries": ents}


def zback(d, ident):
    sl = [o for o in all_objs(d).values() if isinstance(o, dict) and o.get("_pbtype") == "KN.SlideArchive"][0]
    for k in ("drawablesZOrder", "ownedDrawables"):
        lst = sl.get(k) or []
        hit = [r for r in lst if str(r.get("identifier")) == str(ident)]
        if not hit:
            raise SystemExit("%s not in %s" % (ident, k))
        sl[k] = hit + [r for r in lst if str(r.get("identifier")) != str(ident)]


def main():
    src, dst, ej = sys.argv[1], sys.argv[2], sys.argv[3]
    edits = json.load(open(ej))
    if os.path.exists(dst):
        raise SystemExit("refusing to overwrite %s" % dst)
    by_file = {}
    for e in edits:
        by_file.setdefault(e["file"], []).append(e)
    report = []
    with zipfile.ZipFile(src) as zf:
        names = set(zf.namelist())
        new = {}
        decl = {fn: [str(x) for e in es if "declare" in e for x in e["declare"]] for fn, es in by_file.items()}
        if any(decl.values()):
            MD = "Index/Metadata.iwa"
            md = IWAFile.from_buffer(zf.read(MD), MD).to_dict()
            meta = md["chunks"][0]["archives"][0]["objects"][0]
            ss = [c for c in meta["components"] if (c.get("locator") or c.get("preferredLocator")) == "DocumentStylesheet"][0]["identifier"]
            ssd = IWAFile.from_buffer(zf.read("Index/DocumentStylesheet.iwa"), "x").to_dict()
            ss_ids = {str(a["header"]["identifier"]) for ch in ssd["chunks"] for a in ch["archives"]}
            for fn, ids in decl.items():
                if not ids:
                    continue
                stem = fn[len("Index/"):-len(".iwa")]
                comp = [c for c in meta["components"] if (c.get("locator") or c.get("preferredLocator")) == stem]
                if len(comp) != 1:
                    raise SystemExit("no unique component for %s" % fn)
                er = comp[0].setdefault("externalReferences", [])
                have = {str(e.get("objectIdentifier")) for e in er}
                for i in ids:
                    if i not in ss_ids:
                        raise SystemExit("style %s is not in DocumentStylesheet" % i)
                    if i not in have:
                        er.append({"componentIdentifier": ss, "objectIdentifier": i})
                report.append({"file": fn, "declared": ids})
            new[MD] = IWAFile.from_dict(md).to_buffer()
        for fn, es in by_file.items():
            es = [e for e in es if "declare" not in e]
            if not es:
                continue
            if fn not in names:
                raise SystemExit("no member %s" % fn)
            d = IWAFile.from_buffer(zf.read(fn), fn).to_dict()
            for e in es:
                if "settext" in e:
                    st = storage_by_match(d, e["settext"])
                    settext(st, e["text"], e.get("para_style"), e.get("char_runs"))
                    report.append({"file": fn, "settext": e["settext"][:30]})
                    continue
                if "setlist" in e:
                    st = storage_by_match(d, e["setlist"])
                    st["tableListStyle"] = {"entries": [{"characterIndex": 0, "object": {"identifier": str(e["list_style"])}}]}
                    report.append({"file": fn, "setlist": e["setlist"][:30]})
                    continue
                if "zback" in e:
                    zback(d, e["zback"])
                    report.append({"file": fn, "zback": e["zback"]})
                    continue
                if "geom" in e and e["geom"] == "match":
                    e["geom"] = shape_by_match(d, e["match"])
                if "geom" in e:
                    g = find_geometry(d, e["geom"])
                    if g is None:
                        raise SystemExit("no geometry for id %s in %s" % (e["geom"], fn))
                    if "x" in e or "y" in e:
                        g.setdefault("position", {})
                        if "x" in e: g["position"]["x"] = float(e["x"])
                        if "y" in e: g["position"]["y"] = float(e["y"])
                    if "w" in e or "h" in e:
                        g.setdefault("size", {})
                        if "w" in e: g["size"]["width"] = float(e["w"])
                        if "h" in e: g["size"]["height"] = float(e["h"])
                    if "flags" in e: g["flags"] = int(e["flags"])
                    report.append({"file": fn, "geom": e["geom"], "now": g})
                    continue
                n = 0
                for ob in storages(d):
                    n += replace_in_storage(ob, e["find"], e["replace"])
                exp = e.get("expect")
                if (exp is None and n < 1) or (exp is not None and n != exp):
                    raise SystemExit("edit %r on %s matched %d time(s), expected %s"
                                     % (e["find"][:60], fn, n, exp if exp is not None else ">=1"))
                report.append({"file": fn, "find": e["find"][:40], "n": n})
            new[fn] = IWAFile.from_dict(d).to_buffer()
        tmp = dst + ".tmp"
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as out:
            for item in zf.infolist():
                out.writestr(item, new[item.filename] if item.filename in new
                             else zf.read(item.filename))
    os.replace(tmp, dst)
    print(json.dumps(report, default=str))


if __name__ == "__main__":
    main()
