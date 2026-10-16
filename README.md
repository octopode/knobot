# keynotebot

Edit Apple Keynote `.key` decks from [Claude Science](https://claude.ai) and let the
agent **see** what it changed — by having Keynote itself render the slides to PNG.

Built and verified against **Keynote 12.2.1 on macOS 15.7.7**, 2026-10-16.

## Why this exists

Claude Science's sandbox cannot send Apple Events, so it cannot drive Keynote. It also
cannot reach a local MCP server: remote connectors require a public `https://` URL, and
local (stdio) connectors run in a sandbox that refuses to launch binaries from a conda
env and cannot connect to `127.0.0.1`. Every "control Keynote from the agent" approach
fails for one of those reasons.

keynotebot works around all of it:

- **Editing** (including cross-deck slide merges) happens on the FILE, inside the sandbox, with
  [`keynote-parser`](https://github.com/psobot/keynote-parser) — no app involved.
- **Rendering** happens outside the sandbox: the agent drops a JSON request in a watched
  folder, a LaunchAgent on the Mac has Keynote export the slides, and the PNGs land where
  the agent can read them.

Net effect: an edit → render → inspect loop the agent can run unattended, so it catches
text overflow, clipped titles and object collisions instead of guessing.

## Layout

```
skill/      the `keynote-files` Claude Science skill (SKILL.md, kernel.py, scripts/)
service/    the macOS render service (LaunchAgent + queue handler + AppleScript exporter)
docs/       project instructions, setup and troubleshooting
```

## Install

1. **Python env** (conda/mamba; needs 3.10+ — macOS's own `python3` is too old and is
   also denied access to non-boot volumes under launchd):
   ```bash
   mamba create -n keynote -c conda-forge python=3.12 pip
   mamba run -n keynote pip install keynote-parser python-pptx
   ```
   In Claude Science use an env named `keynote`; `skill/kernel.py` points `KN_BIN` at it.
2. **Render service** — edit the paths at the top of `service/render_queue.py` and
   `service/local.keynote-render.plist` for your user, then:
   ```bash
   mkdir -p ~/thermofiles/teaching/_keynote_render/setup
   cp service/* ~/thermofiles/teaching/_keynote_render/setup/
   bash ~/thermofiles/teaching/_keynote_render/setup/install.sh
   osacompile -o /dev/null "$HOME/Library/Application Support/keynote-render/export_slides.applescript"
   ```
   The installer copies the handler to `~/Library/Application Support/keynote-render/`,
   OUTSIDE any agent-writable folder, so the agent can queue work but never change what
   runs on the host.
3. **Grant Full Disk Access** to the env's interpreter
   (`.../envs/keynote/bin/python3.12`) in System Settings → Privacy & Security. Without
   it macOS prompts on EVERY render (the grant does not persist for an unsigned binary).
   This is a broad grant on an interpreter — see `docs/SETUP.md` for the trade-off.
4. **Skill**: publish `skill/` as a Claude Science skill, and paste
   `docs/project_instructions.md` into the project's instructions.

## Use

```python
skill("keynote-files")                       # loads the helpers

idx = kn_index(deck)                         # ordered slides, text, notes, builds
hits = kn_find(deck, "old wording")          # which slides mention it
dst  = kn_new_copy_path(deck, "edit")        # never edit an original
kn_replace(deck, dst, [("old wording", "new wording")])

dup = kn_duplicate_slides(deck, out, "3x2")  # 2 copies of slide 3, appended at the end
kn_slide_ops(dup, out2, delete=[9], order=[1, 6, 2])   # reorder / drop slides

kn_merge_slides(out2, out3, other_deck, [20, 21])      # copy slides BETWEEN decks

imgs = kn_render(out3)                       # one image per slide, FINAL build stage
host.view_image(imgs[0])                     # look at it
```

## Scope

Works: reading structure/text/notes/geometry/builds, text find-and-replace, **slide
delete, reorder, duplicate and cross-deck merge**, **adding text boxes and images from
files on disk**, rendering to PNG/JPEG/PDF, build stages, contact sheets.

An added image is embedded in the saved deck, not linked (verified by digest against the
source file), so decks stay self-contained. Keynote omits skipped slides from exports, so
image k is the k-th non-skipped slide — map through `kn_index`'s `skipped` flags.

Cross-deck merge (`kn_merge_slides`) is also file-level, because Keynote refuses every
cross-document copy: `duplicate` reports "Text items can not be copied" (-1717) and a
cross-document slide reference goes invalid mid-script (-609). Each merged slide's own
objects are copied with fresh identifiers into a new component and keep referencing the
target's existing styles, which requires the decks to share a theme lineage — a foreign
theme is refused with the unresolvable style ids rather than rendered wrong. Verified:
merged slides render **byte-identically** to the source deck's renders of them.

One rule is load-bearing: Keynote deduplicates media by content digest, and registering a
second `datas` entry for bytes the deck already holds makes it **crash on open**. Media
is therefore matched by digest and referenced, not copied.

Delete and reorder are done at the file level — slide order is a single list of
references, so only that list is rewritten and every other zip member is copied through
byte-for-byte (~6 s on a 158 MB deck, Keynote not involved). Duplication is done by
Keynote itself, because deep-copying a slide's protobuf graph is the risky part.

**Two different things, do not confuse them:**

- **Adding an image from a file on disk — WORKS.** `kn_add_image(deck, out, slide=5,
  path="/abs/photo.png", x=…, y=…, width=…)` puts a picture on an existing slide, via
  Keynote's `make new image`. Verified, and the bytes are embedded in the saved deck.
- **Picture IMPORT of whole slides — BROKEN in Keynote 12.2.1, not by us.** The separate
  command `make image slides`, which turns image files into NEW slides (the intended
  fallback for remixing a foreign-theme deck slide-by-slide), **crashes Keynote** on
  12.2.1. Every image tried killed it, including the same files `make new image` places
  without complaint moments earlier, so the fault is the command, not the image. The
  helper raises instead of taking Keynote down, and cross-theme remixing therefore has no
  fallback yet.

Not yet: inserting a blank slide (duplicate a template instead), replacing an existing
image in place (ADDING one works; `file` is read-only after creation, so swapping means
delete-then-add in the same frame), editing tables or charts, package-style (directory) decks, merging across different
themes, and duplicating an individual text box or image — Keynote refuses to copy items
even within one document, so that helper reports the failure instead of faking it. New decks from scratch:
`python-pptx` (Keynote imports .pptx).

## Credits

Editing is [psobot/keynote-parser](https://github.com/psobot/keynote-parser) (MIT).
Note its open issue on unsafe YAML loading: only process decks you trust. The skill's
`kn_preflight` refuses decks containing `.iwa.yaml` members for that reason.

MIT licensed.
