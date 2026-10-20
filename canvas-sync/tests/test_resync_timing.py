import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from canvas_sync import Archive, Canvas, SyncStopped
from test_sync import Response, client
import resync_app as app
from resync_estimates import estimate, duration, load_history, save_history
from resync_history import TimingLog


class RequestTimingTests(unittest.TestCase):
    def test_whole_response_is_measured_without_content_or_private_urls(self):
        c = client(Response(b'{"private":"secret-content"}',headers={'X-Request-Cost':'0.2','X-Rate-Limit-Remaining':'700','Content-Length':'28'}))
        events = []
        c.event_callback = events.append
        self.assertEqual(c.get('/api/v1/courses/123/pages/private-slug?verifier=secret-url')['private'], 'secret-content')
        record = [e['request'] for e in events if 'request' in e][0]
        self.assertEqual(record['bytes_read'], 28)
        self.assertEqual(record['kind'], 'pages')
        self.assertEqual(record['request_cost'], 0.2)
        self.assertEqual(record['rate_remaining'], 700)
        self.assertEqual(record['declared_bytes'], 28)
        self.assertGreaterEqual(record['total_seconds'], record['header_seconds'])
        self.assertEqual(len(record['resource_key']), 24)
        text = json.dumps(events)
        for secret in ('secret-content', 'private-slug', 'secret-url', 'private-test-token', 'canvas.example.edu'):
            self.assertNotIn(secret, text)

    def test_repeated_throttling_stops_all_later_operations(self):
        c = client(*(Response(status=429) for _ in range(6)), Response(b'{}'))
        with tempfile.TemporaryDirectory() as temp, contextlib.redirect_stdout(io.StringIO()):
            a = Archive(c, Path(temp))
            with self.assertRaises(SyncStopped):
                a.attempt('course', lambda: c.get('/api/v1/test'))
            self.assertEqual(c.request_count, 6)
            self.assertEqual(c.rate_limit_events, 6)
            with self.assertRaises(SyncStopped):
                a.attempt('next course', lambda: c.get('/api/v1/next'))
            self.assertEqual(c.request_count, 6)
            a.finish()
            report = json.loads((Path(temp) / 'SYNC_REPORT.json').read_text())
            self.assertEqual(report['http']['stopped_reason'], 'rate_limit')
            self.assertEqual(report['http']['requests'], 6)

    def test_long_server_wait_and_request_budget_are_run_wide_stops(self):
        c = client(Response(status=429, headers={'Retry-After': '7200'}), Response(b'{}'))
        with self.assertRaises(SyncStopped):
            c.get('/api/v1/test')
        self.assertEqual(c.request_count, 1)
        c = client(Response(b'{}'), Response(b'{}'))
        c.max_requests = 1
        c.get('/api/v1/test')
        with self.assertRaises(SyncStopped):
            c.get('/api/v1/next')
        self.assertEqual(c.request_count, 1)

    def test_time_limit_stops_before_sending_a_request(self):
        c = client(Response(b'{}'))
        c.max_seconds = 1
        c.started = time.monotonic() - 2
        with self.assertRaises(SyncStopped):
            c.get('/api/v1/test')
        self.assertEqual(c.request_count, 0)


