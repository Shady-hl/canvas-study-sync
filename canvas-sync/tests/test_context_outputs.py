import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import ai_export as ai
import context_outputs as outputs


class ContextOutputTests(unittest.TestCase):
    def fixture(self, root):
        course = root / 'Course--7'
        (course / 'Pages').mkdir(parents=True)
        (course / 'Books').mkdir()
        (course / 'Files').mkdir()
        (course / 'Pages/lesson.txt').write_text('COURSE_SENTINEL\n' + '    print(value)\n' * 1000, encoding='utf-8')
        (course / 'Books/book.txt').write_text('BOOK_SENTINEL\n' + 'alpha beta\n' * 1000, encoding='utf-8')
        (course / 'Files/data.csv').write_text('a,b\n1,2\n3,4\n', encoding='utf-8')
        (course / 'Files/scan.png').write_bytes(b'original bytes')
        return course

    def build(self, root, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()):
            return ai.build(root, **kwargs)

    def test_chat_subject_isolation_bounds_and_integrity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.fixture(root)
            other = root / 'Other--8/Pages'
            other.mkdir(parents=True)
            (other / 'other.txt').write_text('OTHER_SUBJECT_SENTINEL', encoding='utf-8')
            self.build(root, chunk_budget=128, pack_budget=2300)
            manifest = json.loads((root / 'AI/CONTEXT_MANIFEST.json').read_text(encoding='utf-8'))
            entry = manifest['subjects']['Course--7--7']
            course = next(g for g in entry['groups'] if g['role'] == 'course')
            combined = ''.join((root / 'AI' / r['file']).read_text(encoding='utf-8') for r in course['files'])
            self.assertIn('COURSE_SENTINEL', combined)
            self.assertNotIn('OTHER_SUBJECT_SENTINEL', combined)
            self.assertNotIn('BOOK_SENTINEL', combined)
            self.assertTrue(all(r['estimated_tokens'] <= 2300 for g in entry['groups'] for r in g['files']))
            self.assertTrue(all(p.suffix == '.md' for p in (root / 'AI/CHAT').rglob('*') if p.is_file()))
            checks = json.loads((root / 'AI/CONTEXT_CHECKS.json').read_text())
            self.assertGreater(checks['extracted_chunks'], 100)
            self.assertEqual(checks['extracted_chunks'], checks['chat_chunks_verified'])
            self.assertEqual(checks['extracted_chunks'], checks['notebooklm_chunks_verified'])

    def test_marked_tool_inside_archive_is_not_a_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.fixture(root)
            tool = root / 'canvas-sync'
            tool.mkdir()
            (tool / '.canvas-sync-tool.json').write_text('{}')
            (tool / 'ai_export.py').write_text('TOOL_CONTENT_MUST_NOT_BECOME_A_BOOK')
            (tool / 'dist').mkdir()
            (tool / 'dist/release.zip').write_bytes(b'package')
            records = ai.discover(root, [])
            self.assertFalse(any(r['path'].startswith('canvas-sync/') for r in records))
            self.build(root)
            self.assertNotIn('TOOL_CONTENT_MUST_NOT_BECOME_A_BOOK', (root / 'AI/CHUNKS.jsonl').read_text())

    def test_profiles_keep_every_extracted_chunk_and_originals_on_incremental_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.fixture(root)
            before = {r['path']: ai.digest_file(root / r['path']) for r in ai.discover(root, [])}
            self.build(root)
            with patch('ai_export.extract_units', side_effect=AssertionError('Unchanged sources should reuse extraction')):
                counts = self.build(root)
            self.assertEqual(counts['cached'], 3)
            self.assertEqual(before, {r['path']: ai.digest_file(root / r['path']) for r in ai.discover(root, [])})
            for path in (root / 'AI/PACKAGES').glob('*.zip'):
                with zipfile.ZipFile(path) as archive:
                    self.assertIsNone(archive.testzip())
                    self.assertTrue(all(name.endswith('.md') for name in archive.namelist()))
                    self.assertTrue(all(not name.startswith('/') and '..' not in Path(name).parts for name in archive.namelist()))

    def test_corrupt_profile_retains_previous_generation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.fixture(root)
            self.build(root)
            before = (root / 'AI/CONTEXT_MANIFEST.json').read_bytes()
            real_copy = outputs.shutil.copy2
            def corrupt(source, dest):
                result = real_copy(source, dest)
                if 'CHAT' in Path(dest).parts:
                    path = Path(dest)
                    text = path.read_text(encoding='utf-8')
                    path.write_text(text.replace('COURSE_SENTINEL', 'CORRUPTED_TEXT'), encoding='utf-8')
                return result
            with patch('context_outputs.shutil.copy2', side_effect=corrupt):
                with self.assertRaisesRegex(ai.ExportError, 'text integrity check failed'):
                    self.build(root)
            self.assertEqual((root / 'AI/CONTEXT_MANIFEST.json').read_bytes(), before)
            self.assertFalse(list(root.glob('.canvas-ai-build-*')))

    def test_citation_like_source_content_is_preserved_as_reference_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.fixture(root)
            self.build(root)
            import sqlite3
            with contextlib.closing(sqlite3.connect(root / 'AI/search.sqlite')) as db:
                db.row_factory = sqlite3.Row
                quoted = ai.chunk_block(next(iter(ai.joined_chunks(db))))
            (root / 'Course--7/Pages/quotation.txt').write_text('Example of a citation block:\n' + quoted, encoding='utf-8')
            self.build(root)
            report = json.loads((root / 'AI/CONTEXT_CHECKS.json').read_text())
            self.assertTrue(report['excerpt_text_matches_database'])

    def test_dashboard_escapes_untrusted_course_title_and_workspace_resolves(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.fixture(root)
            (root / 'SYNC_REPORT.json').write_text(json.dumps({'courses': [
                {'id': 7, 'name': '<script>alert("course")</script>'}], 'issues': [
                {'course_id': '7', 'item': '<img src=x>', 'reason': 'HTTP 403'}]}))
            self.build(root)
            page = (root / 'AI/index.html').read_text(encoding='utf-8')
            self.assertNotIn('<script>alert("course")</script>', page)
            self.assertIn('&lt;script&gt;', page)
            self.assertNotIn('<img src=x>', page)
            workspace = next((root / 'AI/VSCODE').glob('*.code-workspace'))
            document = json.loads(workspace.read_text(encoding='utf-8'))
            for folder in document['folders']:
                resolved = (workspace.parent / folder['path']).resolve()
                self.assertTrue(resolved.is_dir())
                resolved.relative_to(root.resolve())

    def test_missing_and_repeated_chunk_detection(self):
        from collections import Counter
        expected = Counter({'a': 1, 'b': 1})
        for chunks in (['a'], ['a', 'b', 'b']):
            with self.subTest(chunks=chunks), self.assertRaises(ai.ExportError):
                outputs.verify_profile(expected, [{'chunks': chunks}], 'fixture')

    def test_shared_root_file_workspace_points_to_archive_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'Shared book.txt').write_text('Shared reference', encoding='utf-8')
            self.build(root)
            workspace = next((root / 'AI/VSCODE').glob('*.code-workspace'))
            folders = json.loads(workspace.read_text(encoding='utf-8'))['folders']
            self.assertTrue(all((workspace.parent / f['path']).resolve().is_dir() for f in folders))

    def test_notebook_mixed_line_endings_preserved_in_every_text_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original = 'first\r\nsecond\rthird\n'
            (root / 'lesson.ipynb').write_text(json.dumps({'nbformat': 4, 'metadata': {}, 'cells': [
                {'cell_type': 'markdown', 'source': original}]}), encoding='utf-8')
            self.build(root)
            for profile in ('UPLOAD', 'CHAT', 'NOTEBOOKLM', 'DOCUMENTS'):
                corpus = ''.join(ai.read_utf8(path) for path in (root / 'AI' / profile).rglob('*.md'))
                self.assertIn(original, corpus)
            checks = json.loads((root / 'AI/CONTEXT_CHECKS.json').read_text())
            self.assertTrue(checks['excerpt_text_matches_database'])

    def test_cli_exports_mixed_line_endings_and_reports_context_errors(self):
        import subprocess
        import sys
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'lesson.ipynb').write_text(json.dumps({'nbformat': 4, 'metadata': {}, 'cells': [
                {'cell_type': 'markdown', 'source': 'Keep this\r\nline ending.'}]}), encoding='utf-8')
            result = subprocess.run([sys.executable, str(Path(ai.__file__)), '--archive', str(root)],
                                    capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('Choose a subject and context format:', result.stdout)


if __name__ == '__main__':
    unittest.main()
