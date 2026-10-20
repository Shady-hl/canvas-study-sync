#!/usr/bin/env python3
"""Personal, read-only Canvas archive. Python 3.11+, standard library only."""
import argparse
import contextlib
import datetime as dt
import email.utils
import getpass
import hashlib
import html
from html.parser import HTMLParser
import http.client
from http.cookies import SimpleCookie, CookieError
import json
import math
import os
from pathlib import Path
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse as U
import urllib.request as R

VERSION = 1

class SyncError(Exception):
    pass

class AuthError(SyncError):
    pass

class SyncStopped(SyncError):
    """A run-wide stop: callers must not continue to another course or file."""
    def __init__(self, reason, message):
        self.reason = reason
        super().__init__(message)

class APIError(SyncError):
    def __init__(self, status):
        self.status = status
        super().__init__(f"HTTP {status}")


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def safe_name(value):
    text = re.sub(r'[\x00-\x1f\x7f/\\:*?"<>|]', "-", str(value)).strip(" .")
    # Bound UTF-8 byte length as well as character count (macOS/Linux filename limits).
    text = text.encode("utf-8")[:150].decode("utf-8", "ignore").rstrip(" .")
    if not text or text.upper().split(".")[0] in {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(10)], *[f"LPT{i}" for i in range(10)]}:
        text = "item-" + text
    return text


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def local_signature(path):
    s = path.stat()
    return [s.st_size, s.st_mtime_ns, s.st_ctime_ns, s.st_dev, s.st_ino]


def normalize_session(value):
    value = value.strip()
    if value.startswith("canvas_session="):
        value = value[len("canvas_session="):]
    if not value or re.search(r'[\s;,"\\\x00-\x1f\x7f]', value) or not value.isascii():
        raise AuthError("Enter only the raw canvas_session value, not a full Cookie header. Keep URL decoding off.")
    return value


def load_api_json(response):
    # Canvas session responses may have a JSON-hijacking protection prefix.
    # Strip only known literal prefixes; never eval a response.
    raw = response.read().decode("utf-8-sig").lstrip()
    if raw.lower().startswith(("<!doctype html", "<html")):
        raise AuthError("The API returned an HTML page, possibly sign-in. Check your login and Canvas address.")
    raw = re.sub(r'^while\s*\(\s*1\s*\)\s*;', '', raw, count=1).lstrip()
    return json.loads(raw)


def atomic_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".canvas-", suffix=".part", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def write_changed(path, data):
    data = data.encode("utf-8") if isinstance(data, str) else data
    if path.exists() and path.read_bytes() == data:
        return False
    atomic_write(path, data)
    return True


