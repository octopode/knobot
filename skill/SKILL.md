---
name: keynote-files
description: "Edit and visually check Apple Keynote (.key) decks from Claude Science without controlling the Keynote app: index slides, find/replace text, unpack/repack structure with keynote-parser, then render through the user's keynote-render LaunchAgent and inspect the slide images for overflow and collisions. Use for any task that reads, edits, or reviews a .key file; for building a brand-new deck from scratch use python-pptx instead. Do NOT try osascript/AppleScript/JXA or a Keynote MCP connector: Apple Events are blocked in this sandbox."
---

# keynote-files

Work on Keynote decks as FILES. The sandbox cannot drive Keynote (Apple Events,
local MCP connectors and localhost are all blocked), so never try osascript, JXA
or a Keynote connector. Rendering goes through a queue that the user's own
LaunchAgent serves outside the sandbox.

Status: v1.0. Validated end to end 2026-10-16 on Keynote 12.2.1 with a real 65-slide,
158 MB deck: `kn_replace` round-trips and reopens cleanly; the render loop produced
correct 1920x1080 PNGs on six consecutive runs (65 slides in ~28 s); build-stage export
matched its predicted image count exactly (78 images for 65 slides, 11 built).

## Hard rules

1. **Never modify an original deck.** Every edit writes a NEW file, by default in
   `KN_WORK_DIR` (`teaching/_keynote_work/`). `teaching/pastslides/` is protected.
   `kn_replace` enforces this; keep the same discipline for unpack/pack edits.
2. **Trusted decks only, and preflight first.** keynote-parser loads `*.iwa.yaml`
   members with an unsafe YAML loader (upstream issue #90 = code execution).
   `kn_preflight(deck)` refuses any deck with YAML members; every helper calls it.
   Never pass `--replacements` a YAML file; the helpers use JSON.
3. **Never trust a deck's embedded previews** (`preview*.jpg`, Data thumbnails).
   They are written by Keynote on save and are stale after any edit. Render.
4. **Do not `rm` in granted folders.** Leave old work files for the user; the
   render handler prunes its own outputs (keeps the newest 40 jobs).
5. **Do NOT use raw unpack -> edit -> pack yet.** It silently corrupts text LAYOUT:
   after a round-trip Keynote rendered a two-paragraph title as its first word only
   ("Combing" instead of "Combing Transcriptomes / FOR SECRETS OF DEEP-SEA SURVIVAL"),
   while `kn_index` still showed the full string -- the text survives, the paragraph
   bookkeeping Keynote lays out from does not. `kn_replace` is unaffected (keynote-parser
   fixes that bookkeeping itself). Verify any structural edit by RENDERING it.

## Environment

- Conda env `keynote` (python 3.12): `keynote-parser` 1.14.5.0 and `python-pptx`,
  both from pip (keynote-parser is not on conda-forge). If missing:
  `manage_environments(create, name="keynote", python_version="3.12", packages=["pip"])`,
  then `manage_packages(install, environment="keynote", packages=["keynote-parser","python-pptx"], use_pip=True)`.
- `skill("keynote-files")` loads helpers into any python kernel; they shell out to
  `KN_BIN` (the keynote env), so they work from the default `python` env too.

## Helpers (kernel.py)

| call | does |
|---|---|
| `kn_index(deck, geometry=False)` | ordered slides: `n`, internal `file`, `skipped`, `builds` (build-event count), `body` text, `notes`; with `geometry=True` also `shapes` (type, x, y, w, h in slide points). Decodes binary `.iwa` only: fast on GB decks, no YAML. |
| `kn_find(deck, pattern, notes=False, regex=False)` | `[(slide_n, text)]` matches |
| `kn_new_copy_path(src, suffix)` | fresh timestamped path in `KN_WORK_DIR` |
| `kn_replace(src, dst, [(find, repl), ...], regex=False)` | batch text replace into NEW file `dst`; returns keynote-parser's "Replaced ..." lines (empty means nothing matched) |
| `kn_render(deck, fmt="png"\|"jpeg"\|"pdf", timeout=900, stages=False)` | Keynote export via the queue; returns `[.../slide-001.png, ...]` or `[<job>.pdf]` |
| `kn_stage_map(deck, n_images=None)` | with `stages=True`, maps images to slides: `[{slide, builds, first, last}]`, `last` = fully built |
| `kn_contact_sheet(images, out_path, cols=4)` | labelled overview grid of many slides |
| `kn_offslide(index, n, margin=2)` | shapes whose box crosses the slide edge (needs geometry) |

## Workflow

1. **Locate**: `idx = kn_index(deck)`; map the user's words ("the roadmap slide",
   "slide 12") to slide numbers with `kn_find` / `idx["slides"]`.
2. **Baseline render** (before editing, when layout matters): `before = kn_render(deck)`.
3. **Edit** into a new file: `dst = kn_new_copy_path(deck, "edit")`, then
   `kn_replace(deck, dst, pairs)`. Check the report: one line per changed text
   storage. Then `kn_find(dst, new_text)` to confirm.
