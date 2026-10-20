"""Approximate local resync timings. History contains counters, never credentials."""
import datetime as dt
import json
import math
from pathlib import Path
import statistics
import secrets

MIN_DELAY, MAX_DELAY, DEFAULT_DELAY = 0.6, 5.0, 2.0


def number(value, default=0, maximum=10**8):
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and 0 <= value <= maximum else default


def delay_value(value):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or not MIN_DELAY <= value <= MAX_DELAY:
        raise ValueError('Choose a pace using the finish-time slider.')
    return round(value, 2)


def read_object(path):
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, UnicodeError):
        return {}


def load_history(storage):
    data = read_object(storage / 'timings.json') if storage else {}
    runs = []
    for row in data.get('runs', [])[-20:] if isinstance(data.get('runs'), list) else []:
        if not isinstance(row, dict) or not number(row.get('requests')) or not number(row.get('total_seconds')):
            continue
        runs.append({key: number(row.get(key)) for key in ('requests', 'total_seconds', 'canvas_seconds',
                    'pacing_wait_seconds', 'backoff_seconds', 'source_count', 'at', 'rate_limit_events')} | {'full': row.get('full') is True})
    try:
        pace = delay_value(data.get('delay_seconds', DEFAULT_DELAY))
    except ValueError:
        pace = DEFAULT_DELAY
    salt = data.get('resource_salt')
    if not isinstance(salt, str) or len(salt) != 64 or any(c not in '0123456789abcdef' for c in salt):
        salt = secrets.token_hex(32)
    return {'version': 1, 'delay_seconds': pace, 'resource_salt': salt, 'runs': runs}


def save_history(storage, history):
    if storage:
        from canvas_sync import atomic_write, json_bytes
        atomic_write(storage / 'timings.json', json_bytes(history))


def duration(model, delay):
    return max(30, model['requests'] * max(delay, model['work_per_request']) + model['local_seconds'])


def estimate(root, history, full=False):
    report = read_object(root / 'SYNC_REPORT.json')
    current = read_object(root / 'ASSISTANT_CONTEXT/CURRENT.json')
    sources = number(current.get('source_count'))
    files, texts, courses = (len(report.get(k, [])) if isinstance(report.get(k), list) else 0 for k in ('file_results', 'text_exports', 'courses'))
    runs = [r for r in history['runs'] if not r['rate_limit_events'] and not r['backoff_seconds']]
    matching = [r for r in runs if r['full'] == full]
    rows = (matching or runs)[-5:]
    if rows:
        # A growing local archive is a useful signal, though future Canvas changes
        # cannot be known before contacting Canvas. Use a range, not a deadline.
        scales = [max(1, sources / r['source_count']) if r['source_count'] else 1 for r in rows]
        requests = statistics.median(r['requests'] * s for r, s in zip(rows, scales))
        work = statistics.median(max(0, r['canvas_seconds'] - r['pacing_wait_seconds'] - r['backoff_seconds']) / r['requests'] for r in rows)
        local = statistics.median(max(5, r['total_seconds'] - r['canvas_seconds']) * s for r, s in zip(rows, scales))
        basis, spread = 'recent_runs', 0.25
    else:
        seconds = 0
        try:
            seconds = max(0, (dt.datetime.fromisoformat(report['finished_at'].replace('Z', '+00:00')) - dt.datetime.fromisoformat(report['started_at'].replace('Z', '+00:00'))).total_seconds())
        except (KeyError, ValueError, TypeError):
            pass
        metrics = report.get('http', {})
        metrics = metrics if isinstance(metrics, dict) else {}
        if number(metrics.get('requests')) and not metrics.get('stopped_reason') and report.get('completed', True):
            requests = number(metrics['requests'])
            work = max(0, number(metrics.get('elapsed_seconds'), seconds) - number(metrics.get('pacing_wait_seconds')) - number(metrics.get('backoff_seconds'))) / requests
        elif seconds:
            requests = max(1, min(files + texts + courses * 8 + 2, seconds / MIN_DELAY))
            work = 0.25
        else:
            requests, work = max(120, files + texts + courses * 8), 0.3
        local = max(15, number(current.get('elapsed_seconds'), 30))
        basis, spread = ('previous_archive' if seconds else 'first_run'), 0.45
    if full and not matching:
        local = max(local * 2, sources * 0.15, 45)
        spread = max(spread, 0.45)
    model = {'requests': round(requests), 'work_per_request': round(work, 3), 'local_seconds': round(local),
             'basis': basis, 'samples': len(rows), 'spread': spread, 'full': full,
             'known_files': files, 'known_texts': texts, 'known_courses': courses, 'known_sources': sources}
    model.update(minimum_seconds=round(duration(model, MIN_DELAY)), maximum_seconds=round(duration(model, MAX_DELAY)))
    return model
