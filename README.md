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

- **Editing** happens on the FILE, inside the sandbox, with
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
imgs = kn_render(dst, stages=True)           # Keynote exports it; stages for builds
smap = kn_stage_map(dst, n_images=len(imgs)) # which image is each slide's final state
host.view_image(imgs[smap[1]["last"] - 1])   # look at it
```

## Scope

Works: reading structure/text/notes/geometry, text find-and-replace, rendering to
PNG/JPEG/PDF, build stages, contact sheets.
Not yet: adding/reordering slides, replacing images, editing tables or charts,
package-style (directory) decks. Raw unpack→pack structural editing is possible but
untrusted — see SKILL.md. New decks from scratch: `python-pptx` (Keynote imports .pptx).

## Credits

Editing is [psobot/keynote-parser](https://github.com/psobot/keynote-parser) (MIT).
Note its open issue on unsafe YAML loading: only process decks you trust. The skill's
`kn_preflight` refuses decks containing `.iwa.yaml` members for that reason.

MIT licensed.
