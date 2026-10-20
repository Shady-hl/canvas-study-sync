#!/usr/bin/env python3
"""Reject common private/generated files and obvious secrets before publication."""
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_DIRS = {'.venv', '.venv-macos', '__pycache__', 'Canvas-Archive', 'AI_READY', 'Schedule', 'ASSISTANT_CONTEXT', 'Practice', 'History', 'Books'}
FORBIDDEN_NAMES = {'.canvas-sync.json', '.canvas-sync.lock', '.timeedit-source.json', 'AI_CONFIG.json', 'BOOK_CATALOG.json', 'timings.json', 'window.json', 'SYNC_REPORT.json', 'SYNC_REPORT.html', 'LAST_RUN.md'}
FORBIDDEN_SUFFIXES = {'.pdf', '.ipynb', '.zip', '.ics', '.csv', '.tsv', '.sqlite', '.jsonl', '.log', '.har', '.pem', '.key', '.pyc'}
PATTERNS = {
    'long credential-like literal': re.compile(r'(?<![A-Za-z0-9_+/=-])[A-Za-z0-9_+/=-]{90,}(?![A-Za-z0-9_+/=-])'),
    'GitHub credential': re.compile(r'(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{30,})'),
    'private key': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    'personal machine path': re.compile(r'/Users' + r'/[^/\s]+/|OneDrive-BI' + r'NorwegianBusinessSchool'),
}


def candidates():
    if (ROOT / '.git').exists():
        result = subprocess.run(['git', 'ls-files', '-z'], cwd=ROOT, capture_output=True, check=True)
        return [ROOT / p for p in result.stdout.decode().split('\0') if p]
    return [p for p in ROOT.rglob('*') if p.is_file() and '.git' not in p.relative_to(ROOT).parts
            and not any(part in {'.venv', '.venv-macos', '__pycache__'} for part in p.relative_to(ROOT).parts)]


def main():
    errors = []
    paths = candidates()
    if not paths:
        errors.append('No source files selected. Stage the intended release files first.')
    for path in paths:
        rel = path.relative_to(ROOT)
        if path.is_symlink():
            errors.append(f'{rel}: symlinks are not allowed in this release')
            continue
        if any(part in FORBIDDEN_DIRS for part in rel.parts) or path.name in FORBIDDEN_NAMES or path.suffix.lower() in FORBIDDEN_SUFFIXES or path.name.startswith('.env'):
            errors.append(f'{rel}: private/generated file type')
            continue
        if path.stat().st_size > 1_000_000:
            errors.append(f'{rel}: unexpected large source file')
            continue
        try:
            text = path.read_text(encoding='utf-8')
        except UnicodeError:
            errors.append(f'{rel}: unexpected binary file')
            continue
        for index, line in enumerate(text.splitlines(), 1):
            for name, pattern in PATTERNS.items():
                if pattern.search(line):
                    errors.append(f'{rel}:{index}: {name} (value withheld)')
    for error in errors:
        print(error)
    if errors:
        print(f'Release check failed: {len(errors)} issue(s). No values were printed.')
        return 1
    print(f'Release check passed for {len(paths)} source/documentation files. Review the staged diff as well; this is not a complete secret detector.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
