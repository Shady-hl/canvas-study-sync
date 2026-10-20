"""Local context entry points and integrity checks; no network or model calls."""
from collections import Counter
from bisect import bisect_right
import datetime as dt
import html
import json
from pathlib import Path
import re
import shutil
import urllib.parse as url
import zipfile


CHUNK_HEADING = re.compile(r'^## (S-[a-f0-9]{12}-r[a-f0-9]{8}-c\d+) \|', re.M)
CHUNK_BOUNDARY = re.compile(r'\n\n---\n## (S-[a-f0-9]{12}-r[a-f0-9]{8}-c\d+) \|')


def href(path):
    return url.quote(Path(path).as_posix(), safe='/')


def write_parts(folder, prefix, text, budget):
    from ai_export import split_text, write_utf8
    files = []
    for number, piece in enumerate(split_text(text, budget), 1):
        path = folder / f'{prefix}_{number:03d}.md'
        write_utf8(path, piece)
        files.append(path)
    return files


def file_record(path, stage):
    from ai_export import digest_file, estimate, read_utf8
    text = read_utf8(path)
    return {'file': path.relative_to(stage).as_posix(), 'bytes': path.stat().st_size,
            'sha256': digest_file(path), 'estimated_tokens': estimate(text),
            'words': len(text.split()), 'chunks': CHUNK_HEADING.findall(text)}


def group_context(key, group, label, output_name='AI'):
    from ai_export import INSTRUCTIONS
    original_folder = output_name + '/UPLOAD/' + key
    guide = INSTRUCTIONS + f'\n# {label} / {group["role"]}\n\n'
    guide += ('Select the Markdown files in this folder for text context. '
              'They contain complete extracted text for this group, with no AI summarization. '
              'The inventory and coverage describe the original sources.\n\n'
              f'Original attachments are in **{original_folder}**. Add relevant PDFs/images '
              'for equations, charts and scans; use original notebooks for rich outputs and '
              'CSV/TSV for calculations. Text extraction cannot preserve every visual detail.\n\n'
              'Each file has a size bound, but all selected files together may exceed your '
              'model context. Reserve room for your question and answer. Books and datasets '
              'are separate groups; include those you need. A local folder being open in '
              'VS Code does not mean its AI extension has read it. Ask the extension to '
              'read specific files and cite their source/page/cell IDs.\n\n')
    index = (group['path'] / '01_SOURCE_INDEX.md').read_text(encoding='utf-8')
    index = index.replace('Original supplied here', 'Original in matching ' + output_name + '/UPLOAD folder')
    guide += index + '\n\n' + (group['path'] / '02_COVERAGE.md').read_text(encoding='utf-8')
    course_guide = (group['path'] / '00_START_HERE.md').read_text(encoding='utf-8')
    heading = '## Course module map from last local sync'
    if heading in course_guide:
        guide += '\n\n' + heading + course_guide.split(heading, 1)[1].split('## Upload size', 1)[0]
    return guide


def verify_profile(expected, files, name, stage=None, blocks=None):
    from ai_export import ExportError, read_utf8
    if stage is None:
        actual = Counter(chunk for file in files for chunk in file['chunks'])
    else:
        # Match the complete generated block against the database excerpt, then
        # skip its entire length. Citation-like headings inside source text are
        # reference content, not extra exported chunks. Concatenation also checks
        # excerpts that span NotebookLM file boundaries without changing bytes.
        starts, text, offset = [], [], 0
        for record in files:
            piece = read_utf8(stage / record['file'])
            starts.append(offset)
            offset += len(piece)
            text.append(piece)
            record['chunks'] = []
        corpus = ''.join(text)
        actual, position = Counter(), 0
        while True:
            match = CHUNK_BOUNDARY.search(corpus, position)
            if match is None:
                break
            chunk = match.group(1)
            block = blocks.get(chunk)
            if block is None or not corpus.startswith(block, match.start()):
                raise ExportError(f'{name} excerpt text integrity check failed for {chunk}. Previous export retained.')
            actual[chunk] += 1
            files[bisect_right(starts, match.start()) - 1]['chunks'].append(chunk)
            position = match.start() + len(block)
    if actual != expected:
        missing = sum((expected - actual).values())
        extra = sum((actual - expected).values())
        raise ExportError(f'{name} context integrity check failed: {missing} missing / {extra} repeated or unexpected chunks. Previous export retained.')


