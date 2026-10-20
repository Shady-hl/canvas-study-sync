import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from canvas_sync import (Archive, Canvas, SyncError, APIError, AuthError, CleanHTML,
                         sha, safe_name, archive_lock, normalize_session)


class Response(io.BytesIO):
    def __init__(self, body=b'', status=200, headers=None):
        super().__init__(body)
        self.status = status
        self.headers = headers or {}
    def getcode(self):
        return self.status


class Opener:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.requests = []
    def open(self, request, timeout):
        self.requests.append(request)
        value = next(self.responses)
        if isinstance(value, Exception):
            raise value
        return value


def client(*responses):
    c = Canvas('https://canvas.example.edu', 'private-test-token')
    c.wait = lambda _: None
    c.opener = Opener(*responses)
    return c


def filemeta(fid=101, stamp='v1'):
    return {'id': fid, 'display_name': 'Lecture.pdf', 'size': 3,
            'updated_at': stamp, 'modified_at': stamp,
            'content-type': 'application/pdf',
            'url': 'https://files.example.edu/lecture.pdf'}


class FakeCanvas:
    base = 'https://canvas.example.edu'
    def __init__(self):
        self.downloads = []
        self.payload = b'old'
        self.locked = False
        self.fail = False
        self.stamp = 'v1'
        self.calls = []
    def get(self, path):
        self.calls.append(path)
        if '/pages/' in path:
            return {'title': 'Page title', 'body': '<p><a href="/courses/7/files/101/download">Notes</a></p><table><tr><td>Cell</td></tr></table>'}
        if '/files/' in path:
            return filemeta(int(path.rsplit('/', 1)[1]), self.stamp)
        return {'id': 7, 'name': 'Course title', 'syllabus_body': '<p>Syllabus</p>'}
    def all(self, path):
        self.calls.append(path)
        if path.split('?')[0].endswith('/modules'):
            yield {'id': 8, 'name': 'Module 1'}  # Inline items deliberately absent.
        elif '/modules/8/items' in path:
            yield {'type': 'Page', 'page_url': 'intro', 'title': 'Intro'}
            yield {'type': 'File', 'content_id': 101, 'title': 'Lecture'}
        elif path.endswith('/pages'):
            yield {'url': 'intro'}
        elif path.endswith('/files'):
            f = filemeta(stamp=self.stamp)
            f['locked_for_user'] = self.locked
            yield f
        elif path.endswith('/assignments'):
            yield {'id': 20, 'name': 'Assignment', 'description': '<p>Do work</p>', 'due_at': '2026-10-01T00:00:00Z'}
        elif 'only_announcements=true' in path:
            yield {'id': 30, 'title': 'Announcement', 'message': '<p>Welcome</p>'}
    def download(self, url, dest, size, mime):
        self.downloads.append(url)
        if self.fail:
            raise SyncError('Interrupted')
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self.payload)
        return sha(dest), len(self.payload)


