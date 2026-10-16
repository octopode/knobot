#!/usr/bin/env python3
"""Index a Keynote .key: ordered slides -> internal file, skip flag, body text, notes.

Reads ONLY Index/*.iwa members straight from the zip with keynote_parser's binary
codec (no YAML parsing, no extraction of Data/ media), so it is fast on GB-sized
decks and avoids keynote-parser's unsafe yaml.load path. Prints JSON to stdout.
Usage: python kn_index.py DECK.key [--geometry]
"""
import json, sys, zipfile, warnings
warnings.filterwarnings("ignore")
from keynote_parser.codec import IWAFile

OBJ_CHAR = "\ufffc"


def load_archives(zf):
    idmap, files = {}, {}
    for name in zf.namelist():
        if not (name.startswith("Index/") and name.endswith(".iwa")):
            continue
        d = IWAFile.from_buffer(zf.read(name), name).to_dict()
        for ch in d.get("chunks", []):
            for a in ch.get("archives", []):
                objs = a.get("objects", [])
                ident = str(a["header"]["identifier"])
                obj = objs[0] if objs else {}
                idmap[ident] = (name, obj.get("_pbtype"), obj)
                files.setdefault(name, []).append(ident)
    return idmap, files


def storage_text(obj):
    t = "".join(s for s in (obj.get("text") or []) if isinstance(s, str))
    return t.replace(OBJ_CHAR, "").strip()


def refs_in(o, out):
    if isinstance(o, dict):
        if set(o.keys()) == {"identifier"}:
            out.append(str(o["identifier"]))
        for v in o.values():
            refs_in(v, out)
    elif isinstance(o, list):
        for v in o:
            refs_in(v, out)
    return out


def geometry(obj):
    g = (obj.get("super") or {}).get("geometry") or obj.get("geometry")
    if not g and isinstance(obj.get("super"), dict):
        g = (obj["super"].get("super") or {}).get("geometry")
    if not g:
        return None
    p, s = g.get("position") or {}, g.get("size") or {}
    return {"x": p.get("x"), "y": p.get("y"), "w": s.get("width"), "h": s.get("height"),
            "angle": g.get("angle", 0)}


def main():
    path = sys.argv[1]
    want_geom = "--geometry" in sys.argv
    with zipfile.ZipFile(path) as zf:
        idmap, files = load_archives(zf)
    show = next(v[2] for v in idmap.values() if v[1] == "KN.ShowArchive")
    nodes = []

    def walk(refs):
        for r in refs:
            n = idmap[str(r["identifier"])][2]
            nodes.append(n)
            walk(n.get("children") or [])
    walk(show["slideTree"]["slides"])

    slides = []
    for i, n in enumerate(nodes, 1):
        sid = str(n["slide"]["identifier"])
        fname, _, sobj = idmap[sid]
        note_ids = set()
        if sobj.get("note"):
            for r in refs_in(idmap[str(sobj["note"]["identifier"])][2], []):
                note_ids.add(r)
        body, notes, shapes = [], [], []
        for ident in files.get(fname, []):
            _, t, o = idmap[ident]
            if t == "TSWP.StorageArchive":
                txt = storage_text(o)
                if txt:
                    (notes if ident in note_ids else body).append(txt)
            if want_geom and t in ("TSWP.ShapeInfoArchive", "TSD.ImageArchive",
                                   "TSD.ShapeArchive", "TSD.MovieArchive",
                                   "TSD.GroupArchive", "KN.PlaceholderArchive"):
                gm = geometry(o)
                if gm:
                    shapes.append({"id": ident, "type": t, **gm})
        rec = {"n": i, "file": fname, "skipped": bool(n.get("isSkipped")),
               "builds": int(n.get("buildEventCount") or 0),
               "body": body, "notes": notes}
        if want_geom:
            rec["shapes"] = shapes
        slides.append(rec)
    size = show.get("size") or {}
    print(json.dumps({"deck": path, "slide_size": [size.get("width"), size.get("height")],
                      "n_slides": len(slides), "slides": slides}, ensure_ascii=False))


if __name__ == "__main__":
    main()
