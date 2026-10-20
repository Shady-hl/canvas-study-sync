import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from canvas_sync import APIError, Archive
from test_sync import FakeCanvas, filemeta


class HiddenCanvas(FakeCanvas):
    def all(self, path):
        if path.endswith('/files'):
            f = filemeta()
            f.update(hidden=True, hidden_for_user=True, locked_for_user=self.locked)
            yield f
        else:
            yield from super().all(path)


class HiddenFileTests(unittest.TestCase):
    def test_hidden_linked_file_downloaded_and_local_link_rewritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, api = Path(tmp), HiddenCanvas()
            a = Archive(api, root)
            a.course({'id': 7, 'name': 'Example'})
            a.finish()
            self.assertEqual(len(api.downloads), 1)
            row = a.report['file_results'][0]
            self.assertTrue(row['hidden_for_user'])
            self.assertEqual(row['status'], 'downloaded')
            self.assertTrue((root / row['path']).is_file())
            page = next(root.glob('*/Pages/*.html')).read_text()
            self.assertIn('../Files/101--Lecture.pdf', page)
            self.assertIn('downloaded', (root / 'SYNC_REPORT.html').read_text())
            self.assertTrue(any(r['kind'] == 'Pages' for r in a.report['text_exports']))

    def test_hidden_and_locked_file_still_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            api = HiddenCanvas()
            api.locked = True
            a = Archive(api, Path(tmp))
            a.course({'id': 7, 'name': 'Example'})
            self.assertEqual(api.downloads, [])
            self.assertEqual(a.report['file_results'][0]['status'], 'locked_or_unpublished')
            self.assertNotIn('7:101', a.state['files'])

    def test_hidden_file_server_denial_not_marked_saved(self):
        class Denied(HiddenCanvas):
            def download(self, *args):
                raise APIError(403)
        with tempfile.TemporaryDirectory() as tmp:
            a = Archive(Denied(), Path(tmp))
            a.course({'id': 7, 'name': 'Example'})
            self.assertNotIn('7:101', a.state['files'])
            self.assertEqual(a.report['file_results'][0]['status'], 'download_failed')
            self.assertTrue(any('HTTP 403' in r['reason'] for r in a.report['issues']))

    def test_hidden_assignment_attachment_absent_from_files_list(self):
        class AssignmentFile(FakeCanvas):
            def all(self, path):
                if path.endswith('/assignments'):
                    yield {'id': 20, 'name': 'Homework', 'description': '<a href="/courses/7/files/202/download">Exercise</a><img src="/courses/7/files/203/preview">'}
                else:
                    yield from super().all(path)
            def get(self, path):
                obj = super().get(path)
                if '/files/' in path:
                    obj.update(hidden_for_user=True, locked_for_user=False)
                return obj
        with tempfile.TemporaryDirectory() as tmp:
            root, api = Path(tmp), AssignmentFile()
            a = Archive(api, root)
            a.course({'id': 7, 'name': 'Example'})
            self.assertEqual(len(api.downloads), 3)
            self.assertEqual({r['file_id'] for r in a.report['file_results']}, {'101', '202', '203'})
            body = next(root.glob('*/Assignments/*.html')).read_text()
            self.assertIn('../Files/202--Lecture.pdf', body)
            self.assertIn('../Files/203--Lecture.pdf', body)

    def test_fix_keeps_previous_download_and_fetches_missing_hidden_file(self):
        class TwoFiles(HiddenCanvas):
            def all(self, path):
                yield from super().all(path)
                if path.endswith('/files'):
                    yield filemeta(102)
        with tempfile.TemporaryDirectory() as tmp:
            root, api = Path(tmp), TwoFiles()
            a = Archive(api, root)
            a.save_file('7', Path('Example--7'), filemeta(102))
            a.state.setdefault('courses', {})['7'] = 'Example--7'
            a.save_state()
            self.assertEqual(len(api.downloads), 1)
            a = Archive(api, root)
            a.course({'id': 7, 'name': 'Example'})
            self.assertEqual(len(api.downloads), 2)
            self.assertEqual(a.report['downloaded'], 1)
            self.assertEqual(a.report['unchanged_files'], 1)
            by_id = {r['file_id']: r['status'] for r in a.report['file_results']}
            self.assertEqual(by_id, {'101': 'downloaded', '102': 'unchanged'})

    def test_missing_file_metadata_is_reported(self):
        class MetadataDenied(FakeCanvas):
            def all(self, path):
                if not path.endswith('/files'):
                    yield from super().all(path)
            def get(self, path):
                if '/files/' in path:
                    raise APIError(403)
                return super().get(path)
        with tempfile.TemporaryDirectory() as tmp:
            a = Archive(MetadataDenied(), Path(tmp))
            a.course({'id': 7, 'name': 'Example'})
            a.finish()
            self.assertEqual(a.report['file_results'][0]['status'], 'metadata_failed')
            saved = json.loads((Path(tmp) / 'SYNC_REPORT.json').read_text())
            self.assertEqual(saved['file_results'][0]['file_id'], '101')

if __name__ == '__main__':
    unittest.main()
