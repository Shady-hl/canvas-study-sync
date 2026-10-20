"""Private JSONL observations for future timing analysis; no raw URLs or secrets."""
import datetime as dt
import hashlib
import json
import math
import os
import platform
from pathlib import Path
import re
import secrets
import time


class TimingLog:
    def __init__(self, storage, salt, metadata, root=None):
        self.handle = None
        self.failed = False
        self.started = time.monotonic()
        self.salt = bytes.fromhex(salt)
        self.previous_source = None
        self.root = root
        if storage:
            folder = storage / 'runs'
            try:
                folder.mkdir(mode=0o700, exist_ok=True)
                name = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ-') + secrets.token_hex(4) + '.jsonl'
                fd = os.open(folder / name, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                self.handle = os.fdopen(fd, 'w', encoding='utf-8')
            except OSError:
                self.failed = True
        self.write('run_started', dict(metadata, schema_version=1, app_version=2, system=platform.system(),
                   system_release=platform.release(), architecture=platform.machine(), python=platform.python_version(),
                   measurement='Observed response timings; connection type is an optional user label.'))

    def write(self, event, values):
        if self.handle:
            try:
                self.handle.write(json.dumps(dict(values, event=event, at=time.time(),
                                  offset_seconds=round(time.monotonic() - self.started, 4)), allow_nan=False) + '\n')
                self.handle.flush()
            except (OSError, ValueError):
                self.failed = True

    def request(self, data):
        from resync_estimates import number
        row = {k: number(data.get(k), None) for k in ('request_id', 'attempt', 'redirect', 'started_offset_seconds',
               'header_seconds', 'total_seconds', 'bytes_read', 'pacing_wait_seconds', 'interval_seconds')}
        row['status'] = int(data['status']) if isinstance(data.get('status'), int) and 100 <= data['status'] <= 599 else None
        row['kind'] = data.get('kind') if data.get('kind') in {'modules', 'pages', 'files', 'assignments', 'discussion_topics', 'announcements', 'quizzes', 'profile', 'courses', 'attachment', 'other'} else 'other'
        key = data.get('resource_key', '')
        row['resource_key'] = key if isinstance(key, str) and re.fullmatch(r'[0-9a-f]{24}', key) else None
        row['destination'] = 'canvas' if data.get('destination') == 'canvas' else 'attachment_host'
        row['error'] = data.get('error') if data.get('error') in {'connection', 'response_read'} else None
        for key in ('request_cost', 'rate_remaining', 'declared_bytes'):
            value = data.get(key)
            row[key] = value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and -10**6 <= value <= 10**12 else None
        self.write('request', row)

    def source(self, line):
        match = re.match(r'^\[(\d+)/(\d+)\] (.+)', line)
        if match:
            self.finish_source()
            index, total, path = match.groups()
            suffix = path.rsplit('.', 1)[-1].lower()
            values = {'index': int(index), 'total': int(total),
                'source_kind': suffix if suffix in {'pdf', 'txt', 'md', 'html', 'ipynb', 'csv', 'tsv', 'png', 'jpg', 'svg', 'docx', 'pptx'} else 'other',
                'resource_key': hashlib.blake2s(path.strip().encode(), key=self.salt, digest_size=12).hexdigest()}
            if self.root:
                try:
                    file = (self.root / path.strip()).resolve()
                    if self.root in file.parents:
                        stat = file.stat()
                        values.update(source_bytes=stat.st_size, source_modified_at=stat.st_mtime)
                except (OSError, ValueError):
                    pass
            self.previous_source = (time.monotonic(), values)
        elif self.previous_source and line.startswith('NotebookLM context ready:'):
            self.finish_source()

    def finish_source(self):
        if self.previous_source:
            started, values = self.previous_source
            self.write('local_source', dict(values, observed_seconds=round(time.monotonic() - started, 4),
                       timing_note='Time between progress messages; may include buffered output or following local work.'))
            self.previous_source = None

    def close(self, summary):
        self.finish_source()
        self.write('run_finished', summary)
        if self.handle:
            try:
                self.handle.close()
            except OSError:
                self.failed = True
            self.handle = None
