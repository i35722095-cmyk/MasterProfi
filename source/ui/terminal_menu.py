from __future__ import annotations

import sys
from typing import Sequence


def choose_option(options: Sequence[str]) -> int | None:
    """Show a small arrow-key terminal menu; return a zero-based choice or None."""
    if not options or not (sys.stdin.isatty() and sys.stdout.isatty()):
        return None
    if sys.platform == "win32":
        return _choose_option_windows(options)
    return _choose_option_posix(options)


def _render(options: Sequence[str], selected: int, first: bool = False) -> None:
    line_count = len(options) + 1
    if not first:
        sys.stdout.write(f"\x1b[{line_count}A")
    for index, label in enumerate(options):
        marker = "❯" if index == selected else " "
        text = f" {marker} {index + 1}. {label} "
        if index == selected:
            text = f"\x1b[7m{text}\x1b[0m"
        sys.stdout.write(f"\x1b[2K\r{text}\n")
    sys.stdout.write("\x1b[2K\r↑/↓ — выбрать, Enter — подтвердить, Esc — отменить\n")
    sys.stdout.flush()


def _choose_option_posix(options: Sequence[str]) -> int | None:
    """Raw-mode arrow-key menu for macOS/Linux terminals."""
    import select
    import termios
    import tty
    import os

    selected = 0
    descriptor = sys.stdin.fileno()
    previous = termios.tcgetattr(descriptor)
    try:
        tty.setraw(descriptor)
        sys.stdout.write("\x1b[?25l")
        _render(options, selected, first=True)
        while True:
            key = os.read(descriptor, 1).decode("utf-8", errors="ignore")
            if key in {"\r", "\n"}:
                return selected
            if key in {"j", "J"}:
                selected = min(selected + 1, len(options) - 1)
                _render(options, selected)
                continue
            if key in {"k", "K"}:
                selected = max(selected - 1, 0)
                _render(options, selected)
                continue
            if key.isdigit() and 1 <= int(key) <= len(options):
                selected = int(key) - 1
                _render(options, selected)
                continue
            if key == "\x1b":
                ready, _, _ = select.select([descriptor], [], [], 0.1)
                if not ready:
                    return None
                sequence = os.read(descriptor, 2).decode("utf-8", errors="ignore")
                if sequence == "[A":
                    selected = max(selected - 1, 0)
                    _render(options, selected)
                elif sequence == "[B":
                    selected = min(selected + 1, len(options) - 1)
                    _render(options, selected)
    finally:
        termios.tcsetattr(descriptor, termios.TCSADRAIN, previous)
        sys.stdout.write("\x1b[?25h\n")
        sys.stdout.flush()


def _enable_windows_ansi() -> None:
    """Best-effort: turn on ANSI escape processing in the Windows console."""
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
    except Exception:
        pass


def _choose_option_windows(options: Sequence[str]) -> int | None:
    """Single-keypress arrow-key menu for the Windows console, via msvcrt."""
    import msvcrt

    _enable_windows_ansi()
    selected = 0
    sys.stdout.write("\x1b[?25l")
    _render(options, selected, first=True)
    try:
        while True:
            key = msvcrt.getwch()
            if key in {"\r", "\n"}:
                return selected
            if key in {"j", "J"}:
                selected = min(selected + 1, len(options) - 1)
                _render(options, selected)
                continue
            if key in {"k", "K"}:
                selected = max(selected - 1, 0)
                _render(options, selected)
                continue
            if key.isdigit() and 1 <= int(key) <= len(options):
                selected = int(key) - 1
                _render(options, selected)
                continue
            if key == "\x1b":
                return None
            if key in {"\x00", "\xe0"}:
                # Extended key: a second call yields the scan code (arrows here).
                scan_code = msvcrt.getwch()
                if scan_code == "H":  # Up
                    selected = max(selected - 1, 0)
                    _render(options, selected)
                elif scan_code == "P":  # Down
                    selected = min(selected + 1, len(options) - 1)
                    _render(options, selected)
    finally:
        sys.stdout.write("\x1b[?25h\n")
        sys.stdout.flush()
