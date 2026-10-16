#!/usr/bin/env python3
"""keynote-render queue handler (stdlib only; version-agnostic python).

Run by the LaunchAgent local.keynote-render, triggered by WatchPaths on
<ROOT>/requests/. Each request is a JSON file:
    {"job": "<id>", "input": "/abs/path/deck.key",
     "format": "png"|"jpeg"|"pdf", "timeout": 900}
For each request it:
  1. validates it (input must be a .key under ALLOWED_ROOT; job id and format whitelisted),
  2. clones the deck into STAGE/<job>/ -- inside Keynote's own sandbox container (see the
     STAGE note below). The original is never opened by Keynote,
  3. clears the clone's xattrs and has Keynote export it to STAGE/<job>/export via
     export_slides.applescript (paths passed as argv, never interpolated into script source),
     watching the output folder to publish progress and to tell "slow" from "blocked",
  4. moves the results to <ROOT>/renders/<job>/slide-NNN.<ext> (or <job>.pdf) -- back on
     the thermofiles volume, where the agent can read them,
  5. writes <ROOT>/status/<job>.json and appends to <ROOT>/render.log,
  6. on success removes the clone and the request; failed clones are pruned once cold.
Anything in requests/ that is not a valid request is moved to <ROOT>/rejected/.
"""
import errno, json, os, re, shutil, subprocess, sys, time
from datetime import datetime, timezone

ALLOWED_ROOT = os.environ.get("KR_ALLOWED_ROOT", "/Users/impactus/thermofiles/teaching")
ROOT = os.environ.get("KR_ROOT", "/Users/impactus/thermofiles/teaching/_keynote_render")
HERE = os.path.dirname(os.path.abspath(__file__))
EXPORT_CMD = json.loads(os.environ["KR_EXPORT_CMD"]) if "KR_EXPORT_CMD" in os.environ else [
    "/usr/bin/osascript", os.path.join(HERE, "export_slides.applescript")]
OPS_CMD = json.loads(os.environ["KR_OPS_CMD"]) if "KR_OPS_CMD" in os.environ else [
    "/usr/bin/osascript", os.path.join(HERE, "slide_ops.applescript")]
SPEC_RE = re.compile(r"^\d+x\d+(,\d+x\d+)*$")
JOB_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
FORMATS = {"png": "png", "jpeg": "jpeg", "pdf": "pdf"}
KEEP_RENDERS = 40
OPEN_GRACE_S = 900      # Keynote may take minutes to OPEN a big deck before the first image appears
STALL_S = 180           # ...but once images are flowing, this long without a new one means blocked
WORK_TTL_S = 1800       # keep a failed job's clone this long: deleting it while Keynote
                        # still holds it is what produces "can't be opened right now" dialogs
# Where the clone is staged: Keynote's OWN sandbox container.
# Evidence, stated precisely. Fresh copies HAVE opened by hand from plain ~ (the A/B
# probe's home-volume leg, rc 0) and from this container, so location alone does not
# explain anything. What is established is narrower: every HANDLER run staging to the
# thermofiles volume or to ~/keynote-render-stage was refused, and the first handler run
# staging into this container opened the deck and exported it. The mechanism is not
# verified -- Keynote needing no sandbox extension for its own container is a plausible
# guess. Writing here triggers a one-off macOS permission prompt the user must allow.
# Trade-off: the agent cannot read ~/Library, so a failed clone is not inspectable;
# status/<job>.json carries the error instead.
STAGE = os.environ.get("KR_STAGE_DIR", os.path.expanduser(
    "~/Library/Containers/com.apple.iWork.Keynote/Data/Documents/keynote-render"))
# Settle-and-retry around the open. NOT a proven diagnosis: a hand test that copied a
# deck and opened it seconds later succeeded, so "freshly written file" does not by
# itself explain the "can't be opened right now" refusals. Kept because retrying an
# open-stage refusal is cheap and harmless; the real cause is still unidentified.
SETTLE_S = int(os.environ.get("KR_SETTLE_S", "10"))
OPEN_RETRIES = int(os.environ.get("KR_OPEN_RETRIES", "3"))
RETRY_WAIT_S = 20
MAX_REQ_BYTES = 65536
IMG_EXT = (".png", ".jpg", ".jpeg", ".tif", ".tiff")


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def d(*p):
    return os.path.join(ROOT, *p)


def log(msg):
    with open(d("render.log"), "a") as fh:
        fh.write("%s %s\n" % (now(), msg))


