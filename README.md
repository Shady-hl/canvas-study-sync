# Canvas Study Sync

**Turn your course files and Ed discussions into searchable, source-linked study packs.**

Keep a local course archive, prepare subject packs for NotebookLM or your editor, and see where each extract came from. Files are processed locally and saved in your chosen study folder; the app does not send them to an AI service.

[**Start here**](docs/QUICKSTART.md) · [Set up with an AI assistant](AI_SETUP.md) · [Download source ZIP](https://github.com/Shady-hl/canvas-study-sync/archive/refs/heads/main.zip)

Experimental beta · Python 3.11+ · macOS and Windows · noncommercial source available

## Choose your workflow

| What you have | What to do |
|---|---|
| Downloaded slides, PDFs, notes or notebooks | Choose **Use downloaded files**, add your materials and **Build study packs**. No Canvas login needed. |
| An Ed Discussion course | Open **Import Ed Discussion** under downloaded files. Add your own Ed API token and optionally remember it on this computer. [Ed setup →](docs/ED_DISCUSSION.md) |
| An institution-configured Canvas integration | Choose **Sync from Canvas** for incremental downloads through approved OAuth. [Connection setup →](docs/OAUTH_SETUP.md) |

Automatic Canvas syncing requires institution-approved configuration. There is no bundled developer key or universal sign-in service. Local files and Ed imports work independently of Canvas OAuth.

## What you get

- **Study packs by subject:** extracted text from PDFs, slides, Word documents, notebooks, code and datasets, with source/page/cell references and a local search index.
- **Ed posts and replies:** accessible threads, answers, nested comments, maths, code and supported attachments. Staff and anonymous posts are labelled; reports show gaps and unmatched courses.
- **A useful local archive:** original material alongside generated packs. With configured Canvas access, new or changed attachments are downloaded and unchanged attachments reused.
- **Honest refresh reports:** Canvas, Ed and local study-context freshness are recorded separately. Failed refreshes retain the previous committed assistant snapshot.
- **Optional timetable context:** a TimeEdit feed or local timetable snapshot.
- **Portable study copies:** move your archive between your own computers. Saved Ed credentials stay in the operating system's secure store and never enter the portable ZIP.

The app does not call an AI service, generate answers, train a model, or automatically upload your course material. Source extraction can miss scanned text, diagrams and visual equations; coverage reports point you back to the originals.

## First run

1. Install **Python 3.11 or newer**. Download and extract the source ZIP outside your private course archive.
2. Open **OPEN_RESYNC.command** on Mac or **OPEN_RESYNC.cmd** on Windows. If Finder does not launch it, run `zsh ./OPEN_RESYNC.command` in the extracted folder.
3. Choose **Use downloaded files**. Enter a course name or Ed code, click **Create course**, select its files and click **Add selected files**. No manual folder naming is needed.
4. Click **Build study packs**, then **Open study packs**. The home screen shows separate results for study packs, Ed and Canvas.

The default study folder is **Documents/Canvas-Archive**, separate from this source repository. The app creates stable folders for your courses. Existing archives and manually managed numeric course folders still work. For automatic Ed matching, use the exact Ed course code as the course name.

PDF, timezone and secure-storage dependencies are prepared automatically on the first run that needs them. Keep your computer awake. Closing the browser tab does not stop an active run; reopen the launcher to return to progress.

### Remembering Ed sign-in

Select the correct region, paste the token into the **local app's hidden field**, and check **Remember on this computer**. A token is saved only after an authenticated import. Later, leave the field blank to reuse it. Use **Forget saved Ed token** to remove the selected region's saved credential.

Storage uses **macOS Keychain**, **Windows Credential Manager**, or **Linux Secret Service**. There is no plaintext fallback. Each person and computer supplies its own token. See [Ed setup and troubleshooting](docs/ED_DISCUSSION.md).

## See what finished

The home screen keeps **study packs**, **Ed imports** and **Canvas downloads** separate, with their own dates. If a local file blocks rebuilding, a successful Ed import stays visible. The blocked file and next action remain on screen after reopening the app; **Open source folder** and **Retry study packs** help you recover. Your previous committed study snapshot is retained.

Repeated file additions reuse identical content; changed files get a separate copy so an annotated original is preserved. File selection copies to the local app, not an external service.

## Where to find the result

| Output | Use |
|---|---|
| `00_START_HERE.md` | Guide to the private archive and current outputs |
| `AI_READY/NOTEBOOKLM` | Subject packs and instructions for choosing sources |
| `AI_READY/VSCODE` | Subject workspaces for a local editor |
| `ASSISTANT_CONTEXT/CURRENT.json` | Latest committed context and source freshness |
| `ED_SYNC_REPORT.html` | Latest Ed attempt, coverage and course matches |
| `SYNC_REPORT.html` | Canvas downloads and access notices, after a Canvas sync |

Originals remain available for checking diagrams, tables and extraction gaps. Keep your own edits in **Practice** or a separate copy: generated packs are refreshed. Updating local packs does not update sources you already uploaded to NotebookLM.

## Is this the right tool?

Choose this project if you want **local study packs with traceable sources and Ed discussions**. It is a source-based beta, not a packaged desktop installer.

For a browser-based Canvas archive using your existing login, consider [jasp-nerd's Canvas Course Downloader](https://github.com/jasp-nerd/canvas-course-downloader). For a packaged desktop downloader with broader sync, conversion and Panopto features, compare [BrkBuilds' Canvas Downloader](https://github.com/BrkBuilds/Canvas-Downloader). Check each project's current requirements and institutional compatibility. [CanvasAPI](https://github.com/ucfopen/canvasapi) is a developer library for building Canvas tools.

## Privacy and access

Keep course material, reports, timetable links and archive state private. A portable study ZIP contains your materials and is intended for your own devices; it is not a public software release. Passwords and tokens belong in the local app, never an AI chat or GitHub issue.

The local window binds to loopback and uses a random access key, host/origin checks and no external page assets. API credentials are restricted to their configured HTTPS service. Dependency installers receive no Canvas or Ed credentials. Ed uses GET requests, regional API tokens and no Canvas credentials; saved tokens use the OS secure store. Canvas sync uses configured OAuth; token issuance and refresh use POST. More detail: [SECURITY.md](SECURITY.md).

Use only materials and access methods your institution permits. Independent community software, not affiliated with or endorsed by Instructure, Canvas, Ed, TimeEdit or any educational institution. Ed's API is beta and may change. Inaccessible, deleted, unpublished and unsupported resources can remain gaps; no export certifies complete coverage. See [usage](docs/USAGE.md) and [supported scope](USE_POLICY.md).

## Development and feedback

```bash
python3 -m pip install -r canvas-sync/requirements-ai.txt
python3 -B -m unittest discover -s canvas-sync/tests
python3 -B tools/check_release.py
```

Use `python` on Windows. Tests use synthetic data and mocked service responses. CI covers Python 3.11 and 3.14 on Mac, Windows and Linux; that does not establish compatibility with every institution.

Useful contributions include clearer setup instructions, extraction improvements, and reproducible import bugs. [Report an issue](https://github.com/Shady-hl/canvas-study-sync/issues/new/choose) with your operating system and a redacted error. Never attach tokens or private course exports. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[PolyForm Noncommercial 1.0.0](LICENSE): **source available, not OSI open source**. Read the license for its permitted uses and grants. The code license grants no rights to course materials, student data, third-party services or trademarks. [USE_POLICY.md](USE_POLICY.md) describes the supported scope without rewriting the license.