def json_bytes(obj):
    return (json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def read_json(path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError):
        raise SyncError(f"Cannot read {path.name}; restore it from backup or choose a new output folder.")


def origin(url):
    p = U.urlsplit(url)
    return (p.scheme.lower(), p.hostname, p.port or (443 if p.scheme == "https" else 80))


def validate_base(url):
    p = U.urlsplit(url.strip())
    if p.scheme != "https" or not p.hostname or p.username or p.password or p.query or p.fragment or p.path not in ("", "/"):
        raise SyncError("Use only the HTTPS Canvas origin, for example https://canvas.example.edu")
    return url.strip().rstrip("/")


class NoRedirect(R.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class TimedResponse:
    """Measure the whole response stream without recording URLs, headers or bodies."""
    def __init__(self, response, client, record, started):
        self.response, self.client, self.record, self.started = response, client, record, started
        self.headers = response.headers
        self.closed = False
        self.record.update(header_seconds=round(time.monotonic() - started, 4),
                           status=response.getcode(), bytes_read=0, error=None)
        for name, header in [('request_cost', 'X-Request-Cost'), ('rate_remaining', 'X-Rate-Limit-Remaining'), ('declared_bytes', 'Content-Length')]:
            try:
                value = float(response.headers.get(header, ''))
                self.record[name] = value if math.isfinite(value) and -10**6 <= value <= 10**12 else None
            except (ValueError, TypeError):
                self.record[name] = None

    def getcode(self):
        return self.response.getcode()

    def read(self, *args):
        try:
            data = self.response.read(*args)
            self.record['bytes_read'] += len(data)
            return data
        except Exception:
            self.record['error'] = 'response_read'
            raise

    def close(self):
        if not self.closed:
            self.closed = True
            try:
                self.response.close()
            finally:
                self.record['total_seconds'] = round(time.monotonic() - self.started, 4)
                self.client.event(request=self.record)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def request_pacing(delay, delay_range=None):
    delay = float(delay)
    if not math.isfinite(delay):
        raise SyncError("Request delay must be a finite number.")
    bounds = None
    if delay_range is not None:
        low, high = map(float, delay_range)
        if not (math.isfinite(low) and math.isfinite(high) and 0.1 <= low <= high):
            raise SyncError("Delay range must be finite, ordered MIN MAX, and at least 0.1 seconds.")
        bounds = (low, high)
    return max(0.1, delay), bounds


class Canvas:
    def __init__(self, base, token, delay=0.6, auth="token", delay_range=None):
        self.base = validate_base(base)
        if auth not in {"token", "session", "oauth"}:
            raise SyncError("Choose OAuth, token or session authentication.")
        self.auth = auth
        self.oauth = None
        if auth == 'oauth':
            from canvas_oauth import Session, OAuthError
            try:
                self.oauth = Session.from_payload(token, self.base)
            except OAuthError as error:
                raise AuthError(str(error)) from None
            self.token = ''
        else:
            self.token = normalize_session(token) if auth == "session" else token
        self.delay, self.delay_range = request_pacing(delay, delay_range)
        self.next_request = 0.0
        self.request_count = 0
        self.started = time.monotonic()
        self.pacing_wait_seconds = self.backoff_seconds = 0.0
        self.rate_limit_events = self.low_budget_events = 0
        self.network_errors = self.server_errors = 0
        self.waiting_until = None
        self.stopped_reason = None
        self.max_requests = self.max_seconds = None
        self.event_callback = None
        salt = os.environ.get('CANVAS_METRICS_SALT', '')
        self.metrics_salt = bytes.fromhex(salt) if re.fullmatch(r'[0-9a-f]{64}', salt) else os.urandom(32)
        self.opener = R.build_opener(NoRedirect())

    def metrics(self):
        return {"requests": self.request_count, "elapsed_seconds": round(time.monotonic() - self.started, 2),
                "delay_seconds": self.delay, "pacing_wait_seconds": round(self.pacing_wait_seconds, 2),
                "backoff_seconds": round(self.backoff_seconds, 2), "rate_limit_events": self.rate_limit_events,
                "low_budget_events": self.low_budget_events, "waiting_until": self.waiting_until,
                "network_errors": self.network_errors, "server_errors": self.server_errors,
                "stopped_reason": self.stopped_reason}

    def event(self, stage="canvas", request=None):
        if self.event_callback:
            data = dict(self.metrics(), stage=stage)
            if request is not None:
                data['request'] = request
            self.event_callback(data)

    def request_record(self, url, api, attempt, redirect):
        parsed = U.urlsplit(url)
        parts = parsed.path.strip('/').split('/')
        kinds = {'modules', 'pages', 'files', 'assignments', 'discussion_topics', 'quizzes', 'profile', 'courses'}
        kind = next((part for part in reversed(parts) if part in kinds), 'attachment' if not api else 'other')
        if kind == 'discussion_topics' and 'only_announcements=true' in parsed.query:
            kind = 'announcements'
        # Salted, stable local identifiers support per-resource analysis. Query
        # strings (including verifiers/cursors) and raw resource paths never log.
        key = hashlib.blake2s((parsed.netloc + parsed.path).encode(), key=self.metrics_salt, digest_size=12).hexdigest()
        return {'request_id': self.request_count, 'kind': kind, 'resource_key': key,
                'destination': 'canvas' if origin(url) == origin(self.base) else 'attachment_host',
                'attempt': attempt + 1, 'redirect': redirect,
                'started_offset_seconds': round(time.monotonic() - self.started, 4)}

    def stop(self, reason, message):
        self.stopped_reason = reason
        self.waiting_until = None
        self.event("stopped")
        raise SyncStopped(reason, message)

    def check_budget(self, extra_wait=0):
        if self.stopped_reason:
            raise SyncStopped(self.stopped_reason, "This sync was stopped; start a later run to continue.")
        if self.max_seconds and time.monotonic() - self.started + max(0, extra_wait) >= self.max_seconds:
            self.stop("time_limit", "Stopped at the run's time limit. Completed downloads are retained.")
        if self.max_requests and self.request_count >= self.max_requests:
            self.stop("request_limit", "Stopped at the run's request limit. Completed downloads are retained.")

    def refresh_session(self, response, same_origin):
        # Honor server rotation in memory only. Ignore cookies from every other host.
        if self.auth != "session" or not same_origin:
            return
        headers = response.headers
        values = headers.get_all("Set-Cookie", []) if hasattr(headers, "get_all") else [headers.get("Set-Cookie", "")]
        for value in values:
            jar = SimpleCookie()
            try:
                jar.load(value)
            except CookieError:
                continue
            if "canvas_session" in jar:
                item = jar["canvas_session"]
                try:
                    ended = bool(item["max-age"]) and int(item["max-age"]) <= 0
                except ValueError:
                    ended = False
                if not item.value or ended:
                    response.close()
                    raise AuthError("Canvas ended this session. Sign in again and provide the new cookie locally.")
                self.token = normalize_session(item.value)

    def wait(self, seconds):
        time.sleep(max(0, seconds))

    def request(self, url, api=False):
        url = U.urljoin(self.base + "/", url)
        for redirect in range(9):
            p = U.urlsplit(url)
            if p.scheme != "https" or p.username or p.password:
                raise SyncError("Refused a non-HTTPS or credential-bearing URL.")
            same = origin(url) == origin(self.base)
            if api and (not same or not p.path.startswith("/api/v1/")):
                if self.auth == "session":
                    raise AuthError("The session request redirected out of the Canvas API, possibly to sign-in. Sign in again; if it persists, use --check-access.")
                raise SyncError("API redirected outside Canvas API; check the Canvas address and login.")
            for attempt in range(6):
                headers = {"User-Agent": "PersonalCanvasSync/1.2", "Accept-Encoding": "identity"}
                if same:
                    if self.auth == "session":
                        headers["Cookie"] = "canvas_session=" + self.token
                    else:
                        if self.oauth:
                            from canvas_oauth import OAuthError
                            try:
                                bearer = self.oauth.bearer()
                            except OAuthError as error:
                                raise AuthError(str(error)) from None
                        else:
                            bearer = self.token
                        headers["Authorization"] = "Bearer " + bearer
                if api:
                    headers["Accept"] = "application/json"
                self.check_budget()
                pacing_wait = max(0, self.next_request - time.monotonic())
                self.check_budget(pacing_wait)
                self.pacing_wait_seconds += pacing_wait
                self.wait(pacing_wait)
                interval = random.uniform(*self.delay_range) if self.delay_range is not None else self.delay
                self.next_request = time.monotonic() + interval
                self.request_count += 1
                self.event()
                request_started = time.monotonic()
                record = self.request_record(url, api, attempt, redirect)
                record.update(pacing_wait_seconds=round(pacing_wait, 4), interval_seconds=interval)
                try:
                    try:
                        response = self.opener.open(R.Request(url, headers=headers), timeout=60)
                    except urllib.error.HTTPError as e:
                        response = e
                except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException):
                    self.network_errors += 1
                    record.update(status=None, bytes_read=0, error='connection', header_seconds=None,
                                  total_seconds=round(time.monotonic() - request_started, 4))
                    self.event(request=record)
                    if attempt == 5:
                        raise SyncError("Network request failed after retries.") from None
                    wait = 2 ** attempt + random.random()
                    self.check_budget(wait)
                    self.backoff_seconds += wait
                    self.waiting_until = time.time() + wait
                    self.event('server_wait')
                    self.wait(wait)
                    self.waiting_until = None
                    continue
                response = TimedResponse(response, self, record, request_started)
                code = response.getcode()
                self.refresh_session(response, same)
                rate_limited = code == 429
                if code == 403:
                    body = response.read(65536).lower()
                    rate_limited = b"rate limit" in body or b"rate_limit" in body
                if rate_limited or code in (500, 502, 503, 504):
                    if rate_limited:
                        self.rate_limit_events += 1
                    else:
                        self.server_errors += 1
                    retry = response.headers.get("Retry-After", "")
                    response.close()
                    if attempt == 5:
                        if rate_limited:
                            self.stop("rate_limit", "Canvas kept rate limiting this request. The entire sync stopped; try again later.")
                        raise APIError(code)
                    wait = 2 ** (attempt + 1) + random.random()
                    try:
                        wait = max(wait, float(retry))
                    except ValueError:
                        try:
                            wait = max(wait, email.utils.parsedate_to_datetime(retry).timestamp() - time.time())
                        except (TypeError, ValueError, OverflowError):
                            pass
                    if not math.isfinite(wait) or wait > 3600:
                        self.stop("server_wait", "Canvas requested a long pause. The entire sync stopped; try again later.")
                    self.check_budget(wait)
                    self.backoff_seconds += wait
                    self.waiting_until = time.time() + wait
                    self.event("rate_wait" if rate_limited else "server_wait")
                    print(f"  Server busy / rate limited; waiting {wait:.0f}s.")
                    self.wait(wait)
                    self.waiting_until = None
                    self.event()
                    continue
                if code in (301, 302, 303, 307, 308):
                    location = response.headers.get("Location")
                    response.close()
                    if not location:
                        raise SyncError("Redirect had no destination.")
                    url = U.urljoin(url, location)
                    break
                if code == 401 and api:
                    response.close()
                    raise AuthError("Canvas rejected the credential. Renew your token or sign in again, and check the Canvas domain.")
                if code < 200 or code >= 300:
                    response.close()
                    raise APIError(code)
                try:
                    if float(response.headers.get("X-Rate-Limit-Remaining", "999")) < 50:
                        self.low_budget_events += 1
                        self.next_request = max(self.next_request, time.monotonic() + 5)
                except ValueError:
                    pass
                self.event()
                return response
        raise SyncError("Too many redirects.")

    def get(self, endpoint):
        with self.request(endpoint, api=True) as response:
            try:
                return load_api_json(response)
            except (ValueError, UnicodeError):
                raise SyncError("Canvas returned non-JSON data; check the address and authentication.") from None

    def all(self, endpoint):
        sep = "&" if "?" in endpoint else "?"
        url = endpoint + sep + "per_page=100"
        seen = set()
        while url:
            if url in seen:
                raise SyncError("Canvas repeated a pagination link.")
            seen.add(url)
            with self.request(url, api=True) as response:
                try:
                    data = load_api_json(response)
                except (ValueError, UnicodeError):
                    raise SyncError("Canvas list returned non-JSON data.") from None
                if not isinstance(data, list):
                    raise SyncError("Expected a list from Canvas.")
                links = response.headers.get("Link", "")
            yield from data
            match = re.search(r'<([^>]+)>\s*;\s*rel=["\']?next\b', links, re.I)
            url = U.urljoin(self.base, match.group(1)) if match else None

    def download(self, url, dest, expected_size, mime):
        # Never put partial bytes at the final pathname.
        dest.parent.mkdir(parents=True, exist_ok=True)
        for attempt in range(3):
            fd, tmp = tempfile.mkstemp(prefix=".canvas-", suffix=".part", dir=dest.parent)
            try:
                with os.fdopen(fd, "wb") as out, self.request(url) as response:
                    actual_type = response.headers.get("Content-Type", "").split(";")[0].lower()
                    first = response.read(65536)
                    looks_html = first.lstrip().lower().startswith((b"<!doctype html", b"<html"))
                    if (actual_type == "text/html" or looks_html) and mime != "text/html":
                        raise SyncError("Download returned HTML instead of the expected file (possibly a login page).")
                    h = hashlib.sha256(first)
                    total = len(first)
                    out.write(first)
                    for block in iter(lambda: response.read(1024 * 1024), b""):
                        total += len(block)
                        h.update(block)
                        out.write(block)
                    if expected_size is not None and total != expected_size:
                        raise SyncError("Download size did not match Canvas metadata; left the existing file intact.")
                    out.flush()
                    os.fsync(out.fileno())
                os.replace(tmp, dest)
                return h.hexdigest(), total
            except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException):
                if attempt == 2:
                    raise SyncError("File transfer interrupted after retries.") from None
                self.wait(2 ** attempt)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)


