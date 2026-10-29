#!/usr/bin/env python3
"""Incremental study refresh and an agent-neutral context handoff. No AI API calls."""
import argparse
import datetime as dt
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

VERSION = 1
SMALL_FILE = 2_000_000
FOLDER = 'ASSISTANT_CONTEXT'


def read_json(path, default):
    return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else default


def save(path, value):
    from canvas_sync import atomic_write
    data = json.dumps(value, ensure_ascii=False, indent=2).encode('utf-8')
    atomic_write(path, data)


def hash_file(path):
    from ai_export import digest_file
    return digest_file(path)


def signature(path):
    item = path.stat()
    return [item.st_size, item.st_mtime_ns, item.st_ctime_ns,
            getattr(item, 'st_ino', 0), getattr(item, 'st_dev', 0)]


def inventory(root, previous=None, full=False):
    """Small sources are always hashed; large unchanged local files reuse hashes."""
    from ai_export import discover
    config = read_json(root / 'AI_CONFIG.json', {})
    previous = previous or {}
    files, cache, hashed, reused = {}, {}, 0, 0
    for item in discover(root, config.get('exclude', [])):
        relative = item['path']
        path = root / relative
        # OneDrive can hydrate a file on its first read, changing metadata even
        # when the contents are unchanged. Retry that file, never accept an
        # unstable hash or weaken the final whole-tree consistency check.
        for attempt in range(3):
            before = signature(path)
            old = previous.get(relative, {})
            if not full and before[0] > SMALL_FILE and old.get('signature') == before:
                digest = old['sha256']
                reused += 1
            else:
                digest = hash_file(path)
                hashed += 1
            if before == signature(path):
                break
        else:
            raise RuntimeError('A source kept changing during refresh. Run again after OneDrive finishes.')
        cache[relative] = {'signature': before, 'sha256': digest}
        files[relative] = {**item, 'sha256': digest, 'bytes': before[0]}
    if not files:
        raise RuntimeError('No course sources found. Previous assistant snapshot was retained.')
    return files, cache, {'hashed': hashed, 'large_hashes_reused': reused}


