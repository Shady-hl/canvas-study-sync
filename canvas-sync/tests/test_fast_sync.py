import contextlib
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from canvas_sync import Archive, Canvas, SyncError, APIError, sha, main
from test_sync import FakeCanvas, Opener, Response, filemeta


class FastSyncTests(unittest.TestCase):
    def seed(self, root, api):
        archive = Archive(api, root)
        relative = archive.save_file('7', Path('Course--7'), filemeta())
        return root / relative

    @unittest.skipIf(os.name == 'nt', 'Windows intentionally keeps full hash verification; covered by test_windows_keeps_hash_checks_in_fast_mode.')
    def test_unchanged_file_skips_hash_and_download_in_fast_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, api = Path(tmp), FakeCanvas()
            self.seed(root, api)
            archive = Archive(api, root, fast=True)
            with patch('canvas_sync.sha', side_effect=AssertionError('Unchanged bytes should not be read')):
                archive.save_file('7', Path('Course--7'), filemeta())
            self.assertEqual(len(api.downloads), 1)
            self.assertEqual(archive.report['local_signature_reused'], 1)
            self.assertEqual(archive.report['local_hash_verified'], 0)

    def test_verify_local_overrides_fast_signature_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, api = Path(tmp), FakeCanvas()
            self.seed(root, api)
            archive = Archive(api, root, fast=True, verify_local=True)
            with patch('canvas_sync.sha', wraps=sha) as digest:
                archive.save_file('7', Path('Course--7'), filemeta())
            self.assertEqual(digest.call_count, 1)
            self.assertEqual(archive.report['local_signature_reused'], 0)
            self.assertEqual(len(api.downloads), 1)

    def test_windows_keeps_hash_checks_in_fast_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, api = Path(tmp), FakeCanvas()
            self.seed(root, api)
            archive, folder = Archive(api, root, fast=True), Path('Course--7')
            with patch('canvas_sync.os.name', 'nt'), patch('canvas_sync.sha', wraps=sha) as digest:
                archive.save_file('7', folder, filemeta())
            self.assertEqual(digest.call_count, 1)
            self.assertEqual(archive.report['local_signature_reused'], 0)

    def test_legacy_manifest_without_signature_is_hash_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, api = Path(tmp), FakeCanvas()
            self.seed(root, api)
            archive = Archive(api, root, fast=True)
            del archive.state['files']['7:101']['local_signature']
            with patch('canvas_sync.sha', wraps=sha) as digest:
                archive.save_file('7', Path('Course--7'), filemeta())
            self.assertEqual(digest.call_count, 1)
            self.assertEqual(archive.report['local_hash_verified'], 1)
            self.assertIn('local_signature', archive.state['files']['7:101'])

    def test_same_size_corruption_with_restored_mtime_is_repaired(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, api = Path(tmp), FakeCanvas()
            path = self.seed(root, api)
            saved = path.stat()
            path.write_bytes(b'BAD')
            os.utime(path, ns=(saved.st_atime_ns, saved.st_mtime_ns))
            archive = Archive(api, root, fast=True)
            archive.save_file('7', Path('Course--7'), filemeta())
            self.assertEqual(path.read_bytes(), b'old')
            self.assertEqual(len(api.downloads), 2)
            self.assertEqual(archive.report['local_signature_reused'], 0)
            self.assertTrue(any(p.read_bytes() == b'BAD' for p in root.glob('*/History/*')))

    def test_changed_remote_metadata_downloads_and_keeps_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, api = Path(tmp), FakeCanvas()
            path = self.seed(root, api)
            api.payload = b'new'
            archive = Archive(api, root, fast=True)
            archive.save_file('7', Path('Course--7'), filemeta(stamp='v2'))
            self.assertEqual(path.read_bytes(), b'new')
            self.assertEqual(len(api.downloads), 2)
            self.assertTrue(any(p.read_bytes() == b'old' for p in root.glob('*/History/*')))

    def test_deleted_local_file_is_downloaded_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, api = Path(tmp), FakeCanvas()
            path = self.seed(root, api)
            path.unlink()
            Archive(api, root, fast=True).save_file('7', Path('Course--7'), filemeta())
            self.assertEqual(path.read_bytes(), b'old')
            self.assertEqual(len(api.downloads), 2)

    def test_force_redownloads_even_with_matching_signature(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, api = Path(tmp), FakeCanvas()
            self.seed(root, api)
            Archive(api, root, fast=True, force=True).save_file('7', Path('Course--7'), filemeta())
            self.assertEqual(len(api.downloads), 2)

    def test_full_batched_page_bodies_save_requests_and_find_attachments(self):
        class Batched(FakeCanvas):
            def all(self, path):
                if path.endswith('/pages?include[]=body'):
                    self.calls.append(path)
                    yield {'url': 'intro', 'title': 'Intro', 'body': '<a href="/courses/7/files/202/download">Extra</a>', 'published': True}
                    yield {'url': 'empty', 'title': 'Empty page', 'body': '', 'published': True}
                else:
                    yield from super().all(path)
        with tempfile.TemporaryDirectory() as tmp:
            api = Batched()
            archive = Archive(api, Path(tmp), fast=True)
            archive.course({'id': 7, 'name': 'Course'})
            self.assertFalse(any('/pages/' in path for path in api.calls))
            self.assertEqual(len(api.downloads), 2)
            self.assertEqual({x['id'] for x in archive.report['text_exports'] if x['kind'] == 'Pages'}, {'intro', 'empty'})

    def test_missing_page_body_still_gets_individual_page(self):
        class Summaries(FakeCanvas):
            def all(self, path):
                if path.endswith('/pages?include[]=body'):
                    yield {'url': 'intro', 'title': 'Intro'}
                else:
                    yield from super().all(path)
        with tempfile.TemporaryDirectory() as tmp:
            api = Summaries()
            archive = Archive(api, Path(tmp), fast=True)
            archive.course({'id': 7, 'name': 'Course'})
            self.assertIn('/api/v1/courses/7/pages/intro', api.calls)
            self.assertTrue(any(x['kind'] == 'Pages' for x in archive.report['text_exports']))

    def test_refused_body_parameter_falls_back_to_plain_listing(self):
        class Refused(FakeCanvas):
            def all(self, path):
                if path.endswith('/pages?include[]=body'):
                    raise APIError(400)
                if path.endswith('/pages'):
                    self.calls.append(path)
                    yield {'url': 'intro'}
                    yield {'url': 'extra'}
                else:
                    yield from super().all(path)
        with tempfile.TemporaryDirectory() as tmp:
            api = Refused()
            archive = Archive(api, Path(tmp), fast=True)
            archive.course({'id': 7, 'name': 'Course'})
            self.assertIn('/api/v1/courses/7/pages', api.calls)
            self.assertIn('/api/v1/courses/7/pages/extra', api.calls)
            self.assertEqual({x['id'] for x in archive.report['text_exports'] if x['kind'] == 'Pages'}, {'intro', 'extra'})

    def test_partial_batch_keeps_received_bodies_and_recovers_remaining_pages(self):
        class Partial(FakeCanvas):
            def all(self, path):
                if path.endswith('/pages?include[]=body'):
                    yield {'url': 'intro', 'title': 'Intro', 'body': '<p>Received body</p>'}
                    raise APIError(500)
                if path.endswith('/pages'):
                    yield {'url': 'intro'}
                    yield {'url': 'extra'}
                else:
                    yield from super().all(path)
        with tempfile.TemporaryDirectory() as tmp:
            api = Partial()
            archive = Archive(api, Path(tmp), fast=True)
            archive.course({'id': 7, 'name': 'Course'})
            self.assertNotIn('/api/v1/courses/7/pages/intro', api.calls)
            self.assertIn('/api/v1/courses/7/pages/extra', api.calls)
            self.assertEqual({x['id'] for x in archive.report['text_exports'] if x['kind'] == 'Pages'}, {'intro', 'extra'})

    def test_batched_locked_page_is_not_exported(self):
        class Locked(FakeCanvas):
            def all(self, path):
                if path.endswith('/pages?include[]=body'):
                    yield {'url': 'intro', 'title': 'Locked', 'body': '<p>Locked text</p>', 'locked_for_user': True}
                else:
                    yield from super().all(path)
        with tempfile.TemporaryDirectory() as tmp:
            archive = Archive(Locked(), Path(tmp), fast=True)
            archive.course({'id': 7, 'name': 'Course'})
            self.assertFalse(any(x['kind'] == 'Pages' for x in archive.report['text_exports']))
            self.assertTrue(archive.report['issues'])

    def test_random_pacing_uses_requested_intervals(self):
        clock = [0.0]
        c = Canvas('https://canvas.example.edu', 'test', delay_range=(0.1, 0.25))
        c.opener = Opener(Response(b'{}'), Response(b'{}'), Response(b'{}'))
        starts = []
        original_open = c.opener.open
        def open_at_clock(request, timeout):
            starts.append(clock[0])
            return original_open(request, timeout)
        c.opener.open = open_at_clock
        def wait(seconds):
            clock[0] += max(0, seconds)
        c.wait = wait
        with patch('canvas_sync.time.monotonic', side_effect=lambda: clock[0]), patch('canvas_sync.random.uniform', side_effect=[0.1, 0.25, 0.15]):
            for _ in range(3):
                c.get('/api/v1/test')
        for actual, expected in zip(starts, [0.0, 0.1, 0.35]):
            self.assertAlmostEqual(actual, expected)
        self.assertEqual(c.request_count, 3)

    def test_retry_after_overrides_fast_pacing(self):
        c = Canvas('https://canvas.example.edu', 'test', delay_range=(0.1, 0.25))
        c.opener = Opener(Response(status=429, headers={'Retry-After': '40'}), Response(b'{}'))
        waits = []
        c.wait = waits.append
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(c.get('/api/v1/test'), {})
        self.assertTrue(any(seconds >= 40 for seconds in waits))

    def test_low_rate_budget_still_slows_fast_pacing(self):
        c = Canvas('https://canvas.example.edu', 'test', delay_range=(0.1, 0.25))
        c.opener = Opener(Response(b'{}', headers={'X-Rate-Limit-Remaining': '10'}), Response(b'{}'))
        clock = [0.0]
        waits = []
        def wait(seconds):
            waits.append(seconds)
            clock[0] += max(0, seconds)
        c.wait = wait
        with patch('canvas_sync.time.monotonic', side_effect=lambda: clock[0]):
            c.get('/api/v1/test')
            c.get('/api/v1/test')
        self.assertGreaterEqual(max(waits), 5)

    def test_invalid_ranges_rejected_and_fixed_minimum_supported(self):
        for bounds in [(0.05, 0.25), (0.25, 0.1), (float('nan'), 0.25), (0.1, float('inf'))]:
            with self.subTest(bounds=bounds), self.assertRaises(SyncError):
                Canvas('https://canvas.example.edu', 'test', delay_range=bounds)
        self.assertEqual(Canvas('https://canvas.example.edu', 'test', delay=0.1).delay, 0.1)

    def test_cli_fast_defaults_and_explicit_fixed_delay(self):
        for extra, expected in [([], (0.1, 0.25)), (['--delay', '0.6'], None)]:
            clients = []
            def make_client(*args, **kwargs):
                c = Canvas(*args, **kwargs)
                c.opener = Opener(Response(b'{}'), Response(b'[{"id":7,"name":"Test course"}]'))
                c.wait = lambda _: None
                clients.append(c)
                return c
            arguments = ['canvas_sync.py', '--base-url', 'https://canvas.example.edu', '--auth', 'token', '--non-interactive', '--fast', '--list-courses', *extra]
            with self.subTest(extra=extra), patch('archive_config.saved_origin', return_value=None), patch('sys.argv', arguments), patch('canvas_sync.get_token', return_value='test'), patch('canvas_sync.Canvas', side_effect=make_client), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(), 0)
            self.assertEqual(clients[0].delay_range, expected)

    def test_cli_invalid_pacing_fails_before_asking_for_a_credential(self):
        arguments = ['canvas_sync.py', '--base-url', 'https://canvas.example.edu', '--delay-range', '0.25', '0.1']
        with patch('sys.argv', arguments), patch('canvas_sync.get_token') as token, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(), 1)
            token.assert_not_called()


if __name__ == '__main__':
    unittest.main()
