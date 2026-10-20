import contextlib
import datetime as dt
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import ai_export as ai
import timeedit_context as te


CSV = '''\ufeff
TimeEdit,Handelshøyskolen BI
"Student",02.10.2026 - 02.11.2026,
Startdato,Starttidspunkt,Sluttdato,Sluttidspunkt,Kurs og klasse,Aktivitet,Foreleser,,Rom,Streaming URL,Kommentar
05.10.2026,10:00,05.10.2026,11:45,"ECO 1001 OH1,Economics I,Forelesning",Forelesning,Teacher,,"D3-020,D3-030",,Prepare
27.10.2026,14:00,27.10.2026,15:45,"CSC 1003 OH1,CSC 1003 OH2,Programming",Veiledning,Assistant,,B2-040,,TA session
'''.encode('utf-8')

ICS = b'''BEGIN:VCALENDAR\r
VERSION:2.0\r
X-WR-CALDESC:Date limit 02.10.2026 - 02.11.2026\r
BEGIN:VEVENT\r
UID:first\r
DTSTART:20261005T080000Z\r
DTEND:20261005T094500Z\r
SUMMARY:ECO 1001 OH1 Economics\\,\r
  Forelesning\r
LOCATION:D3-020\\nD3-030\r
DESCRIPTION:Bring notes\\; do not forget\r
END:VEVENT\r
BEGIN:VEVENT\r
UID:second\r
DTSTART:20261027T130000Z\r
DTEND:20261027T144500Z\r
SUMMARY:CSC 1003 OH1 Programming\r
STATUS:CANCELLED\r
END:VEVENT\r
END:VCALENDAR\r
'''


