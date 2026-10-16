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
| a slide looks empty or half-finished | `kn_index` → `builds` | it has a build; re-render with `stages=True` and use `kn_stage_map` |
| text is in the file but missing from the render | the render is authoritative | Keynote's own layout clips it; report the render |

## Known-good numbers (Keynote 12.2.1, 158 MB / 65-slide deck)

- 3-slide deck: ~24 s per render
- 65 slides: ~28-32 s, 65 images
- 65 slides with `stages=True`: 78 images (11 built slides), ~28 s
- `kn_index`: ~2 s; `kn_replace`: ~4 s