def write_status(job, rec):
    rec = dict(rec, job=job, updated=now())
    tmp = d("status", ".%s.tmp" % job)
    with open(tmp, "w") as fh:
        json.dump(rec, fh, indent=1)
    os.replace(tmp, d("status", job + ".json"))


def reject(path, why):
    base = os.path.basename(path)
    dest = d("rejected", "%s.%d" % (base, int(time.time())))
    try:
        os.replace(path, dest)
    except OSError:
        pass
    log("REJECT %s: %s" % (base, why))


def within(path, root):
    rp, rr = os.path.realpath(path), os.path.realpath(root)
    return rp == rr or rp.startswith(rr + os.sep)


def parse_request(path):
    if not path.endswith(".json"):
        raise ValueError("not a .json request")
    if os.path.islink(path) or not os.path.isfile(path):
        raise ValueError("not a regular file")
    if os.path.getsize(path) > MAX_REQ_BYTES:
        raise ValueError("request too large")
    with open(path) as fh:
        req = json.load(fh)
    job = str(req.get("job", ""))
    if not JOB_RE.match(job):
        raise ValueError("bad job id %r" % job)
    inp = str(req.get("input", ""))
    if not inp.lower().endswith(".key"):
        raise ValueError("input must be a .key")
    if not os.path.isabs(inp) or not within(inp, ALLOWED_ROOT):
        raise ValueError("input must be an absolute path under %s" % ALLOWED_ROOT)
    if within(inp, ROOT):
        raise ValueError("input may not live inside the render folder")
    if not os.path.exists(inp):
        raise ValueError("input does not exist")
    fmt = FORMATS.get(str(req.get("format", "png")).lower())
    if not fmt:
        raise ValueError("format must be one of %s" % sorted(FORMATS))
    try:
        timeout = int(req.get("timeout", 900))
    except (TypeError, ValueError):
        raise ValueError("timeout must be an integer")
    timeout = max(30, min(timeout, 3600))
    if os.path.exists(d("renders", job)) or os.path.exists(d("status", job + ".json")):
        raise ValueError("job id already used: %s" % job)
    stages = bool(req.get("stages", False))
    action = str(req.get("action", "render"))
    if action not in ("render", "duplicate"):
        raise ValueError("action must be 'render' or 'duplicate'")
    spec = out = None
    if action == "duplicate":
        spec = req.get("ops")
        if not isinstance(spec, list) or not spec:
            raise ValueError("'ops' must be a non-empty list of operations")
        if len(spec) > 500:
            raise ValueError("too many operations (%d); cap is 500" % len(spec))
        for o in spec:
            validate_op(o)
        out = str(req.get("output", ""))
        if not out.lower().endswith(".key") or not os.path.isabs(out):
            raise ValueError("duplicate needs an absolute .key 'output' path")
        if not within(out, ALLOWED_ROOT) or within(out, ROOT):
            raise ValueError("output must be under %s and outside the render folder" % ALLOWED_ROOT)
        if os.path.exists(out):
            raise ValueError("output already exists: %s" % out)
    return job, os.path.realpath(inp), fmt, timeout, stages, action, spec, out


def clone(src, dst):
    # Plain byte copy, NOT `cp -c` (APFS clone): a hand probe proved Keynote
    # script-opens a plainly copied deck, and the clone flag is the last
    # difference that remained between that probe and this handler.
    args = ["/bin/cp"] + (["-R"] if os.path.isdir(src) else []) + [src, dst]
    if subprocess.call(args, stderr=subprocess.DEVNULL) != 0:
        if os.path.isdir(src):
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)


OP_FIELDS = {"dupslide": ("slide", "count"),
             "text": ("slide", "x", "y", "width", "text"),
             "image": ("slide", "x", "y", "width", "height", "path"),
             "dupitem": ("slide", "index", "to_slide"),
             "delitem": ("slide", "index"),
             "copyslide": ("deck", "slide"),
             "imageslide": ("path", "notes")}


