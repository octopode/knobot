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
    """Where scripts/ lives. Normally the loaded skill's own directory; set KN_SKILL_DIR
    (global or environment) to run this kernel from a checkout instead."""
    override = globals().get("KN_SKILL_DIR") or os.environ.get("KN_SKILL_DIR")
    if override:
        return os.path.realpath(override)
    here = os.path.dirname(sys._getframe().f_code.co_filename)
    if not here:
        raise RuntimeError("keynote-files skill dir unavailable in this runtime; "
                           "set KN_SKILL_DIR to the directory holding scripts/")
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

    Keynote writes one image per build stage in slide order, so a slide with
    buildEventCount N contributes N+1 images and the LAST of each group is the fully
    built slide. Returns [{"slide", "builds", "first", "last"}] with 1-based indices.

    Raises if any slide's build count is UNKNOWN -- Keynote's own save (so any deck
    written by kn_duplicate_slides) omits buildEventCount and records only
    hasExplicitBuilds, and guessing would silently misattribute images. For such decks
    render WITHOUT stages: that already gives each slide's FINAL state, which is what
    layout review needs."""
    idx = kn_index(deck)
    unknown = [s["n"] for s in idx["slides"] if s.get("builds") is None]
    if unknown:
        raise ValueError(
            "build counts unavailable for slide(s) %s (Keynote-saved deck): cannot map "
            "stages to slides. Render without stages=True -- a default render is each "
            "slide's final built state." % unknown[:10])
    out, cursor = [], 1
    for s in idx["slides"]:
        n = s["builds"] + 1
        out.append({"slide": s["n"], "builds": s["builds"], "first": cursor,
                    "last": cursor + n - 1})
        cursor += n
    total = cursor - 1
    if n_images is not None and n_images != total:
        raise ValueError("stage map predicts %d images but %d were exported; "
                         "the builds-per-slide assumption is wrong for this deck"
                         % (total, n_images))
    return out


def kn_slide_ops(src, dst, delete=None, order=None):
    """Delete and/or reorder slides, writing a NEW deck at dst.

    Runs scripts/kn_slide_ops.py in the `keynote` env (keynote-parser's codec), which
    rewrites only KN.ShowArchive.slideTree.slides in Index/Document.iwa and copies every
    other zip member through byte-for-byte: fast on GB decks, no YAML, no media
    rewriting, build animations preserved.

    delete: slide numbers (1-based, as kn_index reports) to drop.
    order:  new sequence of slide numbers, e.g. [3, 1, 2]; a partial list is allowed and
            unnamed slides keep their relative order after the named ones.
    Deleted slides' archives remain as orphans, so the file does not shrink.
    Returns the new slide count. Always RENDER the result before trusting it."""
    src_r, dst_r = os.path.realpath(src), os.path.realpath(dst)
    if src_r == dst_r:
        raise ValueError("dst must differ from src; originals are never edited in place")
    if os.path.exists(dst):
        raise FileExistsError("refusing to overwrite existing %s" % dst)
    if any(dst_r.startswith(os.path.realpath(x) + os.sep) for x in KN_PROTECTED_DIRS):
        raise ValueError("refusing to write into a protected folder: %s" % dst)
    kn_preflight(src)
    os.makedirs(os.path.dirname(dst_r), exist_ok=True)
    script = os.path.join(kn_skill_dir(), "scripts", "kn_slide_ops.py")
    spec = json.dumps({"delete": list(delete or []), "order": list(order or [])})
    p = subprocess.run([os.path.join(KN_BIN, "python"), script, src_r, dst_r, spec],
                       capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError("kn_slide_ops failed: %s" % (p.stderr or p.stdout)[-2000:])
    res = json.loads(p.stdout)
    got = kn_index(dst)["n_slides"]
    if got != res["n_slides"]:
        raise RuntimeError("wrote %d slides but the new deck reports %d" % (res["n_slides"], got))
    return got


def kn_deck_ops(src, dst, ops, timeout=1800, poll=2.0):
    """Structural edits performed by KEYNOTE, writing a NEW deck at dst.

    Deep-copying a slide's protobuf graph (fresh ids for every shape, style and build)
    is the risky part, so Keynote's own commands do it, through the render service queue.

    ops is a list of dicts, applied in order:
      {"op": "dupslide", "slide": 4, "count": 2}                  copies land at the END
      {"op": "text",  "slide": 1, "x": 80, "y": 600, "width": 700, "text": "Hello"}
      {"op": "image", "slide": 1, "x": 80, "y": 100, "width": 400, "height": 0,
                      "path": "/abs/path.png"}                    0 = keep natural size
      {"op": "dupitem", "slide": 2, "index": 1, "to_slide": 5}    copy an item across
      {"op": "delitem", "slide": 2, "index": 3}
      {"op": "copyslide", "deck": "/abs/other.key", "slide": 7}   from ANOTHER deck
    Coordinates are slide points (kn_index(..., geometry=True) reports the same units;
    the deck's canvas size is index["slide_size"]). Text may contain "\\n" for a line
    break but no tabs. Images must live under the teaching folder; the handler copies
    them into Keynote's container itself.

    The saved deck loses buildEventCount (Keynote writes only hasExplicitBuilds), so
    kn_stage_map will refuse on the result; builds themselves survive.
    ALWAYS render the result and look at it. Returns dst."""
    req_dir = os.path.join(KN_RENDER_ROOT, "requests")
    if not os.path.isdir(req_dir):
        raise RuntimeError("render queue missing (%s); is keynote-render installed?" % req_dir)
    if not ops:
        raise ValueError("no operations given")
    src_r, dst_r = os.path.realpath(src), os.path.abspath(dst)
    if src_r == dst_r:
        raise ValueError("dst must differ from src; originals are never edited in place")
    if os.path.exists(dst_r):
        raise FileExistsError("refusing to overwrite existing %s" % dst_r)
    if any(dst_r.startswith(os.path.realpath(x) + os.sep) for x in KN_PROTECTED_DIRS):
        raise ValueError("refusing to write into a protected folder: %s" % dst_r)
    kn_preflight(src)
    job = "d%s-%s" % (time.strftime("%Y%m%d-%H%M%S"), uuid.uuid4().hex[:6])
    tmp = os.path.join(KN_RENDER_ROOT, ".%s.tmp" % job)
    with open(tmp, "w") as fh:
        json.dump({"job": job, "action": "duplicate", "input": src_r, "ops": list(ops),
                   "output": dst_r, "timeout": int(timeout)}, fh)
    os.replace(tmp, os.path.join(req_dir, job + ".json"))
    try:
        os.utime(req_dir, None)
    except OSError:
        pass
    status = os.path.join(KN_RENDER_ROOT, "status", job + ".json")
    deadline = time.time() + timeout + 120
    while time.time() < deadline:
        if os.path.exists(status):
            st = json.load(open(status))
            if st["state"] == "done":
                if not os.path.exists(dst_r):
                    raise RuntimeError("handler reported done but %s is missing" % dst_r)
                return dst_r
            if st["state"] == "error":
                raise RuntimeError("deck ops %s failed: %s" % (job, st.get("error")))
        else:
            rej = [f for f in os.listdir(os.path.join(KN_RENDER_ROOT, "rejected"))
                   if f.startswith(job)]
            if rej:
                raise RuntimeError("request rejected; see %s/render.log" % KN_RENDER_ROOT)
        time.sleep(poll)
    raise TimeoutError("deck ops %s did not finish within %ss" % (job, timeout + 120))


def kn_duplicate_slides(src, dst, spec, timeout=1800, poll=2.0):
    """Duplicate whole slides. spec "3x2" = two copies of slide 3; "3x2,7x1" adds one of
    slide 7. Copies are APPENDED AT THE END (verified), so existing numbering is
    unchanged -- follow with kn_slide_ops(order=...) to place them."""
    ops = []
    for part in spec.split(","):
        n, _, k = part.partition("x")
        ops.append({"op": "dupslide", "slide": int(n), "count": int(k)})
    return kn_deck_ops(src, dst, ops, timeout=timeout, poll=poll)


def kn_add_text(src, dst, slide, text, x, y, width, **kw):
    """Add one text box to a slide. Coordinates and width in slide points."""
    return kn_deck_ops(src, dst, [{"op": "text", "slide": int(slide), "x": int(x),
                                   "y": int(y), "width": int(width), "text": text}], **kw)


def kn_add_image(src, dst, slide, path, x, y, width=0, height=0, **kw):
    """Add one image to a slide. width/height 0 keeps the image's natural size."""
    return kn_deck_ops(src, dst, [{"op": "image", "slide": int(slide), "x": int(x),
                                   "y": int(y), "width": int(width), "height": int(height),
                                   "path": os.path.realpath(path)}], **kw)


def kn_duplicate_item(src, dst, slide, index, to_slide, **kw):
    """Copy one item (text box, image, shape...) onto another slide, appended there.
    `index` is the item's 1-based position as Keynote lists it on that slide."""
    return kn_deck_ops(src, dst, [{"op": "dupitem", "slide": int(slide), "index": int(index),
                                   "to_slide": int(to_slide)}], **kw)


def kn_import_slides_as_images(src, dst, from_deck, slides, keep_notes=True,
                               timeout=1800, **kw):
    """UNSUPPORTED on Keynote 12.2.1: `make image slides` CRASHES Keynote.

    Verified 2026-10-16: the command kills the app (-609 "Connection is invalid" raised
    from the op, Keynote gone) for every input tried -- a 2.6 KB PNG and a 760 KB one,
    paths under ~/thermofiles that `make new image` reads happily in the same session.
    The crash is the command, not the file. Raises rather than taking Keynote down.

    Use kn_merge_slides for same-theme decks. For a FOREIGN theme, the working primitives
    are duplicate-slide + delete-item + add-image (all three verified), i.e. render the
    wanted slide, duplicate a slide in the target, strip its items and drop the render in
    full-bleed -- not yet implemented here.

    Original intent follows.

    Remix across decks: bring slides in from another deck as PICTURE slides.

    Keynote 12.2.1 refuses to copy slides or items between documents over AppleScript
    ("Text items can not be copied", and cross-document references go invalid), so this
    renders the wanted slides with Keynote and appends them to the target deck with
    `make image slides`. Speaker notes are carried across as text when keep_notes.

    WHAT YOU GET: pixel-exact slides, appended at the END, that are NOT editable -- no
    text, no builds, no object structure. Good for reusing a finished slide verbatim;
    wrong if you need to edit the words afterwards. For editable reuse, work within a
    single deck (kn_duplicate_slides) or open both decks and copy by hand in Keynote.

    slides: 1-based slide numbers in from_deck."""
    raise NotImplementedError(
        "`make image slides` crashes Keynote 12.2.1 (-609) for any image; picture import "
        "is unavailable. Same-theme decks: kn_merge_slides. Foreign theme: duplicate a "
        "slide, delete its items and add the rendered PNG (primitives verified, helper "
        "not written).")

    other = os.path.realpath(from_deck)
    idx = kn_index(other)
    imgs = kn_render(other, fmt="png", timeout=timeout)
    # Keynote omits SKIPPED slides from an export (correctly), so image k is the k-th
    # NON-SKIPPED slide, not slide k. Map through that order instead of assuming 1:1.
    shown = [s["n"] for s in idx["slides"] if not s.get("skipped")]
    if len(imgs) != len(shown):
        raise RuntimeError("rendered %d images for %d non-skipped slides (%d total); "
                           "cannot map slides to images"
                           % (len(imgs), len(shown), idx["n_slides"]))
    image_of = {n: imgs[i] for i, n in enumerate(shown)}
    ops = []
    for n in slides:
        n = int(n)
        if not 1 <= n <= idx["n_slides"]:
            raise ValueError("slide %r is outside %s (1-%d)" % (n, other, idx["n_slides"]))
        if n not in image_of:
            raise ValueError("slide %d of %s is SKIPPED, so Keynote exports no image for "
                             "it; unskip it in Keynote or choose another slide"
                             % (n, os.path.basename(other)))
        notes = ""
        if keep_notes:
            notes = " ".join(idx["slides"][n - 1]["notes"]).replace("\n", "\\n")
        ops.append({"op": "imageslide", "path": image_of[n], "notes": notes or "-"})
    return kn_deck_ops(src, dst, ops, timeout=timeout, **kw)


def kn_merge_slides(src, dst, from_deck, slides):
    """Copy slides from another deck into this one, writing a NEW deck at dst.

    This is the file-level equivalent of dragging slides between presentations, and the
    only one available: Keynote's own `duplicate` refuses cross-document copies (-1717 /
    invalid reference), and `make image slides` flattens slides to pictures.

    Each merged slide's OWN objects (drawables, text, builds) are copied with fresh
    identifiers into a new component; the styles they use are referenced where they
    already live in the target, which is what "keep source formatting" means here. So the
    decks must share a theme lineage -- derived from the same original, or the same theme
    with the same style object ids. A foreign theme raises with the unresolvable styles
    listed rather than producing a deck that renders wrong.

    Media is matched by content digest: identical bytes already in the target are
    referenced, never re-registered. This is load-bearing, not an optimisation -- a second
    `datas` entry whose digest duplicates an existing one makes Keynote crash on open.

    slides: source slide numbers, 1-based as kn_index reports them, e.g. [20, 21].
            Merged slides are appended in that order; reorder afterwards with
            kn_slide_ops(order=...).
    Returns the merge report: per-slide object/reference/media counts and the validator's
    reference-integrity checks. Always RENDER the result before trusting it."""
    src_r, dst_r, from_r = (os.path.realpath(p) for p in (src, dst, from_deck))
    if src_r == dst_r:
        raise ValueError("dst must differ from src; originals are never edited in place")
    if os.path.exists(dst):
        raise FileExistsError("refusing to overwrite existing %s" % dst)
    if any(dst_r.startswith(os.path.realpath(x) + os.sep) for x in KN_PROTECTED_DIRS):
        raise ValueError("refusing to write into a protected folder: %s" % dst)
    kn_preflight(src)
    kn_preflight(from_deck)
    nums = [int(n) for n in slides]
    if not nums:
        raise ValueError("no slides given")
    n_from = kn_index(from_deck)["n_slides"]
    bad = [n for n in nums if not 1 <= n <= n_from]
    if bad:
        raise ValueError("slides %s outside %s (1-%d)" % (bad, os.path.basename(from_deck), n_from))
    before = kn_index(src)["n_slides"]
    os.makedirs(os.path.dirname(dst_r), exist_ok=True)
    script = os.path.join(kn_skill_dir(), "scripts", "kn_merge.py")
    p = subprocess.run([os.path.join(KN_BIN, "python"), script, src_r, from_r, dst_r,
                        json.dumps(nums)], capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError("kn_merge failed: %s" % (p.stderr or p.stdout)[-2000:])
    res = json.loads(p.stdout)
    if res.get("unresolved_styles"):
        raise RuntimeError("source styles absent from the target (the decks do not share "
                           "a theme lineage): %s" % json.dumps(res["unresolved_styles"]))
    got = kn_index(dst)["n_slides"]
    if got != before + len(nums):
        raise RuntimeError("expected %d slides, the new deck reports %d"
                           % (before + len(nums), got))
    if res.get("media_not_found_in_source") or res.get("n_missing_media"):
        raise RuntimeError("media did not land: %s" % json.dumps(
            {k: res[k] for k in ("media_not_found_in_source", "n_missing_media")}))
    res["n_slides"] = got
    return res


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
