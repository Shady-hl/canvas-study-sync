#!/usr/bin/env python3
"""Read-only personal Ed Discussion export, using Ed's own API token.

Only GET requests are implemented. Tokens never go into files, URLs or argv.
Ed's API is beta; reports describe actual coverage rather than certifying it.
"""
import argparse
import email.utils
import getpass
import hashlib
import html
import json
import math
import os
from pathlib import Path
import re
import sys
import time
import urllib.error
import urllib.parse as U
import urllib.request as R
import xml.etree.ElementTree as ET

from canvas_sync import NoRedirect, atomic_write, json_bytes, now, safe_name, write_changed

REGIONS = ('eu', 'us', 'au')
REPORT = 'ED_SYNC_REPORT.json'
MAX_JSON = 32 * 1024 * 1024
MAX_FILE = 200 * 1024 * 1024


class EdError(Exception):
    pass


class EdAuthError(EdError):
    pass


def normalize_token(value):
    if not isinstance(value, str):
        raise ValueError('Enter an Ed API token.')
    value = value.strip()
    if value.lower().startswith('bearer '):
        value = value[7:].strip()
    if not value or len(value) > 4096 or not re.fullmatch(r'[A-Za-z0-9._~+/=\-]+', value):
        raise ValueError('Enter only your Ed API token, not a cookie or a complete header.')
    return value


def integer_id(value):
    if isinstance(value, bool) or not re.fullmatch(r'[1-9][0-9]*', str(value)):
        raise EdError('Ed returned an invalid resource identifier.')
    return str(value)


def public_url(value):
    if not isinstance(value, str):
        return ''
    p = U.urlsplit(value)
    return value if p.scheme == 'https' and p.hostname and not p.username and not p.password else ''


class EdClient:
    def __init__(self, token, region='eu', delay=0.6, event=None):
        if region not in REGIONS:
            raise ValueError('Choose Europe, United States or Australia.')
        self.token = normalize_token(token)
        self.region, self.base = region, f'https://{region}.edstem.org'
        self.delay = float(delay)
        if not math.isfinite(self.delay) or not 0.6 <= self.delay <= 5:
            raise ValueError('Ed request spacing must be between 0.6 and 5 seconds.')
        self.opener = R.build_opener(NoRedirect())
        self.next_request = 0
        self.started = time.monotonic()
        self.requests = self.retries = 0
        self.event = event or (lambda **values: None)

    def download_host(self, url):
        p = U.urlsplit(public_url(url))
        host = p.hostname or ''
        return p.port in (None, 443) and (host in {'edstem.org', f'{self.region}.edstem.org'}
                or host.endswith('.edusercontent.com'))

    def read(self, url, *, api=False, maximum=MAX_JSON):
        """Redirects never receive API credentials. Attachments use no credentials."""
        if api:
            p = U.urlsplit(url)
            if p.scheme != 'https' or p.netloc != f'{self.region}.edstem.org' or not p.path.startswith('/api/'):
                raise EdError('Refused an Ed API request outside the selected region.')
        elif not self.download_host(url):
            raise EdError('Attachment is hosted elsewhere; its original link was retained.')
        for redirect in range(6):
            for attempt in range(4):
                if self.requests >= 10000 or time.monotonic() - self.started > 7200:
                    raise EdError('Ed stopped at its request/time limit; earlier exports are retained.')
                time.sleep(max(0, self.next_request - time.monotonic()))
                self.next_request = time.monotonic() + self.delay
                self.requests += 1
                self.event(stage='ed', requests=self.requests, retries=self.retries)
                headers = {'User-Agent': 'PersonalStudyArchive/Ed-1', 'Accept-Encoding': 'identity'}
                if api:
                    headers.update(Authorization='Bearer ' + self.token, Accept='application/json')
                try:
                    try:
                        response = self.opener.open(R.Request(url, headers=headers, method='GET'), timeout=45)
                    except urllib.error.HTTPError as error:
                        response = error
                    with response:
                        status = response.getcode()
                        if status in (301, 302, 303, 307, 308):
                            if api:
                                raise EdAuthError('Ed redirected the API request. Check the token and region.')
                            url = U.urljoin(url, response.headers.get('Location', ''))
                            if not self.download_host(url):
                                raise EdError('Attachment redirect left Ed hosting; original link retained.')
                            break
                        if status in (401, 403):
                            if api and status == 401:
                                raise EdAuthError('Ed denied access. Check the token, region and course access.')
                            raise EdError('Ed denied this resource; check access or retain the original link.')
                        if status == 429 or status in (500, 502, 503, 504):
                            if attempt == 3:
                                raise EdError('Ed stayed busy or rate-limited; retry a later run.')
                            retry = response.headers.get('Retry-After', '')
                            wait = 2 ** (attempt + 1)
                            if retry:
                                try:
                                    wait = max(wait, float(retry))
                                except ValueError:
                                    try:
                                        wait = max(wait, email.utils.parsedate_to_datetime(retry).timestamp() - time.time())
                                    except (ValueError, TypeError, OverflowError):
                                        pass
                            if not 0 <= wait <= 300:
                                raise EdError('Ed requested a long pause; retry a later run.')
                            self.retries += 1
                            self.event(stage='ed', requests=self.requests, retries=self.retries, waiting_seconds=wait)
                            time.sleep(wait)
                            continue
                        if status != 200:
                            raise EdError(f'Ed returned HTTP {status}; this item was not exported.')
                        data = response.read(maximum + 1)
                        if len(data) > maximum:
                            raise EdError('Ed response exceeded the size limit; this item was not exported.')
                        mime = response.headers.get('Content-Type', '').split(';')[0].lower()
                        if not api and mime == 'text/html':
                            raise EdError('Attachment returned a web page; original link retained.')
                        return data
                except (urllib.error.URLError, TimeoutError, OSError) as error:
                    if attempt == 3:
                        raise EdError('Ed network request failed; retry a later run.') from None
                    self.retries += 1
                    time.sleep(2 ** attempt)
            else:
                raise EdError('Ed request did not finish.')
        raise EdError('Too many Ed attachment redirects.')

    def get(self, endpoint, **query):
        if not re.fullmatch(r'(user|courses/[1-9][0-9]*/threads|threads/[1-9][0-9]*)', endpoint):
            raise EdError('Unsupported Ed read endpoint.')
        url = self.base + '/api/' + endpoint
        if query:
            url += '?' + U.urlencode(query)
        data = self.read(url, api=True)
        try:
            result = json.loads(data)
        except (ValueError, UnicodeError):
            raise EdError('Ed returned non-JSON data. Check the token and region.') from None
        if not isinstance(result, dict):
            raise EdError('Ed returned an unexpected response format.')
        return result