def write_context_outputs(db, stage, groups, labels, sync_report, pack_budget):
    from ai_export import ExportError, chunk_block, digest_file, friendly_name, joined_chunks, output_directory
    root = stage.parent
    from timeedit_context import load_schedule, markdown as schedule_markdown
    schedule = load_schedule(root)
    output_name = output_directory(root).name
    if schedule:
        planning = stage / 'PLANNING'
        planning.mkdir()
        from ai_export import write_utf8
        write_utf8(planning / 'TIMETABLE.md', schedule_markdown(schedule))
        (planning / 'TIMETABLE.json').write_text(json.dumps(schedule, ensure_ascii=False, indent=2), encoding='utf-8')
    chat = stage / 'CHAT'
    workspaces = stage / 'VSCODE'
    packages = stage / 'PACKAGES'
    for folder in (chat, workspaces, packages):
        folder.mkdir()
    sources = list(db.execute('SELECT * FROM sources ORDER BY course,role,path'))
    from study_setup import book_context, PROMPTS, start_guide
    expected = Counter(row[0] for row in db.execute('SELECT id FROM chunks'))
    blocks = {row['id']: chunk_block(row) for row in joined_chunks(db)}
    subjects, chat_files = {}, []
    for key, group in groups.items():
        subject = Path(key).parts[0]
        entry = subjects.setdefault(subject, {'course': group['course'],
            'title': labels.get(group['course'], group['course']), 'groups': [], 'source_ids': []})
        folder = chat / key
        folder.mkdir(parents=True)
        context = group_context(key, group, entry['title'], output_name)
        context += book_context(root, [s for s in sources if s['course'] == group['course'] and s['role'] == 'book'])
        if group['role'] == 'course' and schedule:
            context += '\n\n' + schedule_markdown(schedule, group['course'])
        metadata = write_parts(folder, '00_CONTEXT', context, pack_budget)
        files = list(metadata)
        for pack in group['packs']:
            source = stage / 'UPLOAD' / pack['file']
            dest = folder / source.name
            shutil.copy2(source, dest)
            files.append(dest)
        records = [file_record(path, stage) for path in files]
        if any(record['estimated_tokens'] > pack_budget for record in records):
            raise ExportError('A chat context file exceeds --pack-tokens. Previous export retained.')
        chat_files.extend(records)
        entry['source_ids'].extend(s['id'] for s in group['sources'])
        entry['groups'].append({'folder': 'CHAT/' + key, 'role': group['role'],
            'files': records, 'estimated_tokens': sum(r['estimated_tokens'] for r in records),
            'original_folder': 'UPLOAD/' + key, 'originals': group['originals']})

    # Also bound the NotebookLM guide, which may grow with a very large inventory.
    # 300 KB per piece leaves room under 200,000 words even for one-letter words.
    notebook_files = []
    for subject, entry in subjects.items():
        folder = stage / 'NOTEBOOKLM' / subject
        guide = folder / '00_READ_FIRST.md'
        text = guide.read_text(encoding='utf-8')
        text += book_context(root, [s for s in sources if s['course'] == entry['course']]) + '\n' + PROMPTS
        if schedule:
            timetable_parts = write_parts(folder, '.timetable', schedule_markdown(schedule, entry['course']), 100000)
            for number, part in enumerate(timetable_parts, 1):
                part.rename(folder / ('01_TIMETABLE.md' if number == 1 else f'01_TIMETABLE_{number:03d}.md'))
            text += '\n\nTimeEdit planning context is in the separate `01_TIMETABLE.md` source in this folder. Select it when planning; re-upload it after a refresh.\n'
        pieces = write_parts(folder, '.guide', text, 100000)
        guide.unlink()
        for i, piece in enumerate(pieces):
            piece.rename(folder / ('00_READ_FIRST.md' if i == 0 else f'00_READ_FIRST_{i + 1:03d}.md'))
        from ai_export import read_utf8, write_utf8
        guide_path = folder / '00_READ_FIRST.md'
        guide_text = re.sub(r'Total files in this text set: \d+\.',
                            f'Total files in this text set: {len(list(folder.glob("*.md")))}.', read_utf8(guide_path))
        write_utf8(guide_path, guide_text)
        records = [file_record(path, stage) for path in sorted(folder.glob('*.md'))]
        if any(r['words'] > 200000 or r['bytes'] > 8 * 1024 * 1024 for r in records):
            raise ExportError('A NotebookLM text source exceeds the exporter size limit. Previous export retained.')
        notebook_files.extend(records)
        entry['notebooklm'] = {'folder': 'NOTEBOOKLM/' + subject, 'files': records,
            'estimated_tokens': sum(r['estimated_tokens'] for r in records)}
        entry['chat_files'] = sum(len(g['files']) for g in entry['groups'])
        entry['notebooklm_files'] = len(records)
        if schedule:
            entry['schedule_events'] = sum(entry['course'] in e['course_ids'] for e in schedule['events'])
        entry['sync_notices'] = [i for i in sync_report.get('issues', [])
                                 if str(i.get('course_id')) == entry['course']]
        scoped_sources = [s for s in sources if s['id'] in entry['source_ids']]
        entry['status_counts'] = dict(Counter(s['status'] for s in scoped_sources))
        entry['text_chunks'] = sum(len(r['chunks']) for g in entry['groups'] for r in g['files'])
        # Two browsable folders: canonical archive and subject AI context. No
        # extension configuration, notebook execution, or automatic file attachment.
        original_dirs = sorted({Path(s['path']).parts[0] if len(Path(s['path']).parts) > 1 else '.'
                                for s in scoped_sources})
        folders = [{'name': 'Originals - ' + name, 'path': '../../' + name} for name in original_dirs]
        folders.append({'name': 'AI context and timetable', 'path': '../NOTEBOOKLM/' + subject})
        for directory in original_dirs:
            if directory != '.':
                (root / directory / 'Practice').mkdir(exist_ok=True)
        workspace = workspaces / (subject + '.code-workspace')
        workspace.write_text(json.dumps({'folders': folders, 'settings': {
            'search.exclude': {'**/History/**': True, '**/__pycache__/**': True,
                               '**/AI/**': True, '**/AI_READY/**': True, '**/.venv/**': True}}}, ensure_ascii=False, indent=2), encoding='utf-8')
        entry['vscode'] = workspace.relative_to(stage).as_posix()
        entry['packages'] = {}
        for profile, folder in [('CHAT', chat / subject), ('NOTEBOOKLM', stage / 'NOTEBOOKLM' / subject)]:
            destination = packages / f'{subject}--{profile}.zip'
            with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED) as archive:
                for path in sorted(folder.rglob('*.md')):
                    archive.write(path, path.relative_to(folder).as_posix())
            entry['packages'][profile.lower()] = destination.relative_to(stage).as_posix()

    verify_profile(expected, chat_files, 'Chat', stage, blocks)
    verify_profile(expected, notebook_files, 'NotebookLM', stage, blocks)
    for entry in subjects.values():
        entry['text_chunks'] = sum(len(r['chunks']) for g in entry['groups'] for r in g['files'])
    # Guard against concurrent changes to text sources too, not only copied PDFs.
    for source in sources:
        path = root / source['path']
        if not path.is_file() or digest_file(path) != source['sha']:
            raise ExportError('A source changed during export. Rerun for a consistent snapshot; previous output retained.')
    manifest = {'format': 1, 'created_at': dt.datetime.now(dt.timezone.utc).isoformat(),
        'token_estimate': 'ceil(UTF-8 bytes / 3); originals and model overhead excluded',
        'scope': 'Local archive only; does not certify remote Canvas completeness',
        'subjects': subjects, 'unassigned_sync_notices': [i for i in sync_report.get('issues', [])
                                                       if i.get('course_id') is None],
        'sources': {s['id']: {'path': s['path'], 'sha256': s['sha'], 'title': s['title'],
            'course': s['course'], 'role': s['role'], 'status': s['status'],
            'warnings': json.loads(s['warnings']), 'duplicate_of': s['duplicate_of']} for s in sources}}
    if schedule:
        manifest['timetable'] = {k: schedule[k] for k in ('timezone', 'retrieved_at', 'source_sha256', 'source_type', 'event_date_extent', 'refresh_failed', 'warnings')}
        manifest['timetable']['events'] = len(schedule['events'])
    (stage / 'NOTEBOOKLM/START_HERE.md').write_text(
        '# NotebookLM: one subject per notebook\n\nOpen one subject folder and add its Markdown files. Include 00_READ_FIRST and 01_TIMETABLE; select OPTIONAL book sources only when relevant. Replace outdated local uploads after exporting. Read ../00_START_HERE.md for coding, exam practice and OneDrive.\n\n' +
        '\n'.join(f'- {name}: {entry["notebooklm_files"]} sources.' for name, entry in subjects.items()), encoding='utf-8')
    from ai_export import write_utf8
    write_utf8(stage / '00_START_HERE.md', start_guide(manifest, output_name))
    checks = {'source_files_verified': len(sources), 'extracted_chunks': sum(expected.values()),
              'chat_chunks_verified': sum(len(r['chunks']) for r in chat_files),
              'notebooklm_chunks_verified': sum(len(r['chunks']) for r in notebook_files),
              'each_chunk_exactly_once_per_profile': True, 'chat_file_token_limit': pack_budget,
              'excerpt_text_matches_database': True,
              'notebooklm_export_word_limit': 200000, 'notebooklm_export_byte_limit': 8 * 1024 * 1024}
    (stage / 'CONTEXT_MANIFEST.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    (stage / 'CONTEXT_CHECKS.json').write_text(json.dumps(checks, indent=2), encoding='utf-8')
    (chat / 'START_HERE.md').write_text(
        '# Chat context\n\nChoose one subject, then 01_COURSE. Select all Markdown files '
        'inside that folder if the combined text fits your model. Add relevant books and '
        'datasets from separate groups. Add originals from AI/UPLOAD for visuals. '
        'Opening a folder in VS Code does not automatically load it into the assistant.\n\n'
        'Open AI/index.html for file lists, sizes and coverage. Zips in AI/PACKAGES are '
        'transport archives; extract them before attaching their files. Do not attach '
        'the same extracts from CHAT, NOTEBOOKLM, UPLOAD and DOCUMENTS together.\n'.replace('AI/', output_name + '/'), encoding='utf-8')
    write_dashboard(stage, manifest, checks)
    return {name: {'chat_files': e['chat_files'], 'notebooklm_files': e['notebooklm_files'],
                   'vscode': e['vscode']} for name, e in subjects.items()}


def write_dashboard(stage, manifest, checks):
    escape = html.escape
    def link(path, label, css=''):
        return f'<a class="{css}" href="{href(path)}">{escape(str(label))}</a>'
    def file_list(records):
        return '<ul class="files">' + ''.join('<li>' + link(r['file'], Path(r['file']).name)
            + f'<span>{r["estimated_tokens"]:,} est. tokens</span></li>' for r in records) + '</ul>'
    cards = []
    for subject, entry in manifest['subjects'].items():
        counts = entry['status_counts']
        failures = counts.get('failed', 0) + counts.get('unsupported', 0)
        visual = counts.get('visual_only', 0)
        course_tokens = sum(g['estimated_tokens'] for g in entry['groups'] if g['role'] == 'course')
        notices = len(entry['sync_notices'])
        body = f'<article data-search="{escape(entry["title"].lower(), quote=True)}"><div class="cardtop"><span class="id">Canvas {escape(entry["course"])}</span>'
        body += f'<span class="badge">{entry["text_chunks"]:,} text chunks checked</span></div><h2>{escape(entry["title"])}</h2>'
        body += f'<div class="stats"><div><b>{course_tokens:,}</b><span>course text tokens, estimated</span></div><div><b>{entry["notebooklm_files"]}</b><span>NotebookLM text files, including books</span></div></div>'
        body += f'<p class="coverage">{failures} failed / unsupported · {visual} media without text · {notices} assigned sync notices</p>'
        if 'schedule_events' in entry:
            body += f'<p class="coverage">{entry["schedule_events"]} TimeEdit events matched to this subject. Timetable context is included in the course/chat and NotebookLM guides.</p>'
        body += '<div class="actions">' + link(entry['vscode'], 'VS Code workspace', 'button')
        body += link(entry['packages']['notebooklm'], 'NotebookLM text ZIP', 'button secondary') + '</div>'
        body += '<details><summary>NotebookLM — select these text files</summary><p>One notebook per subject. This set includes local book text. Add original PDFs/images when visual content matters. Replace old local uploads after rebuilding.</p>' + file_list(entry['notebooklm']['files']) + '</details>'
        for group in entry['groups']:
            label = 'Chat — course context' if group['role'] == 'course' else 'Chat — ' + ('book: ' + Path(group['folder']).name if group['role'] == 'book' else 'dataset previews')
            body += '<details><summary>' + escape(label) + f' · {group["estimated_tokens"]:,} est. tokens</summary>'
            body += '<p>Select the text files below. Their combined size must fit your model with room for the conversation.</p>' + file_list(group['files']) + '</details>'
            if group['originals']:
                body += '<details><summary>Originals — ' + escape(group['role']) + f' · {len(group["originals"])} files</summary><ul class="files">'
                for original in group['originals']:
                    body += '<li>' + link(Path(group['original_folder']) / original['file'], original['file']) + f'<span>{original["bytes"] / 1024 / 1024:.1f} MiB</span></li>'
                body += '</ul></details>'
        if entry['sync_notices']:
            body += '<details><summary>Canvas discovery notices</summary><ul>' + ''.join('<li>' + escape(str(n.get('item', ''))) + ': ' + escape(str(n.get('reason', ''))) + '</li>' for n in entry['sync_notices']) + '</ul></details>'
        body += '</article>'
        cards.append(body)
    unassigned = len(manifest['unassigned_sync_notices'])
    date = escape(manifest['created_at'].replace('T', ' ')[:19] + ' UTC')
    page = '''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>Canvas study context</title><style>
:root{font-family:system-ui,sans-serif;color:#20332d;background:#f3f5f1;font-size:15px}*{box-sizing:border-box}body{margin:0}main{max-width:1240px;margin:auto;padding:48px 28px}header{max-width:880px}h1{font-size:44px;letter-spacing:-1.7px;margin:12px 0}h2{font-size:23px;letter-spacing:-.5px;line-height:1.25;margin:20px 0}.eyebrow{font-size:12px;letter-spacing:2px;text-transform:uppercase;color:#537268}.intro{font-size:17px;line-height:1.65;color:#50645c}.note{padding:18px 22px;border-left:3px solid #c7933d;background:#fff7e9;line-height:1.6;margin:28px 0}.toolbar{display:flex;gap:18px;align-items:center;flex-wrap:wrap;margin:26px 0}input{background:white;border:1px solid #c7d3cb;border-radius:9px;padding:13px 16px;min-width:280px;font:inherit;flex:1}a{color:#286551;text-decoration:none}a:hover{text-decoration:underline}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:22px}article{border:1px solid #d5dfd7;background:#fff;border-radius:16px;padding:26px;min-width:0;box-shadow:0 5px 22px #243d2d05}.cardtop{display:flex;gap:8px;justify-content:space-between;align-items:center;flex-wrap:wrap}.id{font-size:12px;color:#63796d}.badge{background:#eaf4ec;color:#337346;padding:6px 9px;border-radius:20px;font-size:11px}.stats{display:flex;gap:30px;margin:22px 0}.stats b{display:block;font-size:25px}.stats span{display:block;color:#6c7d73;font-size:12px;max-width:175px;line-height:1.4;margin-top:5px}.coverage{color:#687d70;font-size:12px;line-height:1.7}.actions{display:flex;gap:10px;flex-wrap:wrap;margin:20px 0}.button{display:inline-block;background:#244f3d;color:white;padding:10px 14px;border-radius:8px;font-size:12px;font-weight:600}.secondary{background:#eef3ed;color:#2c5745}details{border-top:1px solid #e4ebe5;padding:15px 0}summary{cursor:pointer;font-size:13px;font-weight:600;line-height:1.5}details p{color:#64766a;font-size:12px;line-height:1.65}.files{padding-left:18px;margin:15px 0;font-size:12px;line-height:1.6;overflow-wrap:anywhere}.files li{margin:10px 0}.files span{display:block;color:#7b8a80;font-size:11px}footer{color:#758379;font-size:12px;line-height:1.8;margin:28px 0}#empty{display:none;color:#6c7d73}article[hidden]{display:none}@media(max-width:800px){.grid{grid-template-columns:1fr}main{padding:28px 18px}h1{font-size:34px}article{padding:22px}}
</style></head><body><main><header><div class="eyebrow">Your local study library</div><h1>Canvas study context</h1><p class="intro">Choose a subject, then choose how you will use it. Keep text easy to search and keep the original files available for visual details, code and data.</p></header>'''
    page += f'<div class="note"><strong>{checks["source_files_verified"]} local source files verified. {checks["extracted_chunks"]:,} extracted chunks preserved once in each text profile.</strong><br>Text conversion may omit visual meaning. Canvas access notices can leave remote gaps. {unassigned} older notices have no subject assignment; read the sync report for details. Splitting files does not enlarge your model context.</div>'
    if manifest.get('timetable'):
        timetable = manifest['timetable']
        page += '<div class="note"><strong>TimeEdit: ' + str(timetable['events']) + ' events · ' + escape(timetable['timezone']) + '</strong><br>' + link('PLANNING/TIMETABLE.md', 'Your complete timetable and planning context')
        page += '<br>Event dates: ' + escape(str(timetable['event_date_extent'])) + '. Refreshed: ' + escape(timetable['retrieved_at']) + '. No study availability is assumed from empty timetable gaps.'
        if timetable['refresh_failed']:
            page += '<br><strong>Refresh failed: retained snapshot. Confirm changes before planning.</strong>'
        page += '</div>'
    page += '<div class="toolbar">' + link('00_START_HERE.md', 'Start here: NotebookLM and coding', 'button') + link('../00_START_HERE.md', 'Run commands and folder guide', 'button secondary') + '</div>'
    page += '<div class="toolbar"><input id="search" type="search" aria-label="Filter subjects" placeholder="Find a subject…">' + link('../SYNC_REPORT.html', 'Canvas sync report') + link('EXTRACTION_REPORT.md', 'Extraction coverage') + '</div><div class="grid">' + ''.join(cards) + '</div><p id="empty">No subjects match your search.</p>'
    page += '<footer>Local snapshot built ' + date + '. No uploads happen from this page.<br>CHAT: smaller text packs · NOTEBOOKLM: larger text sources · DOCUMENTS: document extracts for local reading · UPLOAD: text and original companions. Choose one text profile for the same material. Extract ZIP files before uploading.<br>' + link('CONTEXT_MANIFEST.json', 'File manifest') + ' · ' + link('CONTEXT_CHECKS.json', 'Integrity checks') + '</footer></main><script>const search=document.getElementById("search");search.addEventListener("input",()=>{let count=0;document.querySelectorAll("article").forEach(card=>{card.hidden=!card.dataset.search.includes(search.value.trim().toLowerCase());if(!card.hidden)count++});document.getElementById("empty").style.display=count?"none":"block"});</script></body></html>'
    (stage / 'index.html').write_text(page, encoding='utf-8')
