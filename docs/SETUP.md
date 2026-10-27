# Setup notes and hard-won constraints

Everything here was established by testing on the target Mac (Keynote 12.2.1,
macOS 15.7.7). Where a claim is a guess, it says so. Change these with evidence.

## Configuration

Nothing is hardcoded to one machine; every path has an environment variable with a
default. Export any of these before running `service/install.sh`:

| variable | default | meaning |
|---|---|---|
| `DECK_ROOT` | `$HOME/thermofiles/teaching` | the ONLY tree the service reads decks from or writes results to; a request naming anything outside it is rejected |
| `KR_ROOT` | `$DECK_ROOT/_keynote_render` | the queue folder the agent drops requests in; must be inside `DECK_ROOT` so the agent can write to it |
| `KR_PYTHON` | `$HOME/.claude-science/conda/envs/keynote/bin/python3.12` | a NON-Apple python 3.10+ (Apple's `/usr/bin/python3` is denied access to non-boot volumes under launchd, which breaks every render) |

`install.sh` renders `service/local.keynote-render.plist.in` with those values and writes
the result to `~/Library/LaunchAgents/local.keynote-render.plist`, passing `KR_ALLOWED_ROOT`
and `KR_ROOT` to the handler through the agent's `EnvironmentVariables`. Install the
template directly and launchd will try to execute `@@PYTHON@@`.

The skill side reads `KN_DECK_ROOT`, `KN_WORK_DIR`, `KN_RENDER_ROOT`, `KN_PROTECTED_DIRS`
and `KN_BIN`, with the same defaults. Point `KN_PROTECTED_DIRS` (colon-separated) at every
folder of originals that must never be written to.

## Architecture

```
Claude Science sandbox                 │  the user's Mac (outside the sandbox)
───────────────────────────────────────┼──────────────────────────────────────────
kn_replace / kn_index  (keynote-parser)│
   edits .key files directly           │
                                       │
kn_render writes                       │  LaunchAgent local.keynote-render
  _keynote_render/requests/<job>.json ──▶   render_queue.py
                                       │     copies deck → Keynote's container
                                       │     clears xattrs, waits, opens + exports
                                       │     (export_slides.applescript)
reads renders/<job>/slide-NNN.png   ◀──┼     copies images back, writes status.json
```

The handler lives in `~/Library/Application Support/keynote-render/`, which the agent
cannot write. The agent's only influence is the request JSON, which is validated:
input must be a `.key` under the allowed root, job id and format are whitelisted.

## Constraints that are load-bearing

1. **Stage the clone inside Keynote's own container**
   (`~/Library/Containers/com.apple.iWork.Keynote/Data/Documents/keynote-render`).
   Handler runs staging anywhere else were refused with *"can't be opened right now /
   Operation not permitted"*: on the separate `thermofiles` APFS volume, under
   `~/Library/Application Support`, and in a plain home folder — through every
   combination of copy method (`cp` vs `cp -c`), xattr state, launch context
   (launchd vs Terminal), settle delays and retries. The first run staging into the
   container succeeded. **The mechanism is unverified** — Keynote needing no sandbox
   extension for its own container is a plausible guess, not a proven cause.
   Decks the user has opened BY HAND behave differently and are not a valid control.
2. **Never run the handler under an Apple platform binary.** `/usr/bin/python3` under
   launchd is denied access to the `thermofiles` volume (`PermissionError` on
   `os.listdir`). The plist runs the conda env's `python3.12`; `install.sh` checks it.
3. **Full Disk Access on that interpreter**, or macOS prompts once per render — the
   grant does not persist for an unsigned binary. Trade-off: anything run through that
   interpreter gains full file access, including the skill's own helpers, which shell
   out to it. A dedicated env limits the blast radius. The alternative is one click per
   render, which works fine for batched work.
4. **Cross-volume moves.** Outputs move from the container (boot volume) to
   `teaching/` (its own volume); `os.replace` raises `EXDEV`, so the handler
   copy-then-deletes.
5. **AppleScript, not JXA.** JXA cannot coerce Keynote's enum values and fails with
   `-1700 Can't convert types`, even with correct parameter names. AppleScript passes
   them as keywords (`as slide images`, `image format:PNG`).
6. **Wrap the export in `with timeout of N seconds`.** The default 2-minute Apple Event
   limit fires on a large deck (`-1712`).
7. **Keynote's dictionary is readable** at
   `/Applications/Keynote.app/Contents/Resources/Keynote.sdef` — parse that XML for the
   installed version's exact parameters. The `sdef` command needs full Xcode.

## Slide operations

`duplicate`, `delete`, `move` and `save` come from the Cocoa Standard Suite that Keynote
inherits (`/System/Library/ScriptingDefinitions/CocoaStandard.sdef`) — they are NOT in
Keynote.sdef, so parsing that file alone makes them look unavailable.

Two measured quirks of Keynote's own save:
- `duplicate` **appends copies at the end** of the deck, not after the source slide.
- The saved file **omits `buildEventCount`**, keeping only `hasExplicitBuilds`. Builds
  still work, but any stage↔slide mapping derived from counts is impossible on such a
  deck, so `kn_stage_map` refuses rather than guessing.

Delete and reorder do not need Keynote at all: slide order is
`KN.ShowArchive.slideTree.slides` in `Index/Document.iwa`.

## Failure modes worth recognising

- **A run of fast, identical failures usually means a modal dialog** is waiting on the
  user's screen, often behind other windows, and it blocks every later job. Deleting a
  clone Keynote still holds is what creates those dialogs, so the handler only clears a
  clone on success. Ask the user to check and to quit Keynote before retrying.
- **"not picked up after 60 s"** — launchd's `WatchPaths` occasionally misses a new
  file. `kn_render` touches the requests directory to re-trigger it. If it persists, the
  LaunchAgent is not loaded (`launchctl print gui/$(id -u)/local.keynote-render`).
  `QueueDirectories` does NOT work here at all; it never fired once.
- **The agent cannot read `~/Library`**, so a failed clone there is not inspectable.
  `status/<job>.json` and `render.log` (both under `teaching/`) carry the error instead.

## Render semantics

A default render writes ONE image per slide and it is that slide's **final** build stage
(verified byte-identical to the last image of a `stages=True` export). `stages=True`
exports every stage, so the image count equals `sum(buildEventCount + 1)`.

## Diagnosing by hand

Run the handler yourself to separate host problems from launchd problems — Terminal
holds permissions a launchd job may not:

```bash
launchctl bootout gui/$(id -u)/local.keynote-render      # stop it stealing the job
/path/to/envs/keynote/bin/python3.12 \
  "$HOME/Library/Application Support/keynote-render/render_queue.py"
bash ~/thermofiles/teaching/_keynote_render/setup/install.sh   # restore
```

Check a deck opens at all: `osascript -e 'tell application "Keynote" to open POSIX file "/abs/path.key"'`
