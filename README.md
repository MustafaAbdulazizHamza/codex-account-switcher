# Codex Account Switcher

[![Tests](https://github.com/MustafaAbdulazizHamza/codex-account-switcher/actions/workflows/tests.yml/badge.svg)](https://github.com/MustafaAbdulazizHamza/codex-account-switcher/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

A terminal account picker for [OpenAI Codex CLI](https://developers.openai.com/codex/cli/)
on Linux. Choose a profile with the arrow keys, keep work and personal accounts
separate, and use different accounts in parallel terminals.

```text
CODEX   /   ACCOUNT SWITCHER

Select an isolated Codex profile to start a session.

  >  Personal                              SIGNED IN
     ~/.codex
     Work                                  SIGNED IN
     ~/.codex-accounts/account-…

↑/↓ Navigate    ENTER Launch    A Add    R Rename
L Login        S Refresh status        Q Quit
```

*Illustrative menu; appearance depends on terminal size and color support.*

## Features

- Navigate with arrow keys or `J` / `K`.
- Add profiles and sign in through the official Codex CLI.
- Rename display names without moving account directories.
- Keep separate configuration and session storage for each profile.
- Discover existing profiles in `~/.codex-accounts/`.
- Forward Codex arguments, including `resume`, `exec`, and `--version`.
- Leave piped and noninteractive commands on the original Codex executable.
- Install for the current user without `sudo` or Python package dependencies.

## Requirements and Linux support

- A working **Codex CLI** executable on `PATH`.
- **Bash** for installation and shell integration.
- **Python 3.10+** with the standard-library **`curses`** module.
- The `install` utility, typically provided by coreutils or BusyBox.
- An interactive terminal with terminfo support, a UTF-8 locale, and at least
  **54 columns × 17 rows**.

The switcher has no Ubuntu-specific runtime commands or compiled extensions.
It is intended for Linux distributions that meet these requirements. GitHub
Actions checks Ubuntu, Debian, Fedora, and Alpine environments, plus Python
3.10–3.14. These tests use a fake Codex executable to verify the switcher and
installer. **Codex itself must support the distribution and CPU architecture**;
passing switcher tests alone does not establish that, particularly on musl-based
distributions such as Alpine. Universal Linux compatibility is not guaranteed.

Automatic shell integration supports **Bash**. Zsh and Fish integration is not
included. The menu can also be invoked directly from other shells.

Verify the prerequisites:

```bash
codex --version
bash --version
python3 -c 'import sys, curses; assert sys.version_info >= (3, 10); print(sys.version)'
```

## Install

Install the [official Codex CLI](https://developers.openai.com/codex/cli/) first,
then clone this repository and run the installer as a regular user:

```bash
git clone https://github.com/MustafaAbdulazizHamza/codex-account-switcher.git
cd codex-account-switcher
bash install.sh
source ~/.bashrc
codex
```

The installer copies the launcher to `~/.local/bin/codex-account-switcher` and
adds a marked `codex()` function to `~/.bashrc`. Before changing an existing
`.bashrc`, it saves a one-time backup at
`~/.bashrc.before-codex-switcher.bak`. Running the installer again updates its
block without adding duplicate blocks.

An earlier `codex()` function remains in the file, but the switcher's later
definition takes precedence. An existing `codex` alias can interfere; remove
that alias before loading the wrapper.

## Usage

Run `codex` in an interactive Bash terminal and select an account. Arguments are
passed to the selected Codex process:

```bash
codex
codex resume
codex exec "Explain this repository"
```

| Key | Action |
| --- | --- |
| `↑` / `↓` or `K` / `J` | Select an account |
| `Enter` | Launch Codex with the selected account |
| `A` | Create a profile and start login |
| `R` | Rename the selected display name |
| `L` | Sign in or explicitly replace a saved login |
| `S` | Refresh login status |
| `Q` / `Esc` | Quit |

Inside name prompts, `Ctrl-U` clears the name and `Esc` cancels.
Names can contain up to 48 characters.

To bypass the picker in Bash:

```bash
command codex --version
```

To launch the picker directly, including from Zsh or Fish, supply the actual
path to the Codex executable:

```bash
python3 ~/.local/bin/codex-account-switcher /absolute/path/to/codex
```

## Account storage

| Path | Purpose |
| --- | --- |
| `~/.codex/` | Default Codex profile |
| `~/.codex-accounts/<directory>/` | Additional profile homes |
| `~/.codex-accounts/.account-labels.json` | Display names only |

Each launched process gets a profile-specific `CODEX_HOME`. The picker always
uses `~/.codex` for its default profile, even if the parent shell has a different
`CODEX_HOME`. It discovers non-hidden direct child directories of
`~/.codex-accounts/`; symlinked profiles are skipped and a symlinked account root
is rejected.

Discovery and renaming do not move or copy credentials, rewrite existing
configuration, or relocate sessions. New profiles receive a private directory
and a `config.toml` containing `cli_auth_credentials_store = "file"` to keep
credentials in that profile. See the official
[Codex configuration reference](https://developers.openai.com/codex/config-reference/)
for credential storage settings.

Normal launch reuses a saved login. A profile with no `auth.json` is offered
login only when `codex login status` reports it is not signed in. Explicit
re-login asks for confirmation when a saved login is detected. Codex may update
its own credentials, configuration, and sessions during login and use.

Account homes contain authentication tokens. Keep them private and exclude
them from Git repositories and shared archives. This is an independent community
project, unaffiliated with OpenAI.

## Update

From the cloned repository:

```bash
git pull --ff-only
bash install.sh
source ~/.bashrc
```

## Uninstall

From the cloned repository, run:

```bash
bash uninstall.sh
```

This removes the installed launcher and only the marked switcher block from
`~/.bashrc`. It backs up `.bashrc` before editing it and refuses to edit a
malformed block. Repeated uninstalls are safe.

**Account directories, credentials, display labels, configuration, and sessions
are preserved.** The original Codex CLI and this cloned repository remain.

Open a new terminal, or reload the current Bash session:

```bash
unset -f codex
source ~/.bashrc
```

Any earlier `codex()` definition in `.bashrc` becomes active again after reload.

## Troubleshooting

- **No menu:** use an interactive Bash terminal, run `source ~/.bashrc`, and
  check `type codex`. Pipes and noninteractive shells intentionally skip the menu.
- **Missing Python or curses:** install Python and its curses support with the
  distribution's package manager. Package names vary by distribution.
- **Terminal UI error:** check that `TERM` matches the terminal, its terminfo
  entry is installed, and the terminal is large enough.
- **Unknown login status:** press `S` to retry. Check one profile directly in Bash
  with `CODEX_HOME="$HOME/.codex-accounts/<directory>" "$(type -P codex)" login status`.
- **Missing preferences or history in a new profile:** each profile has a separate
  Codex home; it does not inherit the default profile's configuration or sessions.

## Contributing and license

See [CONTRIBUTING.md](CONTRIBUTING.md) for development and testing instructions.
Released under the [MIT License](LICENSE).
