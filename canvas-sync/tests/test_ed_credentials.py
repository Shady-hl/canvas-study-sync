import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ed_credentials as credentials
import study_update
import ed_sync


class Store:
    def __init__(self): self.values = {}
    def get_password(self, service, account): return self.values.get((service, account))
    def set_password(self, service, account, token): self.values[(service, account)] = token
    def delete_password(self, service, account): del self.values[(service, account)]


class CredentialTests(unittest.TestCase):
    def test_save_load_replace_forget_are_partitioned_by_region(self):
        store = Store()
        with patch('ed_credentials.backend', return_value=store):
            credentials.save('eu', 'first-fixture-token')
            credentials.save('us', 'other-fixture-token')
            credentials.save('eu', 'replacement-fixture-token')
            self.assertEqual(credentials.load('eu'), 'replacement-fixture-token')
            credentials.forget('eu')
            credentials.forget('eu')
            self.assertEqual(credentials.load('eu'), '')
            self.assertEqual(credentials.load('us'), 'other-fixture-token')

    def test_store_errors_hide_exception_values_and_never_fall_back_to_files(self):
        with tempfile.TemporaryDirectory() as temp, patch('ed_credentials.backend', side_effect=RuntimeError('fixture-secret-token')):
            for operation, args in [(credentials.load, ('eu',)), (credentials.save, ('eu', 'fixture-token')), (credentials.forget, ('eu',))]:
                with self.assertRaises(credentials.CredentialError) as caught:
                    operation(*args)
                self.assertNotIn('fixture-secret-token', str(caught.exception))
            self.assertEqual(list(Path(temp).iterdir()), [])

    def test_configured_third_party_backend_cannot_receive_the_token(self):
        import types
        from unittest.mock import Mock
        for platform, module_name, class_name in [('darwin','macOS','Keyring'),('win32','Windows','WinVaultKeyring'),('linux','SecretService','Keyring')]:
            store = Store()
            package = types.ModuleType('keyring')
            package.get_keyring = Mock(side_effect=AssertionError('Untrusted plugin must not be selected'))
            backends = types.ModuleType('keyring.backends')
            selected = types.ModuleType('keyring.backends.' + module_name)
            setattr(selected,class_name,lambda:store)
            modules = {'keyring':package,'keyring.backends':backends,'keyring.backends.'+module_name:selected}
            with patch.dict('sys.modules',modules), patch('sys.platform',platform):
                credentials.save('eu','fixture-token')
                self.assertEqual(credentials.load('eu'),'fixture-token')
                package.get_keyring.assert_not_called()

    def test_saved_token_is_used_only_for_ed_and_remember_follows_successful_auth(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            events = []
            def imported(root, token, region, **kwargs):
                self.assertEqual(token, 'saved-fixture-token')
                events.append('import')
                return {'status':'complete','requests':1}
            def saved(region, token):
                self.assertEqual(events, ['import'])
                self.assertEqual(token, 'saved-fixture-token')
                events.append('save')
            with patch('ed_credentials.load', return_value='saved-fixture-token'), \
                 patch('ed_credentials.save', side_effect=saved), patch('ed_sync.sync', side_effect=imported), \
                 patch('study_update.refresh', return_value={'study_context':{},'timetable':{}}), \
                 patch.dict('os.environ', {}, clear=True), contextlib.redirect_stdout(io.StringIO()):
                result = study_update.main(['--archive',temp,'--ed-only','--ed-use-saved','--ed-remember'])
            self.assertEqual(result,0)
            self.assertEqual(events,['import','save'])
            self.assertFalse(any('token' in p.name.lower() for p in root.iterdir()))

    def test_failed_auth_does_not_save_a_token(self):
        with tempfile.TemporaryDirectory() as temp, \
             patch.dict('os.environ', ED_API_TOKEN='invalid-fixture-token'), \
             patch('ed_sync.sync', return_value={'status':'failed'}), \
             patch('ed_credentials.save') as saved, \
             patch('study_update.refresh', return_value={'study_context':{},'timetable':{}}), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(study_update.main(['--archive',temp,'--ed-only','--ed-remember']),2)
            saved.assert_not_called()

    def test_missing_saved_token_stops_background_run_without_a_prompt_or_context_build(self):
        with tempfile.TemporaryDirectory() as temp, \
             patch.dict('os.environ', {}, clear=True), patch('ed_credentials.load', return_value=''), \
             patch('getpass.getpass') as prompt, patch('study_update.refresh') as refresh, \
             contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(study_update.main(['--archive',temp,'--ed-only','--ed-use-saved']),1)
            prompt.assert_not_called(); refresh.assert_not_called()

    def test_local_folder_code_can_be_matched_without_canvas_credentials(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); (root/'ECON101--1/Files').mkdir(parents=True)
            targets = ed_sync.course_targets(root,[{'id':12,'code':'ECON 101','status':'active'}])
            self.assertEqual(targets['12'],('1',(root/'ECON101--1').resolve()))
