import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

import ai_export as ai
import study_setup as study
import timeedit_context as te
from test_timeedit import CSV


class StudySetupTests(unittest.TestCase):
    def fixture(self, root):
        course = root / 'ECO 1001--1001'
        (course / 'Pages').mkdir(parents=True)
        (course / 'Books').mkdir()
        (course / 'Practice').mkdir()
        (course / 'Pages/lecture.txt').write_text('LECTURE_CONTEXT', encoding='utf-8')
        (course / 'Practice/my-work.py').write_text('print("MY_WORK")', encoding='utf-8')
        (root / '.canvas-sync.json').write_text(json.dumps({'courses': {'1001': course.name}}))
        (root / 'AI_CONFIG.json').write_text(json.dumps({'output_folder': 'AI_READY'}))
        source = root / '.timeedit.csv'
        source.write_bytes(CSV)
        te.configure_schedule(root, source)
        return course

    def build(self, root):
        with contextlib.redirect_stdout(io.StringIO()):
            ai.build(root)

    def test_portable_schedule_config_excludes_private_feed_url(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            self.fixture(root)
            self.build(root)
            config_path=root/'Schedule/.timeedit-source.json'
            config=json.loads(config_path.read_text())
            config['url']='https://cloud.timeedit.net/example/PRIVATE_SYNTHETIC_FEED_TOKEN.ics'
            config_path.write_text(json.dumps(config))
            package=study.make_portable(root)
            with zipfile.ZipFile(package) as archive:
                data=json.loads(archive.read('Canvas-Archive/Schedule/.timeedit-source.json'))
            self.assertNotIn('url',data)
            self.assertNotIn('PRIVATE_SYNTHETIC_FEED_TOKEN',json.dumps(data))
            self.assertEqual(data['file'],'timeedit.csv')

    def test_portable_archive_is_relocatable_and_rebuilds_in_its_own_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'original'
            root.mkdir()
            self.fixture(root)
            (root / 'AI').mkdir()
            (root / 'AI/legacy.txt').write_text('OLD_OUTPUT')
            self.build(root)
            zip_path = study.make_portable(root)
            with zipfile.ZipFile(zip_path) as archive:
                self.assertIsNone(archive.testzip())
                self.assertFalse(any('/AI/' in n or '/dist/' in n or '__pycache__' in n for n in archive.namelist()))
                self.assertIn('Canvas-Archive/ECO 1001--1001/Practice/my-work.py', archive.namelist())
                for name in study.ROOT_SHORTCUTS:
                    for suffix in ('.cmd', '.command'):
                        self.assertIn('Canvas-Archive/' + name + suffix, archive.namelist())
                self.assertIn('Canvas-Archive/canvas-sync/RUN_STUDY.command', archive.namelist())
                self.assertIn('Canvas-Archive/canvas-sync/resync_ui.html', archive.namelist())
                app_executable = 'Canvas-Archive/Canvas Resync.app/Contents/MacOS/CanvasResync'
                self.assertIn(app_executable, archive.namelist())
                self.assertTrue((archive.getinfo(app_executable).external_attr >> 16) & 0o111)
                self.assertFalse(any('/.venv-macos/' in n for n in archive.namelist()))
                moved = Path(tmp) / 'OneDrive'
                archive.extractall(moved)
            copied = moved / 'Canvas-Archive'
            for workspace in (copied / 'AI_READY/VSCODE').glob('*.code-workspace'):
                for folder in json.loads(workspace.read_text(encoding='utf-8'))['folders']:
                    self.assertTrue((workspace.parent / folder['path']).resolve().is_dir())
            result = subprocess.run([sys.executable, str(copied / 'canvas-sync/ai_export.py')],
                                    cwd=Path(tmp), capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((copied / 'AI_READY/search.sqlite').is_file())
            self.assertIn('"cached": 1', result.stdout)
            self.assertEqual((copied / 'ECO 1001--1001/Practice/my-work.py').read_text(), 'print("MY_WORK")')

    def test_timetable_is_a_named_notebook_source_and_practice_is_not_teaching_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.fixture(root)
            self.build(root)
            subject = next(p for p in (root / 'AI_READY/NOTEBOOKLM').iterdir() if p.is_dir())
            timetable = (subject / '01_TIMETABLE.md').read_text(encoding='utf-8')
            guide = (subject / '00_READ_FIRST.md').read_text(encoding='utf-8')
            self.assertIn('2026-10-27T14:00:00+01:00', timetable)
            self.assertIn('01_TIMETABLE.md', guide)
            self.assertNotIn('2026-10-27T14:00:00+01:00', guide)
            self.assertIn('Ask one practice question', guide)
            self.assertNotIn('MY_WORK', (root / 'AI_READY/CHUNKS.jsonl').read_text(encoding='utf-8'))

    def test_book_editions_and_optional_scope_are_visible_in_notebook_guide(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            course = self.fixture(root)
            (course / 'Books/older.txt').write_text('OLDER_TEXT')
            (course / 'Books/Optional - background.txt').write_text('BACKGROUND_TEXT')
            (root / 'BOOK_CATALOG.json').write_text(json.dumps({'books': {
                'ECO 1001--1001/Books/older.txt': {'use': 'Allowed older edition', 'note': 'Exercise numbers differ.'}}}))
            self.build(root)
            guide = next((root / 'AI_READY/NOTEBOOKLM').glob('*/00_READ_FIRST.md')).read_text(encoding='utf-8')
            self.assertIn('Allowed older edition', guide)
            self.assertIn('Exercise numbers differ', guide)
            self.assertIn('Optional reference', guide)
            names = [p.name for p in (root / 'AI_READY/NOTEBOOKLM').glob('*/*.md')]
            self.assertTrue(any('OPTIONAL--' in name for name in names))
            self.assertTrue(any('OLDER_EDITION--' in name for name in names))
            self.assertNotIn('BOOK_CATALOG.json', (root / 'AI_READY/CHUNKS.jsonl').read_text(encoding='utf-8'))

    def test_nearest_archive_marker_wins_over_default_documents(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'OneDrive/Canvas-Archive'
            tool = root / 'canvas-sync'
            tool.mkdir(parents=True)
            (root / '.canvas-sync.json').write_text('{"base_url":"https://canvas.example.edu"}')
            self.assertEqual(study.default_archive(tool / 'ai_export.py'), root.resolve())

    def test_sync_paths_accept_windows_separators_and_reject_foreign_absolute_paths(self):
        import canvas_sync
        from test_sync import FakeCanvas
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = canvas_sync.Archive(FakeCanvas(), root)
            self.assertEqual(archive.local_path('Course\\Files\\lecture.pdf'), (root / 'Course/Files/lecture.pdf').resolve())
            for path in ('C:\\outside.pdf', '//server/share/file.pdf', '../outside.pdf'):
                with self.subTest(path=path), self.assertRaises(canvas_sync.SyncError):
                    archive.local_path(path)

    def test_portable_keeps_ed_reports_mapping_and_raw_xml(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            course = self.fixture(root)
            folder = course / 'Discussions/Ed-eu-123'
            (folder / '.raw').mkdir(parents=True)
            (folder / 'thread-1.html').write_text('<h1>Ed question</h1><p>Staff reply</p>')
            (folder / '.raw/thread-1.json').write_text('{"posts":[]}')
            for name in ('ED_SYNC_REPORT.json', 'ED_CONFIG.json'):
                (root / name).write_text('{}')
            self.build(root)
            archive_path = study.make_portable(root)
            with zipfile.ZipFile(archive_path) as archive:
                self.assertIn('Canvas-Archive/ED_SYNC_REPORT.json', archive.namelist())
                self.assertIn('Canvas-Archive/ED_CONFIG.json', archive.namelist())
                self.assertIn('Canvas-Archive/' + course.name + '/Discussions/Ed-eu-123/.raw/thread-1.json', archive.namelist())

if __name__ == '__main__':
    unittest.main()
