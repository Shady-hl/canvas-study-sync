import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.parse as U
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import canvas_oauth as oauth
import canvas_sync as sync
import resync_app as app


def config_data():
    return dict(canvas_origin='https://canvas.example.edu', client_id='12345',
                client_secret='SYNTHETIC_CLIENT_SECRET', scopes=['url:GET|/api/v1/courses'])


def token_data():
    return dict(access_token='SYNTHETIC_ACCESS', refresh_token='SYNTHETIC_REFRESH', expires_in=3600, token_type='Bearer')


class OAuthTests(unittest.TestCase):
    def setUp(self):
        self.config = oauth.Config.from_data(config_data())
        self.flow = oauth.Flow(self.config)

    def callback(self, url, **changes):
        state = U.parse_qs(U.urlsplit(url).query)['state'][0]
        values = dict(code='SYNTHETIC_CODE', state=state)
        values.update(changes)
        return self.config.canvas_origin + '/login/oauth2/auth?' + U.urlencode(values)

    def test_authorization_url_has_read_scopes_and_no_client_secret(self):
        url = self.flow.begin()
        values = U.parse_qs(U.urlsplit(url).query)
        self.assertEqual(values['redirect_uri'], [oauth.REDIRECT_URI])
        self.assertEqual(values['scope'], list(self.config.scopes))
        self.assertNotIn(self.config.client_secret, url)
        self.assertNotIn('SYNTHETIC_CLIENT_SECRET', repr(self.config))

    def test_state_is_bound_to_one_flow_and_used_once(self):
        url = self.flow.begin()
        with patch.object(oauth, 'token_request', return_value=token_data()) as exchange:
            session = self.flow.finish(self.callback(url))
            self.assertEqual(session.bearer(), 'SYNTHETIC_ACCESS')
            with self.assertRaises(oauth.OAuthError):
                self.flow.finish(self.callback(url))
            self.assertEqual(exchange.call_count, 1)

    def test_wrong_origin_state_duplicate_fields_and_expiry_never_exchange(self):
        with patch.object(oauth, 'token_request') as exchange:
            for alteration in ('origin', 'state', 'duplicate', 'expired', 'fragment', 'path'):
                with self.subTest(alteration=alteration):
                    url = self.flow.begin()
                    response = self.callback(url)
                    if alteration == 'origin': response = response.replace('canvas.example.edu', 'evil.invalid')
                    if alteration == 'state': response = self.callback(url, state='WRONG_STATE')
                    if alteration == 'duplicate': response += '&code=ANOTHER_CODE'
                    if alteration == 'expired': self.flow.pending = (self.flow.pending[0], time.monotonic() - 1)
                    if alteration == 'fragment': response += '#private'
                    if alteration == 'path': response = response.replace('/login/oauth2/auth', '/wrong')
                    with self.assertRaises(oauth.OAuthError): self.flow.finish(response)
            exchange.assert_not_called()

    def test_new_sign_in_invalidates_older_response(self):
        first = self.flow.begin()
        self.flow.begin()
        with patch.object(oauth, 'token_request') as exchange, self.assertRaises(oauth.OAuthError):
            self.flow.finish(self.callback(first))
        exchange.assert_not_called()

    def test_refresh_is_before_expiry_and_reuses_refresh_token_when_not_rotated(self):
        session = oauth.Session(self.config, token_data())
        session.expires_at = time.time() + 30
        updated = dict(access_token='SYNTHETIC_NEW_ACCESS', expires_in=3600)
        with patch.object(oauth, 'token_request', return_value=updated) as refresh:
            self.assertEqual(session.bearer(), updated['access_token'])
            self.assertEqual(session.bearer(), updated['access_token'])
            refresh.assert_called_once_with(self.config, dict(grant_type='refresh_token', refresh_token='SYNTHETIC_REFRESH'))
        self.assertEqual(session.refresh_token, 'SYNTHETIC_REFRESH')

    def test_invalid_expiry_or_credentials_fail_closed(self):
        for value in (0, -1, float('inf'), float('nan'), 10**9, 'invalid'):
            with self.subTest(value=value), self.assertRaises(oauth.OAuthError):
                oauth.Session(self.config, dict(token_data(), expires_in=value))
        for access in ('', 'token\r\ninjected', None):
            with self.assertRaises(oauth.OAuthError):
                oauth.Session(self.config, dict(token_data(), access_token=access))

    def test_payload_cannot_move_credential_to_another_canvas_origin(self):
        payload = oauth.Session(self.config, token_data()).payload()
        with self.assertRaises(oauth.OAuthError): oauth.Session.from_payload(payload, 'https://other.example.edu')
        with self.assertRaises(sync.AuthError): sync.Canvas('https://other.example.edu', payload, auth='oauth')
        self.assertNotIn('SYNTHETIC', repr(oauth.Session.from_payload(payload, self.config.canvas_origin)))

    def test_token_post_is_exact_origin_and_errors_do_not_reveal_codes_or_secrets(self):
        with patch.object(oauth.R, 'build_opener') as build:
            build.return_value.open.side_effect = urllib.error.HTTPError('https://private.invalid/SYNTHETIC_CODE', 302, 'SYNTHETIC_CLIENT_SECRET', {}, io.BytesIO(b'SYNTHETIC_ACCESS'))
            with self.assertRaises(oauth.OAuthError) as failure:
                oauth.token_request(self.config, dict(grant_type='authorization_code', code='SYNTHETIC_CODE'))
            request = build.return_value.open.call_args.args[0]
            self.assertEqual(request.full_url, self.config.canvas_origin + '/login/oauth2/token')
            self.assertEqual(request.method, 'POST')
            self.assertNotIn('SYNTHETIC', str(failure.exception))
            self.assertIsInstance(build.call_args.args[0], oauth.NoRedirect)

    def test_configuration_refuses_write_scopes_and_insecure_origins(self):
        for data in (dict(config_data(), scopes=['url:POST|/api/v1/courses']),
                     dict(config_data(), scopes=[]), dict(config_data(), canvas_origin='http://canvas.example.edu'),
                     dict(config_data(), canvas_origin='https://canvas.example.edu/extra')):
            with self.assertRaises(oauth.OAuthError): oauth.Config.from_data(data)

    def test_private_configuration_is_never_allowed_inside_source_or_archive(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = root / 'oauth-private.json'
            config.write_text(json.dumps(config_data()), encoding='utf-8')
            config.chmod(0o600)
            self.assertEqual(oauth.load_config(config).canvas_origin, self.config.canvas_origin)
            with self.assertRaises(oauth.OAuthError): oauth.load_config(config, excluded=(root,))
            if os.name != 'nt':
                config.chmod(0o644)
                with self.assertRaises(oauth.OAuthError): oauth.load_config(config)

    def test_dependency_subprocess_environment_excludes_all_auth_material(self):
        names = ('CANVAS_SESSION', 'CANVAS_TOKEN', 'CANVAS_OAUTH_SESSION', 'CANVAS_OAUTH_CONFIG')
        with patch.dict(os.environ, {name: 'SYNTHETIC_PRIVATE' for name in names}):
            self.assertTrue(all(name not in app.clean_environment() for name in names))

    def test_sync_uses_refreshed_token_only_for_the_exact_canvas_origin(self):
        from test_sync import Response, Opener
        session = oauth.Session(self.config, token_data())
        session.expires_at = time.time() - 1
        client = sync.Canvas(self.config.canvas_origin, session.payload(), auth='oauth')
        client.opener = Opener(Response(b'{}'), Response(b'attachment'))
        client.wait = lambda _: None
        with patch.object(oauth, 'token_request', return_value=dict(token_data(), access_token='SYNTHETIC_NEW_ACCESS')) as refresh:
            client.get('/api/v1/courses')
            with client.request('https://attachments.example.org/file') as response:
                self.assertEqual(response.read(), b'attachment')
            refresh.assert_called_once()
        self.assertEqual(client.opener.requests[0].get_header('Authorization'), 'Bearer SYNTHETIC_NEW_ACCESS')
        self.assertIsNone(client.opener.requests[1].get_header('Authorization'))

    def test_scheduler_cannot_enable_manual_token_automation_by_default(self):
        import schedule_mac
        with patch.object(sys, 'argv', ['schedule_mac.py', 'install']), patch.object(sys, 'platform', 'darwin'), patch.object(schedule_mac.subprocess, 'run') as run:
            with self.assertRaises(SystemExit) as failure:
                schedule_mac.run()
            self.assertEqual(failure.exception.code, 2)
            run.assert_not_called()

    def test_worker_gets_oauth_in_memory_and_not_in_command_arguments_or_logs(self):
        session = oauth.Session(self.config, token_data())
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'archive'
            tool = Path(temp) / 'tool'
            tool.mkdir()
            (tool / 'study_update.py').write_text("import os,json,sys\ns=json.loads(os.environ['CANVAS_OAUTH_SESSION'])\nassert s['access_token']=='SYNTHETIC_ACCESS'\nassert 'CANVAS_TOKEN' not in os.environ\nassert 'SYNTHETIC' not in str(sys.argv)\nprint(s['access_token'])\nprint(s['refresh_token'])\nprint(s['config']['client_secret'])\n", encoding='utf-8')
            job = app.ResyncJob(root, auth='oauth')
            with patch.object(app, 'TOOL', tool), patch.object(job, 'python_with_dependencies', return_value=Path(sys.executable)):
                job.start(session.payload(), base_url=self.config.canvas_origin, auth='oauth')
                for _ in range(200):
                    if job.phase not in app.ACTIVE: break
                    time.sleep(.01)
            self.assertEqual(job.phase, 'done', job.snapshot()['log'])
            self.assertNotIn('SYNTHETIC', json.dumps(job.snapshot()))
            self.assertIsNone(job.oauth_session)
            self.assertEqual(job.cookie, '')


if __name__ == '__main__':
    unittest.main()
