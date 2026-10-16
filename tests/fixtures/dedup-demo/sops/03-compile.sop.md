# SOP-03 Compile (fixture)

### Step 8. Finish the compile

- Refresh the index page so that the health token matches the latest findings from the pages.
- Record the registration in the activity log. You append an entry to the activity ledger with the alias set to the project alias.
- Release the compile lock once every page has been written to disk and read back.
- Compare the page count against the previous run and note any page that disappeared from the folder.
- Summarize the open risks in two sentences and place the summary at the top of the index page.
- Close the session by printing the elapsed time together with the number of sources that were read.
- Archive the consumed intake files into the processed folder and keep their original names.
- Verify that every wikilink in the new pages resolves to an existing page in the same folder.
- Rename any temporary scratch folder created during the run and move it beside the other scratch folders.
- Measure how long the longest single page took to produce and keep that figure for the next planning meeting.
- Remove duplicate blank lines at the end of every generated page and keep one trailing newline only.
- Confirm that the front matter of each page holds the same keys in the same order as the previous version.
- Store the list of unresolved questions in a separate short list so that a reviewer can answer them quickly.
- Print a final checklist that names each completed stage, each skipped stage, and each stage that needs a retry.
