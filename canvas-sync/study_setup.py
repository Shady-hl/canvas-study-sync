"""Simple study entry points and a portable archive snapshot; no uploads."""
import contextlib
import json
from pathlib import Path
import plistlib
import sqlite3
import shutil
import tempfile
from urllib.parse import quote
import zipfile


ROOT_SHORTCUTS = {
    'OPEN_RESYNC': 'OPEN_RESYNC',
    'UPDATE_MATERIALS': 'SYNC_AND_EXPORT',
    'REFRESH_CONTEXT': 'EXPORT_ONLY',
    'MAKE_PORTABLE': 'MAKE_PORTABLE',
    'SETUP_THIS_PC': 'SETUP_THIS_PC',
}


def write_shortcuts(root):
    # A new archive can live outside the source checkout. Its generated
    # launchers need the tool, but never copy environments or user data.
    source = Path(__file__).resolve().parent
    target = root / 'canvas-sync'
    if not target.exists() and target.resolve() != source:
        target.mkdir()
        for item in source.iterdir():
            if item.is_file() and (item.suffix in {'.py', '.cmd', '.command', '.md', '.html'} or item.name in {'requirements-ai.txt', '.canvas-sync-tool.json'}):
                shutil.copy2(item, target / item.name)
        if (source / 'tests').is_dir():
            shutil.copytree(source / 'tests', target / 'tests', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    from ai_export import write_utf8
    marker = 'Canvas-Sync generated shortcut'
    for name, command in ROOT_SHORTCUTS.items():
        for suffix in ('.cmd', '.command'):
            shortcut = root / (name + suffix)
            if shortcut.exists() and marker not in shortcut.read_text(encoding='utf-8'):
                continue
            if suffix == '.cmd':
                text = '@echo off\nrem ' + marker + '\ncall "%~dp0canvas-sync\\' + command + '.cmd" %*\nexit /b %ERRORLEVEL%\n'
            else:
                text = '#!/bin/zsh\n# ' + marker + '\nexec /bin/zsh "${0:A:h}/canvas-sync/' + command + '.command" "$@"\n'
            write_utf8(shortcut, text)
            if suffix == '.command':
                shortcut.chmod(shortcut.stat().st_mode | 0o111)
    write_mac_app(root)


def write_mac_app(root):
    """A relocatable Finder app; can also be packaged when built on Windows."""
    from ai_export import write_utf8
    app = root / 'Canvas Resync.app'
    owned = app / 'Contents/.canvas-resync-owned'
    if app.exists() and not owned.is_file():
        return
    executable = app / 'Contents/MacOS/CanvasResync'
    executable.parent.mkdir(parents=True, exist_ok=True)
    write_utf8(owned, 'Canvas-Sync generated resync launcher\n')
    write_utf8(executable, '#!/bin/zsh\n# Canvas-Sync generated resync launcher\narchive_root="${0:A:h:h:h:h}"\n# Terminal already has the user\'s archive access; Finder-launched scripts may not.\n# Dispatch through Launch Services instead of reading the cloud script ourselves.\n/usr/bin/open -g -a Terminal "$archive_root/OPEN_RESYNC.command"\nresync_exit=$?\nif (( resync_exit != 0 )); then\n    /usr/bin/osascript -l AppleScript -e \'display alert "Canvas Resync could not open" message "Open OPEN_RESYNC.command from your Canvas-Archive folder in Finder. The helper will use your installed Python 3.11 or newer, including Python 3.14."\'\nfi\nexit "$resync_exit"\n')
    executable.chmod(executable.stat().st_mode | 0o111)
    with (app / 'Contents/Info.plist').open('wb') as handle:
        plistlib.dump({'CFBundleName': 'Canvas Resync', 'CFBundleDisplayName': 'Canvas Resync',
                      'CFBundleIdentifier': 'local.personal.canvas-resync',
                      'CFBundleExecutable': 'CanvasResync', 'CFBundlePackageType': 'APPL',
                      'CFBundleVersion': '1', 'LSUIElement': True,
                      'NSHighResolutionCapable': True}, handle)


def default_archive(script_path=None):
    location = Path(script_path or __file__).resolve().parent
    for parent in (location, *location.parents):
        if (parent / '.canvas-sync.json').is_file():
            return parent
    return Path.home() / 'Documents' / 'Canvas-Archive'


def book_context(root, sources):
    path = root / 'BOOK_CATALOG.json'
    catalog = json.loads(path.read_text(encoding='utf-8')).get('books', {}) if path.is_file() else {}
    books = [s for s in sources if s['role'] == 'book']
    if not books:
        return ''
    text = '\n## Textbooks: editions and exam scope\n\n'
    text += 'Course instructions and assigned readings determine exam scope. A whole textbook being available does not make every chapter examinable.\n\n'
    for source in books:
        entry = catalog.get(source['path'], {})
        use = entry.get('use', 'Optional reference' if source['title'].startswith('Optional -') else 'Reference; assigned scope must be checked')
        text += f'- **{source["title"]}** — {use}. {entry.get("note", "Follow the current syllabus and Canvas reading assignments.")} Original: `{source["path"]}`.\n'
    return text


PROMPTS = '''## Useful prompts

Exam practice: "Use the current course syllabus, lectures and assigned readings. Ask one practice question at a time, let me answer first, then explain my errors with source citations. Treat optional books as background and keep solutions hidden until I try."

Study planning: "Use 01_TIMETABLE and the current assignment/exam requirements. Ask about my available study hours, commute and other commitments before scheduling. Avoid class clashes; suggest preparation and spaced review. Flag missing topics, stale dates and uncertain exam details."

Lecture coding: "Read the relevant course notebook and the timetable source first. Help me understand the next step with hints. Work on my copy in Practice; cite the lecture file or notebook cell."
'''


def start_guide(manifest, output_name, portable=False):
    text = '# Your study folder\n\n'
    text += 'For a full background update, open **Canvas Resync.app** (macOS) or **OPEN_RESYNC.cmd** (Windows) at the archive root. Enter your approved credential in the private local window, choose **Resync everything**, and reopen the launcher whenever you want to check progress. Missing PDF/timezone support is handled automatically.\n\n'
    text += 'Use NotebookLM for subject learning and exam practice. Use the programming workspace in VS Code or Antigravity for notebooks, data and exercises.\n\n'
    text += '| Subject | NotebookLM: upload the Markdown files here | VS Code workspace |\n|---|---|---|\n'
    for subject, entry in manifest['subjects'].items():
        text += f'| {entry["title"]} | [Read subject guide]({quote("NOTEBOOKLM/" + subject + "/00_READ_FIRST.md", safe="/")}) | [Open workspace]({quote(entry["vscode"], safe="/")}) |\n'
    text += '\nCreate one NotebookLM notebook per subject. Add that subject\'s Markdown files with Add sources. Include `00_READ_FIRST.md` and `01_TIMETABLE.md` when present. Select optional book sources only when useful. Add the original PDF for the specific graph, table or equation you need. TXT books are useful searchable context but do not preserve all visual content.\n\n'
    text += 'The timetable is an explicit source, not a live connection inside NotebookLM. Re-exporting refreshes the local files; replace the changed local uploads in NotebookLM afterward. OneDrive syncing does not update a previously uploaded NotebookLM source.\n\n'
    text += 'For programming, open the workspace with File → Open Workspace from File. It contains the course originals and the subject\'s AI context, including the timetable. Tell the assistant which lecture to read. Copy a notebook into the course\'s `Practice` folder before editing it; downloaded originals and generated context are rebuilt. Practice is preserved and excluded from teaching-source extraction.\n\n'
    text += 'An advertised 1-million-token window is model/app-specific, not a guarantee that all uploaded sources are simultaneously read. Use one subject and relevant book chapters. NotebookLM\'s source limits differ from raw model context limits; the exporter keeps each source conservatively below its documented per-source limits.\n\n'
    text += PROMPTS + '\n## Folder map\n\n'
    text += '- `NOTEBOOKLM`: the files to add to NotebookLM, organised by subject.\n- `VSCODE`: workspaces for original course files plus readable AI context.\n- `PLANNING`: the complete timetable as Markdown and JSON.\n- `_EXTRAS` (or the old top-level alternatives): other formats of the same material and rebuild support.\n\n'
    text += '| Alternative | Purpose | Does it repeat information? |\n|---|---|---|\n'
    text += '| DOCUMENTS | One extracted Markdown document per original; useful for reading in Obsidian. | Yes: same source text, different arrangement. |\n'
    text += '| UPLOAD | Small text packs plus original PDF/image/notebook companions. | Yes: extracted text and copies of the originals. |\n'
    text += '| CHAT | Small text-only packs for other chat assistants. | Yes: same extracted teaching text. |\n'
    text += '| PACKAGES / AI_UPLOAD.zip | ZIP copies for transport. | Yes: extract before uploading. |\n'
    text += '| search.sqlite | Reusable extraction cache and local search. | Internal support; keep it for faster refreshes on other PCs. |\n\n'
    text += '**Choose one text format per subject.** For NotebookLM, use NOTEBOOKLM only, plus specific originals where needed. An LLM receives the files you select or ask it to read; it does not automatically absorb a whole folder tree. The numeric suffix in a subject folder is a stable Canvas ID, not another subject or another layer of files.\n\n'
    if not portable:
        text += 'Older `AI` folders and the tool\'s `dist` backups are legacy output; they are omitted from new portable snapshots.\n\n'
        text += '## OneDrive\n\nUse one extracted `Canvas-Archive` folder inside OneDrive as your working copy on both devices. The Documents copy is independent and may be older. Mark the OneDrive folder **Always keep on this device** before coding or syncing, and wait for the download to finish. Blue cloud icons mean the content still needs downloading when read. Run sync/export on one device at a time, then wait for OneDrive to finish before switching PCs. A local OS lock cannot coordinate two different computers.\n\n'
    else:
        text += '\nThis portable snapshot contains originals, Practice work, NotebookLM context, timetable, workspaces and the extraction cache. Other alternative formats can be recreated. Private feed URLs are excluded; configure permitted live feeds on the destination computer. Use the study archive for your own devices and share only the separate source repository with other people.\n\n'
    text += '## Run the setup\n\nOpen [the main start guide](../00_START_HERE.md) for exact commands, setup, Obsidian and slow-sync troubleshooting. At the archive root, use `.command` files on macOS and `.cmd` files on Windows. Double-click UPDATE_MATERIALS for Canvas + TimeEdit + context, REFRESH_CONTEXT for existing materials + TimeEdit, or MAKE_PORTABLE to make a new portable snapshot, with the extension for your system. Ordinary refreshes do not rebuild the large portable ZIP. Run SETUP_THIS_PC once on each computer to install the PDF reader and timezone data. The Mac setup creates a separate local `canvas-sync/.venv-macos` environment; the Windows launchers select their own Python.\n\n'
    text += 'Sources and citations protect traceability; they do not prove complete Canvas access or perfect PDF extraction. Check coverage notices and use originals for precise mathematical/visual details.\n\n'
    text += 'NotebookLM source documentation: https://support.google.com/gemininotebook/answer/16215270?hl=en\nOneDrive limits: https://support.microsoft.com/en-us/onedrive/restrictions-and-limitations-in-onedrive-and-sharepoint\n'
    return text


def write_portable(stage, manifest):
    from ai_export import digest_file, ExportError, output_directory, write_utf8, FORMAT, PIPELINE
    root = stage.parent
    name = output_directory(root).name
    guide = start_guide(manifest, name)
    package = root / '.Canvas-Study-Portable.incoming.zip'
    portable_guide = start_guide(manifest, name, True)
    tool = Path(__file__).resolve().parent
    included = set()
    source_folders = {Path(s['path']).parts[0] for s in manifest['sources'].values() if len(Path(s['path']).parts) > 1}
    with zipfile.ZipFile(package, 'w', zipfile.ZIP_DEFLATED) as archive:
        def add(path, relative):
            if relative not in included and path.is_file() and not path.is_symlink():
                if relative.endswith('.command') or relative == 'Canvas Resync.app/Contents/MacOS/CanvasResync':
                    # Retain executable Mac launchers even in ZIPs made on Windows.
                    info = zipfile.ZipInfo.from_file(path, 'Canvas-Archive/' + relative)
                    info.create_system = 3
                    info.external_attr = 0o100755 << 16
                    info.compress_type = zipfile.ZIP_DEFLATED
                    archive.writestr(info, path.read_bytes())
                else:
                    archive.write(path, 'Canvas-Archive/' + relative)
                included.add(relative)
        for folder in sorted(source_folders):
            for path in sorted((root / folder).rglob('*')):
                tail = path.relative_to(root / folder)
                if any(p in {'History', '__pycache__', '.venv', '.git'} or p.startswith('.') for p in tail.parts):
                    continue
                if path.is_dir() and path.name == 'Practice' and not path.is_symlink():
                    relative = path.relative_to(root).as_posix() + '/'
                    archive.writestr('Canvas-Archive/' + relative, b'')
                    included.add(relative)
                add(path, path.relative_to(root).as_posix())
        for source in manifest['sources'].values():
            path = root / source['path']
            if digest_file(path) != source['sha256']:
                raise ExportError('A source changed while preparing the portable archive. Previous export retained.')
            add(path, source['path'])
        for filename in ('.canvas-sync.json', 'AI_CONFIG.json', 'BOOK_CATALOG.json', 'index.html', 'SYNC_REPORT.html', 'SYNC_REPORT.json'):
            add(root / filename, filename)
        for filename in ('timeedit.csv', 'timeedit.ics', 'timeedit-live.ics', 'schedule.json'):
            add(root / 'Schedule' / filename, 'Schedule/' + filename)
        config_path = root / 'Schedule/.timeedit-source.json'
        if config_path.is_file():
            config = json.loads(config_path.read_text(encoding='utf-8'))
            snapshot = next((name for name in ('timeedit-live.ics', 'timeedit.ics', 'timeedit.csv') if (root / 'Schedule' / name).is_file()), None)
            if snapshot:
                archive.writestr('Canvas-Archive/Schedule/.timeedit-source.json', json.dumps({'file': snapshot, 'timezone': config.get('timezone', 'UTC')}))
        for profile in ('NOTEBOOKLM', 'PLANNING', 'VSCODE'):
            for path in sorted((stage / profile).rglob('*')):
                add(path, name + '/' + path.relative_to(stage).as_posix())
        from archive_layout import support_file
        # A SQLite backup is consistent even if another connection has the DB
        # open. The archive lock prevents another exporter from replacing it.
        with tempfile.TemporaryDirectory() as temp:
            snapshot = Path(temp) / 'search.sqlite'
            with contextlib.closing(sqlite3.connect(support_file(stage, 'search.sqlite'))) as source_db, contextlib.closing(sqlite3.connect(snapshot)) as dest_db:
                source_db.backup(dest_db)
            cache_relative = name + ('/_EXTRAS/search.sqlite' if (stage / '_EXTRAS').is_dir() else '/search.sqlite')
            add(snapshot, cache_relative)
        for filename in ('CONTEXT_MANIFEST.json', 'CONTEXT_CHECKS.json', 'EXTRACTION_REPORT.md', 'BUILD_REPORT.json'):
            path = support_file(stage, filename)
            add(path, name + '/' + path.relative_to(stage).as_posix())
        for path in sorted(tool.iterdir()):
            if path.suffix.lower() in {'.py', '.cmd', '.command', '.md', '.html'} or path.name in {'requirements-ai.txt', '.canvas-sync-tool.json'}:
                add(path, 'canvas-sync/' + path.name)
        for path in sorted((tool / 'tests').glob('*.py')):
            add(path, 'canvas-sync/tests/' + path.name)
        archive.writestr('Canvas-Archive/' + name + '/00_START_HERE.md', portable_guide)
        archive.writestr('Canvas-Archive/' + name + '/.canvas-ai-owned.json', json.dumps({'format': FORMAT, 'pipeline': PIPELINE}))
        for filename in ('00_START_HERE.md', 'OPEN_THIS_FIRST.md', 'LAST_RUN.md',
                         *(name + suffix for name in ROOT_SHORTCUTS for suffix in ('.cmd', '.command'))):
            add(root / filename, filename)
        for path in sorted((root / 'Canvas Resync.app').rglob('*')):
            add(path, path.relative_to(root).as_posix())
        add(root / 'ASSISTANT_START_HERE.md', 'ASSISTANT_START_HERE.md')
        assistant = root / 'ASSISTANT_CONTEXT'
        for filename in ('PROFILE.md', 'CURRENT.json'):
            add(assistant / filename, 'ASSISTANT_CONTEXT/' + filename)
        if (assistant / 'CURRENT.json').is_file():
            from study_update import previous_sources
            current = json.loads((assistant / 'CURRENT.json').read_text(encoding='utf-8'))
            previous_sources(assistant, current)  # Validate the referenced inventory first.
            if current.get('source_manifest_sha256'):
                filename = current['source_manifest_sha256'] + '.json'
                add(assistant / 'manifests' / filename, 'ASSISTANT_CONTEXT/manifests/' + filename)
    manifest['portable'] = {'file': package.name, 'files': len(included) + 3,
                            'bytes': package.stat().st_size, 'contains_private_timetable_config': False}
    destination = root / 'Canvas-Study-Portable.zip'
    package.replace(destination)
    return destination


def make_portable(root):
    from ai_export import output_directory
    from archive_layout import support_file
    target = output_directory(root)
    path = support_file(target, 'CONTEXT_MANIFEST.json')
    if not path.is_file():
        raise ValueError('Refresh context first with REFRESH_CONTEXT.command (macOS) or REFRESH_CONTEXT.cmd (Windows), then make the portable ZIP.')
    write_root_guide(root)
    return write_portable(target, json.loads(path.read_text(encoding='utf-8')))


def write_root_guide(root):
    from ai_export import output_directory, write_utf8
    marker = '<!-- Canvas-Sync generated study guide -->'
    path = root / 'OPEN_THIS_FIRST.md'
    if path.exists() and not path.read_text(encoding='utf-8').startswith(marker):
        return
    write_utf8(path, marker + '\n# Start here\n\nRead [00_START_HERE.md](00_START_HERE.md).\n')
    name = output_directory(root).name
    text = marker + '\n# Your private study archive\n\n'
    text += 'Open Canvas Resync.app on Mac or OPEN_RESYNC.cmd on Windows. Use an institution-approved credential and choose your pace. Closing the browser is fine once the run starts; keep the computer awake and online.\n\n'
    text += '| Task | macOS | Windows |\n|---|---|---|\n| Full resync window | OPEN_RESYNC.command | OPEN_RESYNC.cmd |\n| Canvas plus local study refresh | UPDATE_MATERIALS.command | UPDATE_MATERIALS.cmd |\n| Refresh existing materials | REFRESH_CONTEXT.command | REFRESH_CONTEXT.cmd |\n| Private portable archive | MAKE_PORTABLE.command | MAKE_PORTABLE.cmd |\n| Install local PDF/timezone support | SETUP_THIS_PC.command | SETUP_THIS_PC.cmd |\n\n'
    text += 'For full local integrity verification, run `zsh ./REFRESH_CONTEXT.command --full` on Mac or `.\\REFRESH_CONTEXT.cmd --full` on Windows. Token access is preferred; cookie-authenticated automation requires institutional permission.\n\n'
    text += 'Read SYNC_REPORT.html for access gaps and notices. Use ' + name + '/NOTEBOOKLM for subject context and ' + name + '/VSCODE for coding workspaces. ASSISTANT_START_HERE.md points a local assistant to the latest committed study snapshot. Uploading material to an AI service is a separate action requiring the necessary rights.\n\n'
    text += 'Keep original edits in Practice or a separate copy. Generated files and downloaded originals can be replaced. This folder and portable study ZIPs contain private/copyrighted material; do not publish them as the source repository.\n'
    write_utf8(root / '00_START_HERE.md', text)
    write_shortcuts(root)