class TimeEditTests(unittest.TestCase):
    def root(self, root):
        for name in ('ECO 1001--1001', 'CSC 1003--1003'):
            folder = root / name / 'Pages'
            folder.mkdir(parents=True)
            (folder / 'lesson.txt').write_text('TEACHING_MATERIAL', encoding='utf-8')

    def test_csv_preamble_quotes_timezone_and_course_codes(self):
        events, coverage, _ = te.parse(CSV, 'Europe/Oslo')
        self.assertEqual(len(events), 2)
        self.assertEqual(coverage, ['2026-10-02', '2026-11-02'])
        self.assertEqual(events[0]['location'], 'D3-020,D3-030')
        self.assertEqual(events[0]['start'], '2026-10-05T10:00:00+02:00')
        self.assertEqual(events[1]['start'], '2026-10-27T14:00:00+01:00')
        self.assertEqual(events[1]['course_codes'], ['CSC 1003'])

    def test_ics_unfolds_and_converts_utc_across_dst(self):
        events, coverage, _ = te.parse(ICS, 'Europe/Oslo')
        self.assertIn('Economics, Forelesning', events[0]['summary'])
        self.assertEqual(events[0]['location'], 'D3-020\nD3-030')
        self.assertEqual(events[0]['start'], '2026-10-05T10:00:00+02:00')
        self.assertEqual(events[1]['start'], '2026-10-27T14:00:00+01:00')
        self.assertEqual(events[1]['status'], 'cancelled')
        self.assertEqual(coverage[1], '2026-11-02')

    def test_unsupported_recurrence_and_invalid_rows_are_not_silently_dropped(self):
        recurring = ICS.replace(b'UID:first', b'UID:first\r\nRRULE:FREQ=WEEKLY;COUNT=10')
        with self.assertRaisesRegex(te.ScheduleError, 'Recurring'):
            te.parse(recurring, 'Europe/Oslo')
        with self.assertRaises(te.ScheduleError):
            te.parse(CSV + b'bad,row\n', 'Europe/Oslo')

    def test_invalid_reimport_preserves_last_local_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'input.csv'
            source.write_bytes(CSV)
            te.configure_schedule(root, source)
            old = (root / 'Schedule/timeedit.csv').read_bytes()
            source.write_bytes(b'<html>sign in</html>')
            with self.assertRaises(te.ScheduleError):
                te.configure_schedule(root, source)
            self.assertEqual((root / 'Schedule/timeedit.csv').read_bytes(), old)

    def test_failed_live_refresh_keeps_previous_events_and_labels_snapshot_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.root(root)
            source = root / '.input.csv'
            source.write_bytes(CSV)
            te.configure_schedule(root, source, 'https://cloud.timeedit.net/bi/private.ics')
            with patch('timeedit_context.fetch', return_value=ICS), contextlib.redirect_stdout(io.StringIO()):
                first = te.prepare_schedule(root)
            with patch('timeedit_context.fetch', side_effect=te.ScheduleError('Network unavailable')), contextlib.redirect_stdout(io.StringIO()):
                second = te.prepare_schedule(root)
            self.assertEqual(first['events'], second['events'])
            self.assertTrue(second['refresh_failed'])
            text = te.markdown(second)
            self.assertIn('Latest refresh failed', text)
            self.assertNotIn('private.ics', text)

    def test_course_mapping_and_schedule_present_in_both_context_profiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.root(root)
            source = root / '.input.csv'
            source.write_bytes(CSV)
            te.configure_schedule(root, source)
            with contextlib.redirect_stdout(io.StringIO()):
                ai.build(root)
            schedule = te.load_schedule(root)
            self.assertEqual(schedule['events'][0]['course_ids'], ['1001'])
            self.assertEqual(schedule['events'][1]['course_ids'], ['1003'])
            for profile in ('CHAT', 'NOTEBOOKLM'):
                for subject in (root / 'AI' / profile).iterdir():
                    if not subject.is_dir():
                        continue
                    text = ''.join(p.read_text(encoding='utf-8') for p in subject.rglob('*.md'))
                    self.assertIn('Your TimeEdit timetable', text)
                    self.assertIn('2026-10-27T14:00:00+01:00', text)
                    self.assertIn('commute, work, sleep', text)
            manifest = json.loads((root / 'AI/CONTEXT_MANIFEST.json').read_text())
            self.assertEqual(manifest['timetable']['events'], 2)
            self.assertFalse(any(path['path'].startswith('Schedule/') for path in ai.discover(root, [])))

    def test_recovery_output_is_used_for_build_and_query(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.root(root)
            (root / 'AI').mkdir()
            (root / 'AI/user-note.txt').write_text('retain this')
            (root / 'AI_CONFIG.json').write_text(json.dumps({'output_folder': 'AI_READY'}))
            with contextlib.redirect_stdout(io.StringIO()):
                ai.build(root)
                report = ai.query_pack(root, 'TEACHING_MATERIAL', budget=2000)
            self.assertTrue((root / 'AI_READY/index.html').is_file())
            self.assertTrue((root / 'AI_READY/QUESTION_PACK.md').is_file())
            self.assertEqual((root / 'AI/user-note.txt').read_text(), 'retain this')
            self.assertNotIn('Original attachments are in **AI/UPLOAD', ''.join(p.read_text() for p in (root / 'AI_READY/CHAT').rglob('00_CONTEXT*.md')))
            self.assertGreater(len(report['selected_chunks']), 0)

    def test_url_validation_rejects_non_timeedit_and_credential_urls(self):
        for value in ('http://cloud.timeedit.net/feed.ics', 'https://timeedit.net.bad.example/feed.ics', 'https://user:password@cloud.timeedit.net/feed.ics'):
            with self.subTest(value=value), self.assertRaises(te.ScheduleError):
                te.validate_url(value)

    def test_different_exam_code_is_title_matched_and_explicitly_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'MAT 1004--1004').mkdir()
            (root / 'SYNC_REPORT.json').write_text(json.dumps({'courses': [{'id': 1004, 'name': 'Mathematics for Data Science - Oslo'}]}))
            source = root / '.exam.ics'
            source.write_bytes(ICS.replace(b'ECO 1001 OH1 Economics', b'MAT 1005 W1 Mathematics for Data Science'))
            te.configure_schedule(root, source)
            with contextlib.redirect_stdout(io.StringIO()):
                schedule = te.prepare_schedule(root)
            self.assertEqual(schedule['events'][0]['course_ids'], ['1004'])
            self.assertEqual(schedule['events'][0]['matching_method'], 'unique_course_title_code_differs')
            self.assertIn('Verify assessment details', te.markdown(schedule))

    def test_csv_supplements_comments_without_overriding_live_room(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.root(root)
            source = root / '.csv'
            source.write_bytes(CSV)
            te.configure_schedule(root, source, 'https://cloud.timeedit.net/bi/feed.ics')
            with patch('timeedit_context.fetch', return_value=ICS), contextlib.redirect_stdout(io.StringIO()):
                schedule = te.prepare_schedule(root)
            event = schedule['events'][0]
            self.assertEqual(event['location'], 'D3-020\nD3-030')
            self.assertIn('CSV snapshot comment: Prepare', event['notes'])
            self.assertEqual(event['lecturers'], 'CSV snapshot: Teacher')

    def test_live_refresh_is_part_of_combined_canvas_sync(self):
        import sys
        import canvas_sync
        from test_sync import FakeCanvas
        from pypdf import PdfWriter
        fake = FakeCanvas()
        buffer = io.BytesIO()
        writer = PdfWriter()
        writer.add_blank_page(612, 792)
        writer.write(buffer)
        fake.payload = buffer.getvalue()
        with tempfile.TemporaryDirectory() as tmp:
            args = ['canvas_sync.py', '--base-url', 'https://canvas.example.edu', '--auth', 'token', '--course', '7', '--output', tmp, '--ai', '--timeedit-url', 'https://cloud.timeedit.net/example/test.ics']
            with patch.object(sys, 'argv', args), patch('canvas_sync.get_token', return_value='test-token'), patch('canvas_sync.Canvas', return_value=fake), patch('timeedit_context.fetch', return_value=ICS) as fetched, contextlib.redirect_stdout(io.StringIO()):
                code = canvas_sync.main()
            self.assertEqual(code, 0)
            fetched.assert_called_once()
            self.assertEqual(te.load_schedule(Path(tmp))['source_type'], 'live_feed_snapshot')


if __name__ == '__main__':
    unittest.main()
