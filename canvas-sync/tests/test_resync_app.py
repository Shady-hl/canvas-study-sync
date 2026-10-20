import contextlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

import ai_export
import resync_app as app
import study_setup


class ResyncJobTests(unittest.TestCase):
    def fixture(self, temp, exit_code=0):
        root = Path(temp).resolve() / 'Canvas archive (school)'
        tool = root / 'canvas-sync'
        tool.mkdir(parents=True)
        (root / '.canvas-sync.json').write_text('{"base_url":"https://canvas.example.edu"}')
        (tool / 'study_update.py').write_text('''import json, os, sys
from pathlib import Path
root = Path(sys.argv[sys.argv.index('--archive') + 1])
assert '--sync' in sys.argv
assert '--delay' in sys.argv and 0.6 <= float(sys.argv[sys.argv.index('--delay') + 1]) <= 5
assert '--progress-events' in sys.argv and '--max-requests' in sys.argv
assert os.environ.get('CANVAS_SESSION') == 'TEST_SESSION_FOR_LOCAL_FIXTURE'
(root / 'AI_READY').mkdir(exist_ok=True)
(root / 'ASSISTANT_CONTEXT').mkdir(exist_ok=True)
(root / 'ASSISTANT_CONTEXT/CURRENT.json').write_text(json.dumps({'fixture_complete': True, 'full': '--full' in sys.argv}))
print('Scanning a fixture course', flush=True)
print(os.environ['CANVAS_SESSION'], flush=True)
print('CANVAS_EVENT ' + json.dumps({'requests':3, 'elapsed_seconds':0.01, 'stage':'context',
      'request':{'request_id':3,'kind':'files','status':200,'bytes_read':42,'resource_key':'a'*24}}), flush=True)
sys.exit(''' + str(exit_code) + ''')
''')
        return root, tool

    def wait(self, job):
        for _ in range(200):
            if job.snapshot()['phase'] not in app.ACTIVE:
                return job.snapshot()
            time.sleep(0.02)
        self.fail('Background fixture did not finish')

    def test_full_background_run_commits_fixture_and_clears_cookie(self):
        with tempfile.TemporaryDirectory() as temp:
            root, tool = self.fixture(temp)
            job = app.ResyncJob(root)
            with patch.object(app, 'TOOL', tool), patch.object(job, 'python_with_dependencies', return_value=Path(sys.executable)):
                job.start('canvas_session=TEST_SESSION_FOR_LOCAL_FIXTURE', True)
                result = self.wait(job)
            self.assertEqual(result['phase'], 'done', result['log'])
            self.assertEqual(json.loads((root / 'ASSISTANT_CONTEXT/CURRENT.json').read_text()),
                             {'fixture_complete': True, 'full': True})
            self.assertNotIn('TEST_SESSION_FOR_LOCAL_FIXTURE', json.dumps(result))
            self.assertIn('[credential hidden]', result['log'])
            self.assertEqual(job.cookie, '')

    def test_notices_and_failures_are_distinct_and_credentials_are_cleared(self):
        for code, expected in ((2, 'notices'), (1, 'error')):
            with self.subTest(code=code), tempfile.TemporaryDirectory() as temp:
                root, tool = self.fixture(temp, code)
                job = app.ResyncJob(root)
                with patch.object(app, 'TOOL', tool), patch.object(job, 'python_with_dependencies', return_value=Path(sys.executable)):
                    job.start('TEST_SESSION_FOR_LOCAL_FIXTURE')
                    result = self.wait(job)
                self.assertEqual(result['phase'], expected)
                self.assertEqual(job.cookie, '')

    def test_duplicate_run_is_rejected_and_bad_cookie_never_starts(self):
        with tempfile.TemporaryDirectory() as temp:
            job = app.ResyncJob(Path(temp))
            with self.assertRaises(Exception):
                job.start('value; other_cookie=value')
            self.assertEqual(job.snapshot()['phase'], 'ready')
            job.phase = 'syncing'
            with self.assertRaises(RuntimeError):
                job.start('test_value')

    def test_successful_run_keeps_private_history_and_selected_pace(self):
        with tempfile.TemporaryDirectory() as temp:
            root, tool = self.fixture(temp)
            storage = Path(temp) / 'local timing cache'
            storage.mkdir()
            job = app.ResyncJob(root, storage)
            with patch.object(app, 'TOOL', tool), patch.object(job, 'python_with_dependencies', return_value=Path(sys.executable)):
                job.start('TEST_SESSION_FOR_LOCAL_FIXTURE', delay_seconds=3.1, connection_type='wifi')
                result = self.wait(job)
            self.assertEqual(result['phase'], 'done', result['log'])
            history = json.loads((storage / 'timings.json').read_text())
            self.assertEqual(history['delay_seconds'], 3.1)
            self.assertEqual(history['runs'][0]['requests'], 3)
            files = list((storage / 'runs').glob('*.jsonl'))
            self.assertEqual(len(files), 1)
            text = files[0].read_text()
            self.assertNotIn('TEST_SESSION_FOR_LOCAL_FIXTURE', text)
            records = [json.loads(line) for line in text.splitlines()]
            self.assertEqual(records[0]['connection_type'], 'wifi')
            self.assertEqual(records[-1]['event'], 'run_finished')
            self.assertEqual(records[-1]['phase'], 'done')
            restored = app.ResyncJob(root, storage)
            self.assertEqual(restored.delay, 3.1)
            self.assertEqual(restored.models[False]['basis'], 'recent_runs')

    def test_dependency_setup_never_receives_the_canvas_credential(self):
        with tempfile.TemporaryDirectory() as temp:
            root, tool = self.fixture(temp)
            job = app.ResyncJob(root)
            job.cookie = 'TEST_SESSION_FOR_LOCAL_FIXTURE'
            calls = []
            def setup(command, credential=False):
                calls.append((command, credential))
                return 0
            bad = subprocess.CompletedProcess([], 1)
            good = subprocess.CompletedProcess([], 0)
            with patch.object(app, 'TOOL', tool), patch.object(job, 'command', side_effect=setup), \
                 patch.object(app.subprocess, 'run', side_effect=[bad, good]):
                python = job.python_with_dependencies()
            self.assertEqual(python.name, 'python.exe' if os.name == 'nt' else 'python3')
            self.assertEqual(len(calls), 2)
            self.assertTrue(all(not credential for _, credential in calls))
            self.assertIn('venv', calls[0][0])
            self.assertIn('pip', calls[1][0])
            with patch.dict(os.environ, CANVAS_SESSION='private', CANVAS_TOKEN='private'):
                self.assertNotIn('CANVAS_SESSION', app.clean_environment())
                self.assertNotIn('CANVAS_TOKEN', app.clean_environment())


class LocalWindowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.server = app.LocalServer(self.root, auth='session', development_auth=True)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.temp.cleanup()

    def request(self, path, data=None, origin=None, headers=None):
        extra = {'Origin': origin or self.server.origin, **(headers or {})}
        if data is not None:
            extra.update({'Content-Type': 'application/json', 'X-Canvas-Client': self.server.key})
        request = urllib.request.Request(self.server.url + path,
                                         data=json.dumps(data).encode() if data is not None else None,
                                         headers=extra)
        return self.opener.open(request, timeout=5)

    def test_window_serves_privately_with_no_external_content(self):
        with self.request('') as response:
            body = response.read().decode()
            self.assertIn('Resync everything', body)
            self.assertNotIn('__LOCAL_KEY__', body)
            self.assertIn("frame-ancestors 'none'", response.headers['Content-Security-Policy'])
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
        with self.request('status') as response:
            status = json.load(response)
        self.assertEqual(status['phase'], 'ready')
        self.assertNotIn('cookie', status)
        self.assertNotIn('session', status)

    def test_foreign_origins_hosts_and_bad_requests_cannot_start_a_run(self):
        for origin, headers, data, expected in (
            ('https://outside.invalid', {}, {'session': 'test'}, 403),
            (self.server.origin, {'Host': 'outside.invalid'}, {'session': 'test'}, 403),
            (self.server.origin, {}, {'session': 'bad; other=value'}, 400),
            (self.server.origin, {}, {'session': 'x' * 17000}, 400),
            (self.server.origin, {}, {'session': 'test', 'delay_seconds': 0.1}, 400),
            (self.server.origin, {}, {'session': 'test', 'connection_type': 'private SSID'}, 400),
        ):
            with self.subTest(origin=origin, expected=expected):
                with self.assertRaises(urllib.error.HTTPError) as error:
                    self.request('start', data, origin, headers)
                self.assertEqual(error.exception.code, expected)
        self.assertEqual(self.server.job.snapshot()['phase'], 'ready')

    def test_valid_start_is_acknowledged_without_echoing_the_credential(self):
        with patch.object(self.server.job, 'start') as start:
            with self.request('start', {'session': 'test-secret', 'full': True}) as response:
                self.assertEqual(response.status, 202)
                self.assertNotIn('test-secret', response.read().decode())
            start.assert_called_once_with('test-secret', True, None, 'unknown', None, 'session')

    def test_public_window_rejects_manual_credentials_even_if_client_changes_auth(self):
        self.server.development_auth = False
        for auth in ('session', 'token'):
            with self.subTest(auth=auth), patch.object(self.server.job, 'start') as start:
                with self.assertRaises(urllib.error.HTTPError) as error:
                    self.request('start', {'session': 'SYNTHETIC_PRIVATE', 'auth': auth})
                self.assertEqual(error.exception.code, 400)
                start.assert_not_called()

    def test_unconfigured_oauth_window_explains_setup_and_never_requests_canvas(self):
        with self.request('status') as response:
            state = json.load(response)
        self.assertFalse(state['oauth_ready'])
        self.assertIn('Administrator', state['oauth_problem'])
        with patch.object(self.server.job, 'start') as start:
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.request('oauth/begin', {})
            self.assertEqual(error.exception.code, 400)
            start.assert_not_called()

    def test_oauth_response_becomes_a_memory_only_worker_payload(self):
        import canvas_oauth as oauth
        from test_oauth import config_data, token_data
        from urllib.parse import parse_qs, urlsplit, urlencode
        self.server.development_auth = False
        self.server.oauth_flow = oauth.Flow(oauth.Config.from_data(config_data()))
        with self.request('oauth/begin', {}) as response:
            url = json.load(response)['authorization_url']
        state = parse_qs(urlsplit(url).query)['state'][0]
        callback = 'https://canvas.example.edu/login/oauth2/auth?' + urlencode(dict(code='SYNTHETIC_CODE', state=state))
        with patch.object(oauth, 'token_request', return_value=token_data()), patch.object(self.server.job, 'start') as start:
            with self.request('start', {'auth': 'oauth', 'session': callback}) as response:
                self.assertEqual(response.status, 202)
                self.assertNotIn('SYNTHETIC', response.read().decode())
            payload = json.loads(start.call_args.args[0])
            self.assertEqual(payload['access_token'], 'SYNTHETIC_ACCESS')
            self.assertEqual(start.call_args.args[-1], 'oauth')
        with self.request('status') as response:
            self.assertNotIn('SYNTHETIC', response.read().decode())


