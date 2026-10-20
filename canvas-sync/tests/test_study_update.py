import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import study_update as update


class StudyUpdateTests(unittest.TestCase):
    def fixture(self, root):
        folder = root / 'Course--7/Assignments'
        folder.mkdir(parents=True)
        path = folder / '12.html'
        path.write_text('<h1>Practice</h1><p>due_at: 2026-10-21T12:00:00Z</p>', encoding='utf-8')
        (root / 'Course--7/large.pdf').write_bytes(b'x' * (update.SMALL_FILE + 1))
        (root / 'AI_CONFIG.json').write_text('{"output_folder":"AI_READY"}')
        return path

    def build(self, root):
        target = root / 'AI_READY'
        target.mkdir(exist_ok=True)
        (target / '.canvas-ai-owned.json').write_text('{}')
        (target / 'search.sqlite').write_bytes(b'fake index')
        return {'cached': 2, 'failed': 0, 'visual_only': 0, 'unsupported': 0}

    def schedule(self, start='2026-10-21T09:00:00+02:00'):
        return {'events': [{'id': 'class-1', 'start': start, 'end': '2026-10-21T10:00:00+02:00',
                            'summary': 'Math', 'url': 'https://private.invalid/secret', 'notes': 'private'}],
                'retrieved_at': '2026-10-18T12:00:00Z', 'refresh_failed': False}

    def run_refresh(self, root, schedule=None):
        with patch('study_update.pipeline_identity', return_value={'pipeline': 'test'}), \
             patch('ai_export.build', side_effect=self.build) as build, \
             patch('timeedit_context.prepare_schedule', return_value=schedule or self.schedule()), \
             patch('timeedit_context.load_schedule', return_value=schedule or self.schedule()), \
             contextlib.redirect_stdout(io.StringIO()):
            current = update.refresh(root)
            return current, build.call_count

    def test_unchanged_skips_build_and_reuses_large_file_hash(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.fixture(root)
            first, calls = self.run_refresh(root)
            self.assertEqual(calls, 1)
            self.assertTrue(first['baseline_created'])
            second, calls = self.run_refresh(root)
            self.assertEqual(calls, 0)
            self.assertEqual(second['changes_since_previous_refresh'], [])
            self.assertEqual(second['scan']['large_hashes_reused'], 1)
            self.assertEqual(second['assignments'][0]['submission_status'], 'unknown')
            self.assertEqual(second['assignments'][0]['due_at'], '2026-10-21T12:00:00Z')

    def test_small_same_size_same_mtime_edit_is_detected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = self.fixture(root)
            self.run_refresh(root)
            before = path.stat()
            path.write_text(path.read_text().replace('Practice', 'Exercise'))
            os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
            current, calls = self.run_refresh(root)
            self.assertEqual(calls, 1)
            self.assertEqual(current['changes_since_previous_refresh'][0]['change'], 'changed')

    def test_timetable_change_updates_small_handoff_without_full_build(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.fixture(root)
            self.run_refresh(root)
            current, calls = self.run_refresh(root, self.schedule('2026-10-21T08:00:00+02:00'))
            self.assertEqual(calls, 0)
            self.assertEqual(current['changes_since_previous_refresh'],
                             [{'kind': 'class', 'key': 'class-1', 'change': 'changed'}])
            self.assertNotIn('private.invalid', json.dumps(current))

    def test_failed_build_retains_last_committed_snapshot(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = self.fixture(root)
            self.run_refresh(root)
            committed = (root / update.FOLDER / 'CURRENT.json').read_bytes()
            path.write_text('changed')
            with patch('ai_export.build', side_effect=OSError('test failure')):
                with self.assertRaises(OSError):
                    update.refresh(root)
            self.assertEqual((root / update.FOLDER / 'CURRENT.json').read_bytes(), committed)

    def test_generated_handoff_and_profile_do_not_become_teaching_sources(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.fixture(root)
            (root / update.FOLDER).mkdir()
            (root / update.FOLDER / 'PROFILE.md').write_text('preferences')
            (root / 'ASSISTANT_START_HERE.md').write_text('entry point')
            files, _, _ = update.inventory(root)
            self.assertEqual(len(files), 2)

    def test_missing_source_is_reported_for_review(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = self.fixture(root)
            self.run_refresh(root)
            path.unlink()
            current, _ = self.run_refresh(root)
            self.assertEqual(current['changes_since_previous_refresh'][0]['change'], 'missing')

    def test_upstream_failure_does_not_publish_fresh_canvas_status(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.fixture(root)
            with patch('subprocess.run') as run, patch('study_update.refresh') as refresh, \
                 contextlib.redirect_stderr(io.StringIO()):
                run.return_value.returncode = 1
                self.assertEqual(update.main(['--sync', '--archive', str(root)]), 1)
                refresh.assert_not_called()

    def test_corrupt_source_manifest_cannot_silently_clear_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.fixture(root)
            first, _ = self.run_refresh(root)
            folder = root / update.FOLDER
            committed = (folder / 'CURRENT.json').read_bytes()
            (folder / first['source_manifest']).write_text('{}')
            with self.assertRaises(ValueError):
                self.run_refresh(root)
            self.assertEqual((folder / 'CURRENT.json').read_bytes(), committed)

    def test_advanced_export_flags_keep_existing_semantics(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch('ai_export.main', return_value=0) as export:
                self.assertEqual(update.main(['--archive', str(root), '--query', 'working capital']), 0)
                export.assert_called_once_with(['--archive', str(root.resolve()), '--query', 'working capital'])


if __name__ == '__main__':
    unittest.main()