# Readable HTML exports preserve tables and lists; no scripts, forms or live embeds.
class CleanHTML(HTMLParser):
    allowed = set("p br hr h1 h2 h3 h4 h5 h6 div span strong b em i u s ul ol li blockquote pre code table thead tbody tfoot tr td th caption a img sub sup dl dt dd figure figcaption details summary".split())
    suppress = {"script", "style", "form", "iframe", "object", "svg", "math"}
    def __init__(self, base, resolve=lambda u: u):
        super().__init__(convert_charrefs=True)
        self.base, self.resolve = base, resolve
        self.out, self.refs = [], set()
        self.block = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        for name in ("href", "src", "data-api-endpoint"):
            raw = attrs.get(name)
            if raw:
                absolute = U.urljoin(self.base, raw)
                p = U.urlsplit(absolute)
                if origin(absolute) == origin(self.base):
                    match = re.search(r"/files/(\d+)(?:/|$)", p.path)
                    if match:
                        self.refs.add(match.group(1))
        if tag in self.suppress:
            if not self.block and tag in {"iframe", "object"}:
                u = U.urljoin(self.base, attrs.get("src") or attrs.get("data") or "")
                if U.urlsplit(u).scheme in {"https", "http"}:
                    self.out.append(f'<p>Online embed: <a href="{html.escape(u, quote=True)}">open in browser</a></p>')
            self.block += 1
            return
        if self.block or tag not in self.allowed:
            return
        keep = []
        for key in ("href", "src", "alt", "title", "colspan", "rowspan"):
            if key not in attrs or attrs[key] is None:
                continue
            value = attrs[key]
            if key in {"href", "src"}:
                value = U.urljoin(self.base, value)
                if U.urlsplit(value).scheme not in {"http", "https", "mailto"}:
                    continue
                value = self.resolve(value)
            keep.append(f'{key}="{html.escape(value, quote=True)}"')
        self.out.append("<" + tag + (" " + " ".join(keep) if keep else "") + ">")

    def handle_endtag(self, tag):
        if tag in self.suppress:
            self.block = max(0, self.block - 1)
        elif not self.block and tag in self.allowed and tag not in {"br", "hr", "img"}:
            self.out.append(f"</{tag}>")

    def handle_data(self, data):
        if not self.block:
            self.out.append(html.escape(data))


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.out = []
    def handle_starttag(self, tag, attrs):
        if tag in {"p", "div", "br", "tr", "blockquote", "pre"} or re.fullmatch("h[1-6]", tag):
            self.out.append("\n")
        if tag == "li":
            self.out.append("\n- ")
        if tag in {"td", "th"}:
            self.out.append(" | ")
    def handle_endtag(self, tag):
        if tag in {"p", "div", "li", "tr"} or re.fullmatch("h[1-6]", tag):
            self.out.append("\n")
    def handle_data(self, data):
        self.out.append(data)