def validate_op(o):
    """Reject anything the AppleScript would mis-parse, before Keynote ever sees it."""
    if not isinstance(o, dict):
        raise ValueError("each op must be an object, got %r" % type(o).__name__)
    name = o.get("op")
    if name not in OP_FIELDS:
        raise ValueError("unknown op %r; expected one of %s" % (name, sorted(OP_FIELDS)))
    for key in OP_FIELDS[name]:
        if key not in o:
            raise ValueError("op %s is missing %r" % (name, key))
    for key in OP_FIELDS[name]:
        if key in ("text", "path", "deck", "notes"):
            v = str(o[key])
            if "\t" in v or "\n" in v or "\r" in v:
                raise ValueError("op %s: %r may not contain tabs or raw newlines "
                                 "(use the two characters \\n for a line break)" % (name, key))
        else:
            if name == "dupitem" and key == "index" and o[key] == -1:
                continue        # -1 = last item on the slide
            if not isinstance(o[key], int) or isinstance(o[key], bool):
                raise ValueError("op %s: %r must be an integer, got %r" % (name, key, o[key]))
    if name == "copyslide":
        other = str(o["deck"])
        if not other.lower().endswith(".key") or not os.path.isabs(other):
            raise ValueError("copyslide needs an absolute .key 'deck' path")
        if not within(other, ALLOWED_ROOT) or within(other, ROOT):
            raise ValueError("source deck must be under %s and outside the render folder"
                             % ALLOWED_ROOT)
        if not os.path.isfile(other):
            raise ValueError("source deck not found: %s" % other)
    if name in ("image", "imageslide"):
        path = str(o["path"])
        if not os.path.isabs(path) or not within(path, ALLOWED_ROOT):
            raise ValueError("image path must be absolute and under %s" % ALLOWED_ROOT)
        if not os.path.isfile(path):
            raise ValueError("image not found: %s" % path)


def write_ops_file(ops, work):
    """Serialise ops to the TSV the AppleScript reads.

    Image files and source decks are copied into the staging folder first: Keynote reads
    them from its own container, the same place it reads the deck being edited."""
    lines = []
    for i, o in enumerate(ops):
        name = o["op"]
        if name == "copyslide":
            local = os.path.join(work, "src%02d.key" % i)
            shutil.copyfile(str(o["deck"]), local)
            clear_xattrs(local)
            vals = [local, o["slide"]]
        elif name in ("image", "imageslide"):
            src = str(o["path"])
            local = os.path.join(work, "img%02d%s" % (i, os.path.splitext(src)[1].lower()))
            shutil.copyfile(src, local)
            clear_xattrs(local)
            vals = ([local, o["notes"] or "-"] if name == "imageslide"
                    else [o["slide"], o["x"], o["y"], o["width"], o["height"], local])
        else:
            vals = [o[k] for k in OP_FIELDS[name]]
        lines.append("\t".join([name] + [str(v) for v in vals]))
    path = os.path.join(work, "ops.tsv")
    with open(path, "w") as fh:
        fh.write("\n".join(lines) + "\n")
    return path


