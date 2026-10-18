#!/usr/bin/env python3
"""Interactive, non-destructive multi-account selector for OpenAI Codex CLI.

Only stores display labels in ~/.codex-accounts/.account-labels.json.
Existing account directories, sessions, configuration and auth files are never
moved, copied, rewritten or deleted by this program.
"""
from __future__ import annotations

import concurrent.futures
import curses
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass
from typing import Any


DEFAULT_KEY = "__default__"
META_FILE = ".account-labels.json"
STATUS_TIMEOUT = 6


@dataclass(frozen=True)
class Account:
    key: str
    name: str
    home: Path
    default: bool = False


def root_dir() -> Path:
    return Path.home() / ".codex-accounts"


def default_dir() -> Path:
    return Path.home() / ".codex"


def prepare_root() -> Path:
    root = root_dir()
    if root.is_symlink():
        raise RuntimeError(f"Refusing symlinked account root: {root}")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root


def read_labels(root: Path) -> dict[str, str]:
    file = root / META_FILE
    if not file.exists():
        return {}
    try:
        raw = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"Cannot read labels ({file}): {exc}") from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("labels"), dict):
        raise RuntimeError(f"Unexpected labels format in {file}; refusing to overwrite it")
    return {
        key: value
        for key, value in raw["labels"].items()
        if isinstance(key, str) and isinstance(value, str)
    }


def write_labels(root: Path, labels: dict[str, str]) -> None:
    """Atomic metadata save. Never touches Codex credentials/configuration."""
    fd, tmp = tempfile.mkstemp(prefix=".labels-", suffix=".tmp", dir=root)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump({"version": 1, "labels": labels}, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, root / META_FILE)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def get_accounts(root: Path, labels: dict[str, str]) -> list[Account]:
    accounts = [
        Account(DEFAULT_KEY, labels.get(DEFAULT_KEY, "Default (existing)"), default_dir(), True)
    ]
    for entry in sorted(root.iterdir(), key=lambda path: path.name.casefold()):
        if entry.name.startswith(".") or entry.is_symlink() or not entry.is_dir():
            continue
        accounts.append(Account(entry.name, labels.get(entry.name, entry.name), entry))
    return accounts