def course_codes(value):
    return set(re.sub(r'[\s-]+', '', code).upper() for code in
               re.findall(r'\b[A-Za-z]{1,12}[\s-]*[0-9]{1,6}[A-Za-z]?\b', str(value)))


def course_targets(root, courses):
    """Match exact course codes only. Never guess between semesters/sections."""
    root = Path(root).resolve()
    state_path = root / '.canvas-sync.json'
    state = json.loads(state_path.read_text('utf-8')) if state_path.is_file() else {}
    from ai_export import course_mapping
    discovered = course_mapping(root)
    if len(set(discovered.values())) != len(discovered):
        raise EdError('Course folders need distinct numeric endings before Ed can be matched.')
    mapping = {cid: name for name, cid in discovered.items()}
    codes = state.get('course_codes', {})
    # In local-file mode a code in the folder name can match Ed exactly.
    codes = {cid: codes.get(cid) or name.rsplit('--', 1)[0] for cid, name in mapping.items()}
    config_path = root / 'ED_CONFIG.json'
    config = json.loads(config_path.read_text('utf-8')) if config_path.is_file() else {}
    overrides = config.get('course_map', {})
    matches = {}
    for course in courses:
        ed_id = integer_id(course.get('id'))
        targets = []
        if ed_id in overrides:
            targets = [str(overrides[ed_id])]
        elif course.get('status') == 'active':
            code = course_codes(course.get('code', ''))
            targets = [str(cid) for cid in mapping if code & course_codes(codes.get(cid, ''))]
        if len(targets) == 1 and targets[0] in mapping:
            matches[ed_id] = targets[0]
    # Multiple active Ed instances with the same code need an explicit choice.
    for cid in set(matches.values()):
        competing = [eid for eid, target in matches.items() if target == cid]
        if len(competing) > 1:
            explicit = [eid for eid in competing if eid in overrides]
            keep = explicit[0] if len(explicit) == 1 else None
            for eid in competing:
                if eid != keep:
                    matches.pop(eid)
    result = {}
    for ed_id, cid in matches.items():
        folder = (root / mapping[cid]).resolve()
        if folder == root or root not in folder.parents or not folder.is_dir():
            raise EdError('Canvas course folder is invalid; Ed export was stopped.')
        result[ed_id] = (cid, folder)
    return result


