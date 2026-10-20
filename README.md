# Canvas Study Sync

A local study archive for Canvas: incremental downloads, a background resync window, timetable snapshots, and study context for NotebookLM or a local coding assistant.

**Experimental beta · macOS and Windows · noncommercial source available.** Independent community software, not affiliated with or endorsed by Instructure, Canvas, TimeEdit, or any educational institution. Use only where your institution permits this form of automation. Successful authentication does not establish permission.

**Authentication setup is required. This is a developer preview, not a ready-to-use public Canvas integration.** The normal launcher requires administrator-configured OAuth and refuses pasted tokens/cookies. The documented native OAuth implementation is for trusted administrator/developer installations. There is no bundled developer key, hosted sign-in service, or claimed approval from any institution. Broad consumer deployment needs an institution-approved architecture that keeps the client secret out of distributed clients. See [OAuth setup and deployment limits](docs/OAUTH_SETUP.md).

## What it does

- Checks your active student courses and downloads new, changed or locally missing accessible attachments. Unchanged attachments are reused; metadata and text still require requests.
- Exports accessible pages, assignments, announcements, discussions, syllabus and quiz descriptions. It does not submit work, answer quizzes or change grades.
- Offers a finish-time slider, one request at a time, server backoff, persistent throttling notices, and protective limits. Estimates use previous runs and are approximate.
- Extracts local text into subject packs with source/page/cell references and a local searchable index. No AI service is called, no model is trained, and no source material is automatically uploaded anywhere.
- Optionally refreshes a TimeEdit feed or imports a local timetable snapshot.
- Retains private, structured timing logs for later analysis, without credentials, raw resource paths, private URLs, response bodies or Wi-Fi identifiers.

## First run

Install **Python 3.11 or newer**, then download this source repository using **Code → Download ZIP** and extract it. Keep the extracted source outside your private study archive.

1. On **Mac**, open `OPEN_RESYNC.command`. If Finder does not launch it, run `zsh ./OPEN_RESYNC.command` from the extracted folder.
2. On **Windows**, open `OPEN_RESYNC.cmd`.
3. Without administrator configuration, the window explains the missing setup and cannot start a sync. Do not substitute a browser cookie or self-generated token to work around this.
4. On a configured, approved installation, start Canvas sign-in, follow **Continue to Canvas**, and approve access on Canvas. For the documented native flow, paste the address of Canvas's completion page into the local authorization-response field. This code is short-lived and bound to that sign-in attempt.
5. Choose your pace and start the resync. OAuth access is refreshed in memory before expiry. PDF/timezone dependencies are prepared locally when needed.

Once running, you can close the tab or browser. Keep the computer on, awake and online. Reopen the launcher to return to progress. First-time setup, network changes, new material and Canvas pauses can extend the finish estimate.

To choose a different empty archive folder:

```bash
# Mac / Linux
python3 -B canvas-sync/resync_app.py --archive "/path/to/Canvas-Archive" --launch
```

```powershell
# Windows
python -B .\canvas-sync\resync_app.py --archive "D:\Study\Canvas-Archive" --launch
```

After the first successful study build, the archive gets its own launchers and tool copy. On Mac, `Canvas Resync.app` is generated inside that private archive. Source ZIPs are not signed or notarized application installers.

## Reports and coverage

Open `index.html` in your private archive, then `SYNC_REPORT.html`. Exit code `2` means the run finished with notices. Locked/unpublished items and denied listings remain gaps; no scan can certify that every course resource was discovered. Previously downloaded material is retained when remote material disappears or becomes inaccessible.

The study exporter creates `AI_READY` and `ASSISTANT_CONTEXT/CURRENT.json`. Uploading context to an AI service is a separate action with separate institutional, privacy and copyright requirements. NotebookLM sources already uploaded are not updated by a local refresh.

## Local export and development

```bash
python3 -B canvas-sync/study_update.py --archive "/path/to/Canvas-Archive"
```

Use `python` on Windows. This command refreshes existing local study output without contacting Canvas. Launching an approved OAuth installation is described in [OAuth setup](docs/OAUTH_SETUP.md). The sync CLI defaults to OAuth and requires an in-memory session supplied by the configured launcher. Existing archives remember their Canvas origin, not a login.

Manual token/session CLI modes and the launcher's explicit `--development-auth` switch are retained for permitted personal development/testing. They are not public-user onboarding. Instructure distinguishes personal testing from applications used by others, which must obtain tokens through OAuth: [official guidance](https://developerdocs.instructure.com/services/canvas/oauth2/file.oauth.md#manual-token-generation). A session cookie still calls the API and does not establish institutional permission. See [usage and troubleshooting](docs/USAGE.md).

## Privacy and limits

Course contents, reports, timetable settings and archive state are private local data. Keep them outside this repository. The optional portable study ZIP contains course material and other private data and is intended for your own devices, not public releases.

The window uses loopback, a random local access key, host/origin checks and no external page assets. Canvas credentials are sent only to the configured exact HTTPS origin. OAuth state is random, expires after ten minutes and is consumed once. Token exchanges refuse redirects, verify TLS and request only configured GET scopes. Sync requests use GET; OAuth token issuance/refresh uses POST. Runtime access/refresh tokens are held in process memory and the sync child process's environment for the run; dependency installers receive none. A provisioned administrator configuration contains a client secret and must be kept private outside source and archive folders. These controls do not protect against malware or another process with sufficient access to your computer.

The window starts with 2 seconds between requests, adjustable from 0.6 to 5 seconds. Server backoff takes priority. Persistent throttling stops the entire run after six attempts for that request. The window additionally limits a run to 5,000 requests and four hours of starting requests; an in-flight download can complete later. These are traffic controls, not a promise against account restrictions.

See [SECURITY.md](SECURITY.md), [responsible use](USE_POLICY.md), and [timing-log schema](canvas-sync/TIMING_LOGS.md).

## Development

```bash
python3 -m pip install -r canvas-sync/requirements-ai.txt
python3 -B -m unittest discover -s canvas-sync/tests
python3 -B tools/check_release.py
```

Tests use synthetic course data and mocked network responses; no Canvas account is required. CI covers Mac, Windows and Linux on Python 3.11 and 3.14. Some platform-specific tests are skipped elsewhere. Tests do not establish compatibility with every institution's Canvas configuration.

## License

[PolyForm Noncommercial 1.0.0](LICENSE). This is **source available, not OSI open source**: the code may be used, modified and redistributed for permitted noncommercial purposes under the license. Commercial purposes outside its grants are not licensed. The standard text expressly permits certain organizations, including educational institutions, regardless of funding; read the full terms rather than treating this as a ban on every payment in every context.

[USE_POLICY.md](USE_POLICY.md) states the project's responsible-use expectations and supported scope. It does not rewrite the license or guarantee that misuse can be prevented. No license or disclaimer guarantees freedom from legal consequences. The code license grants no rights to course materials, student data, third-party services or trademarks.
