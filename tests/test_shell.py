"""Exercise shell integration with temporary homes and a local fake Codex CLI."""
import fcntl
import json
import os
from pathlib import Path
import pty
import select
import struct
import subprocess
import tempfile
import termios
import time
import unittest


PROJECT = Path(__file__).resolve().parents[1]
BEGIN = '# >>> codex account switcher (interactive) >>>'
END = '# <<< codex account switcher (interactive) <<<'


class ShellFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='codex-switcher-test-')
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / 'home with spaces'
        self.home.mkdir()
        self.bashrc = self.home / '.bashrc'
        self.original = '# Existing settings\nexport KEEP_THIS=yes\n'
        self.bashrc.write_text(self.original)
        fake_bin = Path(self.temp.name) / 'fake bin'
        fake_bin.mkdir()
        self.log = self.home / 'calls.jsonl'
        fake = fake_bin / 'codex'
        fake.write_text('''#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

args = sys.argv[1:]
if args == ['login', 'status']:
    sys.exit(0 if (Path(os.environ['CODEX_HOME']) / 'auth.json').exists() else 1)
with open(os.environ['FAKE_CODEX_LOG'], 'a') as stream:
    stream.write(json.dumps({'args': args, 'home': os.environ.get('CODEX_HOME')}) + '\\n')
sys.exit(int(os.environ.get('FAKE_CODEX_EXIT', '0')))
''')
        fake.chmod(0o755)
        self.env = {
            **os.environ,
            'HOME': str(self.home),
            'PATH': str(fake_bin) + os.pathsep + os.environ.get('PATH', ''),
            'FAKE_CODEX_LOG': str(self.log),
            'FAKE_CODEX_EXIT': '0',
            'CODEX_HOME': str(self.home / 'inherited-codex-home'),
            'TERM': 'xterm-256color',
            'RUN_UNINSTALL_TESTS': os.environ.get('RUN_UNINSTALL_TESTS', ''),
        }
        self.launcher = self.home / '.local/bin/codex-account-switcher'

    def script(self, name):
        return subprocess.run(
            ['bash', str(PROJECT / name)], env=self.env, text=True,
            capture_output=True, timeout=15, check=False,
        )

    def install(self):
        result = self.script('install.sh')
        self.assertEqual(result.returncode, 0, result.stderr)

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def run_menu(self, keys, args=()):
        master, slave = pty.openpty()
        self.addCleanup(os.close, master)
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 30, 100, 0, 0))
        process = subprocess.Popen(
            ['bash', '--noprofile', '--norc', '-ic',
             'source "$HOME/.bashrc"; codex "$@"', 'bash', *args],
            env=self.env, stdin=slave, stdout=slave, stderr=slave,
        )
        os.close(slave)
        output = bytearray()
        sent = False
        deadline = time.monotonic() + 15
        try:
            while time.monotonic() < deadline:
                if select.select([master], [], [], 0.1)[0]:
                    try:
                        chunk = os.read(master, 65536)
                    except OSError:
                        break
                    if not chunk:
                        break
                    output.extend(chunk)
                    if not sent and b'ACCOUNT SWITCHER' in output:
                        os.write(master, keys)
                        sent = True
                if process.poll() is not None:
                    break
            if process.poll() is None:
                process.wait(timeout=2)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
        self.assertTrue(sent, output.decode(errors='replace'))
        return process.returncode, output.decode(errors='replace')