def author(item, users):
    if item.get('is_anonymous'):
        return 'Anonymous'
    user = users.get(str(item.get('user_id')), item.get('user') or {})
    role = str(user.get('course_role') or user.get('role') or '')
    label = str(user.get('name') or 'Author not supplied')
    return label + (' [STAFF]' if role in {'admin', 'staff', 'tutor', 'instructor'} else '')


def xml_html(content, attachment, warnings):
    """Convert Ed's XML without executing or embedding source HTML/scripts."""
    if not isinstance(content, str):
        raise EdError('Ed post content has an unexpected format.')
    try:
        root = ET.fromstring(content or '<document/>')
    except ET.ParseError:
        warnings.append('XML could not be rendered; complete source text is displayed literally.')
        return '<pre>' + html.escape(content) + '</pre>'

    def render(node):
        tag = node.tag.split('}')[-1]
        if tag in {'image', 'file'}:
            url = public_url(node.get('src') or node.get('url') or '')
            if not url:
                warnings.append('An embedded resource had no usable HTTPS link; see the raw XML.')
                return '<pre>' + html.escape(ET.tostring(node, encoding='unicode')) + '</pre>'
            local = attachment(url)
            name = node.get('name') or node.get('alt') or (node.text or '').strip() or ('Image' if tag == 'image' else 'Attachment')
            a = '<a href="' + html.escape(local, quote=True) + '">' + html.escape(name) + '</a>'
            # No automatic remote image fetch when viewing a saved page.
            if tag == 'image' and not local.startswith('https:'):
                return '<figure><img src="' + html.escape(local, quote=True) + '" alt="' + html.escape(name, quote=True) + '"></figure>'
            return '<p>' + a + '</p>'
        inner = html.escape(node.text or '') + ''.join(render(child) + html.escape(child.tail or '') for child in node)
        if tag == 'math':
            return '<span> $' + html.escape(''.join(node.itertext())) + '$ </span>'
        if tag == 'heading':
            level = node.get('level', '3')
            level = level if level in {'1', '2', '3', '4', '5', '6'} else '3'
            return '<h' + level + '>' + inner + '</h' + level + '>'
        if tag == 'list':
            kind = 'ol' if node.get('style') == 'number' else 'ul'
            return '<' + kind + '>' + inner + '</' + kind + '>'
        if tag == 'link':
            url = public_url(node.get('href', ''))
            return '<a href="' + html.escape(url, quote=True) + '">' + inner + '</a>' if url else inner
        if tag == 'snippet':
            return '<pre><code>' + inner + '</code></pre>'
        mapped = {'paragraph': 'p', 'bold': 'strong', 'italic': 'em', 'underline': 'u',
                  'list-item': 'li', 'callout': 'blockquote', 'spoiler': 'blockquote',
                  'pre': 'pre', 'code': 'code', 'table': 'table', 'row': 'tr', 'cell': 'td',
                  'table-row': 'tr', 'table-cell': 'td', 'break': 'br', 'figure': 'figure'}
        if tag in mapped:
            target = mapped[tag]
            return '<br>' if target == 'br' else '<' + target + '>' + inner + '</' + target + '>'
        if tag not in {'document', 'span'}:
            warnings.append('Unrecognised Ed XML element ' + str(tag)[:60] + '; text retained, check the original.')
        return inner
    return render(root)


