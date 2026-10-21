# Set up Canvas Study Sync with an AI

Copy the prompt below into your preferred AI assistant. On GitHub, use the copy button on the code block. You do not need to paste the whole repository or upload your study archive. An assistant with computer tools may be able to do the local setup for you; a chat-only assistant can walk you through the same steps.

**Never include your Canvas password, session cookie, API token, OAuth completion address or private course files in the AI chat.** Enter credentials only in the local app, or on your institution's actual sign-in page where directed. If an error needs troubleshooting, share the short error text after removing credentials and personal information.

```text
Help me set up Canvas Study Sync. I am a beginner. Use plain language and give me one action at a time, waiting for my result before continuing.

Project: https://github.com/Shady-hl/canvas-study-sync
Student guide: https://github.com/Shady-hl/canvas-study-sync/blob/main/docs/QUICKSTART.md
Authentication limits: https://github.com/Shady-hl/canvas-study-sync/blob/main/docs/OAUTH_SETUP.md

First ask whether I use Mac or Windows, and which workflow I want: NotebookLM, a local editor, or notes. Read the current student guide. If you cannot open it, ask me to paste that guide's text.

Check whether Python 3.11 or newer is already installed. Python 3.14 works. Install only what is missing, using python.org. Help me download and extract the source ZIP, then open OPEN_RESYNC.command on Mac or OPEN_RESYNC.cmd on Windows. The app prepares PDF/timezone dependencies automatically when needed.

Explain the two setup paths: use permitted downloaded files without Canvas sign-in, or sync Canvas with an institution-approved OAuth integration. If an integration is unavailable, show the local-files path and the app's administrator-request help. Do not ask me to obtain developer secrets.

If I mention a session token, distinguish a browser session cookie from an API access token or OAuth response. Manual credential modes are for permitted personal development/testing, not general student onboarding. Do not assume disabled API-token creation permits a cookie workaround, and do not remove the app's authentication gates.

Never ask me to paste a credential into this chat, a shell command, a screenshot or a shared log. Credential entry must stay in the local app. Preserve my existing files and settings; do not overwrite an unrelated folder.

Keep the steps short. Downloading and building study packs already happen in one resync; do not add a separate export step. Help me reach the finished result and open 00_START_HERE.md, the relevant AI_READY folder and any coverage notices. Uploading to another service is my separate choice. Do not promise complete coverage or freedom from institutional/account restrictions.
```

## How many steps are there?

**First time:** install Python if missing, download/extract the source, open the launcher, then choose the setup path. Institutional approval/configuration is an external prerequisite for automatic Canvas syncing; an AI cannot create that permission.

**Configured Canvas connection:** open the launcher, complete sign-in, click **Resync everything**, then open the output. Downloading, dependency preparation and study-pack generation run together. You may close the browser tab while it works; keep the computer awake and connected.

**Local files:** open the launcher, click **Open my study folder**, add permitted downloads, click **Build study packs**, then open the output.

**Your own permitted personal testing:** a session-enabled development window accepts the session value locally, then **Resync everything** produces the same output in one run. This is not the public sign-in method and does not establish permission from your institution. The explicit development options are documented in the administrator/developer guide above.

Start with `00_START_HERE.md` in the study archive. Use `AI_READY/NOTEBOOKLM` for subject packs or `AI_READY/VSCODE` for editor workspaces. Existing archives may use a different output-folder name; follow their generated guide. NotebookLM sources already uploaded are snapshots and must be updated separately.