class AssignmentMetadata(HTMLParser):
    """Read exported assignment headings/metadata, never quiz questions or attempts."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title, self.metadata, self.text = '', {}, []
        self.heading = False
        self.paragraph = False

    def handle_starttag(self, tag, attrs):
        if tag == 'h1':
            self.heading = True
        if tag == 'p':
            self.paragraph, self.text = True, []

    def handle_data(self, value):
        if self.heading:
            self.title += value
        if self.paragraph:
            self.text.append(value)

    def handle_endtag(self, tag):
        if tag == 'h1':
            self.heading = False
        if tag == 'p' and self.paragraph:
            key, sep, value = ''.join(self.text).partition(': ')
            if sep and key in {'due_at', 'unlock_at', 'lock_at', 'points_possible'}:
                self.metadata[key] = value
            self.paragraph = False


def assignments(root, files):
    result = []
    for relative, item in files.items():
        if 'Assignments' not in Path(relative).parts or Path(relative).suffix.lower() != '.html':
            continue
        parser = AssignmentMetadata()
        parser.feed((root / relative).read_text(encoding='utf-8'))
        result.append({'course_id': item['course'], 'title': parser.title,
                       'source': relative, **parser.metadata,
                       'submission_status': 'unknown'})
    return result


def timetable(schedule):
    if not schedule:
        return {'available': False, 'events': []}
    allowed = ('id', 'start', 'end', 'summary', 'location', 'course_codes',
               'course_ids', 'activity', 'status', 'mapping_note')
    return {key: schedule.get(key) for key in
            ('retrieved_at', 'timezone', 'source_type', 'refresh_failed', 'event_date_extent')} | {
        'available': True,
        'events': [{key: event.get(key) for key in allowed} for event in schedule['events']]}


def difference(old, new):
    result = []
    for kind, before, after in (
        ('source', old.get('sources', {}), new['sources']),
        ('class', {x['id']: x for x in old.get('timetable', {}).get('events', [])},
         {x['id']: x for x in new['timetable'].get('events', [])})):
        for key in sorted(before.keys() | after.keys()):
            if before.get(key) == after.get(key):
                continue
            result.append({'kind': kind, 'key': key,
                           'change': 'added' if key not in before else 'missing' if key not in after else 'changed'})
    return result


def previous_sources(folder, old):
    # First-run format kept the inventory inline; accept it for local migration.
    if 'sources' in old:
        return old['sources']
    digest = old.get('source_manifest_sha256')
    if digest is None:
        return {}
    if not isinstance(digest, str) or not re.fullmatch(r'[a-f0-9]{64}', digest):
        raise ValueError('Invalid source manifest identity.')
    path = folder / 'manifests' / (digest + '.json')
    if hash_file(path) != digest:
        raise ValueError('Source manifest is incomplete or still syncing.')
    return read_json(path, {})


def export_identity(root):
    from ai_export import output_directory
    from archive_layout import support_file
    target = output_directory(root)
    marker = target / '.canvas-ai-owned.json'
    cache = support_file(target, 'search.sqlite')
    if not marker.is_file() or not cache.is_file():
        return None
    return {'marker': hash_file(marker), 'cache_signature': signature(cache)}


def pipeline_identity(root):
    package = Path(__file__).resolve().parent
    names = ('ai_export.py', 'context_outputs.py', 'archive_layout.py', 'study_setup.py')
    return {name: hash_file(package / name) for name in names} | {
        'config': read_json(root / 'AI_CONFIG.json', {})}


def refresh(root, full=False):
    """Caller holds the existing archive OS lock. CURRENT.json is the commit point."""
    from ai_export import build, output_directory
    from timeedit_context import prepare_schedule, load_schedule
    root = root.resolve()
    started = time.monotonic()
    folder = root / FOLDER
    folder.mkdir(exist_ok=True)
    state = read_json(folder / '.build-state.json', {})
    old = read_json(folder / 'CURRENT.json', {})
    files, cache, scan = inventory(root, state.get('files'), full)
    pipeline = pipeline_identity(root)
    identity = export_identity(root)
    rebuild = full or not identity or state.get('export_identity') != identity or state.get('pipeline') != pipeline or state.get('export_sources') != files
    counts = None
    if rebuild:
        counts = build(root)
        # Existing builds can safely rename default course folders. Rescan new paths.
        files, cache, scan = inventory(root, cache, full)
        schedule = load_schedule(root)
    else:
        schedule = prepare_schedule(root)
    # Reject a concurrently changed local tree, even on a skipped build.
    for relative, entry in cache.items():
        if signature(root / relative) != entry['signature']:
            raise RuntimeError('Sources changed while refreshing. Previous assistant snapshot was retained.')
    report = read_json(root / 'SYNC_REPORT.json', {})
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    current = {'format': VERSION, 'published_at': now, 'sources': files,
               'timetable': timetable(schedule), 'assignments': assignments(root, files),
               'canvas': {'last_download_run_started_at': report.get('started_at'),
                          'notices': len(report.get('issues', [])),
                          'note': 'Local refresh does not download new Canvas material; submission and absence approval are unknown.'},
               'study_context': {'folder': output_directory(root).name,
                                 'rebuilt': rebuild, 'counts': counts if counts is not None else old.get('study_context', {}).get('counts'),
                                 'last_rebuilt_at': now if rebuild else old.get('study_context', {}).get('last_rebuilt_at'),
                                 'note': 'Use CURRENT timetable for planning. Timetables embedded in unchanged study packs are snapshots.'},
               'scan': scan,
               'verification': 'Small source contents are hashed every run. Unchanged large files use local signatures; --full verifies all bytes.'}
    ed_path = root / 'ED_SYNC_REPORT.json'
    if ed_path.is_file():
        ed = read_json(ed_path, {})
        current['ed'] = {k: ed.get(k) for k in ('started_at', 'finished_at', 'last_successful_at', 'status', 'region')}
        current['ed'].update(report='ED_SYNC_REPORT.json',
            note='Last Ed import attempt, separate from Canvas and local context freshness. Older Ed snapshots remain on disk; inspect the report for current coverage.')
    changes = difference({**old, 'sources': previous_sources(folder, old)}, current) if old else []
    history = old.get('recent_refreshes', [])
    if changes:
        history = (history + [{'at': now, 'changes': changes}])[-20:]
    current.update(baseline_created=not bool(old), changes_since_previous_refresh=changes,
                   recent_refreshes=history, elapsed_seconds=round(time.monotonic() - started, 2))
    # Keep the first file small. The content-addressed inventory is read only when
    # source-level detail is needed, and an atomic CURRENT never points at half a file.
    inventory_bytes = json.dumps(files, ensure_ascii=False, indent=2).encode('utf-8')
    inventory_hash = hashlib.sha256(inventory_bytes).hexdigest()
    from canvas_sync import atomic_write
    manifest = folder / 'manifests' / (inventory_hash + '.json')
    if not manifest.is_file():
        atomic_write(manifest, inventory_bytes)
    current.pop('sources')
    current.update(source_count=len(files), source_manifest_sha256=inventory_hash,
                   source_manifest='manifests/' + inventory_hash + '.json')
    # Save reusable state first, then publish one complete JSON snapshot atomically.
    save(folder / '.build-state.json', {'format': VERSION, 'files': cache,
         'export_sources': files, 'pipeline': pipeline_identity(root),
         'export_identity': export_identity(root)})
    save(folder / 'CURRENT.json', current)
    print(f"Study refresh: {len(files)} sources; {len(current['timetable']['events'])} classes; "
          f"{len(changes)} changes; AI packs {'rebuilt' if rebuild else 'unchanged, reused'}; "
          f"{current['elapsed_seconds']:.2f}s.")
    print('Assistant entry point: ' + str(root / 'ASSISTANT_START_HERE.md'))
    return current


def main(argv=None):
    from study_setup import default_archive
    from canvas_sync import archive_lock
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', '--output', type=Path, default=default_archive(__file__))
    parser.add_argument('--sync', action='store_true', help='Download Canvas first, using the configured in-memory OAuth session.')
    parser.add_argument('--auth', choices=('oauth', 'session', 'token'), default='oauth')
    parser.add_argument('--ed', action='store_true', help='Import Ed discussions using ED_API_TOKEN (or a hidden prompt).')
    parser.add_argument('--ed-only', action='store_true', help='Refresh Ed and study context without downloading Canvas.')
    parser.add_argument('--ed-remember', action='store_true', help='Save a validated Ed token in the OS credential store.')
    parser.add_argument('--ed-use-saved', action='store_true', help='Use the saved token when no token is supplied; never prompt in a background run.')
    parser.add_argument('--ed-delay', type=float, default=0.6)
    parser.add_argument('--ed-region', choices=('eu', 'us', 'au'), default='eu')
    parser.add_argument('--full', action='store_true', help='Verify every local source and rebuild all study output.')
    args, extra = parser.parse_known_args(argv)
    root = args.archive.expanduser().resolve()
    ed_token = os.environ.pop('ED_API_TOKEN', '')
    if not (args.ed or args.ed_only):
        ed_token = ''
    try:
        if not root.is_dir():
            raise ValueError('Archive folder does not exist.')
        sync_result = 0
        if args.sync and not args.ed_only:
            if '--ai' in extra:
                extra.remove('--ai')  # This wrapper controls the conditional AI build.
            command = [sys.executable, '-B', '-X', 'utf8', str(Path(__file__).with_name('canvas_sync.py')),
                       '--output', str(root), '--auth', args.auth, '--fast']
            if not any(x == '--delay' or x == '--delay-range' or x.startswith('--delay=') or x.startswith('--delay-range=') for x in extra):
                command += ['--delay', '0.6']  # Batch pages without more aggressive request pacing.
            sync_result = subprocess.run(command + extra).returncode
            if sync_result not in (0, 2):
                print('Canvas sync did not complete. Previous assistant snapshot was retained.', file=sys.stderr)
                return sync_result
        elif extra and not args.ed_only:
            # Retain advanced EXPORT_ONLY.cmd flags such as --query, --pages,
            # --timeedit-file and --output-folder with their original semantics.
            from ai_export import main as export_main
            return export_main(['--archive', str(root)] + extra)
        ed_report = None
        with archive_lock(root):
            if args.ed or args.ed_only:
                import getpass
                from ed_sync import sync as sync_ed, normalize_token
                from ed_credentials import load, save, CredentialError
                if not ed_token:
                    try:
                        ed_token = load(args.ed_region)
                    except CredentialError as error:
                        print(str(error), file=sys.stderr)
                        return 1
                if not ed_token and args.ed_use_saved:
                    print('No Ed token is saved for this region. Paste one in the local window for the first run.', file=sys.stderr)
                    return 1
                ed_token = normalize_token(ed_token or getpass.getpass('Ed API token (hidden): '))
                def ed_event(**values):
                    print('ED_EVENT ' + json.dumps(values), flush=True)
                ed_report = sync_ed(root, ed_token, args.ed_region, delay=args.ed_delay, event=ed_event)
                if args.ed_remember and ed_report['status'] != 'failed':
                    try:
                        save(args.ed_region, ed_token)
                        print('Ed token saved in this computer’s secure credential store. Portable exports contain no token.')
                    except CredentialError as error:
                        print(str(error), file=sys.stderr)
                        ed_report['status'] = 'partial'
                ed_event(stage='context', requests=ed_report.get('requests', 0))
                ed_token = ''
            current = refresh(root, args.full)
        counts = current['study_context'].get('counts') or {}
        notices = sync_result == 2 or (ed_report and ed_report['status'] != 'complete') or current['timetable'].get('refresh_failed') or any(counts.get(x) for x in ('failed', 'visual_only', 'unsupported'))
        return 2 if notices else 0
    except KeyboardInterrupt:
        print('Stopped; rerun to finish.', file=sys.stderr)
        return 130
    except Exception as error:
        # Never print an exception that could contain a private feed/credential URL.
        print('Study refresh failed (' + type(error).__name__ + '). Previous committed assistant snapshot retained; check local source access and dependencies.', file=sys.stderr)
        detail = failure_detail(error, root)
        if detail:
            print('  Reason: ' + detail, file=sys.stderr)
        return 1
    finally:
        ed_token = ''


def failure_detail(error, root):
    """A short, URL-free explanation so a failed refresh is actionable."""
    url = re.compile(r'[a-z][a-z0-9+.-]*://\S+', re.I)
    if isinstance(error, OSError):
        name = str(error.filename or '')
        if '://' in name:
            name = ''
        elif name:
            try:
                name = Path(name).resolve().relative_to(Path(root).resolve().parent).as_posix()
            except (OSError, ValueError):
                name = Path(name).name
        text = (error.strerror or type(error).__name__) + (': ' + name if name else '')
        if isinstance(error, PermissionError) and sys.platform == 'darwin':
            text += (' - macOS is blocking this app from that folder. Open a Terminal window, drag that folder '
                     'from Finder into the Terminal window, and press Return once to grant access to just that folder.')
        return text
    if type(error).__name__ in {'ExportError', 'SyncError'} or isinstance(error, (ValueError, RuntimeError)):
        return url.sub('<link hidden>', str(error))[:400]
    return ''


if __name__ == '__main__':
    sys.exit(main())
