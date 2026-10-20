import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ai_export as ai


class SubjectExportTests(unittest.TestCase):
    def build(self, root):
        with contextlib.redirect_stdout(io.StringIO()):
            return ai.build(root)

    def fixture(self, root):
        course = root / 'Programming--1003'
        (course / 'Files').mkdir(parents=True)
        (course / 'Pages').mkdir()
        (root / '.canvas-sync.json').write_text(json.dumps({'courses': {'1003': course.name}}))
        (root / 'SYNC_REPORT.json').write_text(json.dumps({'courses': [{'id': '1003', 'name': 'Programming'}], 'issues': [{'item': 'course files', 'reason': 'HTTP 403'}]}))
        (course / 'Pages/Schedule.html').write_text('<title>Schedule</title><h1>Week 1</h1><p>Learn loops.</p>')
        (course / 'index.html').write_text('<h2>Module A: Loops</h2><ul><li>Lecture A</li></ul><h2>Module B: Functions</h2><ul><li>Lecture B</li></ul><h2>All exported text</h2>')
        return course

    def test_subject_folders_module_order_and_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            course = self.fixture(root)
            (course / 'Books').mkdir()
            (course / 'Books/book.txt').write_text('BOOK ONLY CHAPTER')
            other = root / 'Other--7/Pages'
            other.mkdir(parents=True)
            (root / '.canvas-sync.json').write_text(json.dumps({'courses': {'1003': course.name, '7': 'Other--7'}}))
            (other / 'note.txt').write_text('OTHER SUBJECT MATERIAL')
            self.build(root)
            folder = root / 'AI/UPLOAD/Programming--1003/01_COURSE'
            guide = (folder / '00_START_HERE.md').read_text()
            self.assertLess(guide.index('Module A'), guide.index('Module B'))
            self.assertIn('HTTP 403', (folder / '02_COVERAGE.md').read_text())
            text = '\n'.join(p.read_text() for p in folder.glob('10_*.md'))
            self.assertIn('Learn loops', text)
            self.assertNotIn('BOOK ONLY', text)
            self.assertNotIn('OTHER SUBJECT', text)
            self.assertTrue(list((root / 'AI/UPLOAD/Programming--1003/02_BOOKS').rglob('10_*.md')))

    def test_csv_profile_and_full_original_no_row_dump(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            course = self.fixture(root)
            dataset = course / 'Files/bikes.csv'
            dataset.write_text('name,value\n' + ''.join(f'ROW_{i},{i}\n' for i in range(10000)))
            self.build(root)
            folder = root / 'AI/UPLOAD/Programming--1003/03_DATASETS'
            original = next(folder.glob('SOURCE--*.csv'))
            self.assertEqual(original.read_bytes(), dataset.read_bytes())
            text = '\n'.join(p.read_text() for p in folder.glob('10_*.md'))
            self.assertIn('10,001', text)
            self.assertIn('NOT the full dataset', text)
            self.assertNotIn('ROW_9999', text)
            self.assertLess(ai.estimate(text), 4000)
            teaching = '\n'.join(p.read_text() for p in (folder.parent / '01_COURSE').glob('10_*.md'))
            self.assertNotIn('ROW_0', teaching)
            self.build(root)
            self.assertEqual(original.read_bytes(), dataset.read_bytes())

    def test_notebook_cell_order_code_whitespace_outputs_and_rich_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'lecture.ipynb'
            code = 'for i in range(2):\n    print(i)\n'
            path.write_text(json.dumps({'nbformat': 4, 'metadata': {}, 'cells': [
                {'cell_type': 'markdown', 'source': ['# Functions\n', 'Explanation']},
                {'cell_type': 'code', 'source': [code], 'outputs': [
                    {'output_type': 'stream', 'text': ['0\n', '1\n']},
                    {'output_type': 'display_data', 'data': {'text/plain': 'A saved chart', 'image/png': 'not-decoded-here'}}]},
                {'cell_type': 'markdown', 'source': 'See attachment', 'attachments': {'picture.png': {}}}]}))
            warnings = []
            units = list(ai.extract_units(path, 2000, warnings))
            self.assertIn(code, units[1][1])
            self.assertIn('0\n1\n', units[2][1])
            self.assertIn('image/png', '\n'.join(warnings))
            self.assertIn('embedded attachments', '\n'.join(warnings))
            self.assertIn('NOT executed', '\n'.join(warnings))
            before = path.read_bytes()
            self.build(Path(tmp))
            self.assertEqual(path.read_bytes(), before)
            companion = next((Path(tmp) / 'AI/UPLOAD').rglob('SOURCE--*.ipynb'))
            self.assertEqual(companion.read_bytes(), before)
            self.assertIn('Notebook cell 2', (Path(tmp) / 'AI/CHUNKS.jsonl').read_text())

    def test_split_preserves_every_character_including_code_indentation(self):
        text = '\n    if x:\n        print("økonomi 🦉")\n\n' * 1000
        parts = list(ai.split_text(text, 128))
        self.assertEqual(''.join(parts), text)
        self.assertTrue(all(ai.estimate(p) <= 128 for p in parts))

    def test_originals_copy_with_hash_and_text_zip_excludes_binary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            course = self.fixture(root)
            original = course / 'Files/chart.png'
            original.write_bytes(b'complete visual data')
            self.build(root)
            copy = next((root / 'AI/UPLOAD').rglob('SOURCE--*.png'))
            self.assertEqual(copy.read_bytes(), original.read_bytes())
            report = json.loads((root / 'AI/BUILD_REPORT.json').read_text())
            g = report['groups']['Programming--1003/01_COURSE']
            self.assertEqual(g['originals'][0]['sha256'], ai.digest_file(original))
            with zipfile.ZipFile(root / 'AI/AI_UPLOAD.zip') as z:
                self.assertTrue(all(p.endswith('.md') for p in z.namelist()))
                self.assertTrue(any('/01_COURSE/' in p for p in z.namelist()))

    def test_changed_source_during_copy_does_not_replace_previous_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            course = self.fixture(root)
            (course / 'Files/chart.png').write_bytes(b'original')
            self.build(root)
            old = (root / 'AI/BUILD_REPORT.json').read_bytes()
            def bad_copy(src, dst):
                Path(dst).write_bytes(b'changed')
            with patch('ai_export.shutil.copy2', side_effect=bad_copy):
                with self.assertRaises(ai.ExportError):
                    self.build(root)
            self.assertEqual((root / 'AI/BUILD_REPORT.json').read_bytes(), old)

    def test_failed_text_extraction_preserves_unreadable_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'bad.md').write_bytes(b'\xffbad encoding')
            counts = self.build(root)
            self.assertEqual(counts['failed'], 1)
            original = next((root / 'AI/UPLOAD').rglob('SOURCE--*.md'))
            self.assertEqual(original.read_bytes(), b'\xffbad encoding')


if __name__ == '__main__':
    unittest.main()
