"""Canvas's documented native OAuth flow for trusted, administrator-managed installs.

No shared developer secret is bundled. General public desktop deployment still
requires an institution-approved secret provisioning/exchange architecture.
"""
from dataclasses import dataclass, field
import json
import math
import os
from pathlib import Path
import re
import secrets
import stat
import threading
import time
import urllib.error
import urllib.parse as U
import urllib.request as R

REDIRECT_URI = 'urn:ietf:wg:oauth:2.0:oob'


class OAuthError(ValueError):
    """A controlled message which never contains a token, code or server body."""


def origin(value):
    try:
        p = U.urlsplit(value)
        port = p.port
        if (p.scheme != 'https' or not p.hostname or p.username or p.password
                or p.path not in ('', '/') or p.query or p.fragment):
            raise ValueError
        return 'https://' + p.hostname.lower() + (':' + str(port) if port and port != 443 else '')
    except (ValueError, TypeError, AttributeError):
        raise OAuthError('OAuth requires the exact Canvas HTTPS origin.') from None


def secret(value):
    if not isinstance(value, str) or not re.fullmatch(r'[\x21-\x7e]{1,4096}', value):
        raise OAuthError('The OAuth credential response was invalid.')
    return value


@dataclass(repr=False, frozen=True)
class Config:
    canvas_origin: str
    client_id: str
    client_secret: str = field(repr=False)
    scopes: tuple

    @classmethod
    def from_data(cls, data):
        try:
            base = origin(data['canvas_origin'])
            client_id = data['client_id']
            client_secret = secret(data['client_secret'])
            scopes = data['scopes']
            if not isinstance(client_id, str) or not re.fullmatch(r'[0-9]{1,30}', client_id):
                raise ValueError
            if not isinstance(scopes, (list, tuple)) or not 1 <= len(scopes) <= 100:
                raise ValueError
            if not all(isinstance(s, str) and re.fullmatch(r'url:GET\|/api/v1/[A-Za-z0-9_/:.\-]{1,500}', s) for s in scopes):
                raise ValueError
            return cls(base, client_id, client_secret, tuple(sorted(set(scopes))))
        except (ValueError, KeyError, TypeError):
            raise OAuthError('Administrator OAuth configuration must specify a Canvas origin, client ID, secret and GET-only API scopes.') from None

    def data(self):
        return dict(canvas_origin=self.canvas_origin, client_id=self.client_id,
                    client_secret=self.client_secret, scopes=list(self.scopes))


def load_config(path, excluded=()):
    """Only explicitly provisioned private files outside source/archives qualify."""
    try:
        p = Path(path).expanduser()
        if p.is_symlink():
            raise ValueError
        resolved = p.resolve(strict=True)
        if any(resolved.is_relative_to(Path(root).resolve()) for root in excluded):
            raise ValueError
        info = resolved.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > 16384:
            raise ValueError
        if os.name != 'nt' and (info.st_uid != os.getuid() or info.st_mode & 0o077):
            raise ValueError
        return Config.from_data(json.loads(resolved.read_text(encoding='utf-8')))
    except (OSError, ValueError, TypeError):
        raise OAuthError('OAuth needs an administrator-provisioned private configuration outside the source and archive folders (owner-only permissions on Mac/Linux).') from None


