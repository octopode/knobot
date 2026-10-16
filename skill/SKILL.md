---
name: keynote-files
description: "Edit and visually check Apple Keynote (.key) decks from Claude Science without controlling the Keynote app: index slides, find/replace text, unpack/repack structure with keynote-parser, then render through the user's keynote-render LaunchAgent and inspect the slide images for overflow and collisions. Use for any task that reads, edits, or reviews a .key file; for building a brand-new deck from scratch use python-pptx instead. Do NOT try osascript/AppleScript/JXA or a Keynote MCP connector: Apple Events are blocked in this sandbox."
---

# keynote-files

Work on Keynote decks as FILES. The sandbox cannot drive Keynote (Apple Events,
local MCP connectors and localhost are all blocked), so never try osascript, JXA
or a Keynote connector. Rendering goes through a queue that the user's own
LaunchAgent serves outside the sandbox.

Status: v1.1. Validated end to end 2026-10-16 on Keynote 12.2.1 with a real 65-slide,
158 MB deck: `kn_replace` round-trips and reopens cleanly; rendering produced correct
1920x1080 PNGs on nine consecutive runs (65 slides in ~28 s); build-stage export matched
its predicted image count exactly (78 images for 65 slides, 11 built); delete+reorder and
duplicate were each verified by rendering the result.

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
5. **Verify every structural edit by RENDERING it.** `kn_index` reads the file; only a
   render shows what Keynote draws, and the two can disagree (see the workflow note on
   clipped text).

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
| `kn_stage_map(deck, n_images=None)` | with `stages=True`, maps images to slides: `[{slide, builds, first, last}]`, `last` = fully built; REFUSES when build counts are unknown |
| `kn_slide_ops(src, dst, delete=[...], order=[...])` | delete and/or reorder slides into a NEW deck (file-level, ~6 s on a 158 MB deck, no Keynote) |
| `kn_duplicate_slides(src, dst, "3x2")` | duplicate slides via Keynote itself; copies are APPENDED at the end |
| `kn_merge_slides(src, dst, from_deck, [20, 21])` | copy slides from ANOTHER deck into a NEW deck at `dst` (file-level); returns per-slide object/media counts; requires a shared theme lineage |
| `kn_contact_sheet(images, out_path, cols=4)` | labelled overview grid of many slides |
| `kn_offslide(index, n, margin=2)` | shapes whose box crosses the slide edge (needs geometry) |

## Workflow

1. **Locate**: `idx = kn_index(deck)`; map the user's words ("the roadmap slide",
   "slide 12") to slide numbers with `kn_find` / `idx["slides"]`.
2. **Baseline render** (before editing, when layout matters): `before = kn_render(deck)`.
3. **Edit** into a new file: `dst = kn_new_copy_path(deck, "edit")`, then
   `kn_replace(deck, dst, pairs)`. Check the report: one line per changed text
   storage. Then `kn_find(dst, new_text)` to confirm.
4. **Render and inspect**: `after = kn_render(dst)`. A default render gives ONE image
   per slide and it is that slide's FINAL build stage (verified byte-identical to the
   last stage image), which is what layout review wants. Use `stages=True` only to see
   intermediate stages; then `kn_stage_map` says which images belong to which slide.
   Look at every slide you changed with `host.view_image(path)` (crop to zoom); use
   `kn_contact_sheet` for a whole-deck pass. Compare against `before`. Check for:
   text overflow or clipping, text auto-shrunk too small, wrapped titles pushing
   into other objects, overlaps between text and images, anything past the slide edge.
   Keynote's own layout can clip text the file contains in full (seen on a real title
   slide: stored "Combing Transcriptomes / FOR SECRETS OF DEEP-SEA SURVIVAL", rendered
   "Combing" -- confirmed by the user as how Keynote really draws it), so report what
   the RENDER shows, not what `kn_index` reports.
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

## Structural edits: delete, reorder, duplicate

**Delete and reorder — `kn_slide_ops`, file-level.** Slide order is one list of
references (`KN.ShowArchive.slideTree.slides` in `Index/Document.iwa`). The helper
rewrites only that list with keynote-parser's codec and copies every other zip member
through byte-for-byte: no unpack, no YAML, no media rewriting. ~6 s on a 158 MB deck,
Keynote not involved. Build animations and content are untouched (verified: a 65->5
slide cut reordered to [5,4,1,2,3] kept build counts 1 and 3 on the right slides and
rendered in the requested order). Deleted slides leave orphan archives behind, so the
file does not shrink. Decks whose slideTree nests slides are refused rather than
mangled.

**Duplicate — `kn_duplicate_slides`, via Keynote.** Deep-copying a slide's protobuf
graph (fresh ids for every shape, style and build) is the risky part, so Keynote's own
`duplicate` command does it, through the same service queue as rendering. Verified:
`"4x2"` on a 5-slide deck gave 7 slides whose copies render identically to the source
and still animate (a stages render returned 17 images for 7 slides).

Two consequences worth knowing:
- Copies are **appended at the END**, not inserted after the source. Existing numbering
  is unchanged; follow with `kn_slide_ops(order=...)` to place them.