def clear_xattrs(path):
    """Strip every xattr from our own clone (quarantine, provenance, lastuseddate).

    Copies made by the sandbox carry quarantine and provenance markers. The clone is
    ours, was produced on this machine and is deleted after the export, so clearing
    the lot matches what works by hand and removes a whole class of refusals."""
    subprocess.call(["/usr/bin/xattr", "-cr", path],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def natural_key(p):
    nums = re.findall(r"\d+", os.path.basename(p))
    return (int(nums[-1]) if nums else 0, p)


def move_across(src, dst):
    """Move a file that may be crossing volumes.

    STAGE is in Keynote's container on the boot volume, the renders folder is on the
    thermofiles volume, so os.replace raises EXDEV (Errno 18, cross-device link)."""
    try:
        os.replace(src, dst)
    except OSError as e:
        if e.errno != errno.EXDEV:
            raise
        shutil.copy2(src, dst)
        os.remove(src)


def count_outputs(target, fmt):
    if fmt == "pdf":
        return 1 if os.path.isfile(target) else 0
    n = 0
    for _, _, fns in os.walk(target):
        n += sum(1 for f in fns if f.lower().endswith(IMG_EXT))
    return n


def run_export(job, deck, target, fmt, timeout, work, t0, grace=OPEN_GRACE_S, stall=STALL_S,
               stages=False):
    """Run the exporter, publishing progress and killing it if Keynote goes quiet.

    Distinguishes SLOW from BLOCKED: `grace` covers the open phase (no images yet),
    `stall` covers gaps between images once they start. A modal dialog on the user's
    screen looks exactly like a stall, so that is what the error says."""
    logpath = os.path.join(work, "export.out")
    cmd = EXPORT_CMD + [deck, target, fmt, str(max(30, timeout - 30)), "1" if stages else "0"]
    with open(logpath, "w") as out:
        proc = subprocess.Popen(cmd, stdout=out, stderr=subprocess.STDOUT,
                                universal_newlines=True)
        n_last, t_change = 0, time.time()
        while True:
            rc = proc.poll()
            n = count_outputs(target, fmt)
            if n != n_last:
                n_last, t_change = n, time.time()
                write_status(job, {"state": "running", "format": fmt, "n_done": n,
                                   "elapsed_s": round(time.time() - t0, 1)})
            if rc is not None:
                break
            quiet = time.time() - t_change
            limit = grace if n_last == 0 else stall
            if time.time() - t0 > timeout or quiet > limit:
                proc.kill()
                proc.wait()
                why = ("Keynote produced no images within %ds of opening the deck"
                       % grace if n_last == 0 else
                       "Keynote stopped after %d image(s); no progress for %ds" % (n_last, stall))
                if time.time() - t0 > timeout:
                    why = "overall timeout of %ds reached (%d image(s) done)" % (timeout, n_last)
                raise RuntimeError("%s. Keynote is probably showing a modal dialog on the "
                                   "user's screen (check behind other windows); dismiss it "
                                   "and retry." % why)
            time.sleep(2)
    tail = open(logpath).read().strip()[-1500:]
    return proc.returncode, tail


def run_job(job, inp, fmt, timeout, stages=False, action="render", spec=None, out_path=None):
    # ONE fixed staging folder, reused for every job and cleared on entry. A per-job
    # subfolder made macOS re-prompt for access to Keynote's container on every render;
    # a constant path should need the grant only once. Cleared at the START (not after a
    # failure) so Keynote has released the previous clone by then -- deleting a clone it
    # still holds is what produced the "can't be opened right now" dialog cascade.
    work = os.path.join(STAGE, "current")
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work)
    deck = os.path.join(work, os.path.basename(inp))
    t0 = time.time()
    write_status(job, {"state": "running", "input": inp, "format": fmt,
                       "stages": stages, "started": now()})
    log("START %s %s (%s)" % (job, inp, fmt))
    try:
        clone(inp, deck)
        clear_xattrs(deck)
        if fmt == "pdf":
            target = os.path.join(work, job + ".pdf")
        else:
            target = os.path.join(work, "export")
            os.makedirs(target)
        time.sleep(SETTLE_S)
        if action == "duplicate":
            staged_out = os.path.join(work, "out.key")
            ops_file = write_ops_file(spec, work)
            p = subprocess.run(OPS_CMD + [deck, staged_out, ops_file, str(max(30, timeout - 30))],
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               universal_newlines=True, timeout=timeout)
            if p.returncode != 0:
                raise RuntimeError("duplicate failed (rc=%d): %s" % (p.returncode, p.stdout.strip()[-1500:]))
            if not os.path.exists(staged_out):
                raise RuntimeError("Keynote reported success but no file was saved")
            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            move_across(staged_out, out_path)
            write_status(job, {"state": "done", "input": inp, "action": action,
                               "ops": [o["op"] for o in spec],
                               "elapsed_s": round(time.time() - t0, 1),
                               "n_outputs": 1, "outputs": [out_path]})
            log("DONE %s ops=%s -> %s" % (job, [o["op"] for o in spec], out_path))
            return
        for attempt in range(1, OPEN_RETRIES + 1):
            rc, tail = run_export(job, deck, target, fmt, timeout, work, t0, stages=stages)
            if rc == 0:
                break
            # Only an open-stage refusal is worth retrying; a real export error is not.
            retriable = ("can't be opened" in tail or "can\u2019t be opened" in tail
                         or "returned no document" in tail)
            if not retriable or attempt == OPEN_RETRIES:
                raise RuntimeError("export failed (rc=%d) after %d attempt(s): %s"
                                   % (rc, attempt, tail))
            log("RETRY %s attempt %d/%d after open refusal" % (job, attempt, OPEN_RETRIES))
            write_status(job, {"state": "running", "format": fmt,
                               "note": "open refused; retrying (%d/%d)" % (attempt, OPEN_RETRIES),
                               "elapsed_s": round(time.time() - t0, 1)})
            time.sleep(RETRY_WAIT_S)
            for stale in os.listdir(target) if os.path.isdir(target) else []:
                try:
                    os.remove(os.path.join(target, stale))
                except OSError:
                    pass
        out = d("renders", job + ".partial")
        os.makedirs(out)
        outputs = []
        if fmt == "pdf":
            if not os.path.isfile(target):
                raise RuntimeError("Keynote reported success but no PDF was written")
            dest = os.path.join(out, job + ".pdf")
            move_across(target, dest)
            outputs.append(dest)
        else:
            imgs = []
            for dp, _, fns in os.walk(target):
                imgs += [os.path.join(dp, f) for f in fns if f.lower().endswith(IMG_EXT)]
            if not imgs:
                raise RuntimeError("Keynote reported success but no images were written")
            for i, src in enumerate(sorted(imgs, key=natural_key), 1):
                dest = os.path.join(out, "slide-%03d%s" % (i, os.path.splitext(src)[1].lower()))
                move_across(src, dest)
                outputs.append(dest)
        final = d("renders", job)
        os.replace(out, final)
        outputs = [os.path.join(final, os.path.basename(o)) for o in outputs]
        write_status(job, {"state": "done", "input": inp, "format": fmt, "stages": stages,
                           "elapsed_s": round(time.time() - t0, 1),
                           "n_outputs": len(outputs), "outputs": outputs})
        log("DONE %s %d file(s) in %.1fs" % (job, len(outputs), time.time() - t0))
    except subprocess.TimeoutExpired:
        write_status(job, {"state": "error", "input": inp, "format": fmt,
                           "error": "timed out after %ds; Keynote may be showing a dialog" % timeout})
        log("TIMEOUT %s" % job)
    except Exception as e:  # noqa: BLE001  report every failure back to the client
        write_status(job, {"state": "error", "input": inp, "format": fmt, "error": str(e)})
        log("ERROR %s %s" % (job, e))
    finally:
        # Only clear the clone on success. After a failure Keynote may still have the
        # file open; deleting it is what leaves "can't be opened right now" dialogs
        # that then block every later job. prune() removes it once it is cold.
        if os.path.isdir(d("renders", job)) or (action == "duplicate" and out_path
                                                 and os.path.exists(out_path)):
            shutil.rmtree(work, ignore_errors=True)   # tidy after success; failures are
                                                      # left for the next job to clear
        partial = d("renders", job + ".partial")
        if os.path.isdir(partial):
            shutil.rmtree(partial, ignore_errors=True)


