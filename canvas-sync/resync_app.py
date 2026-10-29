#!/usr/bin/env python3
"""A local, on-demand Canvas resync window. Canvas credentials stay in memory; Ed storage is optional."""
import argparse
from collections import deque
import contextlib
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import webbrowser
from resync_history import TimingLog
from resync_estimates import (load_history, save_history, estimate, duration,
                              delay_value, number)

TOOL = Path(__file__).resolve().parent
ACTIVE = {'preparing', 'syncing'}
DEP_CHECK = "import pypdf; import keyring; from zoneinfo import ZoneInfo; ZoneInfo('Europe/Oslo')"
LOCAL_MARKER = '.canvas-study-local.json'


class SetupError(ValueError):
    """Only controlled, credential-free setup messages may be shown by the server."""


def clean_environment():
    env = dict(os.environ, PYTHONUNBUFFERED='1', PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1')
    for name in ('CANVAS_SESSION', 'CANVAS_TOKEN', 'CANVAS_OAUTH_SESSION', 'ED_API_TOKEN', 'CANVAS_OAUTH_CONFIG'):
        env.pop(name, None)
    return env


def process_options():
    return {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}


def managed_archive(root):
    return any((root / name).is_file() and not (root / name).is_symlink()
               for name in ('.canvas-sync.json', LOCAL_MARKER))


def open_study_folder(root):
    """Open only this launcher's fixed archive, never a client-supplied path."""
    if os.name == 'nt':
        os.startfile(str(root))
    else:
        subprocess.run(['/usr/bin/open' if sys.platform == 'darwin' else 'xdg-open', str(root)],
                       env=clean_environment(), check=True, stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)


def instance_folder(root):
    identity = hashlib.sha256(str(root.resolve()).encode('utf-8')).hexdigest()[:20]
    # Finder, Terminal and different Python builds may have different TMPDIRs.
    # Use one private per-computer location so reopening finds the same run.
    if sys.platform == 'darwin':
        base = Path.home() / 'Library/Caches/CanvasStudySync'
    elif os.name == 'nt':
        base = Path(os.environ.get('LOCALAPPDATA', str(Path.home() / 'AppData/Local'))) / 'CanvasStudySync'
    else:
        base = Path(os.environ.get('XDG_CACHE_HOME', str(Path.home() / '.cache'))) / 'canvas-study-sync'
    folder = base / identity
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    return folder


def history_folder(root):
    identity = hashlib.sha256(str(root.resolve()).encode('utf-8')).hexdigest()[:20]
    if sys.platform == 'darwin':
        base = Path.home() / 'Library/Application Support/CanvasStudySync'
    elif os.name == 'nt':
        base = Path(os.environ.get('LOCALAPPDATA', str(Path.home() / 'AppData/Local'))) / 'CanvasStudySync/History'
    else:
        base = Path(os.environ.get('XDG_STATE_HOME', str(Path.home() / '.local/state'))) / 'canvas-study-sync'
    folder = base / identity
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    return folder


def find_window(root, folder):
    return running_url(folder, root)


@contextlib.contextmanager
def instance_lock(folder):
    with (folder / 'instance.lock').open('a+b') as handle:
        if os.name == 'nt':
            import msvcrt
            handle.seek(0)
            handle.write(b'0')
            handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            if os.name == 'nt':
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class ResyncJob:
    def __init__(self, root, storage=None, base_url=None, auth='session'):
        self.root = root.resolve()
        from archive_config import saved_origin
        self.base_url = base_url or saved_origin(self.root) or ''
        self.auth = auth
        self.storage = storage
        self.history = load_history(storage)
        self.models = {full: estimate(self.root, self.history, full) for full in (False, True)}
        self.delay = self.history['delay_seconds']
        self.full = False
        self.metrics = {}
        self.context_started = None
        self.sync_started = None
        self.timing = None
        self.connection_type = 'unknown'
        self.lock = threading.Lock()
        self.lines = deque(maxlen=400)
        self.phase = 'ready'
        self.message = 'Ready when you are.'
        self.started = None
        self.finished = None
        self.exit_code = None
        self.cookie = ''
        self.oauth_session = None
        self.redactions = []
        self.operation = 'sync'
        self.ed_token = ''
        self.ed_region = 'eu'
        self.ed_remember = False

    def snapshot(self):
        from ai_export import output_directory, ExportError
        try:
            study_output = output_directory(self.root).name
        except (OSError, ValueError, ExportError):
            study_output = 'AI_READY'
        with self.lock:
            model = self.models[self.full]
            remaining = duration(model, self.delay)
            if self.metrics.get('stage') == 'context':
                remaining = max(15, model['local_seconds'] - (time.time() - (self.context_started or time.time())))
            elif self.metrics:
                count = self.metrics.get('requests', 0)
                expected = max(model['requests'], count * 1.15)
                remaining = (expected - count) * max(self.delay, model['work_per_request']) + model['local_seconds']
                remaining += max(0, (self.metrics.get('waiting_until') or 0) - time.time())
            return {'phase': self.phase, 'message': self.message,
                    'archive': str(self.root), 'started': self.started,
                    'base_url': self.base_url, 'auth': self.auth,
                    'operation': self.operation, 'local_ready': managed_archive(self.root),
                    'study_output': study_output,
                    'configured': (self.root / '.canvas-sync.json').is_file(),
                    'finished': self.finished, 'exit_code': self.exit_code,
                    'delay_seconds': self.delay, 'full': self.full, 'metrics': dict(self.metrics),
                    'estimate': self.models[False], 'estimate_full': self.models[True],
                    'timing_folder': str(self.storage / 'runs') if self.storage else None,
                    'timing_log_failed': bool(self.timing and self.timing.failed),
                    'estimated_remaining_seconds': round(remaining),
                    'log': '\n'.join(self.lines)}

    def log(self, line):
        for value in [self.cookie, self.ed_token] + self.redactions:
            if value:
                line = line.replace(value, '[credential hidden]')
        if self.timing:
            self.timing.source(line)
        with self.lock:
            self.lines.append(line.rstrip()[:2000])

    def start(self, cookie, full=False, delay_seconds=None, connection_type='unknown', base_url=None, auth=None):
        with self.lock:
            if self.phase in ACTIVE:
                raise RuntimeError('A resync is already running in this window.')
        from canvas_sync import normalize_session
        from archive_config import canvas_origin
        auth = auth or self.auth
        if auth not in {'token', 'session', 'oauth'}:
            raise SetupError('Choose a configured authentication method.')
        try:
            base = canvas_origin(self.root, base_url or self.base_url)
        except Exception:
            raise SetupError('Enter the correct Canvas HTTPS origin. Existing archives must keep their original site.') from None
        if auth == 'oauth':
            from canvas_oauth import Session, OAuthError
            try:
                session = Session.from_payload(cookie, base)
            except OAuthError:
                raise SetupError('Complete administrator-configured OAuth sign-in first.') from None
        else:
            session = None
            cookie = normalize_session(cookie)
        if not cookie:
            raise ValueError('Enter your canvas_session value.')
        if not isinstance(full, bool):
            raise ValueError('Invalid rebuild option.')
        delay = delay_value(self.delay if delay_seconds is None else delay_seconds)
        if connection_type not in {'unknown', 'wifi', 'ethernet', 'hotspot', 'other'}:
            raise ValueError('Choose a connection type from the list.')
        with self.lock:
            if self.phase in ACTIVE:
                raise RuntimeError('A resync is already running in this window.')
            fresh = not managed_archive(self.root)
            if fresh and self.root.is_dir() and any(self.root.iterdir()):
                raise SetupError('For first setup, select an empty archive folder with --archive. Existing unrelated files were left untouched.')
            self.root.mkdir(parents=True, exist_ok=True)
            if fresh:
                with (self.root / LOCAL_MARKER).open('x', encoding='utf-8') as handle:
                    json.dump({'format': 1, 'purpose': 'local-study'}, handle)
                self.prepare_output()
            self.auth, self.base_url = auth, base
            self.operation = 'sync'
            self.oauth_session = session
            self.redactions = [session.access_token, session.refresh_token, session.config.client_secret] if session else []
            self.phase, self.message = 'preparing', 'Checking the local Python setup…'
            self.started, self.finished = time.time(), None
            self.exit_code, self.cookie = None, cookie
            self.delay, self.full, self.metrics = delay, full, {}
            self.context_started = self.sync_started = None
            self.connection_type = connection_type
            self.models = {flag: estimate(self.root, self.history, flag) for flag in (False, True)}
            self.history['delay_seconds'] = delay
            self.lines.clear()
            model = self.models[full]
            fingerprint = hashlib.sha256()
            for name in ('canvas_sync.py', 'resync_app.py', 'resync_estimates.py', 'resync_history.py'):
                try:
                    fingerprint.update((Path(__file__).parent / name).read_bytes())
                except OSError:
                    pass
            self.timing = TimingLog(self.storage, self.history['resource_salt'],
                {'delay_seconds': delay, 'full': full, 'connection_type': connection_type,
                 'auth_mode': auth,
                 'estimated_requests': model['requests'], 'estimated_total_seconds': round(duration(model, delay)),
                 'estimate_basis': model['basis'], 'estimate_samples': model['samples'],
                 'workload': {key: model[key] for key in ('known_files', 'known_texts', 'known_courses', 'known_sources')},
                 'tool_fingerprint': fingerprint.hexdigest(),
                 'max_requests': 5000, 'max_seconds': 14400}, self.root)
        self.remember()
        threading.Thread(target=self.run, args=(full,), daemon=True).start()

    def prepare_local(self):
        with self.lock:
            if self.phase in ACTIVE:
                raise RuntimeError
            if not managed_archive(self.root):
                if self.root.exists() and (not self.root.is_dir() or any(self.root.iterdir())):
                    raise SetupError('This folder already contains unrelated files. Choose a new empty archive with --archive; nothing was changed.')
                self.root.mkdir(parents=True, exist_ok=True)
                with (self.root / LOCAL_MARKER).open('x', encoding='utf-8') as handle:
                    json.dump({'format': 1, 'purpose': 'local-study'}, handle)
                self.prepare_output()
                (self.root / 'My course--0' / 'Files').mkdir(parents=True)
            return self.root

    def prepare_output(self):
        with (self.root / 'AI_CONFIG.json').open('x', encoding='utf-8') as handle:
            json.dump({'output_folder': 'AI_READY', 'simple_layout': True}, handle)

    def start_local(self, full=False):
        """Use the existing export pipeline without passing a Canvas credential."""
        from ai_export import discover
        with self.lock:
            if self.phase in ACTIVE:
                raise RuntimeError
            if not isinstance(full, bool):
                raise SetupError('Choose whether to rebuild every study pack.')
            if not managed_archive(self.root):
                raise SetupError('Open the study folder first, then add your permitted course files.')
            if not discover(self.root, []):
                raise SetupError('Add course files to My course--0/Files, or an existing course folder, then build your study packs.')
            self.operation = 'local'
            self.phase, self.message = 'preparing', 'Preparing study packs from your local files…'
            self.started, self.finished = time.time(), None
            self.exit_code, self.cookie, self.oauth_session = None, '', None
            self.redactions = []
            self.full, self.metrics = full, {'stage': 'context', 'requests': 0}
            self.context_started = self.sync_started = None
            self.lines.clear()
            self.timing = TimingLog(self.storage, self.history['resource_salt'],
                {'operation': 'local', 'full': full, 'auth_mode': 'none'}, self.root)
        threading.Thread(target=self.run, args=(full,), daemon=True).start()

    def start_ed(self, token='', region='eu', remember=False, forget=False):
        from ed_sync import normalize_token, REGIONS
        if not isinstance(token, str) or region not in REGIONS or not isinstance(remember, bool) or not isinstance(forget, bool):
            raise SetupError('Check the Ed region and storage options.')
        token = normalize_token(token) if token else ''
        with self.lock:
            if self.phase in ACTIVE:
                raise RuntimeError
            if not forget and not managed_archive(self.root):
                raise SetupError('Open the study folder under Use downloaded files first. Create a course folder with its Ed code, such as ECON101--1/Files.')
            self.operation = 'ed-forget' if forget else 'ed'
            self.ed_token, self.ed_region, self.ed_remember = token, region, remember
            self.cookie, self.oauth_session, self.redactions = '', None, []
            self.phase, self.message = 'preparing', 'Preparing Ed Discussion…'
            self.started, self.finished, self.exit_code = time.time(), None, None
            self.full, self.metrics = False, {'stage': 'ed', 'requests': 0}
            self.context_started = self.sync_started = None
            self.lines.clear()
            self.timing = TimingLog(self.storage, self.history['resource_salt'],
                {'operation': self.operation, 'auth_mode': 'ed-api'}, self.root)
        threading.Thread(target=self.run, args=(False,), daemon=True).start()

    def remember(self):
        try:
            save_history(self.storage, self.history)
        except OSError:
            self.log('Timing history could not be saved. The sync can still run.')

    def progress_event(self, line):
        if line.startswith('ED_EVENT '):
            try:
                data = json.loads(line[len('ED_EVENT '):])
                with self.lock:
                    self.metrics.update(stage='context' if data.get('stage') == 'context' else 'ed',
                                        ed_requests=number(data.get('requests'), 0))
            except (ValueError, TypeError, AttributeError):
                pass
            return True
        if not line.startswith('CANVAS_EVENT '):
            return False
        try:
            data = json.loads(line[len('CANVAS_EVENT '):])
            if not isinstance(data, dict):
                return True
            values = {k: number(data.get(k)) for k in ('requests', 'elapsed_seconds', 'delay_seconds',
                      'pacing_wait_seconds', 'backoff_seconds', 'rate_limit_events', 'low_budget_events', 'network_errors', 'server_errors')}
            values['waiting_until'] = number(data.get('waiting_until'), None, 10**11)
            values['stage'] = data.get('stage') if data.get('stage') in {'canvas', 'rate_wait', 'server_wait', 'context', 'stopped'} else 'canvas'
            reason = data.get('stopped_reason')
            values['stopped_reason'] = reason if reason in {'rate_limit', 'server_wait', 'time_limit', 'request_limit'} else None
            if self.timing:
                if isinstance(data.get('request'), dict):
                    self.timing.request(data['request'])
                if values['stage'] in {'rate_wait', 'server_wait', 'context', 'stopped'}:
                    self.timing.write('stage', values)
            with self.lock:
                self.metrics = values
                if values['stage'] == 'context' and not self.context_started:
                    self.context_started = time.time()
        except (ValueError, TypeError):
            pass
        return True

    def command(self, command, credential=False):
        env = clean_environment()
        if credential:
            name = 'CANVAS_OAUTH_SESSION' if self.auth == 'oauth' else 'CANVAS_SESSION' if self.auth == 'session' else 'CANVAS_TOKEN'
            if self.ed_token:
                env['ED_API_TOKEN'] = self.ed_token
            elif self.cookie:
                env[name] = self.cookie
            env['CANVAS_METRICS_SALT'] = self.history['resource_salt']
        with subprocess.Popen(command, cwd=TOOL, env=env, stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, encoding='utf-8', errors='replace',
                              **process_options()) as process:
            env.pop('CANVAS_SESSION', None)
            env.pop('CANVAS_TOKEN', None)
            env.pop('CANVAS_OAUTH_SESSION', None)
            env.pop('ED_API_TOKEN', None)
            for line in process.stdout:
                if not self.progress_event(line):
                    self.log(line)
            return process.wait()

    def python_with_dependencies(self):
        local = TOOL / ('.venv/Scripts/python.exe' if os.name == 'nt' else '.venv-macos/bin/python3')
        candidates = [local, Path(sys.executable)] if local.is_file() else [Path(sys.executable)]
        local_works = False
        for python in candidates:
            try:
                result = subprocess.run([str(python), '-B', '-X', 'utf8', '-c', DEP_CHECK],
                                        env=clean_environment(), capture_output=True,
                                        **process_options())
            except OSError:
                continue
            if python == local:
                local_works = True
            if result.returncode == 0:
                return python
        self.log('First-time setup: preparing a local Python environment.')
        if not local_works:
            environment = local.parent.parent
            code = self.command([sys.executable, '-B', '-m', 'venv', str(environment)])
            if code != 0:
                raise RuntimeError('Python environment setup failed. Check the progress log.')
        code = self.command([str(local), '-B', '-m', 'pip', 'install', '-r', str(TOOL / 'requirements-ai.txt')])
        if code != 0:
            raise RuntimeError('Dependency installation failed. Check your connection and the progress log.')
        check = subprocess.run([str(local), '-B', '-c', DEP_CHECK], env=clean_environment(),
                               capture_output=True, **process_options())
        if check.returncode != 0:
            raise RuntimeError('Python setup is incomplete. Run SETUP_THIS_PC for your system and try again.')
        return local

    def run(self, full):
        final_phase, final_message = 'error', 'Setup or resync failed. Check the progress log.'
        try:
            python = self.python_with_dependencies()
            if self.operation == 'ed-forget':
                code = self.command([str(python), '-B', str(TOOL / 'ed_credentials.py'), '--forget', '--region', self.ed_region])
                self.exit_code = code
                final_phase = 'done' if code == 0 else 'error'
                final_message = 'Saved Ed token removed for this region.' if code == 0 else 'Could not remove the saved Ed token. Check the progress log.'
                return
            ed = self.operation == 'ed'
            local = self.operation == 'local' or ed
            with self.lock:
                self.phase, self.message = 'syncing', 'Importing Ed, then building study packs…' if ed else 'Preparing local study packs…' if local else 'Updating Canvas, timetable and study context…'
            if local:
                self.context_started = time.time()
                self.log('Building from files already on this computer. No Canvas connection or Canvas credential is used.')
                self.log('A previously configured TimeEdit feed may still refresh. Source files are not uploaded.')
            else:
                self.log(f'One request at a time, at least {self.delay:g}s between request starts. Canvas backoff always takes priority.')
            self.log('The sync continues if you close this tab. Reopen Canvas Resync to return.')
            command = [str(python), '-B', '-u', '-X', 'utf8', str(TOOL / 'study_update.py'),
                       '--archive', str(self.root)]
            if ed:
                command += ['--ed-only', '--ed-use-saved', '--ed-region', self.ed_region, '--ed-delay', '2']
                if self.ed_remember:
                    command += ['--ed-remember']
            if not local:
                command += ['--base-url', self.base_url, '--sync', '--auth', self.auth, '--delay', str(self.delay),
                            '--progress-events', '--max-requests', '5000', '--max-seconds', '14400']
            if full:
                command += ['--full'] + ([] if local else ['--verify-local'])
            self.sync_started = time.time()
            code = self.command(command, credential=not local or ed)
            with self.lock:
                self.exit_code = code
                if code == 0:
                    final_phase = 'done'
                    final_message = 'Ed and study packs refreshed.' if ed else 'Study packs ready. Canvas was not contacted.' if local else 'Resync complete. Your study context is up to date.'
                elif code == 2:
                    final_phase, final_message = 'notices', 'Finished with notices. Some sources need review.'
                else:
                    final_phase, final_message = 'error', 'Resync stopped. Check the progress log before retrying.'
                    reason = self.metrics.get('stopped_reason')
                    if reason == 'rate_limit':
                        final_message = 'Canvas kept limiting requests. The entire sync stopped; try again later.'
                    elif reason in {'server_wait', 'time_limit', 'request_limit'}:
                        final_message = 'The sync stopped at a protective limit. Completed downloads are saved.'
                if code in (0, 2) and self.metrics.get('stage') == 'context' and self.metrics.get('requests'):
                    from resync_estimates import read_object
                    current = read_object(self.root / 'ASSISTANT_CONTEXT/CURRENT.json')
                    record = {key: self.metrics.get(key, 0) for key in ('requests', 'pacing_wait_seconds', 'backoff_seconds', 'rate_limit_events')}
                    record.update(at=time.time(), total_seconds=time.time() - self.sync_started,
                                  canvas_seconds=self.metrics['elapsed_seconds'], full=full,
                                  source_count=number(current.get('source_count')))
                    self.history['runs'] = (self.history['runs'] + [record])[-20:]
                    self.models = {flag: estimate(self.root, self.history, flag) for flag in (False, True)}
            self.remember()
        except Exception as error:
            # Do not expose exception values: libraries can include private URLs.
            self.log('The background run could not finish (' + type(error).__name__ + ').')
            with self.lock:
                self.exit_code = 1
        finally:
            with self.lock:
                self.cookie = self.ed_token = ''
                self.oauth_session = None
                self.redactions = []
                finished = time.time()
                summary = dict(self.metrics, phase=final_phase, exit_code=self.exit_code,
                               total_seconds=round(finished - self.started, 3),
                               setup_seconds=round((self.sync_started or finished) - self.started, 3))
            try:
                self.finish_timing(summary, finished)
            except Exception:
                self.log('Some timing details could not be saved. Your sync result is unaffected.')
                if self.timing:
                    self.timing.failed = True
                    self.timing.close(summary)
            finally:
                with self.lock:
                    self.finished, self.phase, self.message = finished, final_phase, final_message

    def finish_timing(self, summary, finished):
        if self.timing:
            from resync_estimates import read_object
            report = read_object(self.root / 'SYNC_REPORT.json')
            try:
                import datetime as dt
                fresh = dt.datetime.fromisoformat(report['started_at'].replace('Z', '+00:00')).timestamp() >= (self.sync_started or finished) - 2
            except (ValueError, TypeError, KeyError):
                fresh = False
            if fresh:
                summary['canvas_results'] = {k: number(report.get(k)) for k in ('downloaded', 'unchanged_files', 'text_updated', 'local_signature_reused', 'local_hash_verified')}
                summary['canvas_results']['notices'] = len(report.get('issues', []))
                summary['canvas_results']['course_count'] = len(report.get('courses', []))
            if self.context_started:
                summary['context_seconds'] = round(finished - self.context_started, 3)
                current = read_object(self.root / 'ASSISTANT_CONTEXT/CURRENT.json')
                summary['source_count'] = number(current.get('source_count'))
                counts = current.get('study_context', {}).get('counts') or {}
                summary['extraction'] = {k: number(counts.get(k)) for k in ('extracted', 'cached', 'visual_only', 'failed', 'unsupported', 'packs')}
            self.timing.close(summary)

class LocalServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, root, storage=None, base_url=None, auth='oauth', oauth_config=None, development_auth=False):
        super().__init__(('127.0.0.1', 0), LocalHandler)
        self.key = secrets.token_urlsafe(32)
        self.origin = 'http://127.0.0.1:' + str(self.server_address[1])
        self.url = self.origin + '/' + self.key + '/'
        self.job = ResyncJob(root, storage, base_url, auth)
        self.development_auth = development_auth
        self.oauth_flow = None
        self.oauth_problem = 'Administrator setup is required. Ask your institution’s Canvas administrator about an approved sign-in integration.'
        if oauth_config:
            from canvas_oauth import load_config, Flow, OAuthError
            try:
                config = load_config(oauth_config, excluded=(TOOL.parent, root))
                if self.job.base_url and self.job.base_url.rstrip('/') != config.canvas_origin:
                    raise OAuthError('The administrator configuration must match this archive’s Canvas origin.')
                self.job.base_url = config.canvas_origin
                self.oauth_flow = Flow(config)
                self.oauth_problem = ''
            except OAuthError as error:
                self.oauth_problem = str(error)
        self.last_request = time.monotonic()


class LocalHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, code, data, content_type='application/json; charset=utf-8'):
        if not isinstance(data, bytes):
            data = json.dumps(data).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('X-Content-Type-Options', 'nosniff')
        nonce = self.server.key
        self.send_header('Content-Security-Policy', "default-src 'none'; script-src 'nonce-" + nonce + "'; style-src 'nonce-" + nonce + "'; connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def allowed(self, write=False):
        server = self.server
        if self.headers.get('Host') != server.origin.removeprefix('http://'):
            return False
        if not self.path.startswith('/' + server.key + '/'):
            return False
        origin = self.headers.get('Origin')
        if origin and origin != server.origin:
            return False
        if write and (origin != server.origin or self.headers.get('X-Canvas-Client') != server.key):
            return False
        server.last_request = time.monotonic()
        return True

    def do_GET(self):
        if not self.allowed():
            self.reply(403, {'error': 'This window is available only from its local launcher.'})
            return
        route = self.path[len('/' + self.server.key + '/'):]
        if route == '':
            html = (TOOL / 'resync_ui.html').read_text(encoding='utf-8')
            self.reply(200, html.replace('__LOCAL_KEY__', self.server.key).encode('utf-8'), 'text/html; charset=utf-8')
        elif route == 'status':
            self.reply(200, dict(self.server.job.snapshot(), oauth_ready=bool(self.server.oauth_flow),
                                oauth_problem=self.server.oauth_problem, development_auth=self.server.development_auth))
        else:
            self.reply(404, {'error': 'Not found.'})

    def do_POST(self):
        if not self.allowed(write=True):
            self.reply(403, {'error': 'Reopen Canvas Resync to continue.'})
            return
        route = self.path.removeprefix('/' + self.server.key + '/')
        if route not in {'start', 'oauth/begin', 'local/open', 'local/start', 'ed/start'}:
            self.reply(404, {'error': 'Not found.'})
            return
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 16384 or self.headers.get_content_type() != 'application/json':
                raise ValueError('Invalid request.')
            body = json.loads(self.rfile.read(size))
            if not isinstance(body, dict):
                raise ValueError('Invalid request.')
            if route == 'ed/start':
                self.server.job.start_ed(body.get('token', ''), body.get('region', 'eu'), body.get('remember', False), body.get('forget', False))
                body.clear()
                self.reply(202, {'started': True})
                return
            if route == 'local/open':
                root = self.server.job.prepare_local()
                try:
                    open_study_folder(root)
                    opened = True
                except (OSError, subprocess.SubprocessError):
                    opened = False
                self.reply(200, {'opened': opened, 'archive': str(root)})
                return
            if route == 'local/start':
                self.server.job.start_local(body.get('full', False))
                self.reply(202, {'started': True})
                return
            if route == 'oauth/begin':
                if self.server.job.snapshot()['phase'] in ACTIVE:
                    raise RuntimeError
                if not self.server.oauth_flow:
                    raise SetupError(self.server.oauth_problem)
                self.reply(200, {'authorization_url': self.server.oauth_flow.begin()})
                return
            if self.server.job.snapshot()['phase'] in ACTIVE:
                raise RuntimeError
            cookie = body.get('session', '')
            if not isinstance(cookie, str):
                raise ValueError('Invalid session value.')
            auth = body.get('auth') or self.server.job.auth
            if auth == 'oauth':
                if not self.server.oauth_flow:
                    raise SetupError(self.server.oauth_problem)
                from canvas_oauth import OAuthError
                try:
                    cookie = self.server.oauth_flow.finish(cookie).payload()
                except OAuthError as error:
                    raise SetupError(str(error)) from None
            elif not self.server.development_auth:
                raise SetupError('Pasted tokens and cookies are disabled in the public launcher. Administrator-configured OAuth is required.')
            self.server.job.start(cookie, body.get('full', False), body.get('delay_seconds'), body.get('connection_type', 'unknown'), body.get('base_url'), auth)
            body.clear()
            cookie = ''
            self.reply(202, {'started': True})
        except RuntimeError:
            self.reply(409, {'error': 'A resync is already running. Wait for it to finish.'})
        except SetupError as error:
            self.reply(400, {'error': str(error)})
        except Exception:
            self.reply(400, {'error': 'Check the session value and finish-time slider, then try again.'})


def running_url(folder, root):
    try:
        data = json.loads((folder / 'window.json').read_text(encoding='utf-8'))
        port, key = data['port'], data['key']
        if not isinstance(port, int) or not 1 <= port <= 65535:
            return None
        if not isinstance(key, str) or len(key) != 43 or not all(c.isalnum() or c in '-_' for c in key):
            return None
        url = 'http://127.0.0.1:' + str(port) + '/' + key + '/'
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url + 'status', timeout=2) as response:
            state = json.load(response)
        return url if state.get('archive') == str(root.resolve()) else None
    except Exception:
        return None


