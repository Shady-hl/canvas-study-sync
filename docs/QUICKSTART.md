# Student setup: bring Canvas materials into your workflow

Canvas Study Sync is for students who want a private local study archive, searchable course extracts and packs for their preferred study tools. You can start with files you are permitted to download. Automatic Canvas syncing additionally requires an institution-approved integration; your normal Canvas login alone does not configure one.

## 1. Download and open

1. Install **Python 3.11 or newer** from [python.org](https://www.python.org/downloads/) if it is not already installed. **Python 3.14 works.** On Windows, include the Python launcher when offered. You do not need Anaconda or an exact Python 3.9 installation.
2. [Download the source ZIP](https://github.com/Shady-hl/canvas-study-sync/archive/refs/heads/main.zip) and extract it. Keep the source separate from course materials.
3. On **Mac**, open `OPEN_RESYNC.command`. On **Windows**, open `OPEN_RESYNC.cmd`. The launcher opens the study window in your browser. This source download is not a signed or notarized application installer.
4. Choose one of the two paths below. PDF and timezone support are prepared automatically when needed; a first build can take longer.

If Finder cannot launch the Mac script, open Terminal in the extracted folder and run `zsh ./OPEN_RESYNC.command`. On Windows, open `OPEN_RESYNC.cmd` from the extracted folder, not from inside the ZIP. If Python is missing, use the official installer and reopen the launcher. The launcher checks existing compatible Python installations.

## 2A. Use downloaded files — no Canvas sign-in

1. In the window, select **Use downloaded files**.
2. Click **1. Open my study folder**. For a new archive, the tool creates `Documents/Canvas-Archive/My course--0/Files`. If the folder could not open automatically, the window shows its address so you can open it in Finder or File Explorer.
3. Copy your permitted course downloads into **Files**. Start with a PDF or text file. PowerPoint `.pptx`, Word `.docx`, notebooks `.ipynb`, Markdown and CSV/TSV datasets are also supported. Image-only documents, diagrams and equations may need manual review of the original; there is no automatic OCR.
4. Click **2. Build study packs** and wait for the result. You can close the tab while it runs. Keep the computer awake; reopen the same launcher to see progress.
5. Open the study folder again and read **00_START_HERE.md**. A result with notices needs a coverage review, not an assumption that every file was read perfectly.

Rename `My course` if you wish while preserving its `--0` ending. For more subjects, add folders such as `Economics--1/Files` and `Programming--2/Files`, with a different number for each course. Existing Canvas archives already have course folders: keep their names and put material in the relevant course's `Files` folder.

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
| Add course files | Put at least one permitted source into the course's Files folder, then build again. |
| Existing unrelated files | Use a new empty destination via the `--archive` option in [usage](USAGE.md); the app leaves unrelated files untouched. |
| Canvas sign-in setup needed | Local files work now. Automatic sync needs the approved configuration described above. |
| Canvas asks to slow down | Let the app wait. Repeated throttling stops the run; try later rather than starting parallel runs. |
| Sign-in expired or denied | Begin the approved sign-in again. Access-denied items may remain unavailable. |
| Tab closed, computer slept, or connection dropped | Reopen the same launcher. Finished downloads remain saved; review progress and retry after the cause is resolved. |
| Finished with notices | Review the coverage report and originals. Completion does not certify full Canvas or extraction coverage. |

The project remains a noncommercial experimental beta. This guide cannot grant institutional permission or guarantee freedom from technical, account or legal consequences.
