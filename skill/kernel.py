import json
import os
import re
import subprocess
import sys
import time
import uuid
import zipfile

KN_BIN = "/Users/impactus/.claude-science/conda/envs/keynote/bin"
KN_WORK_DIR = "/Users/impactus/thermofiles/teaching/_keynote_work"
KN_RENDER_ROOT = "/Users/impactus/thermofiles/teaching/_keynote_render"
KN_PROTECTED_DIRS = ("/Users/impactus/thermofiles/teaching/pastslides",)


def kn_skill_dir():
    here = os.path.dirname(sys._getframe().f_code.co_filename)
    if not here:
        raise RuntimeError("keynote-files skill dir unavailable in this runtime")
    return here


def kn_preflight(deck):
    """Refuse decks that could trigger keynote-parser's unsafe yaml.load (issue #90).
    Returns the deck's member count. Every helper that runs keynote-parser calls this."""
    if not os.path.exists(deck):
        raise FileNotFoundError(deck)
    if os.path.isdir(deck):
        raise ValueError("package-style (directory) .key decks are not supported yet: %s" % deck)
    with zipfile.ZipFile(deck) as zf:
        names = zf.namelist()
    bad = [n for n in names if n.lower().endswith((".yaml", ".yml"))]
    if bad:
        raise ValueError("UNSAFE deck: contains YAML members %s; do not run keynote-parser on it" % bad[:5])
    return len(names)


