# Troubleshooting

Read in this order; the first two need no help from the user.

| Symptom | Where to look | Likely cause |
|---|---|---|
| `render request not picked up after 60 s` | `launchctl print gui/$(id -u)/local.keynote-render` | agent not loaded, or WatchPaths missed the file (the helper touches the dir to retrigger) |
| `state: error`, "no document" / "can't be opened" | `status/<job>.json`, `render.log` | a modal Keynote dialog is blocking the app, or staging is not in Keynote's container |
| several identical failures in a row, each in seconds | ask the user to check for a dialog behind other windows | dialog poisoning from an earlier failure |
| job runs for minutes with `n_done` stuck at 0 | `status/<job>.json` | Keynote is still opening a large deck; `OPEN_GRACE_S` is 900 s |
| `-1712 AppleEvent timed out` | `render.log` | export exceeded the script's `with timeout` |
| `-1700 Can't convert types` | `render.log` | JXA-style enums; the exporter must be AppleScript |
| `EXDEV` / `Cross-device link` | `render.log` | moving outputs across volumes; use the copy-then-delete path |
| macOS prompts on every render | — | add the env interpreter to Full Disk Access |
| a slide looks empty or half-finished | compare with a `stages=True` render | a default render is already the final stage, so this is usually real |
| `kn_stage_map` raises "build counts unavailable" | `kn_index` → `builds` is `None` | the deck was saved by Keynote (e.g. by `kn_duplicate_slides`); render without stages |
| duplicated slides are not where you asked | — | Keynote appends copies at the end; follow with `kn_slide_ops(order=...)` |
| text is in the file but missing from the render | the render is authoritative | Keynote's own layout clips it; report the render |
| **Keynote CRASHES on `open`** (`-609 Connection is invalid` at char offset of the `open` command) | the merge report's `datas` changes | a new media registry entry whose `digest` duplicates one already in the deck. Reference the existing data id instead; `kn_merge_slides` does this automatically |
| "reopen the documents you had open?" modal after a crash | — | window restoration; the service now asserts `NSQuitAlwaysKeepsWindows=false` and `ApplePersistenceIgnoreState=true` on every pass and clears a pending restore request |
| every slide op fails with `-2741 Expected end of line... but found "{"` while renders still work | the offset in `slide_ops.applescript` | one syntax error stops the WHOLE script compiling, so every op in it fails; renders use a separate script. `install.sh` now compile-checks both scripts and refuses to install a broken one. (Seen once: `make image slides` takes `files {...}`, not `with files {...}` -- `with` is only for boolean parameters) |
| an op fails with `-609 Connection is invalid` pointing at the error re-raise near the end of `slide_ops.applescript` | which op was in the ops file | Keynote DIED inside the op. `make image slides` does this for every image (verified 2.6 KB and 760 KB) -- the command is unusable on 12.2.1 |
| you reinstalled and the SAME error persists at the SAME offsets | `cmp` the files in `_keynote_render/setup/` against the working copies | install.sh deploys from the setup dir, so an edit that was never staged there is simply reinstalled stale. Stage first, then reinstall |
| N images for M slides, N < M | `kn_index` -> `skipped` | Keynote omits skipped slides from exports (correct behaviour); image k is the k-th NON-skipped slide |
| merge raises "source styles absent from target" | the listed style object ids | the decks do not share a theme lineage; merged slides reference the target's styles. Use `kn_import_slides_as_images` or rebuild the slide in the target theme |
| merged deck has the right slides but `kn_index` cannot parse it | — | a copied archive was written under a template's header; each archive must keep its OWN header (`messageInfos[].type` governs decoding) |

## Finding a crash cause without a crash report

Keynote's crash reports are not readable from the agent sandbox (`~/Library/Logs/
DiagnosticReports` does not exist for this user; `/Library/Logs/DiagnosticReports` is
permission-denied even for a Full-Disk-Access interpreter), so the loop is: the
AppleScript error carries a CHARACTER OFFSET, and resolving it against the script says
whether Keynote died at `open` or during `export`. That alone separates "the file is
rejected" from "the export failed".

From there, bisect by building decks that add exactly ONE thing to a known-good deck and
rendering each. For the merge, that isolated the cause in four renders:

| probe | adds | result |
|---|---|---|
| V1 | bump `lastObjectIdentifier` | opens |
| V2 | one `datas` entry (duplicate digest) + its file | **crash** |
| V3 | a new component + 18 copied archives | opens |
| V4 | V3 plus the slide node in `Document.iwa` | opens |
| V5 | a `datas` entry with a UNIQUE digest, conventional `fileName`, registered in `dataReferences` | opens |

Validate reference integrity in Python before asking Keynote to open anything, and read
the counts as a DELTA against the untouched deck: a healthy 65-slide deck already reports
28 unresolved references and ~1050 undeclared cross-component references, so only an
increase means anything.

## Known-good numbers (Keynote 12.2.1, 158 MB / 65-slide deck)

- 3-slide deck: ~24 s per render
- 65 slides: ~28-32 s, 65 images
- 65 slides with `stages=True`: 78 images (11 built slides), ~28 s
- delete+reorder 65 -> 5 slides: ~6 s (no Keynote)
- duplicate 2 slides via Keynote: ~18 s
- `kn_index`: ~2 s; `kn_replace`: ~4 s
- cross-deck merge of 2 slides (328 archives copied): ~10 s, then ~25 s to render 7 slides
- a merged slide renders byte-identically to the source deck's render of it (md5 match)
