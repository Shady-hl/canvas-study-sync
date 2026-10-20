import json
import os
from pathlib import Path
import shutil
import shlex
import subprocess
import sys
import tempfile
import unittest

import study_setup as study


TOOL = Path(study.__file__).parent


class ShortcutTests(unittest.TestCase):
    def test_generation_keeps_custom_shortcuts_and_provides_both_platforms(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            custom = root / 'REFRESH_CONTEXT.command'
            custom.write_text('# My own launcher\n')
            study.write_shortcuts(root)
            self.assertEqual(custom.read_text(), '# My own launcher\n')
            for name in study.ROOT_SHORTCUTS:
                self.assertTrue((root / (name + '.cmd')).is_file())
                self.assertTrue((root / (name + '.command')).is_file())
            guide_root = root / 'fresh'
            guide_root.mkdir()
            study.write_root_guide(guide_root)
            guide = (guide_root / '00_START_HERE.md').read_text()
            self.assertIn('zsh ./REFRESH_CONTEXT.command --full', guide)
            self.assertIn('.\\REFRESH_CONTEXT.cmd --full', guide)


@unittest.skipUnless(Path('/bin/zsh').is_file(), 'macOS launchers require zsh')
class MacLauncherTests(unittest.TestCase):
    def fixture(self, temp):
        root = Path(temp).resolve() / 'OneDrive – Example School' / 'Canvas Archive'
        tool = root / 'canvas-sync'
        tool.mkdir(parents=True)
        for path in TOOL.glob('*.command'):
            shutil.copy2(path, tool / path.name)
            # Inner wrappers must also work when ZIP extraction drops mode bits.
            (tool / path.name).chmod(0o644)
        study.write_shortcuts(root)
        python = tool / '.venv-macos/bin/python3'
        python.parent.mkdir(parents=True)
        python.symlink_to(sys.executable)
        (tool / 'check_dependencies.py').write_text(
            'import os, sys\nsys.exit(int(os.environ.get("CHECK_EXIT", "0")))\n')
        stub = '''import json, os, sys
from pathlib import Path
Path(os.environ['STUDY_TRACE']).write_text(json.dumps({
    'script': Path(__file__).name, 'arguments': sys.argv[1:],
    'cwd': str(Path.cwd()), 'python': sys.executable}))
sys.exit(int(os.environ.get('STUDY_EXIT', '0')))
'''
        for name in ('study_update.py', 'ai_export.py'):
            (tool / name).write_text(stub)
        return root, tool

    def run_launcher(self, root, name, arguments=(), **env):
        trace = root / 'trace.json'
        environment = dict(os.environ, PATH='/usr/bin:/bin', CANVAS_NO_PAUSE='1',
                           STUDY_TRACE=str(trace), **env)
        result = subprocess.run(['/bin/zsh', str(root / name), *arguments],
                                cwd=root.parent, env=environment,
                                capture_output=True, text=True, timeout=15)
        return result, json.loads(trace.read_text()) if trace.is_file() else None

    def test_refresh_preserves_arguments_and_uses_mac_environment_from_any_folder(self):
        with tempfile.TemporaryDirectory() as temp:
            root, tool = self.fixture(temp)
            arguments = ['--full', '--archive', '/a folder/with (spaces)', '--query', 'supply & demand']
            result, trace = self.run_launcher(root, 'REFRESH_CONTEXT.command', arguments)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(trace['script'], 'study_update.py')
            self.assertEqual(trace['arguments'], arguments)
            self.assertEqual(trace['cwd'], str(tool.resolve()))
            self.assertEqual(Path(trace['python']), tool / '.venv-macos/bin/python3')

    def test_sync_and_portable_dispatch_and_preserve_notice_and_failure_status(self):
        for name, script, flags, exit_code in (
            ('UPDATE_MATERIALS.command', 'study_update.py', ['--sync'], 2),
            ('MAKE_PORTABLE.command', 'ai_export.py', ['--portable'], 7),
            ('canvas-sync/SYNC_FAST.command', 'study_update.py', ['--sync'], 0),
        ):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temp:
                root, _ = self.fixture(temp)
                result, trace = self.run_launcher(root, name, ['--archive', '/my archive'],
                                                  STUDY_EXIT=str(exit_code))
                self.assertEqual(result.returncode, exit_code, result.stderr)
                self.assertEqual(trace['script'], script)
                self.assertEqual(trace['arguments'], flags + ['--archive', '/my archive'])

    def test_missing_dependencies_stop_before_refresh(self):
        with tempfile.TemporaryDirectory() as temp:
            root, _ = self.fixture(temp)
            result, trace = self.run_launcher(root, 'REFRESH_CONTEXT.command', CHECK_EXIT='1')
            self.assertEqual(result.returncode, 1)
            self.assertIsNone(trace)

    def test_finder_launch_finds_newer_python_without_a_virtual_environment(self):
        with tempfile.TemporaryDirectory() as temp:
            root, tool = self.fixture(temp)
            (tool / '.venv-macos/bin/python3').unlink()
            fake_python = root / 'Anaconda Python/bin/python3'
            fake_python.parent.mkdir(parents=True)
            marker = root / 'anaconda-used'
            fake_python.write_text('#!/bin/zsh\nprint used >> ' + shlex.quote(str(marker)) + '\nexec ' + shlex.quote(sys.executable) + ' "$@"\n')
            fake_python.chmod(0o755)
            runner = tool / 'RUN_STUDY.command'
            runner.write_text(runner.read_text().replace('/opt/anaconda3/bin/python3', shlex.quote(str(fake_python))))
            (tool / 'resync_app.py').write_text((tool / 'study_update.py').read_text())
            result, trace = self.run_launcher(root, 'OPEN_RESYNC.command', ['--no-browser'], CHECK_EXIT='1')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(marker.is_file())
            self.assertEqual(trace['arguments'], ['--launch', '--no-browser'])

    def test_setup_creates_separate_environment_and_installs_with_its_python(self):
        with tempfile.TemporaryDirectory() as temp:
            root, tool = self.fixture(temp)
            (tool / '.venv-macos/bin/python3').unlink()
            windows = tool / '.venv/Scripts/python.exe'
            windows.parent.mkdir(parents=True)
            windows.write_bytes(b'Windows environment remains intact')
            fake_bin = root / 'fake-bin'
            fake_bin.mkdir()
            python = fake_bin / 'python3'
            python.write_text('#!' + sys.executable + '''
import json, os, shutil, sys
from pathlib import Path
with Path(os.environ['STUDY_TRACE']).open('a') as log:
    log.write(json.dumps({'python': str(Path(__file__)), 'args': sys.argv[1:]}) + '\\n')
if '-m' in sys.argv and sys.argv[sys.argv.index('-m') + 1] == 'venv':
    dest = Path(sys.argv[-1]) / 'bin/python3'
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(__file__, dest)
sys.exit(0)
''')
            python.chmod(0o755)
            trace = root / 'setup.jsonl'
            environment = dict(os.environ, PATH=str(fake_bin) + ':/usr/bin:/bin',
                               CANVAS_NO_PAUSE='1', STUDY_TRACE=str(trace))
            result = subprocess.run(['/bin/zsh', str(root / 'SETUP_THIS_PC.command')],
                                    cwd=root.parent, env=environment,
                                    capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            calls = [json.loads(line) for line in trace.read_text().splitlines()]
            venv = tool / '.venv-macos/bin/python3'
            pip_call = next(call for call in calls if 'pip' in call['args'])
            self.assertEqual(pip_call['python'], str(venv))
            self.assertEqual(pip_call['args'][-2:], ['-r', str(tool / 'requirements-ai.txt')])
            self.assertEqual(windows.read_bytes(), b'Windows environment remains intact')
            # A later refresh selects exactly the environment created by setup.
            trace.write_text('')
            result = subprocess.run(['/bin/zsh', str(root / 'REFRESH_CONTEXT.command')],
                                    env=environment, capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            calls = [json.loads(line) for line in trace.read_text().splitlines()]
            self.assertTrue(all(call['python'] == str(venv) for call in calls))


if __name__ == '__main__':
    unittest.main()
