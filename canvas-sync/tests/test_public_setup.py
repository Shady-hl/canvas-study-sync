import contextlib
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import archive_config
import resync_app as app
from canvas_sync import SyncError, AuthError, Archive
from test_sync import Response, client
import study_setup


class PublicSetupTests(unittest.TestCase):
    def test_unauthorized_api_response_stops_even_without_a_challenge_header(self):
        c=client(Response(status=401),Response(b'{}'))
        with tempfile.TemporaryDirectory() as temp:
            archive=Archive(c,Path(temp))
            with self.assertRaises(AuthError):archive.attempt('course',lambda:c.get('/api/v1/test'))
            self.assertEqual(c.request_count,1)

    def test_no_default_institution_and_existing_site_cannot_be_changed(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            with patch.dict('os.environ', {}, clear=True), self.assertRaises(SyncError):
                archive_config.canvas_origin(root)
            self.assertEqual(archive_config.canvas_origin(root,'https://canvas.example.edu'),'https://canvas.example.edu')
            (root/'.canvas-sync.json').write_text('{"base_url":"https://canvas.example.edu"}')
            self.assertEqual(archive_config.canvas_origin(root),'https://canvas.example.edu')
            with self.assertRaises(SyncError):archive_config.canvas_origin(root,'https://other.example.edu')

    def test_clean_launcher_opens_setup_without_an_archive_marker(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'not yet created'
            with patch.object(app,'launch',return_value=0) as launch:
                self.assertEqual(app.main(['--archive',str(root),'--launch','--no-browser']),0)
            launch.assert_called_once_with(root.resolve(),False,None,'token')
            self.assertFalse(root.exists())

    def test_setup_preserves_unrelated_files_and_rejects_wrong_host(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            (root/'keep.txt').write_text('private notes')
            job=app.ResyncJob(root,base_url='https://canvas.example.edu',auth='token')
            with self.assertRaises(app.SetupError):job.start('TEST_TOKEN')
            self.assertEqual((root/'keep.txt').read_text(),'private notes')
            self.assertEqual(job.phase,'ready')

    def test_token_flow_uses_the_selected_host_and_never_the_session_environment(self):
        import sys
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'empty archive'
            tool=Path(temp)/'fixture tool'
            tool.mkdir()
            (tool/'study_update.py').write_text('''import os,sys
assert os.environ.get('CANVAS_TOKEN')=='TEST_LOCAL_TOKEN'
assert 'CANVAS_SESSION' not in os.environ
assert sys.argv[sys.argv.index('--base-url')+1]=='https://canvas.example.edu'
assert sys.argv[sys.argv.index('--auth')+1]=='token'
print('fixture completed',flush=True)
''')
            job=app.ResyncJob(root,auth='token')
            with patch.object(app,'TOOL',tool),patch.object(job,'python_with_dependencies',return_value=Path(sys.executable)):
                job.start('TEST_LOCAL_TOKEN',base_url='https://canvas.example.edu',auth='token')
                for _ in range(200):
                    if job.phase not in app.ACTIVE:break
                    time.sleep(.01)
            self.assertEqual(job.phase,'done',job.snapshot()['log'])
            self.assertEqual(job.cookie,'')

    def test_archive_launchers_get_a_source_only_tool_copy(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            study_setup.write_shortcuts(root)
            tool=root/'canvas-sync'
            self.assertTrue((tool/'archive_config.py').is_file())
            self.assertTrue((tool/'.canvas-sync-tool.json').is_file())
            self.assertFalse((tool/'.venv-macos').exists())
            self.assertFalse((tool/'__pycache__').exists())
            self.assertTrue((tool/'LICENSE.md').is_file())


if __name__=='__main__':unittest.main()