def kn_index(deck, geometry=False):
    """Ordered slide list: n, internal file, skipped, body text, speaker notes
    (+ shapes with x/y/w/h in slide points when geometry=True). Safe on any deck:
    decodes the binary .iwa members only, never YAML, never extracts media."""
    script = os.path.join(kn_skill_dir(), "scripts", "kn_index.py")
    args = [os.path.join(KN_BIN, "python"), script, deck] + (["--geometry"] if geometry else [])
    p = subprocess.run(args, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError("kn_index failed: %s" % p.stderr[-2000:])
    return json.loads(p.stdout)


def kn_find(deck, pattern, notes=False, regex=False):
    """Slides whose body (or notes, if notes=True) text contains pattern.
    Returns [(slide_n, matching_text), ...]."""
    rx = re.compile(pattern if regex else re.escape(pattern))
    hits = []
    for s in kn_index(deck)["slides"]:
        for t in s["body"] + (s["notes"] if notes else []):
            if rx.search(t):
                hits.append((s["n"], t))
    return hits


def kn_new_copy_path(src, suffix="edit"):
    """A fresh output path in KN_WORK_DIR: <stem>_<suffix>_<YYYYmmdd-HHMMSS>.key"""
    os.makedirs(KN_WORK_DIR, exist_ok=True)
    stem = os.path.splitext(os.path.basename(src))[0]
    return os.path.join(KN_WORK_DIR, "%s_%s_%s.key" % (stem, suffix, time.strftime("%Y%m%d-%H%M%S")))


def kn_replace(src, dst, pairs, regex=False):
    """Text find/replace across a deck, writing a NEW file dst (src is never modified).
    pairs: [(find, replace), ...]. Literal by default: find is re.escape'd and backslashes
    in replace are escaped, because keynote-parser feeds both to re.sub. regex=True passes
    them through untouched. Returns keynote-parser's report lines."""
    src_r, dst_r = os.path.realpath(src), os.path.realpath(dst)
    if src_r == dst_r:
        raise ValueError("dst must differ from src; originals are never edited in place")
    if os.path.exists(dst):
        raise FileExistsError("refusing to overwrite existing %s" % dst)
    if any(dst_r.startswith(os.path.realpath(p) + os.sep) for p in KN_PROTECTED_DIRS):
        raise ValueError("refusing to write into a protected folder: %s" % dst)
    kn_preflight(src)
    reps = []
    for f, r in pairs:
        if not regex:
            f, r = re.escape(f), r.replace("\\", "\\\\")
        reps.append({"find": f, "replace": r})
    os.makedirs(os.path.dirname(dst_r), exist_ok=True)
    spec = os.path.join(os.path.dirname(dst_r), ".kn_replacements_%s.json" % uuid.uuid4().hex[:8])
    with open(spec, "w") as fh:
        json.dump({"replacements": reps}, fh)
    try:
        p = subprocess.run([os.path.join(KN_BIN, "keynote-parser"), "replace",
                            "--replacements", spec, "-o", dst, src],
                           capture_output=True, text=True)
    finally:
        os.remove(spec)
    if p.returncode != 0:
        raise RuntimeError("keynote-parser replace failed: %s" % (p.stderr or p.stdout)[-2000:])
    report = [ln for ln in p.stdout.splitlines() if ln.startswith("Replaced")]
    if not report:
        print("WARNING: no text matched; %s is an unchanged copy" % dst)
    return report


def kn_render(deck, fmt="png", timeout=900, wait=True, poll=2.0, stages=False):
    """Ask Keynote (via the user's keynote-render LaunchAgent) to export deck.
    deck must be under the teaching folder and NOT inside _keynote_render. The handler
    clones it first, so the deck itself is never opened. Returns the list of output
    paths (slide-001.png, ... or <job>.pdf).

    stages=False (default) gives ONE image per slide -- which for a slide with a build
    is its FIRST stage, i.e. before anything is revealed. Pass stages=True to export
    every build stage; the image count then exceeds the slide count and
    kn_stage_map(deck) tells you which images belong to which slide.
    Raises with the handler's error text."""
    req_dir = os.path.join(KN_RENDER_ROOT, "requests")
    if not os.path.isdir(req_dir):
        raise RuntimeError("render queue missing (%s); is keynote-render installed?" % req_dir)
    job = "r%s-%s" % (time.strftime("%Y%m%d-%H%M%S"), uuid.uuid4().hex[:6])
    tmp = os.path.join(KN_RENDER_ROOT, ".%s.tmp" % job)
    with open(tmp, "w") as fh:
        json.dump({"job": job, "input": os.path.realpath(deck), "format": fmt,
                   "timeout": int(timeout), "stages": bool(stages)}, fh)
    os.replace(tmp, os.path.join(req_dir, job + ".json"))
    # launchd's WatchPaths occasionally misses a newly written file; touching the
    # directory re-triggers it. Observed once: a request sat unclaimed for 60 s.
    try:
        os.utime(req_dir, None)
    except OSError:
        pass
    if not wait:
        return job
    status = os.path.join(KN_RENDER_ROOT, "status", job + ".json")
    deadline = time.time() + timeout + 120
    queued_deadline = time.time() + 60
    while time.time() < deadline:
        if os.path.exists(status):
            st = json.load(open(status))
            if st["state"] == "done":
                return st["outputs"]
            if st["state"] == "error":
                raise RuntimeError("render %s failed: %s" % (job, st.get("error")))
        elif time.time() > queued_deadline and os.path.exists(os.path.join(req_dir, job + ".json")):
            raise RuntimeError("render request not picked up after 60 s; is the "
                               "local.keynote-render LaunchAgent loaded? (job %s)" % job)
        else:
            rej = [f for f in os.listdir(os.path.join(KN_RENDER_ROOT, "rejected")) if f.startswith(job)]
            if rej:
                raise RuntimeError("render request rejected; see %s/render.log" % KN_RENDER_ROOT)
        time.sleep(poll)
    raise TimeoutError("render %s did not finish within %ss" % (job, timeout + 120))


def kn_stage_map(deck, n_images=None):
    """Map exported images to slides when rendering with stages=True.

    Keynote writes one image per build stage, in slide order. A slide with
    buildEventCount N contributes N+1 images (initial state plus one per event), so the
    LAST image of each group is the fully built slide. Returns
    [{"slide": n, "builds": N, "first": i, "last": j}, ...] with 1-based image indices.
    Pass n_images to assert the mapping matches what was actually exported."""
    idx = kn_index(deck)
    out, cursor = [], 1
    for s in idx["slides"]:
        n = (s.get("builds") or 0) + 1
        out.append({"slide": s["n"], "builds": n - 1, "first": cursor, "last": cursor + n - 1})
        cursor += n
    total = cursor - 1
    if n_images is not None and n_images != total:
        raise ValueError("stage map predicts %d images but %d were exported; "
                         "the builds-per-slide assumption is wrong for this deck"
                         % (total, n_images))
    return out


def kn_contact_sheet(images, out_path, cols=4, thumb_w=480, start=1):
    """Tile slide images into one labelled PNG for a whole-deck overview."""
    from PIL import Image, ImageDraw
    thumbs = []
    for p in images:
        im = Image.open(p).convert("RGB")
        im.thumbnail((thumb_w, thumb_w * 4))
        thumbs.append(im)
    if not thumbs:
        raise ValueError("no images")
    tw, th = max(t.width for t in thumbs), max(t.height for t in thumbs)
    pad, lab = 8, 22
    rows = (len(thumbs) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * (tw + pad) + pad, rows * (th + lab + pad) + pad), "white")
    dr = ImageDraw.Draw(sheet)
    for i, t in enumerate(thumbs):
        r, c = divmod(i, cols)
        x, y = pad + c * (tw + pad), pad + r * (th + lab + pad)
        dr.text((x, y), "slide %d" % (start + i), fill="black")
        sheet.paste(t, (x, y + lab))
        dr.rectangle([x - 1, y + lab - 1, x + t.width, y + lab + t.height], outline="gray")
    sheet.save(out_path)
    return out_path


def kn_offslide(index, slide_n, margin=2.0):
    """Shapes on a slide (needs kn_index(..., geometry=True)) whose box extends past the
    slide edge. Full-bleed images do this on purpose; text boxes usually should not.
    Group children are in group coordinates, so check those visually."""
    W, H = index["slide_size"]
    s = index["slides"][slide_n - 1]
    out = []
    for sh in s.get("shapes", []):
        if None in (sh["x"], sh["y"], sh["w"], sh["h"]):
            continue
        if (sh["x"] < -margin or sh["y"] < -margin or
                sh["x"] + sh["w"] > W + margin or sh["y"] + sh["h"] > H + margin):
            out.append(sh)
    return out