4. **Render and inspect**: `after = kn_render(dst)`. **If any slide you touched has
   builds (`kn_index` -> `builds` > 0), render with `stages=True`** and use
   `kn_stage_map` to find each slide's FINAL image: a default render gives one image per
   slide, which for a built slide is its first stage, with later content not yet
   revealed. Verified on a real deck: slide 2's first stage showed only the heading, the
   final stage showed all three list items. Mistaking that for missing content is the
   easiest wrong call this skill can make.
   Look at every slide you changed with `host.view_image(path)` (crop to zoom); use
   `kn_contact_sheet` for a whole-deck pass. Compare against `before`. Check for:
   text overflow or clipping, text auto-shrunk too small, wrapped titles pushing
   into other objects, overlaps between text and images, anything past the slide edge.
   Keynote's own layout can clip text the file contains in full (seen on a real title
   slide: stored "Combing Transcriptomes / FOR SECRETS OF DEEP-SEA SURVIVAL", rendered
   "Combing"), so report what the RENDER shows, not what `kn_index` reports.
5. **Fix and repeat** from step 3 (always from the last good file, into a new path)
   until clean. Report the final path and which slides changed.

## How replace works (gotchas)

- keynote-parser feeds `find` and `replace` to `re.sub`. `kn_replace` escapes
  both (literal by default); pass `regex=True` only when you mean a regex.
- Matching is per text storage (one text box / placeholder / notes body). A
  `find` spanning two boxes never matches. Multiline text is fine
  (keynote-parser fixes paragraph-style bookkeeping).
- It touches speaker notes too, if the text appears there.
- Longer replacement text does NOT resize boxes: Keynote reflows on open, and
  that is exactly what rendering catches.

## Structural edits (beyond text): unpack -> edit YAML -> pack  [UNSAFE, v0.2]

This path WORKS mechanically but corrupts text layout -- see Hard rule 5. Deleting
slides by trimming `KN.ShowArchive.slideTree.slides` in `Index/Document.iwa.yaml` and
repacking produced a deck Keynote opens and `kn_index` reports correctly, yet the title
rendered as one word. Until the paragraph-style bookkeeping is handled, use it only for
throwaway test decks, and always render the result.

Mechanics, for when that is fixed: `keynote-parser unpack DECK -o DIR` writes
`Index/*.iwa.yaml` plus `Data/` media; `keynote-parser pack DIR -o NEW.key` rebuilds.
`kn_index(..., geometry=True)` gives the slide file and shape ids;
`keynote-parser cat DECK Index/Slide-478.iwa` decodes one member (note `.iwa`, not
`.iwa.yaml`). Trimming the slide list leaves orphan archives and media in place, so the
file does not shrink. Unpacking copies all media -- GB decks need that much scratch.

`keynote_parser.file_utils.dump_yaml` returns BYTES. Write with `open(path, "wb")` to a
temp file and `os.replace` it in: `open(path, "w").write(dump_yaml(d))` truncates the
file before the TypeError fires and destroys it.

Not supported in v0.2: adding/reordering slides, replacing images, editing tables or
charts, package-style (directory) decks. New decks from scratch: python-pptx in the
`keynote` env (Keynote imports .pptx).

## Rendering service (keynote-render)

Installed by the user from `teaching/_keynote_render/setup/install.sh`, which copies the
handler outside the writable folder. LaunchAgent `local.keynote-render` watches
`teaching/_keynote_render/requests/` (WatchPaths + RunAtLoad). For each JSON request it
validates the input, copies the deck into ONE reused staging folder, clears the copy's
xattrs, has Keynote open and export it, moves the images to
`<ROOT>/renders/<job>/slide-NNN.png`, and writes `status/<job>.json` + `render.log`.

Hard-won constraints -- change these only with evidence:

- **Stage inside Keynote's own container**
  (`~/Library/Containers/com.apple.iWork.Keynote/Data/Documents/keynote-render`).
  Handler runs staging to the thermofiles volume or to `~/keynote-render-stage` were
  refused with "can't be opened right now / Operation not permitted", through every
  combination of copy method, xattr state, launch context, settle delay and retries.
  The first run staging into the container worked. The mechanism is unverified; treat
  the location as load-bearing. Decks the user has opened by hand behave differently,
  so hand probes are NOT a valid comparison.
- **The interpreter must not be an Apple platform binary.** `/usr/bin/python3` under
  launchd is denied access to the thermofiles volume; the plist runs the `keynote` env's
  python3.12 and `install.sh` checks it exists.
- **Cross-volume moves**: outputs move from the container (boot volume) to
  `teaching/` (its own APFS volume), so `os.replace` raises EXDEV; the handler
  copy-then-deletes.
- **macOS prompts once per render** to let the interpreter write into Keynote's
  container, and the grant does not persist (unsigned binary). The user can add the
  interpreter to Full Disk Access to make it stick -- their call, since that is a broad
  grant on an interpreter the skill also shells out to.

Progress and failure handling: `status/<job>.json` carries `n_done` while images land.
`OPEN_GRACE_S` (900) covers the open phase, `STALL_S` (180) gaps between images; a
blocked Keynote is reported as "probably showing a modal dialog" rather than hanging.
A failed clone is kept until the next job clears it -- deleting a clone Keynote still
holds produces a modal dialog that then blocks EVERY later job, so a run of fast
identical failures usually means a dialog is waiting on the user's screen, invisible
to the agent. Ask the user to check behind other windows and quit Keynote before
retrying. Exports are whole-deck (65 slides in ~28 s on this Mac) and Keynote names files after
the destination folder (`export.001.png`), which the handler renames to slide-NNN.
`stages=True` sets Keynote's `all stages` export option.

`kn_render` touches the requests directory after writing a request, because WatchPaths
was once observed to miss a new file ("not picked up after 60 s"). If that error still
appears, the agent can retry; if it persists the LaunchAgent is not loaded.
