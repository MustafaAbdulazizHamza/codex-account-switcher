#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source_file="$script_dir/codex-account-switcher.py"
destination="$HOME/.local/bin/codex-account-switcher"

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3.10 or newer is required. Install it with your distribution's package manager." >&2
  exit 1
fi
if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
  echo "Python 3.10 or newer is required." >&2
  exit 1
fi
if ! python3 -c 'import curses' >/dev/null 2>&1; then
  echo "Python curses support is required. Install your distribution's Python curses package." >&2
  exit 1
fi
if [[ ! -f "$source_file" ]]; then
  echo "Missing $source_file (extract both files before installing)." >&2
  exit 1
fi
if ! type -P codex >/dev/null 2>&1; then
  echo "Codex CLI must already be installed and on PATH." >&2
  exit 1
fi

mkdir -p "$HOME/.local/bin"
install -m 755 "$source_file" "$destination"

# Keep shell integration in a marked block so updates and removal are predictable.
python3 - "$HOME/.bashrc" <<'PY'
from pathlib import Path
import shutil
import sys

bashrc = Path(sys.argv[1])
begin = '# >>> codex account switcher (interactive) >>>'
end = '# <<< codex account switcher (interactive) <<<'
block = r'''# >>> codex account switcher (interactive) >>>
codex() {
  local codex_binary
  codex_binary="$(type -P codex)" || return 127
  if [[ -z "$codex_binary" ]]; then
    printf 'Codex executable not found on PATH.\n' >&2
    return 127
  fi
  # Avoid running a curses menu for noninteractive scripts or pipelines.
  if [[ $- != *i* || ! -t 0 || ! -t 1 ]]; then
    "$codex_binary" "$@"
    return $?
  fi
  python3 "$HOME/.local/bin/codex-account-switcher" "$codex_binary" "$@"
}
# <<< codex account switcher (interactive) <<<'''

original = bashrc.read_text(encoding='utf-8') if bashrc.exists() else ''
if begin in original or end in original:
    if original.count(begin) != 1 or original.count(end) != 1:
        raise RuntimeError('Malformed existing Codex switcher block in .bashrc; not editing')
    start = original.index(begin)
    if original.index(end) < start:
        raise RuntimeError('Malformed existing Codex switcher block in .bashrc; not editing')
    finish = original.index(end, start) + len(end)
    updated = original[:start] + block + original[finish:]
else:
    updated = original.rstrip('\n') + '\n\n' + block + '\n'

if updated != original:
    if bashrc.exists():
        backup = bashrc.with_name('.bashrc.before-codex-switcher.bak')
        if not backup.exists():
            shutil.copy2(bashrc, backup)
    bashrc.write_text(updated, encoding='utf-8')
print('Installed interactive codex() wrapper to', bashrc)
PY

echo
echo "Installed successfully."
echo "Run: source ~/.bashrc"
echo "Then: codex"
echo "Preserved: ~/.codex and ~/.codex-accounts/*"
