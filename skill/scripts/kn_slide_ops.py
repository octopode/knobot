#!/usr/bin/env python3
"""Delete and/or reorder slides in a Keynote .key, writing a new deck.

Slide order lives in one place: KN.ShowArchive.slideTree.slides, a list of references.
This rewrites only that list inside Index/Document.iwa (via keynote-parser's codec) and
copies every other zip member through byte-for-byte -- no unpack, no YAML, no media
rewriting. Build animations are preserved. Deleted slides' archives remain as orphans,
so the file does not shrink.

Usage: kn_slide_ops.py SRC DST '{"delete": [6,7], "order": [3,1,2]}'
Prints JSON: {"n_slides": N, "kept": [...]}
"""
import json
import os
import sys
import warnings
import zipfile

warnings.filterwarnings("ignore")
from keynote_parser.codec import IWAFile


def ordered_slide_count(path):
    with zipfile.ZipFile(path) as zf:
        idmap = {}
        for name in zf.namelist():
            if name.startswith("Index/") and name.endswith(".iwa"):
                d = IWAFile.from_buffer(zf.read(name), name).to_dict()
                for ch in d.get("chunks", []):
                    for a in ch.get("archives", []):
                        objs = a.get("objects") or [{}]
                        idmap[str(a["header"]["identifier"])] = objs[0]
        show = next(o for o in idmap.values()
                    if isinstance(o, dict) and o.get("_pbtype") == "KN.ShowArchive")
        total = 0
        stack = list(show["slideTree"]["slides"])
        while stack:
            node = idmap[str(stack.pop()["identifier"])]
            total += 1
            stack.extend(node.get("children") or [])
        return total, len(show["slideTree"]["slides"])


def main():
    src, dst, spec = sys.argv[1], sys.argv[2], json.loads(sys.argv[3])
    delete = set(spec.get("delete") or [])
    order = spec.get("order") or []

    total, top = ordered_slide_count(src)
    if total != top:
        raise SystemExit("deck nests slides (%d total, %d top-level); not supported" % (total, top))
    keep = [i for i in range(1, total + 1) if i not in delete]
    if order:
        named = [i for i in order if i in keep]
        if len(set(named)) != len(named):
            raise SystemExit("order contains duplicates: %r" % order)
        unknown = [i for i in order if i not in keep]
        if unknown:
            raise SystemExit("order names slides that do not exist or were deleted: %r" % unknown)
        keep = named + [i for i in keep if i not in set(named)]
    if not keep:
        raise SystemExit("refusing to produce a deck with no slides")

    with zipfile.ZipFile(src) as zf:
        doc = "Index/Document.iwa"
        if doc not in zf.namelist():
            raise SystemExit("no %s in %s" % (doc, src))
        d = IWAFile.from_buffer(zf.read(doc), doc).to_dict()
        show = None
        for ch in d["chunks"]:
            for a in ch["archives"]:
                for o in (a.get("objects") or []):
                    if isinstance(o, dict) and o.get("_pbtype") == "KN.ShowArchive":
                        show = o
        if show is None:
            raise SystemExit("no KN.ShowArchive in %s" % doc)
        refs = show["slideTree"]["slides"]
        show["slideTree"]["slides"] = [refs[i - 1] for i in keep]
        new_doc = IWAFile.from_dict(d).to_buffer()
        tmp = dst + ".tmp"
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as out:
            for item in zf.infolist():
                out.writestr(item, new_doc if item.filename == doc else zf.read(item.filename))
    os.replace(tmp, dst)
    print(json.dumps({"n_slides": len(keep), "kept": keep}))


if __name__ == "__main__":
    main()
