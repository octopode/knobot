-- Duplicate slides inside a deck, then save the result to a new file.
-- argv: <deck.key> <out.key> <spec> [timeout_seconds]
--   spec: comma-separated "N x K" items, 1-based slide numbers, K copies each,
--         e.g. "3x2,7x1". Duplicates are inserted directly after their source, so
--         later items are applied to the ORIGINAL numbering by processing descending.
-- Uses the Cocoa Standard Suite (duplicate, save, close) that Keynote inherits:
--   duplicate <specifier> [to <location>] ; save <document> in <file>
-- Paths and spec arrive as argv; nothing is interpolated into script source.
on run argv
	if (count of argv) < 3 then error "usage: slide_ops.applescript DECK OUT SPEC [TIMEOUT]"
	set deckPath to item 1 of argv
	set outPath to item 2 of argv
	set spec to item 3 of argv
	set tmo to 3600
	if (count of argv) > 3 then set tmo to (item 4 of argv) as integer

	-- Parse the spec BEFORE talking to Keynote: inside a `tell application` block,
	-- `text items` is resolved against Keynote, which does not implement it (-1728).
	set AppleScript's text item delimiters to ","
	set specItems to text items of spec
	set AppleScript's text item delimiters to ""
	set srcList to {}
	set copyList to {}
	repeat with anItem in specItems
		set AppleScript's text item delimiters to "x"
		set parts to text items of (anItem as string)
		set AppleScript's text item delimiters to ""
		if (count of parts) is not 2 then error "bad spec item: " & anItem
		set end of srcList to ((item 1 of parts) as integer)
		set end of copyList to ((item 2 of parts) as integer)
	end repeat

	tell application "Keynote"
		with timeout of tmo seconds
			set theDoc to open (POSIX file deckPath)
			if theDoc is missing value then error "Keynote returned no document for " & deckPath number -1700
			try
				repeat with i from 1 to (count of srcList)
					set srcIdx to item i of srcList
					set nCopies to item i of copyList
					repeat nCopies times
						duplicate slide srcIdx of theDoc
					end repeat
				end repeat
				save theDoc in POSIX file outPath
			on error errMsg number errNum
				try
					close theDoc saving no
				end try
				error errMsg number errNum
			end try
			close theDoc saving no
			if (count of documents) is 0 then quit
		end timeout
	end tell
	return "ok"
end run
