# Contributing

Bug reports and pull requests are welcome. Include the Linux distribution,
Python version, Bash version, terminal type, and steps to reproduce a problem.
Redact account names, tokens, and other personal information from logs.

## Development

The switcher uses only the Python standard library. With Python 3.10 or newer:

```bash
python3 -m unittest discover -s tests -v
bash -n install.sh uninstall.sh
```

Tests use temporary homes and fake Codex executables; they do not need a Codex
login or make network requests. Uninstall execution tests are opt-in:

```bash
RUN_UNINSTALL_TESTS=1 python3 -m unittest discover -s tests -v
```

GitHub Actions runs both test modes, checks Python versions, and tests the
scripts on several Linux distributions. Container checks use a fake Codex
executable; they do not certify upstream Codex support for a distribution.

## Changes

- Preserve existing credentials, configuration, and sessions.
- Keep profile paths stable when display names change.
- Keep the noninteractive wrapper transparent to the Codex CLI.
- Add regression coverage for changes to account storage or shell integration.
- Keep dependencies in the standard library unless a dependency is necessary
  and discussed in the pull request.