def serve(root, base_url=None, auth='oauth', oauth_config=None, development_auth=False):
    folder = instance_folder(root)
    try:
        with instance_lock(folder), LocalServer(root, history_folder(root), base_url, auth, oauth_config, development_auth) as server:
            metadata = folder / 'window.json'
            # Contains only local window coordinates; never a Canvas credential.
            with metadata.open('w', encoding='utf-8') as handle:
                json.dump({'port': server.server_address[1], 'key': server.key, 'pid': os.getpid()}, handle)
            metadata.chmod(0o600)
            server.timeout = 1
            try:
                while True:
                    server.handle_request()
                    state = server.job.snapshot()
                    done_for = time.time() - (state['finished'] or time.time())
                    if state['phase'] not in ACTIVE and time.monotonic() - server.last_request > 3600 and (state['finished'] is None or done_for > 3600):
                        break
            finally:
                metadata.unlink(missing_ok=True)
    except OSError as error:
        print('The local resync window could not start (' + type(error).__name__ + ').', file=sys.stderr)
        return 1
    return 0


def launch(root, open_browser=True, base_url=None, auth='oauth', oauth_config=None, development_auth=False):
    folder = instance_folder(root)
    url = find_window(root, folder)
    if not url:
        command = [sys.executable, '-B', '-X', 'utf8', str(Path(__file__).resolve()), '--serve', '--archive', str(root)]
        command += ['--auth', auth]
        if base_url:
            command += ['--base-url', base_url]
        if oauth_config:
            command += ['--oauth-config', str(oauth_config)]
        if development_auth:
            command += ['--development-auth']
        options = {'creationflags': subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {'start_new_session': True}
        # Startup diagnostics contain no credential; server request logging is off.
        with (folder / 'startup.log').open('a', encoding='utf-8') as startup:
            (folder / 'startup.log').chmod(0o600)
            subprocess.Popen(command, cwd=TOOL, env=clean_environment(), stdin=subprocess.DEVNULL,
                             stdout=startup, stderr=startup, close_fds=True, **options)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            url = running_url(folder, root)
            if url:
                break
            time.sleep(0.1)
    if not url:
        print('The resync window could not start. Try opening OPEN_RESYNC again.', file=sys.stderr)
        return 1
    if open_browser:
        if sys.platform == 'darwin':
            subprocess.run(['/usr/bin/open', url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            webbrowser.open(url)
    print('Canvas Resync is ready in your browser. The background worker continues if the tab is closed.')
    return 0


def main(argv=None):
    from study_setup import default_archive
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, default=default_archive(__file__))
    parser.add_argument('--base-url', help='Canvas HTTPS origin; can also be entered in the local window.')
    parser.add_argument('--auth', choices=('oauth', 'token', 'session'), default='oauth')
    parser.add_argument('--oauth-config', type=Path, default=os.environ.get('CANVAS_OAUTH_CONFIG'), help='Administrator-provisioned private config outside source/archives; never paste its secret into chat.')
    parser.add_argument('--development-auth', action='store_true', help='Explicitly enable manual credentials for personal development/testing only; not onboarding for other users.')
    parser.add_argument('--launch', action='store_true')
    parser.add_argument('--serve', action='store_true')
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args(argv)
    root = args.archive.expanduser().resolve()
    if args.auth != 'oauth' and not args.development_auth:
        parser.error('Manual credentials require --development-auth and institutional permission; public use requires approved OAuth.')
    return serve(root, args.base_url, args.auth, args.oauth_config, args.development_auth) if args.serve else launch(root, not args.no_browser, args.base_url, args.auth, args.oauth_config, args.development_auth)


if __name__ == '__main__':
    sys.exit(main())
