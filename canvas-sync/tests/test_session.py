import contextlib
from email.message import Message
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from canvas_sync import Archive, Canvas, AuthError, normalize_session
from test_sync import Response, Opener, FakeCanvas


def session_client(*responses):
    c = Canvas('https://canvas.example.edu', 'LOCAL-test-session+value%2F==', auth='session')
    c.wait = lambda _: None
    c.opener = Opener(*responses)
    return c


class SessionTests(unittest.TestCase):
    def test_session_auth_explicit_and_no_bearer(self):
        c = session_client(Response(b'{"ok":true}'))
        self.assertEqual(c.get('/api/v1/test'), {'ok': True})
        req = c.opener.requests[0]
        self.assertEqual(req.headers['Cookie'], 'canvas_session=LOCAL-test-session+value%2F==')
        self.assertNotIn('Authorization', req.headers)
        self.assertEqual(req.get_method(), 'GET')

    def test_cookie_value_is_not_url_decoded(self):
        self.assertEqual(normalize_session('canvas_session=abc+def%2F=='), 'abc+def%2F==')
        for value in ('a\r\nCookie: x', 'abc; log_session_id=other', 'Cookie: abc', ''):
            with self.assertRaises(AuthError):
                normalize_session(value)

    def test_session_never_sent_to_cdn_or_subdomain(self):
        for target in ('https://cdn.example/file', 'https://files.canvas.example.edu/file'):
            c = session_client(Response(status=302, headers={'Location': target}), Response(b'abc'))
            with c.request('/files/1/download') as r:
                self.assertEqual(r.read(), b'abc')
            self.assertIn('Cookie', c.opener.requests[0].headers)
            self.assertNotIn('Cookie', c.opener.requests[1].headers)
            self.assertNotIn('Authorization', c.opener.requests[1].headers)

    def test_session_survives_same_origin_redirect(self):
        c = session_client(Response(status=302, headers={'Location': '/files/1/download?verifier=FAKE'}), Response(b'abc'))
        with c.request('/files/1') as r:
            self.assertEqual(r.read(), b'abc')
        self.assertIn('Cookie', c.opener.requests[1].headers)

    def test_session_rotation_is_kept_in_memory(self):
        h = Message()
        h.add_header('Set-Cookie', 'other=ignored; Path=/')
        h.add_header('Set-Cookie', 'canvas_session=rotated%2Btest==; Secure; HttpOnly; Path=/')
        c = session_client(Response(b'{}', headers=h), Response(b'{}'))
        c.get('/api/v1/one')
        c.get('/api/v1/two')
        self.assertEqual(c.opener.requests[1].headers['Cookie'], 'canvas_session=rotated%2Btest==')

    def test_external_host_cannot_replace_canvas_cookie(self):
        c = session_client(Response(b'abc', headers={'Set-Cookie': 'canvas_session=evil; Path=/'}), Response(b'{}'))
        with c.request('https://cdn.example/file') as r:
            r.read()
        c.get('/api/v1/test')
        self.assertEqual(c.token, 'LOCAL-test-session+value%2F==')

    def test_logout_and_login_redirect_stop_without_following(self):
        c = session_client(Response(b'{}', headers={'Set-Cookie': 'canvas_session=; Max-Age=0; Path=/'}))
        with self.assertRaises(AuthError):
            c.get('/api/v1/test')
        c = session_client(Response(status=302, headers={'Location': 'https://login.example/sso'}))
        with self.assertRaises(AuthError):
            c.get('/api/v1/test')
        self.assertEqual(len(c.opener.requests), 1)

    def test_session_json_prefix_is_parsed_not_executed(self):
        c = session_client(Response(b'while(1); [{"id":1}]'))
        self.assertEqual(list(c.all('/api/v1/test')), [{'id': 1}])
        c = session_client(Response(b'while ( 1 ); {"id":1}'))
        self.assertEqual(c.get('/api/v1/test'), {'id': 1})

    def test_html_signin_detected_as_auth_failure(self):
        c = session_client(Response(b'<!doctype html><html>sign in</html>'))
        with self.assertRaises(AuthError):
            c.get('/api/v1/test')

    def test_unchanged_files_hash_verified_without_redownload(self):
        with tempfile.TemporaryDirectory() as temp:
            api, root = FakeCanvas(), Path(temp)
            a = Archive(api, root)
            a.course({'id': 7, 'name': 'Test'})
            a.finish()
            from canvas_sync import sha
            with patch('canvas_sync.sha', wraps=sha) as digest:
                a = Archive(api, root)
                a.course({'id': 7, 'name': 'Test'})
                self.assertEqual(a.report['unchanged_files'], 1)
                self.assertGreater(digest.call_count, 0)
            from canvas_sync import sha
            with patch('canvas_sync.sha', wraps=sha) as digest:
                a = Archive(api, root, verify_local=True)
                a.course({'id': 7, 'name': 'Test'})
                self.assertGreater(digest.call_count, 0)
            self.assertEqual(len(api.downloads), 1)

    def test_complete_inline_module_items_save_request(self):
        class Inline(FakeCanvas):
            def all(self, path):
                if path.split('?')[0].endswith('/modules'):
                    self.calls.append(path)
                    yield {'id': 8, 'name': 'Module', 'items_count': 1, 'items': [{'type': 'File', 'content_id': 101, 'title': 'Lecture'}]}
                else:
                    yield from super().all(path)
        with tempfile.TemporaryDirectory() as temp:
            api = Inline()
            Archive(api, Path(temp)).course({'id': 7, 'name': 'Test'})
            self.assertFalse(any('/modules/8/items' in p for p in api.calls))
            self.assertEqual(len(api.downloads), 1)

    def test_incomplete_inline_module_items_use_pagination(self):
        class Inline(FakeCanvas):
            def all(self, path):
                if path.split('?')[0].endswith('/modules'):
                    self.calls.append(path)
                    yield {'id': 8, 'name': 'Module', 'items_count': 2, 'items': []}
                else:
                    yield from super().all(path)
        with tempfile.TemporaryDirectory() as temp:
            api = Inline()
            Archive(api, Path(temp)).course({'id': 7, 'name': 'Test'})
            self.assertTrue(any('/modules/8/items' in p for p in api.calls))

    def test_session_secret_does_not_enter_archive_or_reports(self):
        with tempfile.TemporaryDirectory() as temp:
            api = FakeCanvas()
            api.token = 'LOCAL-test-session+value%2F=='
            api.auth = 'session'
            a = Archive(api, Path(temp))
            captured = io.StringIO()
            with contextlib.redirect_stdout(captured):
                a.course({'id': 7, 'name': 'Test'})
                a.finish()
            self.assertNotIn(api.token, captured.getvalue())
            for p in Path(temp).rglob('*'):
                if p.is_file():
                    self.assertNotIn(api.token.encode(), p.read_bytes())

if __name__ == '__main__':
    unittest.main()
