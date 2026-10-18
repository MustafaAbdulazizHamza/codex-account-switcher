"""Non-networked regression tests for account-discovery and credential preservation."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

MODULE = Path(__file__).resolve().parents[1] / 'codex-account-switcher.py'
spec = importlib.util.spec_from_file_location('codex_switcher', MODULE)
switcher = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = switcher
spec.loader.exec_module(switcher)

class AccountsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / 'home'
        self.home.mkdir()
        self.patch = patch.dict(os.environ, {'HOME': str(self.home)})
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.default = self.home / '.codex'
        self.default.mkdir()
        (self.default / 'auth.json').write_bytes(b'SECRET_DEFAULT')
        self.root = switcher.prepare_root()
        self.old = self.root / 'work'
        self.old.mkdir()
        (self.old / 'auth.json').write_bytes(b'SECRET_WORK')
        (self.old / 'config.toml').write_text('model = "custom"\n')
        (self.old / 'sessions').mkdir()
        (self.old / 'sessions' / 'sample.json').write_text('original session')

    def test_discovery_preserves_old_accounts(self):
        accounts = switcher.get_accounts(self.root, {})
        self.assertEqual([a.key for a in accounts], [switcher.DEFAULT_KEY, 'work'])
        self.assertEqual(accounts[0].home, self.default)
        self.assertEqual(accounts[1].home, self.old)

    def test_rename_changes_only_metadata(self):
        labels = {}
        original_account = switcher.get_accounts(self.root, labels)[1]
        switcher.rename_account(self.root, labels, original_account, 'Engineering 🔧')
        self.assertEqual(switcher.read_labels(self.root)['work'], 'Engineering 🔧')
        self.assertEqual((self.old / 'auth.json').read_bytes(), b'SECRET_WORK')
        self.assertEqual((self.old / 'config.toml').read_text(), 'model = "custom"\n')
        self.assertEqual((self.old / 'sessions' / 'sample.json').read_text(), 'original session')
        self.assertFalse((self.root / 'Engineering 🔧').exists())

    def test_add_never_modifies_original_logins(self):
        labels = {}
        new_account = switcher.create_account(self.root, labels, 'New Team')
        self.assertTrue(new_account.home.is_dir())
        self.assertEqual((new_account.home / 'config.toml').read_text(),
                         'cli_auth_credentials_store = "file"\n')
        self.assertFalse((new_account.home / 'auth.json').exists())
        self.assertEqual((self.old / 'auth.json').read_bytes(), b'SECRET_WORK')
        self.assertEqual((self.default / 'auth.json').read_bytes(), b'SECRET_DEFAULT')

    def test_login_status_check_is_read_only(self):
        binary = Path(self.temp.name) / 'codex-fake'
        binary.write_text('#!/bin/sh\nif [ "$1" = login ] && [ "$2" = status ]; then '
                          'test -f "$CODEX_HOME/auth.json"; exit $?; fi\nexit 0\n')
        binary.chmod(0o755)
        self.assertEqual(switcher.auth_status(str(binary), self.old), 'ready')
        self.assertEqual(switcher.auth_status(str(binary), self.home / 'missing'), 'offline')
        self.assertEqual((self.old / 'auth.json').read_bytes(), b'SECRET_WORK')

    def test_duplicate_name_rejected(self):
        labels = {}
        with self.assertRaises(ValueError):
            switcher.create_account(self.root, labels, 'WORK')
        self.assertEqual((self.old / 'auth.json').read_bytes(), b'SECRET_WORK')

    def test_failed_status_preserves_stored_credentials(self):
        with patch.object(subprocess, 'run', return_value=subprocess.CompletedProcess([], 1)):
            self.assertEqual(switcher.auth_status('/fake/codex', self.old), 'stored')
        self.assertEqual((self.old / 'auth.json').read_bytes(), b'SECRET_WORK')

    def test_status_failure_is_unknown(self):
        with patch.object(subprocess, 'run', side_effect=subprocess.TimeoutExpired('codex', 6)):
            self.assertEqual(switcher.auth_status('/fake/codex', self.old), 'unknown')

    def test_discovery_ignores_symlinks_and_hidden_directories(self):
        (self.root / 'linked').symlink_to(self.old, target_is_directory=True)
        (self.root / '.hidden').mkdir()
        accounts = switcher.get_accounts(self.root, {})
        self.assertEqual([a.key for a in accounts], [switcher.DEFAULT_KEY, 'work'])

    def test_symlinked_root_is_rejected(self):
        alternate = self.home / 'alternate'
        alternate.mkdir()
        link = self.home / 'linked-root'
        link.symlink_to(alternate, target_is_directory=True)
        with patch.object(switcher, 'root_dir', return_value=link):
            with self.assertRaises(RuntimeError):
                switcher.prepare_root()

    def test_new_storage_has_private_permissions(self):
        account = switcher.create_account(self.root, {}, 'Private')
        self.assertEqual(self.root.stat().st_mode & 0o777, 0o700)
        self.assertEqual(account.home.stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.root / switcher.META_FILE).stat().st_mode & 0o777, 0o600)

    def test_malformed_labels_are_not_overwritten(self):
        metadata = self.root / switcher.META_FILE
        metadata.write_text('{broken')
        with self.assertRaises(RuntimeError):
            switcher.read_labels(self.root)
        self.assertEqual(metadata.read_text(), '{broken')

if __name__ == '__main__':
    unittest.main(verbosity=2)