class EstimateTests(unittest.TestCase):
    def test_history_separates_full_runs_and_scales_for_more_sources(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'ASSISTANT_CONTEXT').mkdir()
            (root / 'ASSISTANT_CONTEXT/CURRENT.json').write_text('{"source_count":200}')
            history = load_history(None)
            history['runs'] = [dict(requests=100, total_seconds=120, canvas_seconds=100,
                                    pacing_wait_seconds=70, backoff_seconds=0, source_count=100,
                                    full=False, rate_limit_events=0)]
            model = estimate(root, history)
            self.assertEqual(model['requests'], 200)
            self.assertEqual(model['local_seconds'], 40)
            self.assertAlmostEqual(duration(model, 2), 440)
            self.assertGreater(estimate(root, history, True)['local_seconds'], model['local_seconds'])
            self.assertEqual(model['basis'], 'recent_runs')

    def test_old_report_bootstraps_first_estimate_without_network_requests(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'SYNC_REPORT.json').write_text(json.dumps({'started_at':'2026-10-19T10:00:00Z',
                'finished_at':'2026-10-19T10:03:00Z','file_results':[{}]*200,'text_exports':[{}]*50,'courses':[{}]*6}))
            model = estimate(root, load_history(None))
            self.assertEqual(model['basis'], 'previous_archive')
            self.assertAlmostEqual(duration(model, 0.6), 210)
            self.assertGreater(duration(model, 2), duration(model, 0.6))

    def test_corrupt_history_recovers_and_only_allowlisted_features_are_loaded(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            (root / 'timings.json').write_text('{bad')
            h=load_history(root)
            self.assertEqual(h['runs'], [])
            h['runs']=[dict(requests=10,total_seconds=40,full=False,cookie='private',url='private')]
            save_history(root,h)
            cleaned=load_history(root)
            self.assertNotIn('private',json.dumps(cleaned))
            self.assertEqual(cleaned['resource_salt'], h['resource_salt'])


class PrivateLogTests(unittest.TestCase):
    def test_request_schema_drops_cookies_headers_urls_and_response_bodies(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            log=TimingLog(root, '01'*32, {'delay_seconds':2, 'full':False})
            log.request({'request_id':1, 'kind':'files', 'status':200, 'bytes_read':42,
                         'resource_key':'a'*24, 'cookie':'TEST_PRIVATE_COOKIE',
                         'url':'https://private.invalid?secret', 'body':'TEST_PRIVATE_BODY',
                         'headers':{'Cookie':'TEST_PRIVATE_COOKIE'}})
            log.source('[1/2] Private Course/Files/Secret filename.pdf')
            log.source('[2/2] Private Course/Files/Second filename.html')
            log.close({'phase':'done'})
            path=next((root/'runs').glob('*.jsonl'))
            text=path.read_text()
            for private in ('TEST_PRIVATE_COOKIE','TEST_PRIVATE_BODY','private.invalid','Private Course','Secret filename'):
                self.assertNotIn(private,text)
            rows=[json.loads(line) for line in text.splitlines()]
            self.assertEqual([r['event'] for r in rows],['run_started','request','local_source','local_source','run_finished'])
            self.assertEqual(rows[1]['bytes_read'],42)
            if os.name != 'nt':
                self.assertEqual(path.stat().st_mode & 0o777,0o600)

    def test_persistent_warning_survives_log_rotation(self):
        with tempfile.TemporaryDirectory() as temp:
            job=app.ResyncJob(Path(temp),base_url='https://canvas.example.edu')
            job.progress_event('CANVAS_EVENT '+json.dumps({'requests':5,'rate_limit_events':1,'backoff_seconds':10}))
            for i in range(500):job.log('local progress '+str(i))
            self.assertEqual(job.snapshot()['metrics']['rate_limit_events'],1)
            self.assertEqual(len(job.lines),400)

    def test_invalid_slider_or_connection_does_not_start_background_work(self):
        with tempfile.TemporaryDirectory() as temp:
            job=app.ResyncJob(Path(temp),base_url='https://canvas.example.edu')
            for delay in (0.1,6,float('nan'),True,'2'):
                with self.subTest(delay=delay),self.assertRaises(ValueError):job.start('test',delay_seconds=delay)
            with self.assertRaises(ValueError):job.start('test',connection_type='private SSID')
            self.assertEqual(job.phase,'ready')

    def test_log_write_failure_cannot_turn_a_successful_sync_into_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            job=app.ResyncJob(root,root,base_url='https://canvas.example.edu')
            def complete(command,credential=False):
                job.progress_event('CANVAS_EVENT '+json.dumps({'requests':10,'elapsed_seconds':10,'stage':'context'}))
                return 0
            with patch.object(job,'python_with_dependencies',return_value=Path(__import__('sys').executable)), \
                 patch.object(job,'command',side_effect=complete),patch('resync_history.os.open',side_effect=OSError('fixture')):
                job.start('PRIVATE_TEST_COOKIE',delay_seconds=2)
                for _ in range(200):
                    if job.phase not in app.ACTIVE:break
                    time.sleep(.01)
            self.assertEqual(job.phase,'done')
            self.assertEqual(job.cookie,'')
            self.assertTrue(job.snapshot()['timing_log_failed'])


if __name__=='__main__':unittest.main()
