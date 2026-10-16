-- Keynote export helper for render_queue.py.
-- argv: <deck.key> <target> <png|jpeg|pdf>
-- Paths arrive as argv and are never spliced into script source. Enum values are
-- AppleScript keywords (JXA string coercion fails with -1700 on Keynote 12.2.1).
-- Verified against /Applications/Keynote.app/Contents/Resources/Keynote.sdef:
--   export <document> to <file> as <export format> with properties <export options>
--   export format: PDF | slide images ; image export formats: JPEG | PNG | TIFF
--   export options: image format, compression factor, export style, skipped slides,
--                   PDF image quality (Good|Better|Best)
on run argv
	if (count of argv) < 3 then error "usage: export_slides.applescript DECK TARGET FORMAT [TIMEOUT_SECONDS]"
	set deckPath to item 1 of argv
	set targetPath to item 2 of argv
	set fmt to item 3 of argv
	set tmo to 3600
	if (count of argv) > 3 then set tmo to (item 4 of argv) as integer
	set allStages to false
	if (count of argv) > 4 then set allStages to ((item 5 of argv) is "1")
	tell application "Keynote"
		with timeout of tmo seconds
			-- Open the deck, then fall back to finding it by name: Keynote sometimes opens a
			-- deck while returning no handle (a dialog intercepts the reply).
			set wantedName to do shell script "basename " & quoted form of deckPath
			set theDoc to open (POSIX file deckPath)
			if theDoc is missing value then
				repeat 15 times
					delay 2
					repeat with aDoc in documents
						if name of aDoc is wantedName then set theDoc to aDoc
					end repeat
					if theDoc is not missing value then exit repeat
				end repeat
			end if
			if theDoc is missing value then error "Keynote returned no document for " & wantedName number -1700
			try
				if fmt is "pdf" then
					export theDoc to POSIX file targetPath as PDF with properties {PDF image quality:Best, export style:IndividualSlides, skipped slides:true}
				else if fmt is "jpeg" then
					export theDoc to POSIX file targetPath as slide images with properties {image format:JPEG, compression factor:0.9, skipped slides:true, all stages:allStages}
				else
					export theDoc to POSIX file targetPath as slide images with properties {image format:PNG, skipped slides:true, all stages:allStages}
				end if
			on error errMsg number errNum
					try
					close theDoc saving no
					end try
				error errMsg number errNum
			end try
			close theDoc saving no
			-- leave Keynote alone if the user has other decks open
			if (count of documents) is 0 then quit
		end timeout
	end tell
	return "ok"
end run