def document(title, content):
    return '<!doctype html><html><head><meta charset="utf-8">' + \
        '<meta name="viewport" content="width=device-width, initial-scale=1">' + \
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; img-src file: data:; style-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'">' + \
        f'<title>{html.escape(title)}</title><style>body{{font:17px/1.6 system-ui;margin:40px auto;padding:0 24px;max-width:1000px;color:#18313b;background:#fafbf9}}a{{color:#006f78}}h1,h2,h3{{line-height:1.2}}table{{border-collapse:collapse}}td,th{{border:1px solid #ccd8db;padding:8px}}img{{max-width:100%}}pre{{white-space:pre-wrap}}li{{margin:6px 0}}.meta{{color:#567}}</style></head><body><h1>{html.escape(title)}</h1>{content}</body></html>'


def blocked(obj):
    return obj.get("locked_for_user") is True or obj.get("published") is False


class Archive:
    def __init__(self, api, root, force=False, verify_local=False, fast=False):
        self.api, self.root, self.force = api, root, force
        self.verify_local = verify_local
        self.fast = fast
        self.root.mkdir(parents=True, exist_ok=True)
        self.state_path = root / ".canvas-sync.json"
        self.state = read_json(self.state_path, {"version": VERSION, "base_url": api.base, "files": {}})
        if self.state.get("version") != VERSION or self.state.get("base_url") != api.base:
            raise SyncError("Archive belongs to a different Canvas site or format; choose another output folder.")
        # Old Windows snapshots used backslashes; store paths that also work on Mac/Linux.
        for saved in self.state.get("files", {}).values():
            if saved.get("path"):
                saved["path"] = saved["path"].replace("\\", "/")
        self.report = {"started_at": now(), "downloaded": 0, "unchanged_files": 0, "text_updated": 0, "issues": [], "courses": [], "file_results": [], "text_exports": [], "local_hash_verified": 0, "local_signature_reused": 0}

    def issue(self, where, reason):
        self.report["issues"].append({"item": str(where), "reason": str(reason),
                                      "course_id": getattr(self, "active_course", None)})
        print(f"  Note: {where}: {reason}")

    def attempt(self, where, call, default=None):
        try:
            return call()
        except (AuthError, SyncStopped):
            raise
        except (SyncError, OSError, http.client.HTTPException) as e:
            self.issue(where, e if isinstance(e, SyncError) else type(e).__name__)
            return default

    def listing(self, where, endpoint):
        # Retain accessible earlier pages if a later page fails; report partial coverage.
        result = []
        def collect():
            for obj in self.api.all(endpoint):
                result.append(obj)
        self.attempt(where, collect)
        return result

    def save_state(self):
        atomic_write(self.state_path, json_bytes(self.state))

    def local_path(self, relative):
        relative = relative.replace("\\", "/")
        if relative.startswith("/") or re.match(r"^[A-Za-z]:", relative):
            raise SyncError("Archive state contains an unsafe path.")
        p = (self.root / relative).resolve()
        if self.root.resolve() not in p.parents:
            raise SyncError("Archive state contains an unsafe path.")
        return p

    def save_file(self, cid, folder, f):
        fid = str(f["id"])
        label = f.get("display_name") or f.get("filename") or f"file-{fid}"
        # Hidden from the Files listing is distinct from locked for this user.
        # Attempt the URL Canvas provides; the server still enforces download access.
        if blocked(f):
            self.issue(f"file {fid}: {label}", "Canvas marks this file locked for this user or unpublished; checked again on the next run.")
            return None
        key = f"{cid}:{fid}"
        stamp = {k: f.get(k) for k in ("id", "updated_at", "modified_at", "size", "filename", "display_name", "uuid")}
        old = self.state["files"].get(key)
        rel = old["path"] if old else (folder / "Files" / (fid + "--" + safe_name(label))).as_posix()
        dest = self.local_path(rel)
        if not self.force and old and old["metadata"] == stamp and dest.is_file() and dest.stat().st_size == old["size"]:
            signature = local_signature(dest)
            # Fast mode trusts a complete saved stat signature on this device.
            # Full verification stays available and remains the default.
            cached = (self.fast and not self.verify_local and os.name != "nt"
                      and old.get("local_signature") == signature
                      and isinstance(old.get("sha256"), str)
                      and re.fullmatch(r"[0-9a-f]{64}", old["sha256"]) is not None)
            if cached:
                self.report["local_signature_reused"] += 1
            else:
                self.report["local_hash_verified"] += 1
            if cached or sha(dest) == old.get("sha256"):
                old["local_signature"] = signature
                self.report["unchanged_files"] += 1
                return rel
        if not f.get("url"):
            raise SyncError("No permitted download URL in the Canvas file metadata.")
        # Keep the existing version until the complete replacement is validated.
        staging = dest.with_name(dest.name + ".incoming")
        try:
            digest, size = self.api.download(f["url"], staging, f.get("size"), f.get("content-type", "").split(";")[0])
            prior = sha(dest) if dest.exists() else None
            if prior is not None and prior != digest:
                backup = self.root / folder / "History" / f"{fid}--{prior[:16]}--{safe_name(dest.name)}"
                backup.parent.mkdir(parents=True, exist_ok=True)
                if not backup.exists():
                    shutil.copy2(dest, backup)
            os.replace(staging, dest)
        finally:
            if staging.exists():
                staging.unlink()
        self.state["files"][key] = {"metadata": stamp, "path": rel, "sha256": digest, "size": size, "local_signature": local_signature(dest)}
        self.save_state()  # Resume safely after every completed file.
        self.report["downloaded"] += 1
        print(f"  Saved: {label}")
        return rel

    def course(self, c):
        self.active_course = str(c['id'])
        try:
            return self._course(c)
        finally:
            self.active_course = None

    def _course(self, c):
        cid = str(c["id"])
        prefix = f"/api/v1/courses/{cid}"
        title = c.get("name") or c.get("course_code") or f"Course {cid}"
        # Stable directory mapping even if the teacher renames the course later.
        courses = self.state.setdefault("courses", {})
        if c.get('course_code'):
            self.state.setdefault('course_codes', {})[cid] = c['course_code']
        directory_title = re.sub(r'\s+-\s+Oslo$', '', title, flags=re.I)
        folder = Path(courses.setdefault(cid, f"{safe_name(directory_title)}--{cid}"))
        self.local_path(str(folder)).mkdir(parents=True, exist_ok=True)
        self.save_state()
        self.report["courses"].append({"id": cid, "name": title, "folder": str(folder)})
        print(f"\nScanning {title} ({cid})")
        refs, texts, module_rows = set(), {}, []
        page_slugs = set()
        module_objects = {"Assignment": set(), "Discussion": set(), "Quiz": set()}

        def add_text(kind, ident, obj, body_key):
            key = (kind, str(ident))
            if key in texts:
                return
            if blocked(obj):
                self.issue(f"{kind} {ident}", "Locked or unpublished; checked again on the next run.")
                return
            body = obj.get(body_key) or ""
            parser = CleanHTML(self.api.base)
            parser.feed(body)
            refs.update(parser.refs)
            attachment = obj.get("attachment")
            if isinstance(attachment, dict) and attachment.get("id"):
                refs.add(str(attachment["id"]))
                body += f'<p><a href="{self.api.base}/courses/{cid}/files/{attachment["id"]}">{html.escape(attachment.get("display_name") or "Attachment")}</a></p>'
            name = obj.get("title") or obj.get("name") or title
            meta = {k: obj[k] for k in ("due_at", "unlock_at", "lock_at", "points_possible", "posted_at") if obj.get(k) is not None}
            texts[key] = {"name": name, "body": body, "meta": meta,
                          "url": obj.get("html_url") or f"{self.api.base}/courses/{cid}",
                          "path": (folder / kind / (safe_name(ident) + "--" + hashlib.sha256(str(ident).encode()).hexdigest()[:10] + ".html")).as_posix()}

        course = self.attempt("syllabus", lambda: self.api.get(prefix + "?include[]=syllabus_body"), c)
        add_text("Syllabus", cid, course, "syllabus_body")
        modules = self.listing("modules", prefix + "/modules?include[]=items&include[]=content_details")
        for mod in modules:
            if blocked(mod) or mod.get("state") == "locked":
                self.issue(mod.get("name", "module"), "Module locked; checked again on the next run.")
                module_rows.append((mod, []))
                continue
            items = mod.get("items")
            # Inline items save a request only when Canvas supplies a complete set.
            if not isinstance(items, list) or len(items) != mod.get("items_count"):
                items = self.listing(f"module {mod['id']}", prefix + f"/modules/{mod['id']}/items?include[]=content_details")
            module_rows.append((mod, items))
            for item in items:
                if blocked(item) or blocked(item.get("content_details") or {}):
                    self.issue(item.get("title", "module item"), "Item locked; checked again on the next run.")
                    continue
                typ = item.get("type")
                if typ == "File" and item.get("content_id"):
                    refs.add(str(item["content_id"]))
                elif typ == "Page" and item.get("page_url"):
                    page_slugs.add(item["page_url"])
                elif typ in module_objects and item.get("content_id"):
                    module_objects[typ].add(str(item["content_id"]))
        pages_endpoint = prefix + "/pages"
        issues_before = len(self.report["issues"])
        pages = self.listing("pages", pages_endpoint + ("?include[]=body" if self.fast else ""))
        if self.fast and len(self.report["issues"]) > issues_before:
            # Recover the ordinary listing if optional body inclusion is refused
            # or its pagination fails; retain any bodies already received.
            pages.extend(self.listing("pages fallback", pages_endpoint))
        page_bodies = {}
        for page in pages:
            if page.get("url"):
                page_slugs.add(page["url"])
                if self.fast and isinstance(page.get("body"), str):
                    page_bodies[page["url"]] = page
        for slug in sorted(page_slugs):
            page = page_bodies.get(slug)
            if page is None:
                page = self.attempt(f"page {slug}", lambda slug=slug: self.api.get(prefix + "/pages/" + U.quote(slug, safe="")))
            if page:
                add_text("Pages", slug, page, "body")
        for kind, endpoint, body_key, typ in [
            ("Assignments", "assignments", "description", "Assignment"),
            ("Discussions", "discussion_topics", "message", "Discussion"),
            ("Quiz-info", "quizzes", "description", "Quiz"),
            ("Announcements", "discussion_topics?only_announcements=true", "message", None),
        ]:
            seen = set()
            for obj in self.listing(kind, prefix + "/" + endpoint):
                seen.add(str(obj["id"]))
                # Announcements can appear in discussions on some Canvas versions.
                if kind == "Discussions" and obj.get("is_announcement"):
                    continue
                add_text(kind, obj["id"], obj, body_key)
            for ident in sorted(module_objects.get(typ, set()) - seen):
                obj = self.attempt(f"{kind} {ident}", lambda ident=ident: self.api.get(prefix + "/" + endpoint + "/" + ident))
                if obj:
                    add_text(kind, ident, obj, body_key)
        files = {str(f["id"]): f for f in self.listing("course files", prefix + "/files")}
        for fid in sorted(refs - files.keys()):
            f = self.attempt(f"file metadata {fid}", lambda fid=fid: self.api.get(prefix + "/files/" + fid))
            if f:
                files[fid] = f
            else:
                self.report["file_results"].append({"course_id": cid, "file_id": fid, "name": "Metadata unavailable", "status": "metadata_failed", "linked_from_content": True})
        local_files = {}
        for fid, f in sorted(files.items()):
            result = {"course_id": cid, "file_id": fid,
                      "name": f.get("display_name") or f.get("filename") or fid,
                      "hidden_for_user": f.get("hidden_for_user"),
                      "locked_for_user": f.get("locked_for_user"),
                      "linked_from_content": fid in refs, "status": "interrupted"}
            self.report["file_results"].append(result)
            before = self.report["downloaded"]
            saved = self.attempt(f"file {fid}", lambda f=f: self.save_file(cid, folder, f))
            if saved:
                local_files[fid] = saved
                result.update(path=saved, status="downloaded" if self.report["downloaded"] > before else "unchanged")
            else:
                result["status"] = "locked_or_unpublished" if blocked(f) else "download_failed"

        # Resolve successful downloads into local links only after all transfers finish.
        def resolve(url, from_path):
            if origin(url) == origin(self.api.base):
                match = re.search(r"/files/(\d+)(?:/|$)", U.urlsplit(url).path)
                if match and match.group(1) in local_files:
                    return U.quote(os.path.relpath(self.root / local_files[match.group(1)], (self.root / from_path).parent).replace(os.sep, "/"), safe="/.")
            return url

        guide = [f"# {title}\n", "Text companion. Open index.html for formatted tables, links and module order.\n"]
        for (kind, ident), item in texts.items():
            parser = CleanHTML(self.api.base, lambda u, p=item["path"]: resolve(u, p))
            parser.feed(item["body"])
            content = "".join(parser.out)
            meta_html = "".join(f"<p>{html.escape(k)}: {html.escape(str(v))}</p>" for k, v in item["meta"].items())
            source = item["url"] if U.urlsplit(item["url"]).scheme in {"https", "http"} else self.api.base
            rendered = document(item["name"], f'<p><a href="../index.html">Course index</a> · <a href="{html.escape(source, quote=True)}">Canvas source</a></p>' + meta_html + content)
            self.report["text_updated"] += write_changed(self.root / item["path"], rendered)
            self.report["text_exports"].append({"course_id": cid, "kind": kind, "id": ident, "title": item["name"], "path": item["path"]})
            plain = PlainText()
            plain.feed(content)
            guide.extend([f"\n## {kind}: {item['name']}\n", "\n".join(f"{k}: {v}" for k, v in item["meta"].items()), "\n", re.sub(r"\n{3,}", "\n\n", "".join(plain.out)).strip(), "\n"])

        def link(relative, name):
            target = U.quote(os.path.relpath(self.root / relative, self.root / folder).replace(os.sep, "/"), safe="/.")
            return f'<a href="{target}">{html.escape(str(name))}</a>'

        index = ['<p><a href="../index.html">All courses</a> · <a href="STUDY_GUIDE.md">Text companion</a> · <a href="../SYNC_REPORT.html">Latest sync report</a></p>', '<p>Snapshot of content accessible on the latest scan. The sync report lists discovered files, results and gaps; it does not certify that all course content was discovered. Earlier downloads are retained.</p>']
        for mod, items in module_rows:
            index.append(f"<h2>{html.escape(mod.get('name', 'Module'))}</h2><ul>")
            if mod.get("state") == "locked" or blocked(mod):
                index.append("<li>Locked on this scan.</li>")
            for item in items:
                name = item.get("title", "Item")
                typ = item.get("type")
                fid = str(item.get("content_id"))
                kind = {"Page": "Pages", "Assignment": "Assignments", "Discussion": "Discussions", "Quiz": "Quiz-info"}.get(typ)
                ident = item.get("page_url") if typ == "Page" else fid
                entry = texts.get((kind, ident))
                if blocked(item) or blocked(item.get("content_details") or {}):
                    value = html.escape(name) + " — locked"
                elif typ == "File" and fid in local_files:
                    value = link(local_files[fid], name)
                elif entry:
                    value = link(entry["path"], name)
                else:
                    url = item.get("external_url") or item.get("html_url") or ""
                    value = html.escape(name)
                    if U.urlsplit(url).scheme in {"https", "http"}:
                        value = f'<a href="{html.escape(url, quote=True)}">{value}</a> (online)'
                index.append(f"<li>{value}</li>")
            index.append("</ul>")
        index.append("<h2>All exported text</h2><ul>")
        for (kind, ident), item in texts.items():
            index.append(f"<li>{html.escape(kind)}: {link(item['path'], item['name'])}</li>")
        index.append("</ul><h2>Files</h2><ul>")
        for fid, rel in local_files.items():
            index.append(f"<li>{link(rel, files[fid].get('display_name') or fid)}</li>")
        index.append("</ul>")
        write_changed(self.root / folder / "index.html", document(title, "".join(index)))
        write_changed(self.root / folder / "STUDY_GUIDE.md", "\n".join(guide))

    def finish(self):
        self.report["finished_at"] = now()
        if hasattr(self.api, 'metrics'):
            self.report['http'] = self.api.metrics()
        self.save_state()
        atomic_write(self.root / "SYNC_REPORT.json", json_bytes(self.report))
        sections = ['<p><a href="index.html">All courses</a></p>',
                    '<p>This report covers only this run and the resources it discovered. Check the notes for inaccessible sections. Absence from the report does not prove that an item does not exist.</p>',
                    '<h2>Files discovered</h2><table><thead><tr><th>Course</th><th>File ID</th><th>Name</th><th>Result</th></tr></thead><tbody>']
        metrics = self.report.get('http', {})
        if metrics:
            sections.insert(2, '<p>HTTP requests (including retries and redirects): ' + str(metrics['requests'])
                            + '; rate-limit responses: ' + str(metrics['rate_limit_events'])
                            + '; backoff: ' + str(metrics['backoff_seconds']) + ' seconds.</p>')
        for entry in self.report["file_results"]:
            sections.append('<tr>' + ''.join('<td>' + html.escape(str(entry.get(k, ''))) + '</td>' for k in ('course_id', 'file_id', 'name', 'status')) + '</tr>')
        sections.append('</tbody></table><h2>Text exported</h2><ul>')
        for entry in self.report["text_exports"]:
            label = f"{entry['course_id']} / {entry['kind']}: {entry['title']}"
            url = U.quote(entry['path'].replace(os.sep, '/'), safe='/.')
            sections.append(f'<li><a href="{url}">{html.escape(label)}</a></li>')
        sections.append('</ul><h2>Notes and failures</h2><ul>')
        for entry in self.report["issues"]:
            sections.append('<li>' + html.escape(entry['item'] + ': ' + entry['reason']) + '</li>')
        if not self.report["issues"]:
            sections.append('<li>No issues were reported by this scan. This is not a completeness guarantee.</li>')
        sections.append('</ul>')
        write_changed(self.root / 'SYNC_REPORT.html', document('Latest Canvas sync report', ''.join(sections)))
        rows = []
        for c in self.report["courses"]:
            url = U.quote(c["folder"].replace(os.sep, "/") + "/index.html", safe="/.")
            rows.append(f'<li><a href="{url}">{html.escape(c["name"])}</a></li>')
        write_changed(self.root / "index.html", document("Canvas course archive", '<p>Latest run: ' + html.escape(now()) + '</p><p><a href="SYNC_REPORT.html">View file results, exported pages and any gaps</a></p><ul>' + "".join(rows) + "</ul>"))


