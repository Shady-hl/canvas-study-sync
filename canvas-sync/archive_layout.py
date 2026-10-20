"""Readable course names, with rollback and unchanged canonical file contents."""
import contextlib
import json
from pathlib import Path
import re
import time
import zipfile
from urllib.parse import quote


def rename_with_retry(source, target):
    for delay in (0.5, 1, 2, 4, 8, 0):
        try:
            source.rename(target)
            return
        except OSError as error:
            if getattr(error, 'winerror', None) != 32 or not delay:
                raise
            time.sleep(delay)


class FolderRenames(dict):
    def __init__(self):
        super().__init__()
        self.deferred = []
        self.committed = False


@contextlib.contextmanager
def named_subjects(root, titles=None):
    from canvas_sync import safe_name
    root = Path(root).resolve()
    state_path = root / '.canvas-sync.json'
    if not state_path.is_file():
        yield {}
        return
    state = json.loads(state_path.read_text(encoding='utf-8'))
    report_path = root / 'SYNC_REPORT.json'
    report = json.loads(report_path.read_text(encoding='utf-8')) if report_path.is_file() else {}
    labels = {str(c['id']): c.get('name') for c in report.get('courses', [])}
    labels.update(titles or {})
    changes = FolderRenames()
    for cid, folder in state.get('courses', {}).items():
        # Rename only the old code-based defaults. Preserve custom folder names.
        if not re.fullmatch(r'[A-Z]{2,5} \d{3,5}--\d+', folder) or not labels.get(cid):
            continue
        title = re.sub(r'\s+-\s+Oslo$', '', labels[cid], flags=re.I)
        name = safe_name(title) + '--' + cid
        source, target = root / folder, root / name
        if source.is_symlink() or source.resolve().parent != root or target.resolve().parent != root:
            raise ValueError('Subject rename must stay directly inside the archive.')
        if target.exists():
            raise ValueError('Subject rename destination already exists; no folders were combined.')
        if source.is_dir():
            changes[folder] = name

    def translated(value):
        if isinstance(value, dict):
            return {translated(k): translated(v) for k, v in value.items()}
        if isinstance(value, list):
            return [translated(v) for v in value]
        if isinstance(value, str):
            normalized = value.replace('\\', '/')
            for old, new in changes.items():
                if normalized == old or normalized.startswith(old + '/'):
                    return new + normalized[len(old):]
        return value

    backups, moved = {}, []
    try:
        for old, new in list(changes.items()):
            try:
                rename_with_retry(root / old, root / new)
                moved.append((old, new))
            except OSError as error:
                if getattr(error, 'winerror', None) != 32:
                    raise
                changes.deferred.append(old)
                del changes[old]
        if changes:
            for filename in ('.canvas-sync.json', 'SYNC_REPORT.json', 'AI_CONFIG.json', 'BOOK_CATALOG.json'):
                path = root / filename
                if path.is_file():
                    backups[path] = path.read_bytes()
                    data = json.loads(backups[path].decode('utf-8'))
                    if filename == '.canvas-sync.json':
                        code_map = data.setdefault('course_codes', {})
                        for cid, folder in data.get('courses', {}).items():
                            if folder in changes:
                                code_map.setdefault(cid, folder.rsplit('--', 1)[0])
                    path.write_text(json.dumps(translated(data), ensure_ascii=False, indent=2), encoding='utf-8')
            path = root / 'index.html'
            if path.is_file():
                backups[path] = path.read_bytes()
                text = backups[path].decode('utf-8')
                for old, new in changes.items():
                    text = text.replace(quote(old, safe='/.') + '/', quote(new, safe='/.') + '/')
                path.write_text(text, encoding='utf-8')
        yield changes
    except BaseException:
        if changes.committed:
            raise
        for path, content in backups.items():
            path.write_bytes(content)
        for old, new in reversed(moved):
            rename_with_retry(root / new, root / old)
        raise


def support_file(folder, name):
    folder = Path(folder)
    compact = folder / '_EXTRAS' / name
    return compact if compact.is_file() else folder / name


