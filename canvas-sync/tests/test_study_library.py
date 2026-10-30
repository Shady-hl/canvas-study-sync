import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.parse
import urllib.request
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ai_export
import resync_app as app
import source_access
import study_library as library
import study_setup
import study_update


class LibraryTests(unittest.TestCase):
    def prepare(self, root):
        root.mkdir(parents=True, exist_ok=True)
        (root / '.canvas-study-local.json').write_text('{"format":1,"purpose":"local-study"}')
        (root / 'AI_CONFIG.json').write_text('{"output_folder":"AI_READY"}')

    def fixture(self, root):
        self.prepare(root)
        model = library.StudyLibrary(root)
        return model, model.create_course('ECON101')

    def test_new_courses_use_stable_ids_without_colliding_with_canvas(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            model, course = self.fixture(root)
            self.assertEqual(course['id'], 'local-1')
            (root / 'Canvas--1').mkdir()
            (root / '.canvas-sync.json').write_text(json.dumps({'courses':{'1':'Canvas--1'}}))
            mapping = ai_export.course_mapping(root)
            self.assertEqual(mapping[course['folder']], 'local-1')
            self.assertEqual(mapping['Canvas--1'], '1')
            second = model.create_course('ECON101')
            self.assertNotEqual(second['folder'],course['folder'])
            import ed_sync
            targets = ed_sync.course_targets(root,[{'id':123,'code':'ECON101','status':'active'}])
            self.assertEqual(targets,{})  # Two sections require an explicit choice.

    def test_repeated_copy_reuses_identical_file_and_preserves_annotated_original(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve(); model, course = self.fixture(root)
            first = model.import_file(course['id'],'lecture.txt',io.BytesIO(b'Lecture'),7)
            again = model.import_file(course['id'],'lecture.txt',io.BytesIO(b'Lecture'),7)
            self.assertTrue(again['reused'])
            self.assertEqual(first['path'],again['path'])
            (root / first['path']).write_bytes(b'My annotations')
            update = model.import_file(course['id'],'lecture.txt',io.BytesIO(b'New lecture'),11)
            self.assertEqual((root / first['path']).read_bytes(),b'My annotations')
            self.assertEqual((root / update['path']).read_bytes(),b'New lecture')
            self.assertNotEqual(update['path'],first['path'])

    def test_interrupted_copy_and_bad_names_do_not_leave_partial_sources(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve(); model,course=self.fixture(root)
            folder=root/course['folder']/'Files'
            with self.assertRaises(library.LibraryError):
                model.import_file(course['id'],'lecture.txt',io.BytesIO(b'short'),20)
            for name in ('../outside.txt','sub/file.txt','sub\\file.txt','.env','bad\x00.txt'):
                with self.subTest(name=name), self.assertRaises(library.LibraryError):
                    model.import_file(course['id'],name,io.BytesIO(b'a'),1)
            with self.assertRaises(library.LibraryError):
                model.import_file(course['id'],'huge.pdf',io.BytesIO(),library.MAX_UPLOAD+1)
            self.assertEqual(list(folder.iterdir()),[])
            self.assertFalse(model.busy)

    def test_long_unicode_filename_keeps_the_document_extension(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve(); model,course=self.fixture(root)
            result=model.import_file(course['id'],'Lærebok'*50+'.pdf',io.BytesIO(b'pdf'),3)
            self.assertTrue(result['name'].endswith('.pdf'))
            self.assertLess(len(result['name'].encode('utf-8')),200)

    def test_symlinked_course_destination_is_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve(); model,course=self.fixture(root)
            folder=root/course['folder']/'Files'; folder.rmdir()
            outside=root.parent/(root.name+'-outside');outside.mkdir()
            try:
                try:folder.symlink_to(outside,target_is_directory=True)
                except OSError:self.skipTest('Symlink creation is unavailable.')
                with self.assertRaises(library.LibraryError):
                    model.import_file(course['id'],'lecture.txt',io.BytesIO(b'hi'),2)
                self.assertEqual(list(outside.iterdir()),[])
            finally:
                if folder.is_symlink():folder.unlink()
                outside.rmdir()

    def test_local_course_mapping_and_sources_survive_a_portable_copy(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve(); model,course=self.fixture(root)
            original=model.import_file(course['id'],'lecture.txt',io.BytesIO(b'Opportunity cost is the value of the next best alternative.'),59)
            with contextlib.redirect_stdout(io.StringIO()):
                current=study_update.refresh(root)
                package=study_setup.make_portable(root)
            self.assertEqual(current['source_count'],1)
            with zipfile.ZipFile(package) as archive:
                self.assertIn('Canvas-Archive/.study-courses.json',archive.namelist())
                self.assertIn('Canvas-Archive/ASSISTANT_CONTEXT/REFRESH_STATUS.json',archive.namelist())
                moved=root/'Moved';archive.extractall(moved)
            copied=moved/'Canvas-Archive'
            self.assertEqual(ai_export.course_mapping(copied)[course['folder']],course['id'])
            self.assertEqual((copied/original['path']).read_bytes(),(root/original['path']).read_bytes())
            self.assertEqual(library.StudyLibrary(copied).snapshot()['study']['sources'],1)

    def test_local_build_never_receives_canvas_or_ed_credentials(self):
        import time
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve();model,course=self.fixture(root)
            model.import_file(course['id'],'lecture.txt',io.BytesIO(b'Course text'),11)
            tool=root/'Tool';tool.mkdir()
            (tool/'study_update.py').write_text("import os,sys\nassert '--sync' not in sys.argv\nassert '--delay' not in sys.argv\nassert '--ed' not in sys.argv\nassert not any(k in os.environ for k in ('CANVAS_TOKEN','CANVAS_SESSION','CANVAS_OAUTH_SESSION','ED_API_TOKEN'))\n")
            job=app.ResyncJob(root)
            with patch.object(app,'TOOL',tool),patch.object(job,'python_with_dependencies',return_value=Path(sys.executable)),patch.dict(os.environ,CANVAS_TOKEN='fixture-canvas',ED_API_TOKEN='fixture-ed',CANVAS_OAUTH_SESSION='fixture-oauth'):
                job.start_local()
                deadline=time.monotonic()+10
                while job.phase in app.ACTIVE and time.monotonic()<deadline:time.sleep(.01)
            self.assertEqual(job.phase,'done',job.snapshot()['log'])

    def test_damaged_optional_report_does_not_break_the_home_screen(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve();model,_=self.fixture(root)
            (root/'ED_SYNC_REPORT.json').write_text('{"courses":[{"threads":17}]}')
            (root/'SYNC_REPORT.json').write_text('{"issues":17}')
            snapshot=model.snapshot()
            self.assertEqual(snapshot['ed']['threads'],0)
            self.assertEqual(snapshot['canvas']['notices'],0)

    def test_unreadable_report_does_not_claim_an_import_never_happened(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve();model,_=self.fixture(root)
            (root/'ED_SYNC_REPORT.json').write_text('{broken fixture-private-value')
            snapshot=model.snapshot()
            self.assertEqual(snapshot['ed']['status'],'unavailable')
            self.assertNotIn('fixture-private-value',json.dumps(snapshot))

    def test_open_targets_are_fixed_and_cannot_escape_the_archive(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve();model,_=self.fixture(root)
            for target in ('../../outside','file:///outside','https://example.invalid',None,[]):
                with self.subTest(target=target),self.assertRaises(library.LibraryError):model.open_target(target)
            self.assertEqual(model.open_target('archive'),root)


class RecoveryTests(unittest.TestCase):
    def test_cloud_failure_identifies_the_file_and_retains_the_committed_snapshot(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve();LibraryTests().prepare(root)
            folder=root/'ECON101--1/Files';folder.mkdir(parents=True)
            source=folder/'lecture.pdf';source.write_bytes(b'Unavailable cloud source')
            assistant=root/'ASSISTANT_CONTEXT';assistant.mkdir()
            committed=b'{"published_at":"2026-10-25T10:00:00Z","source_count":1}'
            (assistant/'CURRENT.json').write_bytes(committed)
            (root/'ED_SYNC_REPORT.json').write_text(json.dumps({'status':'complete','finished_at':'2026-10-29T10:00:00Z','courses':[{'threads':[{'replies_exported':2}]}]}))
            original_open=Path.open
            def opened(path,*args,**kwargs):
                if path==source:raise TimeoutError('https://private.invalid/fixture-secret')
                return original_open(path,*args,**kwargs)
            with patch.object(Path,'open',opened),self.assertRaises(source_access.SourceAccessError):
                study_update.refresh(root)
            self.assertEqual((assistant/'CURRENT.json').read_bytes(),committed)
            status=json.loads((root/source_access.STATUS).read_text())
            self.assertEqual(status['problem']['source'],'ECON101--1/Files/lecture.pdf')
            self.assertEqual(status['problem']['kind'],'cloud')
            self.assertNotIn('fixture-secret',json.dumps(status))
            snapshot=library.StudyLibrary(root).snapshot()
            self.assertEqual(snapshot['ed']['status'],'complete')
            self.assertEqual(snapshot['study']['at'],'2026-10-25T10:00:00Z')
            self.assertEqual(snapshot['study']['attempt']['state'],'failed')
            self.assertTrue(snapshot['available']['source-folder'])
            self.assertEqual(library.StudyLibrary(root).open_target('source-folder'),folder)

    def test_success_clears_previous_recovery_advice_without_losing_source_counts(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve();model,course=LibraryTests().fixture(root)
            model.import_file(course['id'],'lecture.txt',io.BytesIO(b'Accounting source text.'),23)
            source_access.record(root,'failed',source_access.timestamp(),error=source_access.SourceAccessError(root/'old.pdf',TimeoutError()))
            with contextlib.redirect_stdout(io.StringIO()):study_update.refresh(root)
            snapshot=library.StudyLibrary(root).snapshot()
            self.assertEqual(snapshot['study']['attempt']['state'],'complete')
            self.assertNotIn('problem',snapshot['study']['attempt'])
            self.assertEqual(snapshot['study']['sources'],1)


class LibraryWindowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name).resolve()
        if hasattr(app.ResyncJob,'prepare_local'):
            self.server=app.LocalServer(self.root)
        else:
            LibraryTests().prepare(self.root);self.server=app.LocalServer(self.root)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join(timeout=3);self.temp.cleanup()

    def request(self,route,body=None,*,origin=None,raw=False,extra=None):
        headers={'Origin':origin or self.server.origin,'X-Canvas-Client':self.server.key}
        if raw:headers.update({'Content-Type':'application/octet-stream',**(extra or {})});data=body
        else:headers['Content-Type']='application/json';data=json.dumps(body or {}).encode()
        return self.opener.open(urllib.request.Request(self.server.url+route,data=data,headers=headers),timeout=10)

    def test_create_copy_and_build_work_without_canvas_credentials(self):
        with self.request('library/course',{'name':'ECON101'}) as response:course=json.load(response)
        self.assertFalse((self.root/'My course--0').exists())
        with self.request('library/upload',b'Course notes',raw=True,extra={'X-Study-Course':course['id'],'X-Study-Name':urllib.parse.quote('Lecture 1.txt')}) as response:result=json.load(response)
        self.assertEqual((self.root/result['path']).read_bytes(),b'Course notes')
        with patch.object(self.server.job,'start_local') as started:
            with self.request('library/build',{'full':False}) as response:self.assertEqual(response.status,202)
            started.assert_called_once_with(False)
        with patch.object(app,'open_study_folder') as opened:
            with self.request('library/open',{'target':'archive'}) as response:self.assertTrue(json.load(response)['opened'])
            opened.assert_called_once_with(self.root)

    def test_foreign_origins_and_active_runs_cannot_import_files(self):
        for route in ('library/course','library/upload','library/build','library/open'):
            with self.subTest(route=route),self.assertRaises(urllib.error.HTTPError) as caught:
                self.request(route,{},origin='https://outside.invalid')
            self.assertEqual(caught.exception.code,403)
        self.server.job.phase='syncing'
        try:
            with self.assertRaises(urllib.error.HTTPError) as caught:self.request('library/course',{'name':'Course'})
            self.assertEqual(caught.exception.code,409)
        finally:self.server.job.phase='ready'

    def test_window_contains_the_library_without_external_assets(self):
        with self.opener.open(self.server.url) as response:
            text=response.read().decode();policy=response.headers['Content-Security-Policy']
        self.assertIn('id="study-home-title"',text)
        self.assertIn('id="study-file-input"',text)
        self.assertNotIn('__STUDY_HOME__',text)
        self.assertIn("connect-src 'self'",policy)
        self.assertIn('nonce="'+self.server.key+'"',text)