@contextlib.contextmanager
def archive_lock(root):
    """OS lock, released automatically even after a crash; file presence is not a lock."""
    root.mkdir(parents=True, exist_ok=True)
    with open(root / ".canvas-sync.lock", "a+b") as f:
        try:
            if os.name == "nt":
                import msvcrt
                f.seek(0)
                f.write(b"0")
                f.flush()
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise SyncError("Another sync is already using this output folder.") from None
        try:
            yield
        finally:
            if os.name == "nt":
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def get_token(base, noninteractive=False):
    token = os.environ.get("CANVAS_TOKEN", "").strip()
    if not token and sys.platform == "darwin":
        result = subprocess.run(["/usr/bin/security", "find-generic-password", "-s", "personal-canvas-sync", "-a", base, "-w"], capture_output=True, text=True)
        if result.returncode == 0:
            token = result.stdout.strip()
    if not token and not noninteractive:
        token = getpass.getpass("Canvas access token (hidden; stays on this computer): ").strip()
    if not token:
        raise AuthError("No local token. Use CANVAS_TOKEN or save it in macOS Keychain as described in README.")
    if "\n" in token or "\r" in token:
        raise AuthError("Invalid token format.")
    return token


def get_session(noninteractive=False):
    value = os.environ.get("CANVAS_SESSION", "").strip()
    if not value and not noninteractive:
        value = getpass.getpass("Raw canvas_session cookie (hidden, memory only; never paste it into chat): ")
    if not value:
        raise AuthError("No session cookie. Run interactively with --auth session; session mode does not use token Keychain entries.")
    return normalize_session(value)


