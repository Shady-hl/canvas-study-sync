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


class AITests(unittest.TestCase):
    def fixture(self, root):
        course = root / 'EXC--1002'
        (course / 'Pages').mkdir(parents=True)
        (course / 'Books').mkdir()
        (root / '.canvas-sync.json').write_text(json.dumps({'courses': {'1002': 'EXC--1002'}}))
        (course / 'Pages' / 'intro.html').write_text('<html><head><title>Accounting introduction</title><style>bad css</style></head><body><h1>Assets</h1><p><a href="../index.html">Course index</a> · <a href="https://example.edu/course">Canvas source</a></p><p>Assets equal liabilities plus equity.</p><table><tr><th>Item</th><th>Value</th></tr><tr><td>Cash</td><td>200</td></tr></table><script>bad script</script></body></html>', encoding='utf-8')
        (course / 'Books' / 'Textbook.txt').write_text('CHAPTER 1\nAssets and liabilities describe financial position.\n\nCHAPTER 2\nCash flow differs from profit.\n')
        return course

    def run_build(self, root, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()):
            return ai.build(root, **kwargs)

    def test_html_preserves_headings_links_tables_strips_navigation_scripts(self):
        p = ai.MarkdownHTML()
        p.feed('<head><title>Test title</title></head><h2>Heading</h2><p><strong>bold</strong> <a href="https://example.org">reference</a></p><table><tr><td>A</td><td>10</td></tr></table><script>secret()</script>')
        md = p.markdown()
        self.assertEqual(p.title, 'Test title')
        for item in ('## Heading', '**bold**', '[reference](https://example.org)', '<td>A</td><td>10</td>'):
            self.assertIn(item, md)
        self.assertNotIn('secret()', md)

    def test_complete_build_and_query_with_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.fixture(root)
            result = self.run_build(root)
            self.assertEqual(result['extracted'], 2)
            paths = list((root / 'AI/UPLOAD').rglob('10_*_PART_*.md'))
            self.assertEqual(len(paths), 2)
            self.assertTrue(any('02_BOOKS' in p.parts for p in paths))
            self.assertTrue(any('01_COURSE' in p.parts for p in paths))
            for p in paths:
                self.assertLessEqual(ai.estimate(p.read_text(encoding='utf-8')), 24000)
            self.assertIn('Accounting introduction', (root / 'AI/UPLOAD/01_SOURCE_INDEX.md').read_text(encoding='utf-8'))
            with contextlib.redirect_stdout(io.StringIO()):
                report = ai.query_pack(root, 'liabilities', budget=2500, course='1002')
            self.assertGreater(len(report['selected_chunks']), 0)
            self.assertLessEqual(report['estimated_tokens'], 2500)
            self.assertIn('TXT lines', (root / 'AI/QUESTION_PACK.md').read_text(encoding='utf-8'))
            with zipfile.ZipFile(root / 'AI/AI_UPLOAD.zip') as z:
                self.assertIsNone(z.testzip())
                self.assertFalse(any(n.endswith('.sqlite') for n in z.namelist()))

    def test_incremental_cache_and_source_edits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            course = self.fixture(root)
            self.run_build(root)
            with patch('ai_export.extract_units', side_effect=AssertionError('Should reuse extraction')):
                result = self.run_build(root)
            self.assertEqual(result['cached'], 2)
            (course / 'Books/Textbook.txt').write_text('Changed textbook content.')
            result = self.run_build(root)
            self.assertEqual(result['cached'], 1)
            self.assertEqual(result['extracted'], 1)

    def test_exact_duplicates_and_derived_files_not_repeated(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            course = self.fixture(root)
            (course / 'Books/copy.txt').write_bytes((course / 'Books/Textbook.txt').read_bytes())
            (course / 'STUDY_GUIDE.md').write_text('DUPLICATED COURSE DUMP')
            (course / 'index.html').write_text('navigation')
            (course / 'History').mkdir()
            (course / 'History/stale.txt').write_text('obsolete')
            result = self.run_build(root)
            self.assertEqual(result['duplicates'], 1)
            data = (root / 'AI/CHUNKS.jsonl').read_text(encoding='utf-8')
            self.assertNotIn('DUPLICATED COURSE DUMP', data)
            self.assertNotIn('obsolete', data)
            with contextlib.closing(sqlite3.connect(root / 'AI/search.sqlite')) as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM sources').fetchone()[0], 3)

    def test_books_at_course_and_archive_root_are_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            course = self.fixture(root)
            (course / 'Book at root.txt').write_text('local book')
            (root / 'Shared text.txt').write_text('shared book')
            records = {r['path']: r for r in ai.discover(root, [])}
            self.assertEqual(records['EXC--1002/Book at root.txt']['role'], 'book')
            self.assertEqual(records['Shared text.txt']['course'], 'shared')

    def test_unsupported_visuals_reported_and_original_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            course = self.fixture(root)
            image = course / 'Books/diagram.png'
            image.write_bytes(b'FAKE PNG BYTES')
            before = image.read_bytes()
            result = self.run_build(root)
            self.assertEqual(result['visual_only'], 1)
            self.assertIn('diagram.png', (root / 'AI/EXTRACTION_REPORT.md').read_text(encoding='utf-8'))
            self.assertEqual(image.read_bytes(), before)

    def test_ownership_guard_and_failed_build_preserves_old_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.fixture(root)
            (root / 'AI').mkdir()
            (root / 'AI/user.txt').write_text('keep')
            with self.assertRaises(ai.ExportError):
                self.run_build(root)
            self.assertEqual((root / 'AI/user.txt').read_text(encoding='utf-8'), 'keep')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.fixture(root)
            self.run_build(root)
            old = (root / 'AI/UPLOAD/01_SOURCE_INDEX.md').read_bytes()
            with patch('ai_export.write_outputs', side_effect=ai.ExportError('test failure')):
                with self.assertRaises(ai.ExportError):
                    self.run_build(root)
            self.assertEqual((root / 'AI/UPLOAD/01_SOURCE_INDEX.md').read_bytes(), old)
            self.assertEqual(list(root.glob('.canvas-ai-build-*')), [])

    def test_utf8_long_line_chunks_preserve_nonwhitespace_and_bounds(self):
        text = 'økonomi 🦉 金融 ' * 2000
        chunks = list(ai.split_text(text, 128))
        self.assertEqual(''.join(''.join(ch.split()) for ch in chunks), ''.join(text.split()))
        self.assertTrue(all(ai.estimate(ch) <= 128 for ch in chunks))

    def test_over_million_estimated_tokens_is_packed_without_truncation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'Huge textbook.txt'
            text = 'alpha beta gamma delta\n' * 145000
            self.assertGreater(ai.estimate(text), 1000000)
            source.write_text(text)
            result = self.run_build(root)
            self.assertGreater(result['packs'], 30)
            with contextlib.closing(sqlite3.connect(root / 'AI/search.sqlite')) as db:
                all_text = '\n'.join(r[0] for r in db.execute('SELECT text FROM chunks ORDER BY ordinal'))
            self.assertEqual(all_text.split(), text.split())
            book_packs = list((root / 'AI/UPLOAD').rglob('10_BOOK_PART_*.md'))
            self.assertGreater(len(book_packs), 30)
            for p in book_packs:
                self.assertLessEqual(ai.estimate(p.read_text(encoding='utf-8')), 24000)
            with contextlib.redirect_stdout(io.StringIO()):
                report = ai.query_pack(root, 'alpha', budget=6000)
            self.assertGreater(report['omitted_matching_chunks'], 0)
            self.assertLessEqual(report['estimated_tokens'], 6000)

    def test_pdf_page_locators_and_sparse_page_warning(self):
        try:
            from reportlab.pdfgen.canvas import Canvas as PDFCanvas
            import pypdf
        except ImportError:
            self.skipTest('PDF test dependencies missing')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            c = PDFCanvas(str(root / 'book.pdf'))
            c.drawString(50, 700, 'Financial accounting: assets, liabilities and equity belong to the balance sheet.')
            c.showPage()
            c.showPage()
            c.save()
            self.run_build(root)
            chunks = [json.loads(l) for l in (root / 'AI/CHUNKS.jsonl').read_text(encoding='utf-8').splitlines()]
            self.assertEqual({r['locator'] for r in chunks}, {'PDF page 1', 'PDF page 2'})
            self.assertIn('Sparse/image-only pages', (root / 'AI/EXTRACTION_REPORT.md').read_text(encoding='utf-8'))

    def test_failed_pdf_visible_and_retry_not_cached(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'broken.pdf').write_bytes(b'not pdf')
            with patch('ai_export.extract_units', side_effect=ai.ExportError('PDF reader unavailable')):
                result = self.run_build(root)
            self.assertEqual(result['failed'], 1)
            self.assertIn('PDF reader unavailable', (root / 'AI/EXTRACTION_REPORT.md').read_text(encoding='utf-8'))
            with patch('ai_export.extract_units', return_value=iter([('PDF page 1', 'Now it works')])):
                result = self.run_build(root)
            self.assertEqual(result['extracted'], 1)

    def test_deleted_source_removed_from_new_packs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            course = self.fixture(root)
            self.run_build(root)
            (course / 'Books/Textbook.txt').unlink()
            self.run_build(root)
            self.assertNotIn('Cash flow differs', (root / 'AI/CHUNKS.jsonl').read_text(encoding='utf-8'))
            self.assertFalse(list((root / 'AI/UPLOAD').rglob('10_BOOK_PART_*.md')))

    def test_query_no_hits_does_not_replace_existing_question(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.fixture(root)
            self.run_build(root)
            with contextlib.redirect_stdout(io.StringIO()):
                ai.query_pack(root, 'equity')
            before = (root / 'AI/QUESTION_PACK.md').read_bytes()
            with self.assertRaises(ai.ExportError):
                ai.query_pack(root, 'zzzznotfound')
            self.assertEqual((root / 'AI/QUESTION_PACK.md').read_bytes(), before)

    def test_manual_source_selection_and_exclusions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            course = self.fixture(root)
            (root / 'AI_CONFIG.json').write_text(json.dumps({'exclude': ['**/Textbook.txt']}))
            self.run_build(root)
            with contextlib.closing(sqlite3.connect(root / 'AI/search.sqlite')) as db:
                source = db.execute('SELECT id FROM sources').fetchone()[0]
                self.assertEqual(db.execute('SELECT COUNT(*) FROM sources').fetchone()[0], 1)
            with contextlib.redirect_stdout(io.StringIO()):
                report = ai.query_pack(root, None, source=source)
            self.assertEqual(len(report['selected_chunks']), 1)

    def test_office_formats_and_unsupported_sources_are_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with zipfile.ZipFile(root / 'lecture.pptx', 'w') as z:
                z.writestr('ppt/slides/slide1.xml', '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:p><a:r><a:t>Leadership theory</a:t></a:r></a:p></p:sld>')
            with zipfile.ZipFile(root / 'notes.docx', 'w') as z:
                z.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Organizational behavior</w:t></w:r></w:p></w:body></w:document>')
            (root / 'old-format.doc').write_bytes(b'unsupported old format')
            result = self.run_build(root)
            self.assertEqual(result['unsupported'], 1)
            chunks = (root / 'AI/CHUNKS.jsonl').read_text(encoding='utf-8')
            self.assertIn('Leadership theory', chunks)
            self.assertIn('Organizational behavior', chunks)
            self.assertNotIn('unsupported old format', chunks)

    def test_exact_pdf_page_selection_not_prefix_matching(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'book.pdf').write_bytes(b'fixture')
            units = [('PDF page 12', 'target page text'), ('PDF page 120', 'wrong page text')]
            with patch('ai_export.extract_units', return_value=iter(units)):
                self.run_build(root)
            with contextlib.closing(sqlite3.connect(root / 'AI/search.sqlite')) as db:
                source = db.execute('SELECT id FROM sources').fetchone()[0]
            with contextlib.redirect_stdout(io.StringIO()):
                ai.query_pack(root, None, source=source, pages='12-12')
            content = (root / 'AI/QUESTION_PACK.md').read_text(encoding='utf-8')
            self.assertIn('target page text', content)
            self.assertNotIn('wrong page text', content)

    def test_sync_ai_flag_uses_existing_output_folder(self):
        import canvas_sync
        from test_sync import FakeCanvas
        with tempfile.TemporaryDirectory() as tmp:
            args = ['canvas_sync.py', '--base-url', 'https://canvas.example.edu', '--auth', 'token', '--course', '7', '--output', tmp, '--ai']
            with patch.object(sys, 'argv', args), patch('canvas_sync.get_token', return_value='test-token'), patch('canvas_sync.Canvas', return_value=FakeCanvas()), patch('ai_export.build', return_value={'failed': 0}) as build_mock, contextlib.redirect_stdout(io.StringIO()):
                code = canvas_sync.main()
            self.assertEqual(code, 0)
            build_mock.assert_called_once_with(Path(tmp).resolve(), pack_budget=24000)
            self.assertTrue((Path(tmp) / 'SYNC_REPORT.json').exists())

if __name__ == '__main__':
    unittest.main()
