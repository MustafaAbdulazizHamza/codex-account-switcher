#!/usr/bin/env bash
set -euo pipefail

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is required to safely remove the Bash integration." >&2
  exit 1
fi

python3 - "$HOME/.bashrc" "$HOME/.local/bin/codex-account-switcher" <<'PY'
from pathlib import Path
import shutil
import sys

bashrc = Path(sys.argv[1])
launcher = Path(sys.argv[2])
begin = '# >>> codex account switcher (interactive) >>>'
end = '# <<< codex account switcher (interactive) <<<'

original = bashrc.read_text(encoding='utf-8') if bashrc.exists() else ''
updated = original
if begin in original or end in original:
    if original.count(begin) != 1 or original.count(end) != 1:
        raise SystemExit('Malformed Codex switcher block in .bashrc; nothing was removed.')
    start = original.index(begin)
    if original.index(end) < start:
        raise SystemExit('Malformed Codex switcher block in .bashrc; nothing was removed.')
    finish = original.index(end, start) + len(end)
    if original[finish:finish + 1] == '\n':
        finish += 1
    updated = original[:start] + original[finish:]

if updated != original:
    backup = bashrc.with_name('.bashrc.before-codex-switcher-uninstall.bak')
    # Keep the first backup intact across repeated uninstallations.
    if not backup.exists():
        shutil.copy2(bashrc, backup)
    bashrc.write_text(updated, encoding='utf-8')
    print('Removed Codex Account Switcher block from', bashrc)

try:
    launcher.unlink()
    print('Removed', launcher)
except FileNotFoundError:
    pass

print('Uninstall complete. Account homes, credentials, labels, and sessions were preserved.')
print('Open a new terminal, or run: unset -f codex; source ~/.bashrc')
PY