def export_thread(thread, users, folder, client, previous, warnings):
    from canvas_sync import document
    tid, cid = integer_id(thread.get('id')), integer_id(thread.get('course_id'))
    for key in ('answers', 'comments'):
        if not isinstance(thread.get(key), list):
            raise EdError('Thread reply structure is incomplete; previous export retained.')
    source = f'https://edstem.org/{client.region}/courses/{cid}/discussion/{tid}'
    assets = folder / 'assets'
    if assets.is_symlink():
        raise EdError('Ed attachment folder is a symbolic link; export stopped.')
    assets.mkdir(exist_ok=True)
    downloaded = {}

    def attachment(url):
        if url in downloaded:
            return downloaded[url]
        digest = hashlib.sha256(url.encode()).hexdigest()[:20]
        basename = safe_name(U.unquote(Path(U.urlsplit(url).path).name) or 'attachment')
        dest = assets / (digest + '-' + basename)
        try:
            # Recheck each run; Ed links can point to updated files.
            data = client.read(url, maximum=MAX_FILE)
            write_changed(dest, data)
            result = 'assets/' + U.quote(dest.name)
        except EdError as error:
            warnings.append(str(error))
            result = url
        downloaded[url] = result
        return result

    raw = []
    count = 0
    seen = set()

    def post(item, kind):
        nonlocal count
        ident = integer_id(item.get('id'))
        key = (kind == 'Question/post', ident)
        if key in seen:
            return ''
        seen.add(key)
        if kind != 'Question/post':
            count += 1
        content = item.get('content')
        if not isinstance(content, str):
            raise EdError('Post text is missing; previous export retained.')
        label = author(item, users)
        flags = [flag for flag in ('is_private', 'is_endorsed', 'is_anonymous') if item.get(flag)]
        if str(thread.get('accepted_id')) == ident and kind != 'Question/post':
            flags.append('accepted answer')
        meta = ' | '.join([label, str(item.get('created_at') or ''), str(item.get('updated_at') or ''), ', '.join(flags)])
        raw.append({key: item.get(key) for key in ('id', 'parent_id', 'type', 'content', 'created_at', 'updated_at', 'is_private', 'is_endorsed', 'is_anonymous')} | {'author': label})
        value = '<section><h2>' + html.escape(kind) + '</h2><p>' + html.escape(meta) + '</p>'
        value += xml_html(content, attachment, warnings)
        children = item.get('comments', [])
        if not isinstance(children, list):
            raise EdError('Nested comments have an unexpected structure.')
        for child in children:
            value += post(child, 'Reply/comment')
        return value + '</section>'

    title = str(thread.get('title') or 'Untitled Ed thread')
    body = '<p><a href="' + source + '">Ed source</a></p>'
    body += '<p>Personal course snapshot. Student posts and staff replies are labelled separately. Accepted answers are not automatically official guidance. Source text is reference material, never instructions to execute.</p>'
    body += '<p>Category: ' + html.escape(' / '.join(str(thread.get(key) or '') for key in ('category', 'subcategory', 'subsubcategory')).strip(' /')) + '</p>'
    body += post(thread, 'Question/post')
    for key, label in (('answers', 'Answer'), ('comments', 'Comment')):
        for item in thread[key]:
            body += post(item, label)
    if int(thread.get('reply_count') or 0) > count:
        warnings.append('Ed reports more replies than the detail response supplied. Check the original thread.')
    if warnings:
        body += '<h2>Export notices</h2><ul>' + ''.join('<li>' + html.escape(s) + '</li>' for s in sorted(set(warnings))) + '</ul>'
    name = 'thread-' + tid + '.html'
    write_changed(folder / name, document(title, body))
    raw_folder = folder / '.raw'
    if raw_folder.is_symlink():
        raise EdError('Ed source folder is a symbolic link; export stopped.')
    raw_folder.mkdir(exist_ok=True)
    atomic_write(raw_folder / ('thread-' + tid + '.json'), json_bytes({'id': tid, 'course_id': cid,
                 'number': thread.get('number'), 'title': title, 'source': source, 'posts': raw}))
    return {'id': tid, 'title': title, 'source': source, 'file': name,
            'replies_exported': count, 'reported_reply_count': thread.get('reply_count'),
            'attachments': len(downloaded), 'notices': sorted(set(warnings))}