def auth_status(binary: str, home: Path) -> str:
    env = os.environ.copy()
    env["CODEX_HOME"] = str(home)
    try:
        p = subprocess.run(
            [binary, "login", "status"], env=env,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=STATUS_TIMEOUT, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    if p.returncode == 0:
        return "ready"
    # A stored credential that fails validation must not be treated as absent.
    if (home / "auth.json").exists():
        return "stored"
    return "offline"


def create_account(root: Path, labels: dict[str, str], name: str) -> Account:
    name = name.strip()
    if not name:
        raise ValueError("Account name cannot be empty")
    if len(name) > 48:
        raise ValueError("Account name must be 48 characters or less")
    accounts = get_accounts(root, labels)
    if any(account.name.casefold() == name.casefold() for account in accounts):
        raise ValueError("That display name is already being used")

    # Stable ID unrelated to the display label: renaming never moves a home.
    while True:
        key = "account-" + uuid.uuid4().hex[:12]
        home = root / key
        try:
            home.mkdir(mode=0o700)
            break
        except FileExistsError:
            pass

    # Only new accounts receive new config. Existing config is never rewritten.
    try:
        with (home / "config.toml").open("x", encoding="utf-8") as f:
            f.write('cli_auth_credentials_store = "file"\n')
        new_labels = dict(labels)
        new_labels[key] = name
        write_labels(root, new_labels)
        labels.clear()
        labels.update(new_labels)
    except Exception:
        # Keep the just-created directory intact rather than risking data loss.
        raise
    return Account(key, name, home)


def rename_account(root: Path, labels: dict[str, str], account: Account, new_name: str) -> None:
    new_name = new_name.strip()
    if not new_name:
        raise ValueError("Account name cannot be empty")
    if len(new_name) > 48:
        raise ValueError("Account name must be 48 characters or less")
    if any(
        other.key != account.key and other.name.casefold() == new_name.casefold()
        for other in get_accounts(root, labels)
    ):
        raise ValueError("That display name is already being used")
    updated = dict(labels)
    updated[account.key] = new_name
    write_labels(root, updated)
    labels.clear()
    labels.update(updated)


def terminal_text(value: str) -> str:
    """Prevent special terminal control characters in user-supplied labels."""
    return "".join(ch if ch.isprintable() else " " for ch in value)


class AccountMenu:
    def __init__(self, binary: str, root: Path, labels: dict[str, str]) -> None:
        self.binary = binary
        self.root = root
        self.labels = labels
        self.accounts = get_accounts(root, labels)
        self.selected = 0
        self.notice = ""
        self.statuses: dict[str, str] = {}
        self._tasks: dict[str, concurrent.futures.Future[str]] = {}

    def _start_checks(self, executor: concurrent.futures.ThreadPoolExecutor) -> None:
        for account in self.accounts:
            self.statuses[account.key] = "checking"
            self._tasks[account.key] = executor.submit(auth_status, self.binary, account.home)

    def _finish_checks(self) -> None:
        for key, future in list(self._tasks.items()):
            if future.done():
                try:
                    self.statuses[key] = future.result()
                except Exception:
                    self.statuses[key] = "unknown"
                del self._tasks[key]

    @staticmethod
    def _style(stdscr: Any) -> None:
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        stdscr.keypad(True)
        stdscr.timeout(120)
        if curses.has_colors():
            curses.start_color()
            try:
                curses.use_default_colors()
                background = -1
            except curses.error:
                background = curses.COLOR_BLACK
            curses.init_pair(1, curses.COLOR_CYAN, background)
            curses.init_pair(2, curses.COLOR_GREEN, background)
            curses.init_pair(3, curses.COLOR_YELLOW, background)
            curses.init_pair(4, curses.COLOR_WHITE, background)

    @staticmethod
    def _color(number: int) -> int:
        return curses.color_pair(number) if curses.has_colors() else 0

    @staticmethod
    def _put(win: Any, y: int, x: int, s: str, attrs: int = 0) -> None:
        height, width = win.getmaxyx()
        if y < 0 or y >= height or x < 0 or x >= width - 1:
            return
        try:
            win.addnstr(y, x, terminal_text(str(s)), max(0, width - x - 1), attrs)
        except curses.error:
            pass

    def _render(self, stdscr: Any) -> None:
        stdscr.erase()
        height, width = stdscr.getmaxyx()
        if width < 54 or height < 17:
            self._put(stdscr, 1, 2, "Resize terminal to at least 54 columns x 17 rows.")
            self._put(stdscr, 3, 2, "Q to quit")
            stdscr.refresh()
            return
        span = min(width - 4, 92)
        x = (width - span) // 2
        top = 1
        status_count = sum(v == "ready" for v in self.statuses.values())

        self._put(stdscr, top, x, "CODEX   /   ACCOUNT SWITCHER", self._color(1) | curses.A_BOLD)
        self._put(stdscr, top + 2, x, "Select an isolated Codex profile to start a session.", curses.A_BOLD)
        self._put(stdscr, top + 3, x, f"{len(self.accounts)} profiles  ·  {status_count} signed in  ·  Existing authentication preserved")
        self._put(stdscr, top + 5, x, "─" * span, self._color(1))

        # Two lines per entry, scroll for a large number of profiles.
        rows = max(1, (height - 15) // 2)
        first = max(0, min(self.selected - rows // 2, len(self.accounts) - rows))
        for index in range(first, min(len(self.accounts), first + rows)):
            account = self.accounts[index]
            y = top + 7 + 2 * (index - first)
            selected = index == self.selected
            status = self.statuses.get(account.key, "checking")
            status_name = {
                "ready": "● SIGNED IN",
                "offline": "○ SIGN IN REQUIRED",
                "stored": "! CHECK LOGIN",
                "unknown": "? STATUS UNKNOWN",
                "checking": "◌ CHECKING",
            }.get(status, "? UNKNOWN")
            attr = curses.A_REVERSE | curses.A_BOLD if selected else curses.A_BOLD
            prefix = "❯" if selected else " "
            # Keep the right-side badge visible even with long labels.
            badge_at = max(1, span - len(status_name) - 2)
            self._put(stdscr, y, x, " " * min(span, width - x - 1), curses.A_REVERSE if selected else 0)
            self._put(stdscr, y, x + 1, f"{prefix}  {account.name[:max(1, badge_at - 7)]}", attr)
            self._put(stdscr, y, x + badge_at, status_name,
                      curses.A_REVERSE if selected else self._color(2 if status == "ready" else 3))
            show_home = str(account.home)
            try:
                show_home = "~/" + str(account.home.relative_to(Path.home()))
            except ValueError:
                pass
            self._put(stdscr, y + 1, x + 5, show_home[:span - 6], curses.A_DIM)

        footer = height - 5
        self._put(stdscr, footer - 1, x, "─" * span, self._color(1))
        self._put(stdscr, footer, x, "↑/↓  Navigate       ENTER  Launch       A  Add       R  Rename", curses.A_BOLD)
        self._put(stdscr, footer + 1, x, "L    Login         S  Refresh status             Q  Quit")
        if self.notice:
            self._put(stdscr, footer + 3, x, self.notice[:span - 1], self._color(3))
        stdscr.refresh()

    def _ask(self, stdscr: Any, heading: str, initial: str = "") -> str | None:
        height, width = stdscr.getmaxyx()
        if height < 17 or width < 54:
            self.notice = "Resize the terminal before editing an account."
            return None
        buffer = list(initial)
        stdscr.timeout(-1)
        try:
            try:
                curses.curs_set(1)
            except curses.error:
                pass
            while True:
                self._render(stdscr)
                mid_y = height // 2 - 3
                panel_width = min(width - 4, 70)
                panel_x = (width - panel_width) // 2
                panel = curses.newwin(7, panel_width, mid_y, panel_x)
                panel.erase()
                panel.box()
                self._put(panel, 1, 2, heading[:panel_width - 5], self._color(1) | curses.A_BOLD)
                self._put(panel, 2, 2, "Display name (credentials stay in place)")
                shown = "".join(buffer)
                usable = panel_width - 6
                shown = shown[-usable:]
                self._put(panel, 3, 2, " " * usable, curses.A_REVERSE)
                self._put(panel, 3, 2, shown, curses.A_REVERSE)
                self._put(panel, 5, 2, "Enter: Save      Esc: Cancel      Ctrl-U: Clear")
                panel.refresh()
                try:
                    panel.move(3, min(panel_width - 4, 2 + len(shown)))
                    panel.refresh()
                except curses.error:
                    pass
                key = stdscr.get_wch()
                if key in ("\n", "\r", curses.KEY_ENTER):
                    result = "".join(buffer).strip()
                    return result if result else None
                if key in ("\x1b", "\x03"):
                    return None
                if key == "\x15":  # Ctrl-U
                    buffer.clear()
                elif key in (curses.KEY_BACKSPACE, "\b", "\x7f"):
                    if buffer:
                        buffer.pop()
                elif isinstance(key, str) and key.isprintable() and len(buffer) < 48:
                    buffer.append(key)
        finally:
            try:
                curses.curs_set(0)
            except curses.error:
                pass
            stdscr.timeout(120)

    def _confirm_login(self, stdscr: Any, account: Account) -> bool:
        """Require explicit confirmation before replacing any saved credentials."""
        h, w = stdscr.getmaxyx()
        if w < 54 or h < 17:
            self.notice = "Resize the terminal before changing a login."
            return False
        stdscr.timeout(-1)
        try:
            while True:
                self._render(stdscr)
                panel_w = min(w - 4, 72)
                panel = curses.newwin(7, panel_w, h // 2 - 3, (w - panel_w) // 2)
                panel.erase()
                panel.box()
                self._put(panel, 1, 2, "Replace existing login?", self._color(3) | curses.A_BOLD)
                self._put(panel, 2, 2, account.name[:panel_w - 5])
                self._put(panel, 3, 2, "A failed login can invalidate this account's saved sign-in.")
                self._put(panel, 5, 2, "Y: Continue      N / Esc: Keep existing login")
                panel.refresh()
                key = stdscr.get_wch()
                if key in ("y", "Y"):
                    return True
                if key in ("n", "N", "\x1b", "\n", "\r", "\x03"):
                    return False
        finally:
            stdscr.timeout(120)

    def run(self, stdscr: Any) -> tuple[str, Account | None]:
        self._style(stdscr)
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            self._start_checks(executor)
            while True:
                self._finish_checks()
                self._render(stdscr)
                ch = stdscr.getch()
                if ch == -1 or ch == curses.KEY_RESIZE:
                    continue
                if ch in (curses.KEY_UP, ord("k"), ord("K")):
                    self.selected = (self.selected - 1) % len(self.accounts)
                    self.notice = ""
                elif ch in (curses.KEY_DOWN, ord("j"), ord("J")):
                    self.selected = (self.selected + 1) % len(self.accounts)
                    self.notice = ""
                elif ch in (10, 13, curses.KEY_ENTER):
                    return "launch", self.accounts[self.selected]
                elif ch in (ord("q"), ord("Q"), 27):
                    return "quit", None
                elif ch in (ord("r"), ord("R")):
                    account = self.accounts[self.selected]
                    value = self._ask(stdscr, "Rename account", account.name)
                    if value is not None:
                        try:
                            rename_account(self.root, self.labels, account, value)
                            self.accounts = get_accounts(self.root, self.labels)
                            self.notice = "Renamed. Login, folder and running sessions unchanged."
                        except (OSError, ValueError) as exc:
                            self.notice = str(exc)
                elif ch in (ord("a"), ord("A")):
                    value = self._ask(stdscr, "Add Codex account")
                    if value is not None:
                        try:
                            new_account = create_account(self.root, self.labels, value)
                            self.accounts = get_accounts(self.root, self.labels)
                            self.selected = next(i for i, a in enumerate(self.accounts) if a.key == new_account.key)
                            return "login", new_account
                        except (OSError, ValueError) as exc:
                            self.notice = str(exc)
                elif ch in (ord("l"), ord("L")):
                    account = self.accounts[self.selected]
                    # Explicit confirmation prevents accidental replacement of
                    # existing login data, including an auth file with errors.
                    has_saved_login = (account.home / "auth.json").exists()
                    if not has_saved_login:
                        has_saved_login = (self.statuses.get(account.key) == "ready"
                                           or auth_status(self.binary, account.home) == "ready")
                    if has_saved_login:
                        if not self._confirm_login(stdscr, account):
                            self.notice = "Existing login preserved; re-login cancelled."
                            continue
                    return "login", account
                elif ch in (ord("s"), ord("S")):
                    # Avoid duplicate checks if a previous check is still active.
                    for account in self.accounts:
                        if account.key not in self._tasks:
                            self.statuses[account.key] = "checking"
                            self._tasks[account.key] = executor.submit(auth_status, self.binary, account.home)
                    self.notice = "Refreshing login status without modifying credentials."


def reenter_menu(binary: str, root: Path, focus_key: str | None = None) -> tuple[str, Account | None]:
    labels = read_labels(root)
    app = AccountMenu(binary, root, labels)
    if focus_key:
        for i, account in enumerate(app.accounts):
            if account.key == focus_key:
                app.selected = i
                break
    try:
        return curses.wrapper(app.run)
    except curses.error as exc:
        print(f"Terminal UI error: {exc}", file=sys.stderr)
        print("Try an interactive terminal with TERM=xterm-256color.", file=sys.stderr)
        return "quit", None


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: codex-account-switcher.py /path/to/codex [codex arguments...]", file=sys.stderr)
        return 2
    binary = sys.argv[1]
    arguments = sys.argv[2:]
    try:
        root = prepare_root()
    except (OSError, RuntimeError) as exc:
        print(f"Cannot access account storage: {exc}", file=sys.stderr)
        return 1
    focus_key = None
    while True:
        try:
            action, account = reenter_menu(binary, root, focus_key)
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        if action == "quit" or account is None:
            return 0
        env = os.environ.copy()
        env["CODEX_HOME"] = str(account.home)
        if action == "login":
            print(f"\nSigning in to: {account.name}")
            print(f"Codex home: {account.home}\n")
            result = subprocess.run([binary, "login"], env=env, check=False)
            if result.returncode:
                print(f"Login exited with status {result.returncode}.")
            focus_key = account.key
            continue
        if action == "launch":
            # Automatic first login ONLY for homes with no existing auth file
            # and no recognized login. Never overwrite existing auth implicitly.
            if not (account.home / "auth.json").exists() and auth_status(binary, account.home) == "offline":
                print(f"\nFirst login for: {account.name}\n")
                result = subprocess.run([binary, "login"], env=env, check=False)
                if result.returncode:
                    print("Login was not completed; Codex was not launched.", file=sys.stderr)
                    return result.returncode
            print(f"Launching Codex with {account.name} ...", flush=True)
            try:
                os.execvpe(binary, [binary, *arguments], env)
            except OSError as exc:
                print(f"Cannot launch Codex: {exc}", file=sys.stderr)
                return 127
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print()
        sys.exit(130)
