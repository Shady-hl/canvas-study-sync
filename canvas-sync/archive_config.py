"""Local archive selection and Canvas origin validation; no institution defaults."""
import json
import os


def saved_origin(root):
    path = root / '.canvas-sync.json'
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding='utf-8'))
    return data.get('base_url') if isinstance(data, dict) else None


def canvas_origin(root, explicit=None):
    from canvas_sync import SyncError, validate_base
    value = explicit or saved_origin(root) or os.environ.get('CANVAS_BASE_URL')
    if not value:
        raise SyncError('Enter your institution\'s Canvas HTTPS address, or supply --base-url. There is no default institution.')
    base = validate_base(value)
    saved = saved_origin(root)
    if saved and base != validate_base(saved):
        raise SyncError('This archive belongs to a different Canvas site. Choose a separate archive folder.')
    return base
