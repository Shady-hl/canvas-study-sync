import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.request

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ai_export
import ed_sync as ed
import resync_app as app
import study_update


def post(ident, text, **extra):
    return dict(id=ident, course_id=123, user_id=1, content='<document><paragraph>' + text + '</paragraph></document>',
                created_at='2026-10-25T10:00:00Z', comments=[], **extra)


class FixtureClient:
    region = 'eu'
    token = 'local-fixture-token'
    requests = 0

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def get(self, endpoint, **query):
        self.requests += 1
        self.calls.append((endpoint, query))
        result = self.responses[(endpoint, query.get('offset', 0))]
        if isinstance(result, Exception):
            raise result
        return result

    def read(self, url, **options):
        return b'fixture-image'


class ExportTests(unittest.TestCase):
    def fixture(self, root):
        (root / 'Accounting--1').mkdir()
        (root / '.canvas-sync.json').write_text(json.dumps({'courses': {'1': 'Accounting--1'},
                'course_codes': {'1': 'ECON 101 OH1'}}))
        return {'user': {'email': 'not-exported@example.invalid'},
                'courses': [{'course': {'id': 123, 'code': 'ECON 101', 'status': 'active'}}]}

    def run_sync(self, root, client):
        with contextlib.redirect_stdout(io.StringIO()):
            return ed.sync(root, 'local-fixture-token', client=client)

    def test_all_pages_nested_replies_math_code_anonymity_and_attachments(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            user = self.fixture(root)
            answer = post(9, 'Staff answer', type='answer')
            answer['comments'] = [post(10, 'Nested reply', type='comment')]
            thread = post(8, 'Question', title='Exam procedure', answers=[answer], reply_count=2,
                          accepted_id=9, is_anonymous=True)
            thread['content'] = '<document><paragraph>Hello <math>x^2</math></paragraph><snippet language="python">print(&quot;hi&quot;)</snippet><image src="https://static.eu.edusercontent.com/files/a.png"/><file url="https://static.eu.edusercontent.com/files/a.pdf">Notes.pdf</file><script>alert(1)</script></document>'
            second = post(11, 'Other question', title='Other', answers=[], reply_count=0)
            client = FixtureClient({('user', 0): user,
                ('courses/123/threads', 0): {'threads': [{'id': 8}]},
                ('courses/123/threads', 1): {'threads': [{'id': 8}, {'id': 11}]},
                ('courses/123/threads', 3): {'threads': []},
                ('threads/8', 0): {'thread': thread, 'users': [{'id': 1, 'name': 'Tutor', 'course_role': 'tutor', 'email': 'secret@example.invalid'}]},
                ('threads/11', 0): {'thread': second, 'users': []}})
            report = self.run_sync(root, client)
            entry = report['courses'][0]
            self.assertTrue(entry['listing_complete'])
            self.assertEqual(len(entry['threads']), 2)
            self.assertEqual(entry['threads'][0]['replies_exported'], 2)
            path = root / entry['folder'] / 'thread-8.html'
            text = path.read_text()
            self.assertIn('Nested reply', text)
            self.assertIn('Tutor [STAFF]', text)
            self.assertIn('Anonymous', text)
            self.assertIn('$x^2$', text)
            self.assertIn('print(&quot;hi&quot;)', text)
            self.assertNotIn('<script>', text)
            self.assertNotIn('secret@example.invalid', text)
            self.assertNotIn('not-exported@example.invalid', text)
            self.assertEqual(len(list((path.parent / 'assets').iterdir())), 2)
            self.assertIn(('threads/8', {'view': 0}), client.calls)
            sources = ai_export.discover(root, [])
            paths = [s['path'] for s in sources]
            self.assertIn(path.relative_to(root).as_posix(), paths)
            self.assertFalse(any('.raw' in p or 'ED_SYNC_REPORT' in p for p in paths))
            self.assertTrue(all(s['course'] == '1' and s['role'] == 'course' for s in sources))
            chunks = list(ai_export.extract_units(path, 4000, []))
            self.assertIn('Nested reply', str(chunks))

    def test_repeated_page_and_failed_detail_are_partial_without_deleting_old_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            user = self.fixture(root)
            folder = root / 'Accounting--1/Discussions/Ed-eu-123'
            folder.mkdir(parents=True)
            old = folder / 'thread-8.html'
            old.write_text('Earlier saved answer')
            (root / ed.REPORT).write_text(json.dumps({'last_successful_at': '2026-10-24'}))
            client = FixtureClient({('user', 0): user,
                ('courses/123/threads', 0): {'threads': [{'id': 8}]},
                ('courses/123/threads', 1): {'threads': [{'id': 8}]},
                ('threads/8', 0): ed.EdError('Detail unavailable')})
            report = self.run_sync(root, client)
            self.assertEqual(report['status'], 'partial')
            self.assertEqual(report['last_successful_at'], '2026-10-24')
            self.assertFalse(report['courses'][0]['listing_complete'])
            self.assertEqual(old.read_text(), 'Earlier saved answer')

    def test_auth_failure_is_reported_and_token_is_cleared(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.fixture(root)
            client = FixtureClient({('user', 0): ed.EdAuthError('Denied')})
            report = self.run_sync(root, client)
            self.assertEqual(report['status'], 'failed')
            self.assertEqual(client.token, '')
            self.assertNotIn('local-fixture-token', (root / ed.REPORT).read_text())

    def test_reply_returned_both_nested_and_flat_is_exported_and_counted_once(self):
        with tempfile.TemporaryDirectory() as temp:
            child = post(10, 'Nested reply', type='comment')
            answer = post(9, 'Answer', type='answer')
            answer['comments'] = [child]
            thread = post(8, 'Question', title='Duplicate representation', answers=[answer], reply_count=2)
            thread['comments'] = [child]
            record = ed.export_thread(thread, {}, Path(temp), FixtureClient({}), {}, [])
            self.assertEqual(record['replies_exported'], 2)
            self.assertEqual((Path(temp) / 'thread-8.html').read_text().count('Nested reply'), 1)
            self.assertEqual(record['notices'], [])

    def test_ambiguous_courses_require_explicit_mapping(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.fixture(root)
            courses = [{'id': ident, 'code': 'ECON101', 'status': 'active'} for ident in (123, 124)]
            self.assertEqual(ed.course_targets(root, courses), {})
            (root / 'ED_CONFIG.json').write_text(json.dumps({'course_map': {'123': '1'}}))
            # An explicit override wins over a competing automatic match.
            self.assertEqual(set(ed.course_targets(root, courses)), {'123'})

    def test_missing_reply_structure_does_not_overwrite_previous_thread(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            (folder / 'thread-8.html').write_text('Previous complete export')
            with self.assertRaises(ed.EdError):
                ed.export_thread(post(8, 'Incomplete'), {}, folder, FixtureClient({}), {}, [])
            self.assertEqual((folder / 'thread-8.html').read_text(), 'Previous complete export')


class Response:
    def __init__(self, code, data=b'{}', headers=None):
        self.code, self.data, self.headers = code, data, headers or {'Content-Type': 'application/json'}
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def getcode(self): return self.code
    def read(self, n): return self.data[:n]


class TransportTests(unittest.TestCase):
    def client(self):
        return ed.EdClient('local-fixture-token', delay=0.6)

    def test_own_region_get_only_and_no_auth_on_attachments(self):
        client = self.client()
        with patch.object(client.opener, 'open', return_value=Response(200)) as opened, patch('time.sleep'):
            client.get('user')
            request = opened.call_args.args[0]
            self.assertEqual(request.full_url, 'https://eu.edstem.org/api/user')
            self.assertEqual(request.get_method(), 'GET')
            self.assertEqual(request.get_header('Authorization'), 'Bearer local-fixture-token')
            client.read('https://static.eu.edusercontent.com/files/a.png')
            self.assertIsNone(opened.call_args.args[0].get_header('Authorization'))
            with self.assertRaises(ed.EdError): client.get('threads/8/delete')
            with self.assertRaises(ed.EdError): client.read('https://attacker.invalid/a')
            with self.assertRaises(ed.EdError): client.read('https://us.edstem.org/api/user', api=True)

    def test_api_redirect_is_not_followed_with_a_token(self):
        client = self.client()
        with patch.object(client.opener, 'open', return_value=Response(302, headers={'Location': 'https://attacker.invalid'})) as opened, patch('time.sleep'):
            with self.assertRaises(ed.EdAuthError): client.get('user')
            self.assertEqual(opened.call_count, 1)

    def test_long_rate_pause_and_non_json_are_explicit_failures(self):
        client = self.client()
        with patch.object(client.opener, 'open', return_value=Response(429, headers={'Retry-After': '3600'})), patch('time.sleep'):
            with self.assertRaises(ed.EdError): client.get('user')
        with patch.object(client.opener, 'open', return_value=Response(200, b'<html>Sign in</html>')), patch('time.sleep'):
            with self.assertRaises(ed.EdError): client.get('user')

    def test_invalid_credential_values_are_rejected(self):
        for token in ('bad\nheader', 'cookie=a; cookie=b', {}, 'x' * 4097):
            with self.subTest(token_type=type(token).__name__):
                with self.assertRaises(ValueError): ed.normalize_token(token)


class IntegrationTests(unittest.TestCase):
    def test_ed_only_worker_clears_and_redacts_both_credentials(self):
        from test_resync_app import ResyncJobTests
        import sys
        import time
        with tempfile.TemporaryDirectory() as temp:
            root, tool = ResyncJobTests().fixture(temp)
            (tool / 'study_update.py').write_text('''import os,sys
assert '--ed-only' in sys.argv and '--sync' not in sys.argv
assert 'CANVAS_SESSION' not in os.environ
assert os.environ['ED_API_TOKEN']=='local-fixture-token'
print(os.environ['ED_API_TOKEN'])
print('ED_EVENT {"requests":3,"retries":0}')
''')
            job = app.ResyncJob(root)
            with patch.object(app, 'TOOL', tool), patch.object(job, 'python_with_dependencies', return_value=Path(sys.executable)):
                job.start_ed('local-fixture-token')
                result = ResyncJobTests().wait(job)
            self.assertEqual(result['phase'], 'done', result['log'])
            self.assertNotIn('local-fixture-token', json.dumps(result))
            self.assertEqual(job.ed_token, '')
            self.assertEqual(result['metrics']['ed_requests'], 3)
            with patch.dict(os.environ, ED_API_TOKEN='secret'):
                self.assertNotIn('ED_API_TOKEN', app.clean_environment())

    def test_wrapper_runs_ed_before_context_and_does_not_pass_token_to_canvas(self):
        import subprocess
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            calls = []
            def canvas(command, **kwargs):
                self.assertNotIn('ED_API_TOKEN', os.environ)
                self.assertNotIn('--ed', command)
                calls.append('canvas')
                return subprocess.CompletedProcess(command, 0)
            def sync(root, token, region, **kwargs):
                self.assertEqual(token, 'local-fixture-token')
                calls.append('ed')
                return {'status': 'partial'}
            def refresh(*args):
                calls.append('context')
                return {'study_context': {'counts': {}}, 'timetable': {}}
            with patch.dict(os.environ, ED_API_TOKEN='local-fixture-token'), \
                 patch('subprocess.run', side_effect=canvas), patch('ed_sync.sync', side_effect=sync), \
                 patch('study_update.refresh', side_effect=refresh):
                self.assertEqual(study_update.main(['--archive', str(root), '--sync', '--ed']), 2)
            self.assertEqual(calls, ['canvas', 'ed', 'context'])


if __name__ == '__main__':
    unittest.main()