def prune():
    # STAGE holds one reused "current" folder, cleared at the start of each job.
    jobs = [e for e in os.listdir(d("renders")) if not e.endswith(".partial")]
    jobs.sort(key=lambda j: os.path.getmtime(d("renders", j)))
    for j in jobs[:-KEEP_RENDERS] if len(jobs) > KEEP_RENDERS else []:
        shutil.rmtree(d("renders", j), ignore_errors=True)
        try:
            os.remove(d("status", j + ".json"))
        except OSError:
            pass


def suppress_restore_prompt():
    """Keep a Keynote crash from wedging the next job.

    After a crash macOS offers to reopen the documents that were open at the time, and
    that modal blocks the `open` command of the next render indefinitely. The answer is
    always no here, and the deck in the restore list may be the very file that crashed
    Keynote. Both keys are asserted on every pass because a Keynote upgrade or a
    Preferences reset can quietly restore the default.
    """
    for key, val in (("NSQuitAlwaysKeepsWindows", "false"),
                     ("ApplePersistenceIgnoreState", "true")):
        try:
            subprocess.run(["/usr/bin/defaults", "write", "com.apple.iWork.Keynote",
                            key, "-bool", val], capture_output=True, timeout=20)
        except Exception as e:  # noqa: BLE001
            log("could not set %s: %s" % (key, e))
    saved = os.path.expanduser(
        "~/Library/Saved Application State/com.apple.iWork.Keynote.savedState")
    if os.path.isdir(saved):
        # Window-position state only. Removing it drops a pending restore request; it is
        # recreated by Keynote as needed.
        try:
            shutil.rmtree(saved)
            log("cleared a pending Keynote restore request")
        except Exception as e:  # noqa: BLE001
            log("could not clear saved state: %s" % e)


def main():
    suppress_restore_prompt()
    for sub in ("requests", "renders", "status", "work", "rejected"):
        os.makedirs(d(sub), exist_ok=True)
    os.makedirs(STAGE, exist_ok=True)
    while True:
        entries = sorted(os.listdir(d("requests")))
        if not entries:
            break
        for name in entries:
            path = d("requests", name)
            try:
                job, inp, fmt, timeout, stages, action, spec, out = parse_request(path)
            except Exception as e:  # noqa: BLE001
                reject(path, e)
                continue
            os.remove(path)
            run_job(job, inp, fmt, timeout, stages, action, spec, out)
    prune()
    return 0


if __name__ == "__main__":
    sys.exit(main())