class LauncherTests(unittest.TestCase):
    def test_mac_window_registry_does_not_depend_on_terminal_tmpdir(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve() / 'Archive'
            with patch.object(app.sys, 'platform', 'darwin'), patch.object(app.Path, 'home', return_value=Path(temp)):
                with patch.dict(os.environ, TMPDIR='/a-terminal-temp'):
                    first = app.instance_folder(root)
                with patch.dict(os.environ, TMPDIR='/a-finder-temp'):
                    second = app.instance_folder(root)
            self.assertEqual(first, second)
            self.assertEqual(first.parent, Path(temp) / 'Library' / 'Caches' / 'CanvasStudySync')

    def test_app_and_window_files_are_excluded_from_teaching_sources(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'Course--7').mkdir()
            (root / 'Course--7/lesson.txt').write_text('Lesson')
            study_setup.write_shortcuts(root)
            self.assertEqual([item['path'] for item in ai_export.discover(root, [])], ['Course--7/lesson.txt'])
            self.assertTrue((root / 'Canvas Resync.app/Contents/MacOS/CanvasResync').is_file())

    @unittest.skipUnless(Path('/bin/zsh').is_file(), 'Finder app uses zsh')
    def test_finder_app_remains_relocatable(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve() / 'Archive with (spaces)'
            root.mkdir()
            study_setup.write_shortcuts(root)
            executable = root / 'Canvas Resync.app/Contents/MacOS/CanvasResync'
            opener = root / 'fixture-opener'
            opener.write_text("#!/bin/zsh\nprintf '%s\\n' \"$@\"\n")
            opener.chmod(0o755)
            executable.write_text(executable.read_text().replace('/usr/bin/open -g -a Terminal', '"' + str(opener) + '" -g -a Terminal'))
            result = subprocess.run(['/bin/zsh', str(executable)],
                                    cwd=root.parent, capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.splitlines(), ['-g', '-a', 'Terminal', str(root / 'OPEN_RESYNC.command')])

    def test_detached_launcher_reuses_an_existing_window(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch.object(app, 'instance_folder', return_value=root), \
                 patch.object(app, 'running_url', return_value='http://127.0.0.1:12345/local/'), \
                 patch.object(app.subprocess, 'Popen') as spawn, \
                 patch.object(app.subprocess, 'run') as native_open, \
                 patch.object(app.webbrowser, 'open') as browser, contextlib.redirect_stdout(__import__('io').StringIO()):
                self.assertEqual(app.launch(root), 0)
                spawn.assert_not_called()
                if sys.platform == 'darwin':
                    native_open.assert_called_once()
                else:
                    browser.assert_called_once()


if __name__ == '__main__':
    unittest.main()