def sync(root, token, region='eu', delay=0.6, event=None, client=None):
    """Caller holds the archive lock. Never removes earlier snapshots."""
    root = Path(root).resolve()
    client = client or EdClient(token, region, delay, event)
    previous_path = root / REPORT
    previous = json.loads(previous_path.read_text('utf-8')) if previous_path.is_file() else {}
    report = {'format': 1, 'started_at': now(), 'region': region, 'status': 'running',
              'last_successful_at': previous.get('last_successful_at'), 'courses': [], 'issues': [],
              'scope': 'Accessible matched courses only. Hidden/deleted/inaccessible posts are not claimed. Old snapshots are retained; current coverage is in this report.'}
    try:
        user = client.get('user')
        memberships = user.get('courses')
        if not isinstance(memberships, list):
            raise EdError('Ed did not return an enrollment list.')
        courses = [item['course'] for item in memberships if isinstance(item, dict) and isinstance(item.get('course'), dict)]
        targets = course_targets(root, courses)
        for course in courses:
            cid = integer_id(course.get('id'))
            if cid not in targets:
                if course.get('status') == 'active':
                    report['issues'].append({'course_id': cid, 'reason': 'No unique course-code match. Name a local course folder with its Ed code or set ED_CONFIG.json course_map explicitly.'})
                continue
            canvas_id, canvas_folder = targets[cid]
            folder = canvas_folder / 'Discussions' / ('Ed-' + region + '-' + cid)
            if root not in folder.resolve().parents:
                raise EdError('Ed output folder leaves the archive.')
            folder.mkdir(parents=True, exist_ok=True)
            entry = {'ed_course_id': cid, 'canvas_course_id': canvas_id, 'code': course.get('code'),
                     'folder': folder.relative_to(root).as_posix(), 'listing_complete': False,
                     'threads': [], 'issues': []}
            report['courses'].append(entry)
            print('Ed: updating matched course ' + str(course.get('code') or cid), flush=True)
            seen, listed, offset = set(), [], 0
            try:
                while True:
                    page = client.get('courses/' + cid + '/threads', limit=100, offset=offset, sort='new')
                    rows = page.get('threads')
                    if not isinstance(rows, list):
                        raise EdError('Ed thread listing changed format; coverage is incomplete.')
                    if not rows:
                        entry['listing_complete'] = True
                        break
                    ids = [integer_id(row.get('id')) for row in rows]
                    if not set(ids) - seen:
                        raise EdError('Ed repeated a page; pagination stopped with incomplete coverage.')
                    for ident in ids:
                        if ident not in seen:
                            listed.append(ident)
                            seen.add(ident)
                    offset += len(rows)
                entry['listed_threads'] = len(listed)
            except EdAuthError:
                raise
            except EdError as error:
                entry['issues'].append(str(error))
            for ident in listed:
                try:
                    response = client.get('threads/' + ident, view=0)
                    thread = response.get('thread')
                    if not isinstance(thread, dict) or str(thread.get('id')) != ident or str(thread.get('course_id')) != cid:
                        raise EdError('Ed thread identity did not match the requested course.')
                    users = {str(item.get('id')): item for item in response.get('users', []) if isinstance(item, dict)}
                    entry['threads'].append(export_thread(thread, users, folder, client, previous, []))
                except EdAuthError:
                    raise
                except EdError as error:
                    entry['issues'].append('Thread ' + ident + ': ' + str(error))
            from canvas_sync import document
            body = '<p>Latest export attempt: ' + report['started_at'] + '. Check ED_SYNC_REPORT.json for incomplete coverage. Older saved threads can remain on disk.</p><ul>'
            body += ''.join('<li><a href="' + x['file'] + '">' + html.escape(x['title']) + '</a></li>' for x in entry['threads']) + '</ul>'
            write_changed(folder / 'index.html', document('Ed Discussion', body))
        if not targets:
            report['issues'].append({'reason': 'No accessible Ed courses could be matched to this study archive.'})
        partial = bool(report['issues']) or any(not c['listing_complete'] or c['issues'] or any(t['notices'] for t in c['threads']) for c in report['courses'])
        report['status'] = 'partial' if partial else 'complete'
        if not partial:
            report['last_successful_at'] = now()
    except EdError as error:
        report['status'] = 'failed'
        report['issues'].append({'reason': str(error)})
    except (ValueError, OSError, TypeError, KeyError) as error:
        report['status'] = 'failed'
        report['issues'].append({'reason': 'Ed export failed (' + type(error).__name__ + '). Earlier snapshots were retained.'})
    finally:
        report.update(finished_at=now(), requests=client.requests)
        atomic_write(root / REPORT, json_bytes(report))
        from canvas_sync import document
        body = '<p>Ed status: ' + report['status'] + '</p><p>Attempt: ' + report['started_at'] + '</p><p>Only sources actually returned by Ed are included. Attachments that could not be downloaded remain links. See the JSON report for per-thread notices.</p>'
        for entry in report['courses']:
            body += '<p><a href="' + U.quote(entry['folder'] + '/index.html') + '">' + html.escape(str(entry['code'] or entry['ed_course_id'])) + '</a>: ' + str(len(entry['threads'])) + ' threads exported.</p>'
        body += '<pre>' + html.escape(json.dumps(report['issues'], ensure_ascii=False, indent=2)) + '</pre>'
        write_changed(root / 'ED_SYNC_REPORT.html', document('Ed sync report', body))
        client.token = ''
    print('Ed: ' + report['status'] + '; report: ' + str(root / 'ED_SYNC_REPORT.html'), flush=True)
    return report


def main(argv=None):
    from canvas_sync import archive_lock
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--region', choices=REGIONS, default='eu')
    args = parser.parse_args(argv)
    token = os.environ.pop('ED_API_TOKEN', '') or getpass.getpass('Ed API token (hidden): ')
    try:
        token = normalize_token(token)
        with archive_lock(args.archive.resolve()):
            report = sync(args.archive, token, args.region)
        return 0 if report['status'] == 'complete' else 2
    except (ValueError, EdError) as error:
        print(str(error), file=sys.stderr)
        return 1
    finally:
        token = ''


if __name__ == '__main__':
    sys.exit(main())
