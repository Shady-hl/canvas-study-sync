# Usage and troubleshooting

## Setup

The root launchers open a local browser window. No archive is needed beforehand. The destination defaults to `Documents/Canvas-Archive`; use `--archive` with an empty folder to change it. The first successful sync saves the Canvas origin in the archive state and generates standalone archive launchers. A folder from another Canvas origin must not be reused for a different site.

Use an institution-approved token. The optional session mode requires permission for cookie-authenticated API automation. Disabled token creation does not establish permission to substitute a cookie. The tool does not extract browser credentials or modify account permissions.

The resync window prepares local PDF/timezone dependencies when needed. For manual export setup, run `canvas-sync/SETUP_THIS_PC.command` on Mac or `canvas-sync/SETUP_THIS_PC.cmd` on Windows. Keep environments local to the computer.

## Incremental behavior

Each run fetches listings and accessible content to detect changes. File metadata is compared with the saved state, and unchanged attachments are reused. The GUI batches page bodies where Canvas supports it and reuses matching local file signatures. Windows retains byte hashing for downloaded files. Full verification rechecks every local source and rebuilds study output.

Failed downloads retain the completed prior version. Changed downloads preserve differing previous bytes in History. Deleted or inaccessible remote resources do not cause earlier downloads to be removed. The OS archive lock protects against overlapping writes on one computer; it does not coordinate independent computers through cloud storage.

## Optional TimeEdit timetable

Import a local CSV/iCalendar file or configure a private export link with the CLI:

```bash
python3 canvas-sync/canvas_sync.py --base-url https://canvas.your-school.edu --auth token --output "/path/to/Canvas-Archive" --timeedit-file "/path/to/schedule.ics" --timeedit-timezone Europe/Oslo --delay 2
```

Use the correct IANA timezone for your institution. Existing TimeEdit parser support includes iCalendar and Norwegian/English CSV headings; other exports may need adaptation. Course-code matching is best effort; verify timetable mapping and assessment dates in the official system. Canvas credentials are not sent to TimeEdit. Feed URLs are private credentials in practice; do not share them.

## Study context

Use the subject guides in `AI_READY/NOTEBOOKLM`, selected context packs for another assistant, or workspaces in `AI_READY/VSCODE`. Text extraction has limitations for diagrams, scans, equations and rich notebook outputs. Inspect the coverage report and include the original relevant file where needed. The exporter performs local extraction, not AI summarization, and does not upload or train on your data.

For planning, use the latest `ASSISTANT_CONTEXT/CURRENT.json`; generated packs and previously uploaded sources are snapshots. Keep edits in your course's `Practice` folder or a separate copy. Downloaded originals and generated study output can be replaced during refresh.

## Notices and interruptions

- `429` or an explicit rate-limit response: the client waits automatically; repeated throttling stops the entire run.
- `403`: access was denied. Do not try to force it open. This status alone does not prove rate limiting or an account ban.
- `404`: the endpoint/item was unavailable. Some institutions disable listings while allowing linked items.
- Login/HTML response: renew the approved credential locally; no returned login page is accepted as a PDF.
- PDF reader/timezone error: rerun setup with the selected interpreter.
- Sleep/network loss: a run may pause, fail or stop. Completed files remain saved; reopen and retry once the cause is resolved.

Exit codes: `0` complete without notices, `2` finished with notices/coverage limitations, `1` fatal/setup/authentication/protective stop, `130` interrupted. Inspect `SYNC_REPORT.html` rather than assuming a finished run means complete Canvas coverage.

## Private portable archives

`MAKE_PORTABLE` creates a study ZIP for your own devices. It contains downloaded materials, timetable snapshots, reports and study context. It is not a source-code release and must not be uploaded to this repository. It does not grant redistribution rights. Browser credentials and virtual environments are excluded. Run setup on the destination computer and renew any permitted login/feed configuration locally.