- Keynote's save **omits `buildEventCount`**, recording only `hasExplicitBuilds`. So on
  any deck it wrote, `kn_index` reports `builds: None` (unknown, not zero) and
  `kn_stage_map` refuses to map stages. Builds still work; just render without
  `stages=True`, which gives final states anyway.

**Template workflow** (duplicate a template slide, edit the copy, place it):
```python
dup = kn_duplicate_slides(deck, kn_new_copy_path(deck, "dup"), "3x1")   # copy -> last
kn_replace(dup, (ed := kn_new_copy_path(deck, "text")), [("PLACEHOLDER", "real text")])
kn_slide_ops(ed, (final := kn_new_copy_path(deck, "final")), order=[1, 2, 8, 3])
kn_render(final)                                                        # then LOOK
```

**Raw `unpack` -> edit YAML -> `pack`** also works and does NOT corrupt text (an early
claim to the contrary was disproved by a control render of the untouched original), but
`kn_slide_ops` supersedes it for order changes: no media round-trip, no YAML. If you do
use it, note `keynote_parser.file_utils.dump_yaml` returns BYTES -- write with
`open(path, "wb")` to a temp file and `os.replace` it in, since `open(path, "w")`
truncates the file before the TypeError fires and destroys it.

Still unsupported: inserting a blank slide (duplicate a template instead), replacing
images, editing tables or charts, package-style (directory) decks, and duplicating an
individual item (text box or image) -- Keynote refuses to copy items even within one
document ("Text items can not be copied" / "Images can not be copied", -1717), so
`kn_duplicate_item` reports that failure rather than working around it. New decks from
scratch: python-pptx in the `keynote` env (Keynote imports .pptx).

## Cross-deck merge: `kn_merge_slides`

Keynote itself cannot do this. `duplicate` refuses any cross-document copy ("Text items
can not be copied", -1717) and a cross-document slide reference goes invalid mid-script
(-609), so the merge happens at the file level: each merged slide's OWN objects
(drawables, text storages, builds) are copied with fresh identifiers into a new
component, and the styles they use are referenced where they already live in the target.
That is the file-level equivalent of "keep source formatting".

Verified end to end: slides 20-21 of a 65-slide deck merged into a 5-slide working copy
gave 7 slides whose merged renders are **byte-identical** to the source deck's renders
of those slides (md5 match, 0 of 2,073,600 pixels differing), builds preserved
(`builds: 1` on both).

**Three invariants govern the package.** They are not guesses -- each was isolated by
building a deck that adds exactly one thing and rendering it:

1. A slide component's `identifier` IS its root `KN.SlideArchive`'s object id (true for
   all 65 components in the reference deck), and the Document component references each
   slide component as a bare `{"componentIdentifier": ...}`.
2. A slide component does NOT own the styles its shapes use: those live in the document
   stylesheet component and are declared in the slide component's `externalReferences`
   (~50 entries for a typical slide). Copying the stylesheet into a slide component
   inverts that ownership.
3. Every archive carries its own header, and `messageInfos[].type` governs payload
   decoding -- a copied archive must keep its source header (remapped), never a template's.

**The crash rule: media is deduplicated by digest.** Adding a `datas` entry whose
`digest` duplicates one already in the deck makes Keynote **crash on open** (-609
"Connection is invalid" at the `open` command, before any export). A lone duplicate-digest
entry is enough; new components and copied archives are accepted happily. So
`kn_merge_slides` matches source media by digest and REFERENCES bytes the target already
holds, registering only genuinely new media (a unique-digest entry opens fine).
The on-disk name is the `datas` entry's `fileName` (`<stem>-<id><ext>`), NOT
`preferredFileName`, which is a display name and is not unique.

**Requirement: a shared theme lineage.** Merged slides reference the target's existing
style objects, so the decks must be derived from the same original or share a theme with
the same style object ids. A foreign theme raises with the unresolvable style ids listed
(verified against an unrelated 188-slide deck) rather than producing a deck that renders
wrong. For genuinely foreign decks the fallback is
`kn_import_slides_as_images` (picture import; editable text is lost) -- implemented but
NOT yet exercised end to end, and it needs the service's `imageslide` op installed.

```python
rep = kn_merge_slides(deck, (m := kn_new_copy_path(deck, "merged")), other_deck, [20, 21])
kn_slide_ops(m, (final := kn_new_copy_path(deck, "final")), order=[1, 6, 7, 2, 3])
kn_render(final)                                                        # then LOOK
```

Debugging note: `kn_merge.py` validates the file it wrote (reference integrity, media
presence, undeclared cross-component references) because a bad reference costs a Keynote
crash to discover. Interpret those counts as a DELTA against the untouched deck -- a
healthy 65-slide deck already reports 28 unresolved references and ~1050 undeclared
cross-component references.

One library gotcha: `IWAFile.from_dict` consumes/mutates the dict passed to it, so reuse
of a member dict for a second file needs `copy.deepcopy`. And `Index/Metadata.iwa` does
NOT round-trip byte-identically (a no-op decode/encode grows it ~48 bytes); that is
harmless -- a control deck rewritten this way renders fine.

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
