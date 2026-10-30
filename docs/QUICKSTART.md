# Student setup: bring Canvas materials into your workflow

Canvas Study Sync is for students who want a private local study archive, searchable course extracts and packs for their preferred study tools. You can start with files you are permitted to download. Automatic Canvas syncing additionally requires an institution-approved integration; your normal Canvas login alone does not configure one.

**Want an AI to walk you through it?** Copy the single prompt in [Set up with AI](../AI_SETUP.md). The assistant can guide installation and explain the output without seeing any credential or private course material.

Once Python and the source download are set up, the usual path is **open the launcher → complete your configured sign-in → Resync everything → open the output**. The app downloads and prepares the packs in one run; you do not need a separate export command. A permitted personal-testing session window accepts its session value locally. The public sign-in path requires the approved OAuth setup described below.

## 1. Download and open

1. Install **Python 3.11 or newer** from [python.org](https://www.python.org/downloads/) if it is not already installed. **Python 3.14 works.** On Windows, include the Python launcher when offered. You do not need Anaconda or an exact Python 3.9 installation.
2. [Download the source ZIP](https://github.com/Shady-hl/canvas-study-sync/archive/refs/heads/main.zip) and extract it. Keep the source separate from course materials.
3. On **Mac**, open `OPEN_RESYNC.command`. On **Windows**, open `OPEN_RESYNC.cmd`. The launcher opens the study window in your browser. This source download is not a signed or notarized application installer.
4. Choose one of the two paths below. PDF and timezone support are prepared automatically when needed; a first build can take longer.

If Finder cannot launch the Mac script, open Terminal in the extracted folder and run `zsh ./OPEN_RESYNC.command`. On Windows, open `OPEN_RESYNC.cmd` from the extracted folder, not from inside the ZIP. If Python is missing, use the official installer and reopen the launcher. The launcher checks existing compatible Python installations.

## 2A. Use downloaded files — no Canvas sign-in

1. Select **Use downloaded files**.
2. Enter a course name or its exact Ed code, then click **Create course**. Existing archives list their courses automatically.
3. Choose the course, select its permitted downloads, then click **Add selected files**. You can select multiple files. PDFs, `.pptx`, `.docx`, notes, notebooks and CSV/TSV datasets are supported; visual material may still need the originals. Each file can be up to 200 MB.
4. Click **Build study packs**. You can close the tab while it works. Keep the computer awake and reopen the launcher to return.
5. Use **Open study packs** on the home screen. Check notices and the coverage report before relying on every extract.

The app creates course folders for you. Adding an identical file again reuses it; a changed file with the same name gets a separate copy, preserving your original and annotations. Copies stay local. **Open my study folder** is available for manual organization; legacy folders such as `Economics--1/Files` still work.

The home screen records study packs, Ed and Canvas separately. A successful Ed import can be followed by a blocked local rebuild. If that happens, **Open source folder** shows the problem’s location and **Retry study packs** retries without downloading Canvas or Ed again. Make cloud-only files available offline first. The previous committed study snapshot is retained on failure, and the advice remains after reopening the app.

This path makes no Canvas requests and uses no Canvas credential. A saved TimeEdit feed can still refresh. First-time setup may download dependencies. Files are processed locally and are not uploaded to a third-party study or AI service.

## 2B. Sync from Canvas — approved integration required

1. Select **Sync from Canvas**. If sign-in is unavailable, open **My institution is not connected yet**. It contains a request you can copy, review and send to your Canvas administrator yourself. The tool sends no message for you.
2. Your institution must approve the purpose and configure a suitable integration. The [administrator guide](OAUTH_SETUP.md) explains the current native developer installation and its limits. Students should not obtain or share developer-key secrets.
3. On a configured installation, click **Start Canvas sign-in**, then **Continue to Canvas**. Check that the destination is your institution's actual Canvas site. Sign in and approve the requested access there.
4. For this native flow, copy the **complete address of Canvas's completion page** into the app's hidden authorization-response field. This is a one-time code/state response, not your password or session cookie. If it expires, begin sign-in again.
5. Keep the default gentle pace unless you have a reason to adjust it, then click **Resync everything**. The finish estimate is approximate. Server-requested waiting takes priority.
6. When finished, review `SYNC_REPORT.html` and any notices. The tool checks metadata to discover changes even when existing attachments are cached. It retains previously downloaded content when remote items disappear or become inaccessible.

If your institution disables personal token creation, do not assume a session cookie is an approved substitute. The public launcher does not ask students to extract browser cookies. Successful authentication, slow pacing and school Wi-Fi cannot establish institutional permission or guarantee freedom from account restrictions. See [Canvas's OAuth guidance](https://developerdocs.instructure.com/services/canvas/oauth2/file.oauth.md#manual-token-generation).

## 3. Use the output in your workflow

| Workflow | What to open |
|---|---|
| NotebookLM | `AI_READY/NOTEBOOKLM`, then the relevant subject's guide and packs. Choose what to upload yourself where permitted. |
| VS Code or another compatible editor | A workspace in `AI_READY/VSCODE`, plus the original course files. |
| Notes, planning or another assistant | Start with `00_START_HERE.md`; use the relevant extracted Markdown and source references. `ASSISTANT_CONTEXT/CURRENT.json` is the latest local snapshot. |

No AI subscription or API key is required to generate the packs. No model is trained and no AI service is called. There is no built-in Notion sync; copying selected Markdown into your notes is a separate action. Already uploaded NotebookLM sources do not update automatically after a local refresh.

For your own edits, use a course's **Practice** folder or a separate copy. Downloaded originals and generated packs may be replaced during refresh. Review course copyright, privacy and assessment rules before sharing or uploading content anywhere.

## When something does not work

| Message or symptom | Next step |
|---|---|
| Python 3.11 or newer required | Install a current Python from python.org, then reopen. Python 3.14 is supported. |
| Add course files | Choose or create a course, select its files and click Add selected files, then build. |
| Source not available locally | Use Open source folder, make the file available offline in your cloud app, then Retry study packs. |
| Refresh needs attention | Read the named file and recovery advice on the home screen. Previous study packs are retained. |
| Existing unrelated files | Use a new empty destination via the `--archive` option in [usage](USAGE.md); the app leaves unrelated files untouched. |
| Canvas sign-in setup needed | Local files work now. Automatic sync needs the approved configuration described above. |
| Canvas asks to slow down | Let the app wait. Repeated throttling stops the run; try later rather than starting parallel runs. |
| Sign-in expired or denied | Begin the approved sign-in again. Access-denied items may remain unavailable. |
| Tab closed, computer slept, or connection dropped | Reopen the same launcher. Finished downloads remain saved; review progress and retry after the cause is resolved. |
| Finished with notices | Review the coverage report and originals. Completion does not certify full Canvas or extraction coverage. |

The project remains a noncommercial experimental beta. This guide cannot grant institutional permission or guarantee freedom from technical, account or legal consequences.