class NoRedirect(R.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def token_request(config, parameters):
    """POST only to this configured Canvas origin, with TLS and no redirects."""
    values = dict(parameters, client_id=config.client_id, client_secret=config.client_secret)
    request = R.Request(config.canvas_origin + '/login/oauth2/token',
                        data=U.urlencode(values).encode('ascii'),
                        headers={'Content-Type': 'application/x-www-form-urlencoded', 'Accept': 'application/json'},
                        method='POST')
    try:
        with R.build_opener(NoRedirect()).open(request, timeout=30) as response:
            raw = response.read(65537)
            if len(raw) > 65536:
                raise ValueError
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError
            return data
    except urllib.error.HTTPError as error:
        limited = error.code == 429
        error.close()
        raise OAuthError('Canvas rate-limited sign-in. Wait before trying again.' if limited
                         else 'Canvas did not complete OAuth sign-in. Start a new authorization or ask the administrator.') from None
    except (OSError, ValueError, urllib.error.URLError):
        raise OAuthError('OAuth could not complete securely. Check the connection and administrator setup.') from None


class Session:
    def __init__(self, config, data):
        self.config = config
        self.lock = threading.Lock()
        self._accept(data)

    def _accept(self, data):
        try:
            access = secret(data['access_token'])
            refresh = secret(data.get('refresh_token', getattr(self, 'refresh_token', None)))
            expiry = float(data['expires_in'])
            if data.get('token_type', 'Bearer').lower() != 'bearer' or not math.isfinite(expiry) or not 120 < expiry <= 86400:
                raise ValueError
        except (ValueError, KeyError, TypeError, AttributeError):
            raise OAuthError('Canvas returned an invalid or unusable OAuth token response.') from None
        self.access_token, self.refresh_token = access, refresh
        self.expires_at = time.time() + expiry

    def bearer(self):
        with self.lock:
            if time.time() + 90 >= self.expires_at:
                self._accept(token_request(self.config, {'grant_type': 'refresh_token', 'refresh_token': self.refresh_token}))
            return self.access_token

    def payload(self):
        return json.dumps(dict(config=self.config.data(), access_token=self.access_token,
                               refresh_token=self.refresh_token, expires_at=self.expires_at))

    @classmethod
    def from_payload(cls, payload, expected_origin):
        try:
            data = json.loads(payload)
            config = Config.from_data(data['config'])
            if config.canvas_origin != origin(expected_origin):
                raise ValueError
            expiry = float(data['expires_at'])
            if not math.isfinite(expiry) or expiry > time.time() + 86400:
                raise ValueError
            instance = cls(config, dict(access_token=data['access_token'], refresh_token=data['refresh_token'], expires_in=3600))
            instance.expires_at = expiry
            return instance
        except (ValueError, KeyError, TypeError):
            raise OAuthError('The private OAuth session was missing, invalid or issued for a different Canvas origin.') from None

    def __repr__(self):
        return '<Canvas OAuth session: credentials hidden>'


class Flow:
    def __init__(self, config):
        self.config = config
        self.lock = threading.Lock()
        self.pending = None

    def begin(self):
        with self.lock:
            state = secrets.token_urlsafe(32)
            self.pending = (state, time.monotonic() + 600)
            query = U.urlencode(dict(client_id=self.config.client_id, response_type='code',
                                     redirect_uri=REDIRECT_URI, state=state,
                                     scope=' '.join(self.config.scopes), purpose='Canvas Study Sync'))
            return self.config.canvas_origin + '/login/oauth2/auth?' + query

    def finish(self, response_url):
        """Consume a bounded, exact-origin native callback once, including failure."""
        with self.lock:
            pending, self.pending = self.pending, None
        try:
            if not pending or time.monotonic() >= pending[1] or not isinstance(response_url, str) or len(response_url) > 8192:
                raise ValueError
            p = U.urlsplit(response_url.strip())
            if (origin(U.urlunsplit((p.scheme, p.netloc, '', '', ''))) != self.config.canvas_origin
                    or p.path != '/login/oauth2/auth' or p.fragment or p.username or p.password):
                raise ValueError
            query = U.parse_qs(p.query, strict_parsing=True, max_num_fields=8)
            if set(query) != {'code', 'state'} or any(len(v) != 1 for v in query.values()):
                raise ValueError
            if not secrets.compare_digest(query['state'][0], pending[0]):
                raise ValueError
            code = secret(query['code'][0])
        except (ValueError, TypeError, KeyError):
            raise OAuthError('Authorization was missing, expired, denied or did not match this window. Start sign-in again.') from None
        data = token_request(self.config, dict(grant_type='authorization_code', code=code, redirect_uri=REDIRECT_URI))
        return Session(self.config, data)
