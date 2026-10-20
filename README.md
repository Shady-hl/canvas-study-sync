# Canvas Study Sync — Canvas LMS Downloader for macOS and Windows

A Canvas LMS course downloader and local study archive for students who want course materials in their own workflow. Organize downloaded files into searchable study packs for NotebookLM, local editors and notes, or use an institution-approved Canvas connection for incremental background syncing.

**[Student setup guide](docs/QUICKSTART.md) · [Download source ZIP](https://github.com/Shady-hl/canvas-study-sync/archive/refs/heads/main.zip) · [What works without a Canvas connection?](#choose-how-to-start)**

**Experimental beta · macOS and Windows · noncommercial source available.** Independent community software, not affiliated with or endorsed by Instructure, Canvas, TimeEdit, or any educational institution. Use only where your institution permits this form of automation. Successful authentication does not establish permission.

**Local-file study packs work without Canvas sign-in. Automatic Canvas syncing is a developer preview and requires institution-approved OAuth setup.** There is no bundled developer key, hosted sign-in service, or claimed approval from any institution. The documented native implementation is for trusted administrator/developer installations; a broad consumer integration still needs an approved architecture that protects the client secret. See [OAuth setup and deployment limits](docs/OAUTH_SETUP.md).

## Choose how to start

| Your situation | Start here |
|---|---|
| You have permitted course downloads and want study packs | Open the launcher, choose **Use downloaded files**, open the study folder, add files, then choose **Build study packs**. No Canvas login is needed. |
| Your institution has configured an approved integration | Choose **Sync from Canvas**, complete Canvas sign-in, choose your pace, then start the background resync. |
| You want automatic sync but your institution is not connected | Use downloaded files for now. The window includes a request you can copy, review and send to your Canvas administrator yourself. |

This is intended for students across institutions. Automatic access depends on each institution's configuration and permission; it cannot work universally just because a student can log in to Canvas.

## What it does

- Builds local study packs from permitted PDFs, PowerPoint/Word files, text, notebooks and datasets, with no Canvas account connection. Files and generated packs remain on your computer.
- With approved OAuth setup, checks your active student courses and downloads new, changed or locally missing accessible attachments. Unchanged attachments are reused; metadata and text still require requests.
- Exports accessible pages, assignments, announcements, discussions, syllabus and quiz descriptions. It does not submit work, answer quizzes or change grades.
- Offers a finish-time slider, one request at a time, server backoff, persistent throttling notices, and protective limits. Estimates use previous runs and are approximate.
- Extracts local text into subject packs with source/page/cell references and a local searchable index. No AI service is called, no model is trained, and no source material is automatically uploaded anywhere.
- Optionally refreshes a TimeEdit feed or imports a local timetable snapshot.
- Retains private, structured timing logs for later analysis, without credentials, raw resource paths, private URLs, response bodies or Wi-Fi identifiers.

## First run

Install **Python 3.11 or newer**, then download this source repository using **Code → Download ZIP** and extract it. Keep the extracted source outside your private study archive.

1. On **Mac**, open `OPEN_RESYNC.command`. If Finder does not launch it, run `zsh ./OPEN_RESYNC.command` from the extracted folder.
2. On **Windows**, open `OPEN_RESYNC.cmd`.
3. Choose **Use downloaded files**. Click **Open my study folder**, add permitted course files inside `My course--0/Files`, then click **Build study packs**. Existing archives keep their course folders. Open `00_START_HERE.md` in the study folder when finished.
4. For automatic syncing on a configured, approved installation, choose **Sync from Canvas**, start sign-in, follow **Continue to Canvas**, and approve access on Canvas. Paste the address of Canvas's native completion page into the local response field. This short-lived response is bound to that sign-in attempt; it is not a session cookie.
5. Choose your pace and start the resync. PDF/timezone dependencies are prepared automatically when a build or sync first needs them. Python 3.14 is supported; you do not need Python 3.9.

Once running, you can close the tab or browser. Keep the computer on, awake and online. Reopen the launcher to return to progress. First-time setup, network changes, new material and Canvas pauses can extend the finish estimate.

Local-file mode makes no Canvas requests. A previously configured TimeEdit feed can still refresh, and first-time dependency setup needs internet access. Original course files are not uploaded. Scanned PDFs and visual material may need the original; review extraction coverage.

The default destination is `Documents/Canvas-Archive`, separate from the source ZIP. To group additional local courses, create folders such as `Economics--1/Files` and `Programming--2/Files`. Preserve the distinct numeric endings. The [student guide](docs/QUICKSTART.md) explains where to find each workflow's output.

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

## Looking for a Canvas scraper or Canvas backup tool?

Canvas Study Sync uses Canvas API endpoints for approved syncing, and local file extraction for study packs. It can help download Canvas course files and maintain an offline study archive. It is not a complete account backup: locked items, inaccessible resources and unsupported content can be missing. Previously downloaded material is retained when remote material disappears.

Searching for a “Canvas scraper” does not change the access requirements. The public launcher uses configured OAuth, respects server backoff and does not bypass login or course permissions. Using a session cookie still calls the API. The tool does not provide an automatic Notion integration, submit work, or send course content to an AI service.

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