def check_access(api, course_ids):
    endpoints = ["/api/v1/users/self/profile", "/api/v1/courses?enrollment_type=student&enrollment_state=active&per_page=1"]
    for cid in dict.fromkeys(course_ids or []):
        endpoints.extend(f"/api/v1/courses/{cid}/{part}?per_page=1" for part in ("modules", "pages", "files", "assignments"))
    failed = False
    for endpoint in endpoints:
        try:
            data = api.get(endpoint)
            kind = "list" if isinstance(data, list) else "object"
            print(f"{endpoint.split('?')[0]}: HTTP 200; JSON {kind}")
        except AuthError as e:
            print(f"Authentication check stopped: {e}")
            return 1
        except SyncError as e:
            failed = True
            print(f"{endpoint.split('?')[0]}: {e}")
    print("No content bodies, cookie values or response headers were printed.")
    return 2 if failed else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", help="Your institution's Canvas HTTPS origin. Reused from an existing archive when omitted.")
    parser.add_argument("--auth", choices=["oauth", "token", "session"], default="oauth", help="OAuth (default); manual token/session modes are for permitted personal development/testing only.")
    from study_setup import default_archive
    parser.add_argument("--output", type=Path, default=default_archive(__file__))
    parser.add_argument("--course", action="append", type=int, help="Course ID; repeat for several. Default: active student enrollments.")
    parser.add_argument("--list-courses", action="store_true")
    parser.add_argument("--check-access", action="store_true", help="Print read-only endpoint status checks without downloading course material.")
    parser.add_argument("--force", action="store_true", help="Re-download all accessible files; keep prior changed versions.")
    parser.add_argument("--verify-local", action="store_true", help="Hash-check existing downloads even in fast mode; normal mode already verifies every file.")
    parser.add_argument("--fast", action="store_true", help="Use batched page bodies and reuse hashes when local file signatures match. Default fast pacing: random 0.1-0.25 seconds.")
    parser.add_argument("--ai", action="store_true", help="After sync, build local AI Markdown packs and a searchable book index. PDF extraction requires pypdf.")
    parser.add_argument("--ai-pack-tokens", type=int, default=24000, help="Approximate token budget per generated AI pack.")
    parser.add_argument("--timeedit-file", type=Path, help="Import TimeEdit CSV/iCalendar; retained for future runs.")
    parser.add_argument("--timeedit-url", help="Save a TimeEdit calendar/export URL for automatic timetable refresh.")
    parser.add_argument("--timeedit-timezone", default="Europe/Oslo")
    pacing = parser.add_mutually_exclusive_group()
    pacing.add_argument("--delay", type=float, help="Fixed request interval in seconds (minimum 0.1; normal default 0.6). Overrides fast pacing.")
    pacing.add_argument("--delay-range", type=float, nargs=2, metavar=("MIN", "MAX"), help="Random request interval between MIN and MAX seconds (minimum 0.1).")
    parser.add_argument("--non-interactive", action="store_true", help="Fail instead of prompting; use for scheduling.")
    parser.add_argument("--progress-events", action="store_true", help="Emit credential-free progress for the local resync window.")
    parser.add_argument("--max-requests", type=int, help="Stop the entire run after this many HTTP requests, including retries and redirects.")
    parser.add_argument("--max-seconds", type=float, help="Stop starting requests after this run time, in seconds.")
    args = parser.parse_args()
    archive = None
    ai_counts = {}
    started = time.monotonic()
    try:
        if args.max_requests is not None and args.max_requests < 1:
            raise SyncError("Request limit must be positive.")
        if args.max_seconds is not None and (not math.isfinite(args.max_seconds) or args.max_seconds <= 0):
            raise SyncError("Time limit must be positive and finite.")
        from archive_config import canvas_origin
        base = canvas_origin(args.output.expanduser().resolve(), args.base_url)
        delay_range = args.delay_range
        if args.fast and delay_range is None and args.delay is None:
            delay_range = (0.1, 0.25)
        delay, delay_range = request_pacing(args.delay if args.delay is not None else 0.6, delay_range)
        credential = os.environ.pop('CANVAS_OAUTH_SESSION', '') if args.auth == 'oauth' else get_session(args.non_interactive) if args.auth == "session" else get_token(base, args.non_interactive)
        api = Canvas(base, credential, delay, args.auth, delay_range=delay_range)
        api.max_requests, api.max_seconds = args.max_requests, args.max_seconds
        if args.progress_events:
            api.event_callback = lambda data: print('CANVAS_EVENT ' + json.dumps(data), flush=True)
            api.event()
        if args.fast:
            pace = f"random {api.delay_range[0]:g}-{api.delay_range[1]:g}s" if api.delay_range else f"fixed {api.delay:g}s"
            checks = "full hash verification" if args.verify_local or os.name == "nt" else "cached hashes for unchanged local files"
            print(f"Fast sync: {pace}; {checks}; batched pages. Server backoff remains active.")
        if args.check_access:
            return check_access(api, args.course)
        # Fail upfront on invalid credentials without creating misleading course reports.
        api.get("/api/v1/users/self/profile")
        if args.course:
            courses = [api.get(f"/api/v1/courses/{cid}") for cid in dict.fromkeys(args.course)]
        else:
            courses = list(api.all("/api/v1/courses?enrollment_type=student&enrollment_state=active"))
        if args.list_courses:
            for c in courses:
                print(f"{c['id']}\t{c.get('course_code', '')}\t{c.get('name', '')}")
            return 0
        if not courses:
            raise SyncError("No active student courses returned. Try --list-courses or --course COURSE_ID.")
        with archive_lock(args.output.expanduser().resolve()):
            from archive_layout import named_subjects
            try:
                with named_subjects(args.output.expanduser().resolve(), {str(c['id']): c.get('name') or c.get('course_code') for c in courses}) as changes:
                    if getattr(changes, 'deferred', []):
                        print('An open subject folder kept its existing valid path. Close apps using the archive and refresh again to finish its naming change.')
            except OSError as error:
                if getattr(error, 'winerror', None) != 32:
                    raise
                print('Subject naming deferred: close apps using the archive and refresh again. Canvas syncing will use the existing valid folders.')
            archive = Archive(api, args.output.expanduser().resolve(), args.force, args.verify_local, fast=args.fast)
            try:
                for c in courses:
                    archive.attempt(f"course {c['id']}", lambda c=c: archive.course(c))
                archive.report['completed'] = True
            except (KeyboardInterrupt, AuthError, SyncStopped):
                archive.report['completed'] = False
                archive.issue("run", "Interrupted; rerun to retry unfinished items.")
                raise
            finally:
                archive.finish()
            if args.progress_events:
                api.event('context')
            if args.timeedit_file or args.timeedit_url:
                from timeedit_context import configure_schedule
                configure_schedule(archive.root, args.timeedit_file, args.timeedit_url, args.timeedit_timezone)
            if args.ai:
                try:
                    config_path = archive.root / 'AI_CONFIG.json'
                    config = read_json(config_path, {})
                    config['simple_layout'] = True
                    write_changed(config_path, json.dumps(config, indent=2))
                    from ai_export import build
                    ai_counts = build(archive.root, pack_budget=args.ai_pack_tokens)
                except Exception as e:
                    print(f"Canvas sync is saved: {archive.report['downloaded']} files downloaded. AI export failed ({type(e).__name__}).", file=sys.stderr)
                    print("Next command in this tool folder (Windows PowerShell):", file=sys.stderr)
                    print("  .\\EXPORT_ONLY.cmd", file=sys.stderr)
                    return 1
            else:
                from timeedit_context import prepare_schedule
                prepare_schedule(archive.root)
        r = archive.report
        print(f"\nFinished: {r['downloaded']} files downloaded, {r['unchanged_files']} unchanged, {r['text_updated']} text exports updated, {len(r['issues'])} notices.")
        request_count = getattr(api, 'request_count', 'unavailable')
        print(f"Elapsed: {time.monotonic() - started:.1f}s; {request_count} HTTP requests; {r['local_signature_reused']} local hashes reused, {r['local_hash_verified']} existing files hash-checked.")
        print(f"Archive updated: {archive.root}")
        print(f"Start guide: {archive.root / '00_START_HERE.md'}")
        return 2 if r["issues"] or ai_counts.get("failed") or ai_counts.get("visual_only") or ai_counts.get("unsupported") else 0
    except KeyboardInterrupt:
        print("\nStopped. Completed downloads are saved; rerun to continue.", file=sys.stderr)
        return 130
    except (SyncError, OSError, ValueError) as e:
        print(f"Error: {e if isinstance(e, SyncError) else type(e).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
