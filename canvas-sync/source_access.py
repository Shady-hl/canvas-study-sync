"""Actionable local source failures without exposing exception values or URLs."""
import datetime as dt
import errno
import json
from pathlib import Path

STATUS = 'ASSISTANT_CONTEXT/REFRESH_STATUS.json'


class SourceAccessError(OSError):
    def __init__(self, path, error):
        self.source = Path(path)
        self.kind = ('cloud' if isinstance(error, TimeoutError) or error.errno == errno.ETIMEDOUT
                     else 'missing' if isinstance(error, FileNotFoundError)
                     else 'permission' if isinstance(error, PermissionError) else 'unavailable')
        super().__init__('A local source could not be read.')


class SourceChangedError(RuntimeError):
    def __init__(self, path):
        self.source, self.kind = Path(path), 'changing'
        super().__init__('A local source kept changing during refresh.')


def timestamp():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')


def problem(error, root):
    kind = getattr(error, 'kind', 'other')
    messages = {
        'cloud': ('A source file is not available locally.', 'Make this file available offline in OneDrive or your cloud app, wait for it to finish downloading, then retry the study packs.'),
        'missing': ('A source file disappeared during refresh.', 'Restore the file, or finish moving your materials, then retry the study packs.'),
        'permission': ('The app could not read a source file.', 'Allow access to this source folder, or copy the file into your study folder, then retry.'),
        'changing': ('A source file is still changing.', 'Wait for saving or cloud syncing to finish, then retry the study packs.'),
        'unavailable': ('A source file could not be opened.', 'Check that the file opens on this computer, then retry the study packs.'),
        'other': ('Study packs could not be refreshed.', 'Review the progress log and source files, then retry. The previous committed study snapshot was retained.'),
    }
    title, action = messages.get(kind, messages['other'])
    relative = ''
    source = getattr(error, 'source', None)
    if source:
        try:
            relative = Path(source).resolve().relative_to(Path(root).resolve()).as_posix()
            if '://' in relative or relative.startswith('..'):
                relative = ''
        except (OSError, ValueError):
            pass
    return {'kind': kind, 'title': title, 'action': action, 'source': relative}


def record(root, state, started, *, error=None, current=None):
    """A failed status write never masks the refresh result or replaces CURRENT."""
    root = Path(root).resolve()
    value = {'format': 1, 'state': state, 'started_at': started,
             'finished_at': None if state == 'running' else timestamp()}
    if error is not None:
        value['problem'] = problem(error, root)
    if current:
        value.update(source_count=current.get('source_count'),
                     published_at=current.get('published_at'))
    try:
        path = root / STATUS
        if path.parent.is_symlink() or root not in path.resolve().parents:
            return False
        from canvas_sync import atomic_write, json_bytes
        atomic_write(path, json_bytes(value))
        return True
    except OSError:
        return False
