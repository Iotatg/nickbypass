"""Remote control for the machine the bot runs on.

The point: once the bot is up, the owner should never have to open a terminal
again. ``/restart``, ``/update``, ``/logs`` and ``/diag`` are handled here so
``handlers.py`` stays free of process plumbing.

Everything is best-effort and read-only until the owner actually asks for a
restart: a missing git checkout, a read-only container or a locked-down Android
just produces a clear message instead of a traceback.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import List, Optional, Tuple

from .config import BASE_DIR

# How long we wait before replacing the process, so the Telegram reply that
# says "restarting" actually leaves the phone first.
RESTART_DELAY = 2.0


# --------------------------------------------------------------------- layout

def repo_root() -> Path:
    """The folder that holds ``nickbot/`` - also the git checkout, if any."""
    return BASE_DIR


def is_git_repo() -> bool:
    return (repo_root() / ".git").exists()


def log_path() -> Path:
    return repo_root() / "data" / "bot.log"


def is_termux() -> bool:
    return "com.termux" in os.environ.get("PREFIX", "") or Path(
        "/data/data/com.termux"
    ).exists()


# ------------------------------------------------------------------------ git

def _git(*args: str, timeout: float = 30.0) -> Tuple[bool, str]:
    if not is_git_repo():
        return False, "This copy is not a git checkout, so there is nothing to pull."
    if shutil.which("git") is None:
        return False, "git is not installed here (pkg install git)."
    try:
        p = subprocess.run(
            ["git", "-C", str(repo_root()), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, "git timed out - the network is probably slow."
    except Exception as exc:  # pragma: no cover - odd filesystems
        return False, f"git failed: {exc}"
    out = (p.stdout or "").strip() + (("\n" + p.stderr.strip()) if p.stderr.strip() else "")
    return p.returncode == 0, out.strip()


def head_commit() -> str:
    ok, out = _git("rev-parse", "--short", "HEAD", timeout=10)
    return out.splitlines()[0] if ok and out else "-"


def branch() -> str:
    ok, out = _git("rev-parse", "--abbrev-ref", "HEAD", timeout=10)
    return out.splitlines()[0] if ok and out else "-"


def pull() -> Tuple[bool, str, bool]:
    """Fast-forward the checkout.

    Returns ``(ok, message, requirements_changed)``. Only ``--ff-only`` is used:
    if the phone has local edits we refuse rather than clobber them.
    """
    if not is_git_repo():
        return False, "Not a git checkout - update it by hand once.", False
    before = head_commit()
    ok, out = _git("pull", "--ff-only", timeout=120)
    if not ok:
        return False, out or "git pull failed.", False
    after = head_commit()
    if before == after:
        return True, f"Already up to date ({after}).", False
    ok2, changed = _git("diff", "--name-only", before, after, timeout=30)
    req = ok2 and any(
        line.strip().startswith("requirements") for line in changed.splitlines()
    )
    return True, f"{before} → {after}\n{out}".strip(), req


def changed_between(before: str, after: str) -> List[str]:
    ok, out = _git("diff", "--name-only", before, after, timeout=30)
    return [line for line in out.splitlines() if line.strip()] if ok else []


# ----------------------------------------------------------------------- logs

def tail_log(lines: int = 25, max_bytes: int = 60_000) -> str:
    p = log_path()
    if not p.exists():
        return ""
    try:
        with p.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - max_bytes))
            data = fh.read().decode("utf-8", "replace")
    except Exception as exc:  # pragma: no cover
        return f"(could not read the log: {exc})"
    got = [ln for ln in data.splitlines() if ln.strip()]
    return "\n".join(got[-lines:])


def rotate_log(max_bytes: int = 5_000_000) -> bool:
    """Keep the log from eating the phone's storage. True if it was rotated."""
    p = log_path()
    try:
        if p.exists() and p.stat().st_size > max_bytes:
            p.replace(p.with_suffix(".log.1"))
            return True
    except Exception:
        pass
    return False


# ------------------------------------------------------------------- restart

def restart_soon(delay: float = RESTART_DELAY) -> None:
    """Replace this process with a fresh one, after ``delay`` seconds.

    ``os.execv`` keeps the PID, so it works the same under tmux, systemd,
    Docker or a plain ``nohup`` - no supervisor required.
    """
    argv = [sys.executable, "-m", "nickbot"]

    def _go() -> None:
        time.sleep(delay)
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        except Exception:
            pass
        try:
            os.execv(sys.executable, argv)
        except Exception:
            os._exit(1)

    threading.Thread(target=_go, name="restart", daemon=False).start()


# ---------------------------------------------------------------- diagnostics

def _read(path: str) -> str:
    try:
        return Path(path).read_text().strip()
    except Exception:
        return ""


def _human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def _mem_android() -> str:
    """Total/free RAM without psutil (Android has no /proc/meminfo guarantee)."""
    info = _read("/proc/meminfo")
    total = free = 0
    for line in info.splitlines():
        if line.startswith("MemTotal:"):
            total = int(line.split()[1]) * 1024
        elif line.startswith("MemAvailable:"):
            free = int(line.split()[1]) * 1024
    if not total:
        return "-"
    return f"{_human(total - free)} / {_human(total)} used"


def _disk() -> str:
    try:
        u = shutil.disk_usage(str(repo_root()))
        return f"{_human(u.free)} free of {_human(u.total)}"
    except Exception:
        return "-"


def _battery() -> str:
    cap = _read("/sys/class/power_supply/battery/capacity")
    status = _read("/sys/class/power_supply/battery/status")
    if not cap:
        return ""
    return f"{cap}%{' · ' + status.lower() if status else ''}"


def _has(mod: str) -> bool:
    try:
        __import__(mod)
        return True
    except Exception:
        return False


def diagnostics(uptime: int) -> List[Tuple[str, str]]:
    """Label/value pairs for the /diag card."""
    rows: List[Tuple[str, str]] = [
        ("Host", "Termux (Android)" if is_termux() else platform.system()),
        ("Python", f"{platform.python_version()} · {platform.machine()}"),
        ("Uptime", f"{uptime // 3600}h {(uptime % 3600) // 60}m"),
        ("Commit", f"{head_commit()} ({branch()})"),
        ("RAM", _mem_android() if is_termux() else "-"),
        ("Disk", _disk()),
    ]
    bat = _battery()
    if bat:
        rows.append(("Battery", bat))
    rows.append(("Log", _human(log_path().stat().st_size) if log_path().exists() else "none"))
    deps = [
        name
        for mod, name in (
            ("pyrogram", "kurigram"),
            ("telegram", "ptb"),
            ("httpx", "httpx"),
            ("h2", "h2"),
            ("playwright", "playwright"),
        )
        if _has(mod)
    ]
    rows.append(("Deps", " · ".join(deps) or "none"))
    return rows


def where_am_i() -> str:
    """One line describing how this process is being kept alive."""
    parts = []
    if is_termux():
        parts.append("Termux")
    if os.environ.get("INVOCATION_ID"):
        parts.append("systemd")
    if Path("/.dockerenv").exists():
        parts.append("docker")
    if os.environ.get("TMUX"):
        parts.append("tmux")
    return " · ".join(parts) or "plain shell"


def autostart_installed() -> Optional[bool]:
    """True/False on Termux (Termux:Boot hook present), None elsewhere."""
    if not is_termux():
        return None
    hook = Path.home() / ".termux" / "boot" / "nickbot.sh"
    return hook.exists()


def stop_marker() -> Path:
    """A file that tells the boot hook not to resurrect a stopped bot."""
    return repo_root() / "data" / "stopped"
