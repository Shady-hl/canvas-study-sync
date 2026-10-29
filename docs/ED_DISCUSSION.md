# Import Ed Discussion

Ed is separate from Canvas. A Canvas login or CanvasAPI library cannot read Ed threads on its own. This importer uses your own regional Ed API token and reads only accessible content.

## Set up

1. Open the launcher and choose **Use downloaded files → Open my study folder**.
2. For each Ed course, create a course folder with the exact code shown by Ed and a distinct numeric ending: for example `ECON101--1/Files`. You can put local slides and notes there too. Existing Canvas archives automatically use their saved course codes.
3. Open **Import Ed Discussion**. Select Europe, United States or Australia, matching your account's region. Follow **Open Ed API token settings**, create a token in Ed, then paste it into the app's hidden field. Keep it out of chat, screenshots and reports.
4. Optionally select **Remember on this computer**, then **Refresh Ed & study packs**.
5. Read **ED_SYNC_REPORT.html** in your archive. Check course matches and notices before relying on the snapshot.

A region change needs that region's token. Europe has been checked with a real account; other regions use the same regional API pattern but have not been tested with live accounts. Ed's API is beta and can change.

## What is saved

Each matched course gets `Discussions/Ed-<region>-<course-id>/`, an index, readable HTML threads, supported attachments and hidden raw XML sidecars. Threads include answers and nested comments returned by Ed. Maths and code are preserved in text form. Staff, anonymous authors and accepted answers are labelled; an accepted answer is not automatically official course guidance.

Reports distinguish current coverage from older snapshots retained on disk. Failed items do not erase previous successful thread exports. Private, removed or otherwise inaccessible content is not claimed. Attachments on unsupported hosts or requiring separate access remain links with notices. Embedded remote images are not automatically loaded when viewing saved pages.

Ed HTML becomes teaching-source input for the local study exporter. Raw sidecars and import reports are retained for verification, separately from teaching extracts. The study-context snapshot records Ed freshness separately from Canvas downloads and local rebuilding. A failed local rebuild can therefore leave a newer Ed archive alongside the previous study packs.

## Saved tokens and portable copies

Remembering uses the operating system's secure store: macOS Keychain, Windows Credential Manager, or Linux Secret Service. The app explicitly selects those built-in stores and does not fall back to plaintext files or third-party keyring plugins.

Once a token is saved, leave the field blank on later Ed refreshes. A replacement token is saved only if you select Remember and authentication succeeds. **Forget saved Ed token** removes the selected region's local credential; it does not revoke the token at Ed or remove it from other computers. Revoke tokens in Ed settings when needed.

Portable copies contain the importer, course data and non-secret mapping/report files. They contain no saved token, system keychain or Python environment. On another computer—or for another person—enter that account's own token. Copies running under the same OS account on the same computer share this app's saved token per region.

If the secure store is locked or unavailable, unlock it or paste a token for one run with Remember unchecked. Linux needs a running Secret Service session. The operating system may show a credential-access prompt. Saving credentials does not protect them from software already running with sufficient access to your account.

## Courses did not match

The importer matches course codes only when the result is unique. It does not guess between semesters or sections. Name a local course folder using the Ed code, or create `ED_CONFIG.json` in your private archive with an explicit mapping from Ed course ID to the numeric suffix of a course folder:

```json
{"course_map": {"123": "1"}}
```

Here Ed course 123 maps to `ECON101--1`. Use the actual ID from your Ed course URL and your own folder suffix. Reports list unmatched active courses. This settings file is local data and is excluded from the public repository.

## Command line

```bash
python3 -B canvas-sync/study_update.py --archive "/path/to/Canvas-Archive" --ed-only --ed-region eu --ed-remember
```

A hidden prompt is used if no token is supplied or saved. Background runs use `--ed-use-saved` to stop with a clear message rather than prompt. Ed credentials are removed from the environment before a Canvas child runs. Do not put tokens in command arguments, URLs or scripts.

Remove the saved token for a region:

```bash
python3 -B canvas-sync/ed_credentials.py --forget --region eu
```

If a cloud folder times out during a study rebuild, make the source files available offline in your cloud app and retry. Previous committed study packs are retained on a failed rebuild.