class SyncTests(unittest.TestCase):
    def test_pagination_follows_next_even_short_pages(self):
        c = client(Response(b'[{"id":1}]', headers={'Link': '<https://canvas.example.edu/api/v1/files?cursor=x>; rel="next"'}), Response(b'[{"id":2}]'))
        self.assertEqual([i['id'] for i in c.all('/api/v1/files')], [1, 2])
        self.assertEqual(c.opener.requests[1].full_url, 'https://canvas.example.edu/api/v1/files?cursor=x')

    def test_reject_foreign_api_pagination(self):
        c = client(Response(b'[]', headers={'Link': '<https://evil.example/api/v1/files>; rel="next"'}))
        with self.assertRaises(SyncError):
            list(c.all('/api/v1/files'))
        self.assertEqual(len(c.opener.requests), 1)

    def test_token_removed_on_cdn_redirect(self):
        c = client(Response(status=302, headers={'Location': 'https://cdn.example/file'}), Response(b'abc'))
        with c.request('/files/123/download') as r:
            self.assertEqual(r.read(), b'abc')
        self.assertIn('Authorization', c.opener.requests[0].headers)
        self.assertNotIn('Authorization', c.opener.requests[1].headers)

    def test_rate_limit_and_retry_after(self):
        c = client(Response(status=429, headers={'Retry-After': '40'}), Response(b'{}'))
        waits = []
        c.wait = waits.append
        self.assertEqual(c.get('/api/v1/test'), {})
        self.assertTrue(any(x >= 40 for x in waits))

    def test_rate_limit_403_is_retryable_but_permission_403_is_not(self):
        c = client(Response(b'Rate Limit Exceeded', 403), Response(b'{}'))
        self.assertEqual(c.get('/api/v1/test'), {})
        c = client(Response(b'Forbidden', 403))
        with self.assertRaises(APIError):
            c.get('/api/v1/test')
        self.assertEqual(len(c.opener.requests), 1)

    def test_expired_auth_vs_item_permission(self):
        with self.assertRaises(AuthError):
            client(Response(status=401, headers={'WWW-Authenticate': 'Bearer'})).get('/api/v1/test')
        with self.assertRaises(AuthError):
            client(Response(status=401)).get('/api/v1/test')
        with self.assertRaises(APIError):
            client(Response(status=401)).request('https://cdn.example/attachment.pdf')

    def test_failed_download_preserves_existing_bytes(self):
        c = client(Response(b'bad', headers={'Content-Type': 'application/pdf'}))
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp) / 'file.pdf'
            p.write_bytes(b'original')
            with self.assertRaises(SyncError):
                c.download('https://cdn.example/file', p, 99, 'application/pdf')
            self.assertEqual(p.read_bytes(), b'original')
            self.assertEqual(list(Path(temp).glob('*.part')), [])

    def test_reject_html_login_as_pdf(self):
        c = client(Response(b'<html>Sign in</html>', headers={'Content-Type': 'text/html'}))
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp) / 'file.pdf'
            with self.assertRaises(SyncError):
                c.download('https://cdn.example/file', p, None, 'application/pdf')
            self.assertFalse(p.exists())

    def test_repeat_scan_then_changed_file_and_local_deletion(self):
        with tempfile.TemporaryDirectory() as temp:
            root, api = Path(temp), FakeCanvas()
            for _ in range(2):
                a = Archive(api, root)
                a.course({'id': 7, 'name': 'Course title'})
                a.finish()
            self.assertEqual(len(api.downloads), 1)
            self.assertEqual(a.report['unchanged_files'], 1)
            self.assertEqual(a.report['text_updated'], 0)
            self.assertTrue(any('/modules/8/items' in p for p in api.calls))
            state = json.loads((root / '.canvas-sync.json').read_text())
            path = root / state['files']['7:101']['path']
            api.payload, api.stamp = b'new', 'v2'
            a = Archive(api, root)
            a.course({'id': 7, 'name': 'Course renamed'})
            self.assertEqual(path.read_bytes(), b'new')
            self.assertTrue(any(p.read_bytes() == b'old' for p in root.glob('*/History/*')))
            self.assertEqual(len(list(root.glob('*/Files'))), 1)
            path.unlink()
            a = Archive(api, root)
            a.course({'id': 7, 'name': 'Course renamed'})
            self.assertEqual(path.read_bytes(), b'new')
            self.assertEqual(len(api.downloads), 3)

    def test_locked_then_newly_released(self):
        with tempfile.TemporaryDirectory() as temp:
            root, api = Path(temp), FakeCanvas()
            api.locked = True
            a = Archive(api, root)
            a.course({'id': 7, 'name': 'Course'})
            self.assertFalse(api.downloads)
            self.assertNotIn('7:101', a.state['files'])
            api.locked = False
            a = Archive(api, root)
            a.course({'id': 7, 'name': 'Course'})
            self.assertEqual(len(api.downloads), 1)

    def test_failed_file_not_marked_complete_then_retry(self):
        with tempfile.TemporaryDirectory() as temp:
            api = FakeCanvas()
            api.fail = True
            a = Archive(api, Path(temp))
            a.course({'id': 7, 'name': 'Course'})
            self.assertNotIn('7:101', a.state['files'])
            api.fail = False
            a = Archive(api, Path(temp))
            a.course({'id': 7, 'name': 'Course'})
            self.assertIn('7:101', a.state['files'])

    def test_existing_downloads_do_not_false_skip_first_run(self):
        with tempfile.TemporaryDirectory() as temp:
            root, api = Path(temp), FakeCanvas()
            (root / 'Lecture.pdf').write_bytes(b'old')
            a = Archive(api, root)
            a.course({'id': 7, 'name': 'Course'})
            self.assertEqual(len(api.downloads), 1)
            self.assertEqual((root / 'Lecture.pdf').read_bytes(), b'old')

    def test_hash_detects_same_size_local_corruption(self):
        with tempfile.TemporaryDirectory() as temp:
            a, api = None, FakeCanvas()
            a = Archive(api, Path(temp))
            a.course({'id': 7, 'name': 'Course'})
            p = Path(temp) / a.state['files']['7:101']['path']
            p.write_bytes(b'BAD')
            a = Archive(api, Path(temp))
            a.course({'id': 7, 'name': 'Course'})
            self.assertEqual(p.read_bytes(), b'old')
            self.assertEqual(len(api.downloads), 2)

    def test_files_discovered_when_files_listing_forbidden(self):
        class Limited(FakeCanvas):
            def all(self, path):
                if path.endswith('/files'):
                    raise APIError(403)
                yield from super().all(path)
        with tempfile.TemporaryDirectory() as temp:
            api = Limited()
            a = Archive(api, Path(temp))
            a.course({'id': 7, 'name': 'Course'})
            self.assertEqual(len(api.downloads), 1)
            self.assertTrue(any('/files/101' in p for p in api.calls))
            self.assertTrue(a.report['issues'])

    def test_html_safe_and_embedded_files_discovered(self):
        p = CleanHTML('https://canvas.example.edu')
        p.feed('<script>secret()</script><img src="/courses/7/files/99/preview" onerror="bad()"><a href="javascript:bad()">Link</a><a data-api-endpoint="/api/v1/files/101">File</a>')
        result = ''.join(p.out)
        self.assertNotIn('secret', result)
        self.assertNotIn('onerror', result)
        self.assertNotIn('javascript', result)
        self.assertEqual(p.refs, {'99', '101'})

    def test_archive_lock_blocks_overlapping_runs(self):
        with tempfile.TemporaryDirectory() as temp:
            with archive_lock(Path(temp)):
                with self.assertRaises(SyncError):
                    with archive_lock(Path(temp)):
                        pass
            with archive_lock(Path(temp)):
                pass

    def test_names_and_site_identity(self):
        self.assertNotIn('/', safe_name('../../unsafe/file.pdf'))
        self.assertLessEqual(len(safe_name('🦉' * 200).encode()), 150)
        with tempfile.TemporaryDirectory() as temp:
            api = FakeCanvas()
            a = Archive(api, Path(temp))
            a.save_state()
            api.base = 'https://another.example.edu'
            with self.assertRaises(SyncError):
                Archive(api, Path(temp))

if __name__ == '__main__':
    unittest.main()