def simplify(stage):
    """Move owned alternative formats, updating generated references only."""
    from ai_export import read_utf8, write_utf8, digest_file, estimate
    stage = Path(stage)
    profiles = ('CHAT', 'DOCUMENTS', 'UPLOAD', 'PACKAGES')
    support = ('search.sqlite', 'CHUNKS.jsonl', 'AI_UPLOAD.zip', 'BUILD_REPORT.json',
               'CONTEXT_MANIFEST.json', 'CONTEXT_CHECKS.json', 'EXTRACTION_REPORT.md')
    def reference(value):
        if isinstance(value, dict):
            return {k: reference(v) for k, v in value.items()}
        if isinstance(value, list):
            return [reference(v) for v in value]
        if isinstance(value, str) and (value in support or any(value.startswith(p + '/') for p in profiles)):
            return '_EXTRAS/' + value
        return value
    for name in ('CONTEXT_MANIFEST.json', 'BUILD_REPORT.json'):
        path = stage / name
        data = reference(json.loads(read_utf8(path)))
        data['simple_layout'] = True
        write_utf8(path, json.dumps(data, ensure_ascii=False, indent=2))
    page = read_utf8(stage / 'index.html')
    for name in (*profiles, *support):
        page = page.replace('href="' + name, 'href="_EXTRAS/' + name)
    write_utf8(stage / 'index.html', page)
    # Inventory/usage guides have no source excerpt bodies. Never rewrite the
    # teaching text: verified source blocks must remain byte-for-byte intact.
    guide_names = {'00_READ_FIRST.md', 'START_HERE.md', '00_START_HERE.md'}
    for path in stage.rglob('*.md'):
        if path.name in guide_names:
            text = read_utf8(path)
            for name in profiles:
                text = re.sub(r'(AI(?:[-_][A-Za-z0-9_-]+)?)/' + name + '/', r'\1/_EXTRAS/' + name + '/', text)
            if path.parts[-2] == 'CHAT' and path.name == 'START_HERE.md':
                text = text.replace('../00_START_HERE.md', '../../00_START_HERE.md')
            write_utf8(path, text)
    extras = stage / '_EXTRAS'
    extras.mkdir()
    for name in (*profiles, *support):
        (stage / name).rename(extras / name)
    def refreshed(value):
        if isinstance(value, list):
            return [refreshed(v) for v in value]
        if not isinstance(value, dict):
            return value
        value = {k: refreshed(v) for k, v in value.items()}
        if {'file', 'sha256', 'words', 'estimated_tokens'} <= value.keys():
            path = stage / value['file']
            if path.is_file():
                text = read_utf8(path)
                value.update(bytes=path.stat().st_size, sha256=digest_file(path), words=len(text.split()), estimated_tokens=estimate(text))
        return value
    for name in ('CONTEXT_MANIFEST.json', 'BUILD_REPORT.json'):
        path = extras / name
        write_utf8(path, json.dumps(refreshed(json.loads(read_utf8(path))), ensure_ascii=False, indent=2))
    with zipfile.ZipFile(extras / 'AI_UPLOAD.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted((extras / 'UPLOAD').rglob('*.md')):
            if not path.name.startswith('SOURCE--'):
                archive.write(path, path.relative_to(extras / 'UPLOAD').as_posix())
    write_utf8(extras / '00_READ_ME.md', '# Extra formats and rebuild support\n\n'
        'You only need NOTEBOOKLM, VSCODE and PLANNING for ordinary study. These are alternative versions of the same teaching material.\n\n'
        '| Item | Purpose |\n|---|---|\n'
        '| CHAT | Smaller Markdown packs for other chat assistants. |\n'
        '| DOCUMENTS | One readable Markdown extract per original document, useful in Obsidian. |\n'
        '| UPLOAD | Smaller text packs plus copies of original PDF/image/notebook attachments. |\n'
        '| PACKAGES / AI_UPLOAD.zip | Compressed transport copies; extract before uploading. |\n'
        '| search.sqlite | Reusable extraction cache and local keyword search. Keep it when moving between PCs. |\n'
        '| Reports / CHUNKS.jsonl | Coverage, source inventory and integrity records. |\n\n'
        'Do not upload all these alternatives together. Use one text format for a subject and add originals only where useful.\n')