class InstallerTest(ShellFixture):
    def test_install_is_repeatable_and_preserves_shell_settings(self):
        self.install()
        first = self.bashrc.read_text()
        self.install()
        self.assertEqual(self.bashrc.read_text(), first)
        self.assertEqual(first.count(BEGIN), 1)
        self.assertTrue(first.startswith(self.original.rstrip('\n')))
        self.assertEqual((self.home / '.bashrc.before-codex-switcher.bak').read_text(), self.original)
        self.assertTrue(os.access(self.launcher, os.X_OK))

    def test_install_preserves_existing_bin_permissions(self):
        self.launcher.parent.mkdir(parents=True, mode=0o755)
        self.launcher.parent.chmod(0o755)
        self.install()
        self.assertEqual(self.launcher.parent.stat().st_mode & 0o777, 0o755)

    def test_malformed_shell_block_is_not_edited(self):
        for content in [BEGIN + '\n', END + '\n' + BEGIN + '\n', BEGIN + '\n' + BEGIN + '\n' + END]:
            with self.subTest(content=content):
                self.bashrc.write_text(content)
                result = self.script('install.sh')
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.bashrc.read_text(), content)

    def test_noninteractive_passthrough_preserves_arguments_home_and_exit(self):
        self.install()
        self.env['FAKE_CODEX_EXIT'] = '23'
        result = subprocess.run(
            ['bash', '--noprofile', '--norc', '-c',
             'source "$HOME/.bashrc"; codex exec "two words" "" "*"'],
            env=self.env, capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 23, result.stderr)
        self.assertEqual(self.calls(), [{
            'args': ['exec', 'two words', '', '*'],
            'home': self.env['CODEX_HOME'],
        }])
        self.assertFalse((self.home / '.codex-accounts').exists())

    def test_interactive_selection_passes_profile_arguments_and_exit(self):
        self.install()
        for profile in [self.home / '.codex', self.home / '.codex-accounts/work']:
            profile.mkdir(parents=True)
            (profile / 'auth.json').write_text('synthetic-test-credential')
        self.env['FAKE_CODEX_EXIT'] = '17'
        status, output = self.run_menu(b'j\r', ['exec', 'two words', ''])
        self.assertEqual(status, 17, output)
        self.assertEqual(self.calls(), [{
            'args': ['exec', 'two words', ''],
            'home': str(self.home / '.codex-accounts/work'),
        }])
        self.assertEqual((self.home / '.codex/auth.json').read_text(), 'synthetic-test-credential')

    def test_quitting_menu_does_not_launch_or_login(self):
        self.install()
        status, output = self.run_menu(b'q')
        self.assertEqual(status, 0, output)
        self.assertEqual(self.calls(), [])


@unittest.skipUnless(os.environ.get('RUN_UNINSTALL_TESTS') == '1',
                     'Uninstall execution is opt-in; enabled in isolated GitHub CI homes only.')
class UninstallTest(ShellFixture):
    def test_uninstall_preserves_account_data_and_unrelated_shell_content(self):
        self.install()
        suffix = '\n# Settings added after installation\nexport KEEP_TOO=yes\n'
        self.bashrc.write_text(self.bashrc.read_text() + suffix)
        before = self.bashrc.read_text()
        files = {
            '.codex/auth.json': 'synthetic-default-credential',
            '.codex/config.toml': 'model = "example"\n',
            '.codex-accounts/work/auth.json': 'synthetic-work-credential',
            '.codex-accounts/work/sessions/example.json': 'synthetic-session',
            '.codex-accounts/.account-labels.json': '{"version":1,"labels":{"work":"Work"}}',
        }
        for name, text in files.items():
            destination = self.home / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(text)
        for _ in range(2):
            result = self.script('uninstall.sh')
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.launcher.exists())
        self.assertNotIn(BEGIN, self.bashrc.read_text())
        self.assertTrue(self.bashrc.read_text().startswith(self.original))
        self.assertTrue(self.bashrc.read_text().endswith(suffix))
        backup = self.home / '.bashrc.before-codex-switcher-uninstall.bak'
        self.assertEqual(backup.read_text(), before)
        for name, text in files.items():
            self.assertEqual((self.home / name).read_text(), text)

    def test_uninstall_refuses_malformed_blocks_before_removing_launcher(self):
        self.install()
        for content in [BEGIN + '\n', END + '\n' + BEGIN + '\n', BEGIN + '\n' + BEGIN + '\n' + END]:
            with self.subTest(content=content):
                self.bashrc.write_text(content)
                result = self.script('uninstall.sh')
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.bashrc.read_text(), content)
                self.assertTrue(self.launcher.exists())

    def test_uninstall_with_no_installation_is_safe(self):
        self.bashrc.unlink()
        result = self.script('uninstall.sh')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.bashrc.exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
