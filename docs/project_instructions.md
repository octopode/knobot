## Teaching materials: slides

This project builds and edits teaching materials, mostly Apple Keynote slides.
The user runs Keynote 12.2.1 on macOS.

**Keynote files (.key): always use the `keynote-files` skill.** Load it with
`skill("keynote-files")` before reading, editing, or reviewing any .key file, and
follow its workflow: index -> edit into a new file -> render -> inspect -> fix.

- Do NOT try to control the Keynote app (osascript, AppleScript, JXA, Keynote MCP
  servers or connectors). Apple Events, local connectors and localhost are blocked
  in the Claude Science sandbox; this was tested and is not a transient failure.
- **Never modify an existing deck.** Originals, especially everything in
  `teaching/pastslides/`, are read-only. Write every edit to a NEW file, by default
  in `teaching/_keynote_work/`, and tell the user the new path.
- **Every edit gets a visual check.** Render the edited deck with `kn_render`
  (Keynote's own export via the user's keynote-render service) and look at each
  changed slide before reporting: text overflow or clipping, overlaps between text
  and images, objects past the slide edge, titles that wrap into other content.
  Fix and re-render until clean. Never judge layout from a deck's embedded
  preview images; they are stale after any edit.
- Only process decks the user provided or created (keynote-parser has a known
  code-execution issue with crafted files); the skill's preflight enforces this.
- New decks from scratch: python-pptx in the `keynote` env, saved to
  `teaching/_keynote_work/` (Keynote opens .pptx). Then render and inspect the
  same way.
- If rendering fails (not picked up, timeout, permission), stop and tell the user
  what the status file and render.log say; the fix is usually on their Mac
  (LaunchAgent not loaded, or a Keynote dialog waiting).
- The skill is v0. When a task needs something it cannot yet do, say so and
  propose an improvement to the skill instead of improvising around its rules.
