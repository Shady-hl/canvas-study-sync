"""Local course-file imports and a small, persistent study-library overview."""
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import threading
import time
import urllib.parse

from canvas_sync import archive_lock, atomic_write, json_bytes, safe_name

COURSES = '.study-courses.json'
MAX_UPLOAD = 200 * 1024 * 1024
MAX_JSON = 4 * 1024 * 1024


class LibraryError(ValueError):
    pass


def read_object(path, root):
    try:
        if path.is_symlink() or root not in path.resolve().parents or path.stat().st_size > MAX_JSON:
            return {'_unavailable': True}
        def invalid_constant(value):
            raise ValueError('Invalid JSON constant.')
        value = json.loads(path.read_text(encoding='utf-8'), parse_constant=invalid_constant)
        return value if isinstance(value, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        return {'_unavailable': True}


def local_courses(root):
    """Explicit local IDs avoid collisions with a later Canvas connection."""
    path = root / COURSES
    if not path.exists():
        return {}
    state = read_object(path, root)
    if state.get('format') != 1 or not isinstance(state.get('courses'), dict):
        raise LibraryError('The local course list could not be read. Keep it and restore a valid copy before adding courses.')
    result = {}
    for ident, item in state['courses'].items():
        if not re.fullmatch(r'local-[1-9][0-9]*', ident) or not isinstance(item, dict):
            raise LibraryError('The local course list has an invalid entry.')
        name = item.get('folder')
        if not isinstance(name, str) or not name or '/' in name or '\\' in name or name.startswith('.'):
            raise LibraryError('A local course folder is invalid.')
        folder = root / name
        if folder.is_symlink() or folder.resolve().parent != root or not folder.is_dir():
            raise LibraryError('A local course folder is missing or has moved. Restore it before importing more files.')
        result[name] = ident
    return result


class StudyLibrary:
    def __init__(self, root, prepare=None):
        self.root = Path(root).resolve()
        self.prepare = prepare
        self.lock = threading.Lock()
        self.busy = False
        self.cached_at = 0
        self.cached = None

    def invalidate(self):
        self.cached_at = 0

    def ensure_ready(self):
        if self.prepare:
            self.prepare()
        elif not any((self.root / name).is_file() for name in ('.canvas-sync.json', '.canvas-study-local.json')):
            raise LibraryError('Open a managed study archive before adding files.')

    def courses(self):
        from ai_export import course_mapping
        if not self.root.is_dir():
            return []
        mapping = course_mapping(self.root)
        extra = read_object(self.root / COURSES, self.root).get('courses', {})
        result = []
        for folder, ident in mapping.items():
            path = self.root / folder
            if path.is_symlink() or path.resolve().parent != self.root or not path.is_dir():
                continue
            label = (extra.get(ident) or {}).get('name') or folder.rsplit('--', 1)[0]
            result.append({'id': ident, 'name': str(label), 'folder': folder})
        return sorted(result, key=lambda row: (row['name'].casefold(), row['id']))

    def course(self, ident):
        if not isinstance(ident, str):
            raise LibraryError('Choose a course before adding files.')
        matches = [row for row in self.courses() if row['id'] == ident]
        if len(matches) != 1:
            raise LibraryError('Choose a course with a unique ID before adding files.')
        return matches[0]

    @contextlib.contextmanager
    def editing(self):
        if not self.lock.acquire(blocking=False):
            raise LibraryError('Another file import is already running.')
        try:
            self.busy = True
            self.ensure_ready()
            with archive_lock(self.root):
                yield
        finally:
            self.busy = False
            self.invalidate()
            self.lock.release()

    def create_course(self, label):
        if not isinstance(label, str) or not label.strip() or len(label) > 120 or any(ord(c) < 32 for c in label):
            raise LibraryError('Enter a course name or the course code shown in Ed, up to 120 characters.')
        label = label.strip()
        with self.editing():
            existing = local_courses(self.root)
            state = read_object(self.root / COURSES, self.root) or {'format': 1, 'courses': {}}
            number = 1
            while True:
                ident = 'local-' + str(number)
                folder = safe_name(label) + '--' + ident
                if ident not in existing.values() and not (self.root / folder).exists():
                    break
                number += 1
            target = self.root / folder
            (target / 'Files').mkdir(parents=True)
            state['courses'][ident] = {'name': label, 'folder': folder}
            try:
                atomic_write(self.root / COURSES, json_bytes(state))
            except OSError:
                (target / 'Files').rmdir()
                target.rmdir()
                raise LibraryError('The course list could not be saved. Check the study folder’s permissions.') from None
            return {'id': ident, 'name': label, 'folder': folder}

    def import_file(self, ident, filename, stream, size):
        if not isinstance(size, int) or isinstance(size, bool) or not 0 <= size <= MAX_UPLOAD:
            raise LibraryError('Choose files up to 200 MB each.')
        if not isinstance(filename, str) or not filename or filename.startswith('.') or '/' in filename or '\\' in filename or any(ord(c) < 32 for c in filename):
            raise LibraryError('Choose a regular course file with a visible filename.')
        name = safe_name(filename)
        suffix = Path(filename).suffix
        if suffix and len(suffix) <= 12 and not name.endswith(suffix):
            name = safe_name(Path(filename).stem.encode('utf-8')[:125].decode('utf-8', 'ignore')) + '.' + safe_name(suffix[1:])
        with self.editing():
            row = self.course(ident)
            parent = self.root / row['folder'] / 'Files'
            if parent.is_symlink() or self.root not in parent.resolve().parents:
                raise LibraryError('The course Files folder is not safe to write into.')
            parent.mkdir(exist_ok=True)
            if size + 16 * 1024 * 1024 > shutil.disk_usage(parent).free:
                raise LibraryError('There is not enough free space to copy this file. Free some space, then retry.')
            started, remaining = time.monotonic(), size
            digest = hashlib.sha256()
            descriptor, temporary = tempfile.mkstemp(prefix='.study-import-', dir=parent)
            temporary = Path(temporary)
            try:
                with os.fdopen(descriptor, 'wb') as handle:
                    while remaining:
                        if time.monotonic() - started > 300:
                            raise LibraryError('The file copy took too long. Retry this file.')
                        block = stream.read(min(1024 * 1024, remaining))
                        if not block:
                            raise LibraryError('The file copy was interrupted. Retry this file.')
                        handle.write(block)
                        digest.update(block)
                        remaining -= len(block)
                    handle.flush()
                    os.fsync(handle.fileno())
                target, number = parent / name, 2
                if not target.is_symlink() and target.is_file() and target.stat().st_size == size:
                    try:
                        from ai_export import digest_file
                        if digest_file(target) == digest.hexdigest():
                            return {'name': target.name, 'course_id': ident, 'bytes': size,
                                    'sha256': digest.hexdigest(), 'path': target.relative_to(self.root).as_posix(), 'reused': True}
                    except OSError:
                        pass  # Preserve an unavailable original and save a separate copy.
                # Never replace an original or an annotated local copy.
                while True:
                    try:
                        handle = target.open('xb')
                        break
                    except FileExistsError:
                        target = parent / (Path(name).stem + ' (' + str(number) + ')' + Path(name).suffix)
                        number += 1
                try:
                    with handle, temporary.open('rb') as copied:
                        shutil.copyfileobj(copied, handle)
                        handle.flush()
                        os.fsync(handle.fileno())
                except Exception:
                    target.unlink(missing_ok=True)
                    raise
                return {'name': target.name, 'course_id': ident, 'bytes': size,
                        'sha256': digest.hexdigest(), 'path': target.relative_to(self.root).as_posix()}
            finally:
                temporary.unlink(missing_ok=True)

    def snapshot(self):
        if self.cached is not None and time.monotonic() - self.cached_at < 3:
            return dict(self.cached, importing=self.busy)
        from source_access import STATUS
        current = read_object(self.root / 'ASSISTANT_CONTEXT/CURRENT.json', self.root)
        refresh = read_object(self.root / STATUS, self.root)
        ed = read_object(self.root / 'ED_SYNC_REPORT.json', self.root)
        canvas = read_object(self.root / 'SYNC_REPORT.json', self.root)
        try:
            courses, course_problem = self.courses(), ''
        except (OSError, ValueError):
            courses, course_problem = [], 'The course list could not be read. Check the study folder before adding files.'
        def items(value):
            return value if isinstance(value, list) else []
        entries = items(ed.get('courses'))
        threads = [t for c in entries if isinstance(c, dict) for t in items(c.get('threads')) if isinstance(t, dict)]
        value = {'courses': courses, 'course_problem': course_problem,
                 'study': {'unavailable': bool(current.get('_unavailable') or refresh.get('_unavailable')), 'at': current.get('published_at'), 'sources': current.get('source_count', 0),
                           'attempt': refresh, 'folder': (current.get('study_context') or {}).get('folder', 'AI_READY')},
                 'ed': {'at': ed.get('finished_at'), 'status': 'unavailable' if ed.get('_unavailable') else ed.get('status'), 'threads': len(threads),
                        'replies': sum(max(0, t['replies_exported']) for t in threads if type(t.get('replies_exported')) is int)},
                 'canvas': {'unavailable': bool(canvas.get('_unavailable')), 'at': canvas.get('finished_at') or canvas.get('started_at'),
                            'notices': len(items(canvas.get('issues')))},
                 'available': {name: self._available(path) for name, path in {
                    'archive': '.', 'packs': self._packs_name(), 'ed-report': 'ED_SYNC_REPORT.html',
                    'canvas-report': 'SYNC_REPORT.html', 'source-folder': self._source_folder(refresh)}.items()}}
        self.cached, self.cached_at = value, time.monotonic()
        return dict(value, importing=self.busy)

    def _packs_name(self):
        from ai_export import output_directory
        try:
            return output_directory(self.root).name
        except (OSError, ValueError):
            return 'AI_READY'

    def _source_folder(self, refresh):
        source = (refresh.get('problem') or {}).get('source')
        if not isinstance(source, str) or not source or source.startswith(('/', '\\')) or '..' in Path(source).parts:
            return ''
        return Path(source).parent.as_posix()

    def _available(self, relative):
        if not relative:
            return False
        path = self.root / relative
        try:
            resolved = path.resolve()
            return (resolved == self.root or self.root in resolved.parents) and not path.is_symlink() and path.exists()
        except OSError:
            return False

    def open_target(self, name):
        from source_access import STATUS
        if not isinstance(name, str):
            raise LibraryError('Choose a study result to open.')
        targets = {'archive': '.', 'packs': self._packs_name(), 'ed-report': 'ED_SYNC_REPORT.html',
                   'canvas-report': 'SYNC_REPORT.html',
                   'source-folder': self._source_folder(read_object(self.root / STATUS, self.root))}
        relative = targets.get(name)
        if not self._available(relative):
            raise LibraryError('This result is not available yet. Add files or finish a refresh first.')
        return (self.root / relative).resolve()


def handle_request(handler, route, opener):
    """Called only after the launcher's Host, Origin and client-key checks."""
    job = handler.server.job
    if route not in {'library/course', 'library/upload', 'library/open', 'library/build'}:
        handler.reply(404, {'error': 'Not found.'})
        return
    try:
        if job.snapshot()['phase'] in {'preparing', 'syncing'}:
            handler.reply(409, {'error': 'Wait for the current refresh to finish before changing your library.'})
            return
        if route == 'library/upload':
            if handler.headers.get('Transfer-Encoding') or handler.headers.get_content_type() != 'application/octet-stream':
                raise LibraryError('Choose files using Add files in this window.')
            size = int(handler.headers.get('Content-Length', '-1'))
            name = urllib.parse.unquote(handler.headers.get('X-Study-Name', ''), errors='strict')
            ident = handler.headers.get('X-Study-Course', '')
            previous = handler.connection.gettimeout()
            try:
                handler.connection.settimeout(30)
                result = job.library.import_file(ident, name, handler.rfile, size)
            finally:
                handler.connection.settimeout(previous)
            handler.reply(201, result)
            return
        size = int(handler.headers.get('Content-Length', '0'))
        if not 0 < size <= 16384 or handler.headers.get_content_type() != 'application/json':
            raise LibraryError('Reopen the local app and try again.')
        body = json.loads(handler.rfile.read(size))
        if not isinstance(body, dict):
            raise LibraryError('Check the study-library options.')
        if route == 'library/course':
            handler.reply(201, job.library.create_course(body.get('name')))
        elif route == 'library/build':
            if job.library.busy:
                raise LibraryError('Wait for the file copy to finish, then build your study packs.')
            job.start_local(body.get('full', False))
            handler.reply(202, {'started': True})
        else:
            if body.get('target') == 'archive':
                job.library.ensure_ready()
            path = job.library.open_target(body.get('target'))
            opener(path)
            handler.reply(200, {'opened': True})
    except LibraryError as error:
        handler.reply(400, {'error': str(error)})
    except (OSError, RuntimeError):
        handler.reply(409, {'error': 'The library is busy or a file is unavailable. Check the study folder and try again.'})
    except (ValueError, TypeError, UnicodeError):
        handler.reply(400, {'error': 'Check the course and file options, then try again.'})
