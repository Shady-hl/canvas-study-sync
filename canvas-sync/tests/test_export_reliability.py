import contextlib
import io
import logging
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import ai_export as ai
from canvas_sync import Archive


class ReliabilityTests(unittest.TestCase):
    def test_pdf_padding_compacted_without_dropping_text_or_normal_indent(self):
        text = ' ' * 1200 + 'Assets' + ' ' * 800 + 'Liabilities\n' + '\n' * 80 + '    if ready:\n        print(value)\n'
        compact = ai.compact_pdf_spacing(text)
        self.assertEqual(compact.split(), text.split())
        self.assertIn('    if ready:\n        print(value)', compact)
        self.assertLess(len(compact), 150)

    def test_windows_open_directory_fallback_and_rollback(self):
        for fail_install in (False, True):
            with self.subTest(fail_install=fail_install), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                target, stage, backup = root / 'AI', root / '.new', root / '.old'
                target.mkdir()
                stage.mkdir()
                (target / 'old.txt').write_text('previous')
                (stage / 'new.txt').write_text('updated')
                rename = Path.rename
                def simulate(path, dest):
                    if path == target or (fail_install and path == stage / 'new.txt'):
                        raise PermissionError('Windows open handle')
                    return rename(path, dest)
                with patch('ai_export.os.name', 'nt'), patch.object(Path, 'rename', simulate):
                    if fail_install:
                        with self.assertRaises(ai.ExportError):
                            ai.replace_output(stage, target, backup)
                        self.assertEqual((target / 'old.txt').read_text(), 'previous')
                        self.assertEqual((stage / 'new.txt').read_text(), 'updated')
                    else:
                        ai.replace_output(stage, target, backup)
                        self.assertEqual((target / 'new.txt').read_text(), 'updated')
                        self.assertEqual((backup / 'old.txt').read_text(), 'previous')

    def test_real_rotated_pdf_text_survives_with_page_locator(self):
        from pypdf import PdfWriter
        from pypdf.generic import NameObject, DictionaryObject, DecodedStreamObject
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'rotation.pdf'
            writer = PdfWriter()
            page = writer.add_blank_page(612, 792)
            font = DictionaryObject({NameObject('/Type'): NameObject('/Font'),
                                     NameObject('/Subtype'): NameObject('/Type1'),
                                     NameObject('/BaseFont'): NameObject('/Helvetica')})
            page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'):
                DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
            stream = DecodedStreamObject()
            stream.set_data(b'BT /F1 12 Tf 1 0 0 1 50 700 Tm (UPRIGHT material about assets and liabilities.) Tj ET\n'
                            b'BT /F1 12 Tf 0 1 -1 0 300 100 Tm (ROTATED learning objective must survive.) Tj ET')
            page[NameObject('/Contents')] = writer._add_object(stream)
            writer.add_blank_page(612, 792)
            writer.write(path)
            warnings = []
            units = list(ai.extract_units(path, 2000, warnings))
            self.assertEqual(units[0][0], 'PDF page 1')
            self.assertIn('UPRIGHT', units[0][1])
            self.assertIn('ROTATED learning objective must survive', units[0][1])
            self.assertTrue(any('plain extraction' in w for w in warnings))
            self.assertTrue(any('Sparse/image-only pages' in w for w in warnings))

    def test_pdf_layout_error_recovers_and_restores_logger(self):
        page = Mock()
        page.extract_text.side_effect = [ValueError('bad layout'), 'Recovered paragraph']
        logger = logging.getLogger('pypdf')
        before = (logger.propagate, list(logger.handlers))
        warnings = []
        self.assertEqual(ai.pdf_page_text(page, 3, warnings), 'Recovered paragraph')
        self.assertEqual((logger.propagate, logger.handlers), before)
        self.assertTrue(any('PDF page 3' in w for w in warnings))

    def test_notebooklm_subject_contains_book_and_course_without_binary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            course = root / 'Subject--7'
            (course / 'Pages').mkdir(parents=True)
            (course / 'Books').mkdir()
            (course / 'Pages/lesson.txt').write_text('LECTURECONTENT', encoding='utf-8')
            (course / 'Books/book.txt').write_text('BOOKCONTENT', encoding='utf-8')
            (course / 'Pages/scan.png').write_bytes(b'original image')
            with contextlib.redirect_stdout(io.StringIO()):
                ai.build(root)
            folder = root / 'AI/NOTEBOOKLM/Subject--7--7'
            files = list(folder.iterdir())
            self.assertTrue(all(p.suffix == '.md' for p in files))
            text = '\n'.join(p.read_text(encoding='utf-8') for p in files)
            self.assertIn('LECTURECONTENT', text)
            self.assertIn('BOOKCONTENT', text)
            self.assertIn('scan.png', text)
            self.assertIn('Local book folders included: 1', text)
            with contextlib.redirect_stdout(io.StringIO()):
                counts = ai.build(root)
            self.assertEqual(counts['cached'], 2)

    def test_notices_scoped_to_course_and_reset_after_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Archive(Mock(base='https://example.edu'), Path(tmp))
            def fail(course):
                archive.issue('pages', 'HTTP 404')
                raise ValueError('fixture interruption')
            archive._course = fail
            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(ValueError):
                    archive.course({'id': 7})
                archive.issue('run', 'Stopped')
            self.assertEqual(archive.report['issues'][0]['course_id'], '7')
            self.assertIsNone(archive.report['issues'][1]['course_id'])


if __name__ == '__main__':
    unittest.main()
