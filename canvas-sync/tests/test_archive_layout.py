import contextlib
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ai_export as ai
from archive_layout import named_subjects, support_file
import study_setup as study
import timeedit_context as te
from test_timeedit import CSV


class LayoutTests(unittest.TestCase):
    def fixture(self, root):
        course = root / 'ECO 1001--1001'
        (course / 'Books').mkdir(parents=True)
        (course / 'Pages').mkdir()
        (course / 'Practice').mkdir()
        (course / 'Practice/work.py').write_bytes(b'MY_WORK')
        (course / 'Pages/lecture.txt').write_bytes(b'LECTURE_BYTES\r\n')
        (course / 'Books/book.txt').write_text('BOOK_TEXT')
        (course / 'Books/alternate.txt').write_text('EXCLUDED_TEXT')
        (root / '.canvas-sync.json').write_text(json.dumps({'courses': {'1001': course.name}, 'files': {'1001:3': {'path': course.name + '\\Pages\\lecture.txt'}}}))
        (root / 'AI_CONFIG.json').write_text(json.dumps({'output_folder': 'AI_READY', 'exclude': [course.name + '/Books/alternate.txt']}))
        (root / 'BOOK_CATALOG.json').write_text(json.dumps({'books': {course.name + '/Books/book.txt': {'use': 'Allowed older edition'}}}))
        return course

    def labels(self, root):
        (root / 'SYNC_REPORT.json').write_text(json.dumps({'courses': [{'id': '1001', 'name': 'Economics I - Oslo', 'folder': 'ECO 1001--1001'}]}))

    def build(self, root, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()):
            return ai.build(root, **kwargs)

    def test_rename_preserves_bytes_practice_catalog_state_excludes_and_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = self.fixture(root)
            hashes = {p.relative_to(old).as_posix(): ai.digest_file(p) for p in old.rglob('*') if p.is_file()}
            self.build(root)
            self.labels(root)
            with patch('ai_export.extract_units', side_effect=AssertionError('Rename must reuse cache')):
                counts = self.build(root)
            new = root / 'Economics I--1001'
            self.assertFalse(old.exists())
            self.assertEqual(hashes, {p.relative_to(new).as_posix(): ai.digest_file(p) for p in new.rglob('*') if p.is_file()})
            self.assertEqual(counts['cached'], 2)
            state = json.loads((root / '.canvas-sync.json').read_text())
            self.assertEqual(state['files']['1001:3']['path'], 'Economics I--1001/Pages/lecture.txt')
            self.assertEqual(state['course_codes']['1001'], 'ECO 1001')
            text = (root / 'AI_READY/CHUNKS.jsonl').read_text()
            self.assertNotIn('EXCLUDED_TEXT', text)
            self.assertIn('Allowed older edition', next((root / 'AI_READY/NOTEBOOKLM').glob('*/00_READ_FIRST.md')).read_text())
            csv = root / '.schedule.csv'
            csv.write_bytes(CSV)
            te.configure_schedule(root, csv)
            schedule = te.prepare_schedule(root)
            matched = [e for e in schedule['events'] if 'ECO 1001' in e['course_codes']]
            self.assertTrue(all('1001' in e['course_ids'] and e['matching_method'] == 'course_code' for e in matched))

    def test_rename_rolls_back_on_failure_and_never_combines_existing_folders(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = self.fixture(root)
            self.labels(root)
            before = (root / '.canvas-sync.json').read_bytes()
            with self.assertRaises(RuntimeError):
                with named_subjects(root):
                    raise RuntimeError('simulated export failure')
            self.assertTrue(old.is_dir())
            self.assertEqual((root / '.canvas-sync.json').read_bytes(), before)
            (root / 'Economics I--1001').mkdir()
            with self.assertRaisesRegex(ValueError, 'already exists'):
                with named_subjects(root):
                    pass
            self.assertTrue(old.is_dir())

    def test_simple_layout_search_manifest_hashes_and_portable_cache_survive_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'original'
            root.mkdir()
            self.fixture(root)
            config = json.loads((root / 'AI_CONFIG.json').read_text())
            config['simple_layout'] = True
            (root / 'AI_CONFIG.json').write_text(json.dumps(config))
            self.build(root)
            target = root / 'AI_READY'
            self.assertFalse((target / 'UPLOAD').exists())
            self.assertTrue((target / '_EXTRAS/UPLOAD').is_dir())
            self.assertFalse((root / 'Canvas-Study-Portable.zip').exists())
            with contextlib.redirect_stdout(io.StringIO()):
                ai.query_pack(root, 'LECTURE_BYTES', 20000)
            manifest = json.loads(support_file(target, 'CONTEXT_MANIFEST.json').read_text())
            for entry in manifest['subjects'].values():
                for record in entry['notebooklm']['files'] + [r for g in entry['groups'] for r in g['files']]:
                    self.assertEqual(record['sha256'], ai.digest_file(target / record['file']))
            package = study.make_portable(root)
            with zipfile.ZipFile(package) as archive:
                self.assertIsNone(archive.testzip())
                self.assertIn('Canvas-Archive/AI_READY/_EXTRAS/search.sqlite', archive.namelist())
                copied = Path(tmp) / 'copy'
                archive.extractall(copied)
            with patch('ai_export.extract_units', side_effect=AssertionError('Portable cache missing')):
                counts = self.build(copied / 'Canvas-Archive')
            self.assertEqual(counts['cached'], 2)
            self.assertEqual(counts['extracted'], 0)

    def test_missing_pdf_reader_does_not_replace_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            course = self.fixture(root)
            self.build(root)
            before = (root / 'AI_READY/CONTEXT_MANIFEST.json').read_bytes()
            (course / 'Pages/lesson.pdf').write_bytes(b'PDF_FIXTURE')
            with patch.dict(sys.modules, {'pypdf': None}):
                with self.assertRaisesRegex(ai.ExportError, 'SETUP_THIS_PC'):
                    self.build(root)
            self.assertEqual((root / 'AI_READY/CONTEXT_MANIFEST.json').read_bytes(), before)
            self.assertFalse(list(root.glob('.canvas-ai-build-*')))

    def test_locked_subject_defer_keeps_valid_paths_and_repairs_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = self.fixture(root)
            self.labels(root)
            error = PermissionError('Sharing violation')
            error.winerror = 32
            with patch('archive_layout.rename_with_retry', side_effect=error):
                counts = self.build(root)
            self.assertTrue(counts['folder_names_deferred'])
            self.assertTrue(old.is_dir())
            self.assertIn('LECTURE_BYTES', (root / 'AI_READY/CHUNKS.jsonl').read_text())
            self.assertIn('deferred', (root / 'LAST_RUN.md').read_text())

    def test_cleanup_lock_after_publish_does_not_undo_source_rename(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.fixture(root)
            self.build(root)
            self.labels(root)
            real = ai.shutil.rmtree
            def cleanup(path, *args, **kwargs):
                if Path(path).name.startswith('.canvas-ai-old-'):
                    raise PermissionError('Directory cleanup is temporarily locked')
                return real(path, *args, **kwargs)
            with patch('ai_export.shutil.rmtree', side_effect=cleanup):
                self.build(root)
            self.assertTrue((root / 'Economics I--1001/Pages/lecture.txt').is_file())
            manifest = json.loads((root / 'AI_READY/CONTEXT_MANIFEST.json').read_text())
            self.assertTrue(all((root / record['path']).is_file() for record in manifest['sources'].values()))


if __name__ == '__main__':
    unittest.main()
