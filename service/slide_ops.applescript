-- Structural edits inside a Keynote deck, then save to a new file.
-- argv: <deck.key> <out.key> <opsfile> [timeout_seconds]
--
-- opsfile is UTF-8, one operation per line, TAB-separated. Parsed BEFORE talking to
-- Keynote: inside a `tell application` block `text items` is resolved against Keynote,
-- which does not implement it (-1728).
--   dupslide <slide> <count>                     duplicate a slide (copies land at END)
--   text     <slide> <x> <y> <width> <content>   new text item ("\n" = line break)
--   image    <slide> <x> <y> <width> <height> <path>   new image from a file
--   dupitem  <srcSlide> <index> <dstSlide>       copy one item onto another slide
--   delitem  <slide> <index>                     delete one item
--   copyslide <srcDeckPath> <slide>              copy a slide IN from another deck
--                                                (Keynote 12.2.1 REFUSES this; kept so
--                                                 the failure is reported precisely)
--   imageslide <imagePath> <notes>               append a slide made from an image file
--                                                ("\n" allowed in notes, "-" = none)
-- Items are indexed as Keynote reports them (1-based, per slide).
on run argv
	if (count of argv) < 3 then error "usage: slide_ops.applescript DECK OUT OPSFILE [TIMEOUT]"
	set deckPath to item 1 of argv
	set outPath to item 2 of argv
	set opsPath to item 3 of argv
	set tmo to 3600
	if (count of argv) > 3 then set tmo to (item 4 of argv) as integer

	set opsText to read (POSIX file opsPath) as «class utf8»
	set AppleScript's text item delimiters to linefeed
	set opLines to text items of opsText
	set AppleScript's text item delimiters to ""
	set ops to {}
	repeat with aLine in opLines
		set theLine to aLine as string
		if theLine is not "" then
			set AppleScript's text item delimiters to tab
			set fields to text items of theLine
			set AppleScript's text item delimiters to ""
			set end of ops to fields
		end if
	end repeat

	tell application "Keynote"
		with timeout of tmo seconds
			set theDoc to open (POSIX file deckPath)
			if theDoc is missing value then error "Keynote returned no document for " & deckPath number -1700
			try
				repeat with f in ops
					set opName to item 1 of f
					if opName is "dupslide" then
						set srcIdx to (item 2 of f) as integer
						repeat ((item 3 of f) as integer) times
							duplicate slide srcIdx of theDoc
						end repeat

					else if opName is "text" then
						set sIdx to (item 2 of f) as integer
						set xPos to (item 3 of f) as integer
						set yPos to (item 4 of f) as integer
						set wVal to (item 5 of f) as integer
						set bodyText to item 6 of f
						tell slide sIdx of theDoc
							set newItem to make new text item with properties {object text:bodyText, position:{xPos, yPos}, width:wVal}
						end tell

					else if opName is "image" then
						set sIdx to (item 2 of f) as integer
						set xPos to (item 3 of f) as integer
						set yPos to (item 4 of f) as integer
						set wVal to (item 5 of f) as integer
						set hVal to (item 6 of f) as integer
						set imgPath to item 7 of f
						tell slide sIdx of theDoc
							set newImg to make new image with properties {file:POSIX file imgPath, position:{xPos, yPos}}
						end tell
						if wVal > 0 then set width of newImg to wVal
						if hVal > 0 then set height of newImg to hVal

					else if opName is "dupitem" then
						set srcS to (item 2 of f) as integer
						set idx to (item 3 of f) as integer
						set dstS to (item 4 of f) as integer
						if idx < 0 then set idx to (count of iWork items of slide srcS of theDoc)
						duplicate iWork item idx of slide srcS of theDoc to end of slide dstS of theDoc

					else if opName is "delitem" then
						set sIdx to (item 2 of f) as integer
						set idx to (item 3 of f) as integer
						delete iWork item idx of slide sIdx of theDoc

					else if opName is "copyslide" then
						set otherPath to item 2 of f
						set otherIdx to (item 3 of f) as integer
						set otherDoc to open (POSIX file otherPath)
						if otherDoc is missing value then error "could not open source deck " & otherPath number -1700
						set copyErr to ""
						try
							duplicate slide otherIdx of otherDoc to end of slides of theDoc
						on error e1
							try
								duplicate slide otherIdx of otherDoc to end of theDoc
							on error e2
								try
									tell theDoc to duplicate slide otherIdx of otherDoc to end of slides
								on error e3
									set copyErr to "cross-deck copy unsupported; Keynote said: [" & e1 & "] [" & e2 & "] [" & e3 & "]"
								end try
							end try
						end try
						try
							close otherDoc saving no
						end try
						if copyErr is not "" then error copyErr number -1717

					else if opName is "imageslide" then
						set imgPath to item 2 of f
						set noteText to item 3 of f
						make image slides theDoc files {POSIX file imgPath} without set titles
						if noteText is not "-" then
							set presenter notes of last slide of theDoc to noteText
						end if

					else
						error "unknown op: " & opName
					end if
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
