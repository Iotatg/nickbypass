"""Remote-control tests (offline).

Covers the plumbing behind /restart, /update, /logs and /diag: log tailing,
rotation, diagnostics rows, the boot hook detection, and the owner gate that
stops a stranger from restarting the owner's bot.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from nickbot import format as F          # noqa: E402
from nickbot import handlers as H        # noqa: E402
from nickbot import remote as R          # noqa: E402

ok = fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  ✓ {name}")
    else:
        fail += 1
        print(f"  ✗ {name}  {extra}")


class FakeEvent:
    """The smallest thing the handlers actually touch."""

    def __init__(self, uid=1, args=(), text=""):
        self.uid = uid
        self.args = list(args)
        self.text = text
        self.said = []
        self.rt = SimpleNamespace(
            cfg=SimpleNamespace(is_owner=lambda u: u == 1),
            started=time.time() - 3725,
            db=SimpleNamespace(is_banned=lambda u: False,
                               touch_user=lambda *a, **k: None),
        )

    async def reply(self, text, keyboard=()):
        self.said.append(text)
        return SimpleNamespace(edit=self._edit)

    async def _edit(self, text, keyboard=()):
        self.said.append(text)


def main() -> int:
    print("\n[1] layout and git detection")
    check("repo root holds nickbot/", (R.repo_root() / "nickbot" / "__main__.py").exists())
    check("sandbox copy is not a git checkout", R.is_git_repo() is False)
    check("head_commit degrades to '-'", R.head_commit() == "-")
    check("pull refuses politely", R.pull()[0] is False)

    print("\n[2] log tail")
    log = R.log_path()
    log.parent.mkdir(parents=True, exist_ok=True)
    backup = log.read_bytes() if log.exists() else None
    log.write_text("\n".join(f"line {i}" for i in range(1, 101)) + "\n")
    tail = R.tail_log(5)
    check("last 5 lines only", tail.splitlines()[-1] == "line 100" and len(tail.splitlines()) == 5)
    check("tail keeps order", tail.splitlines()[0] == "line 96")
    check("blank lines dropped", all(ln.strip() for ln in tail.splitlines()))
    if backup is not None:
        log.write_bytes(backup)
    else:
        log.unlink(missing_ok=True)

    print("\n[3] log rotation")
    big = log
    big.parent.mkdir(parents=True, exist_ok=True)
    big.write_text("x" * 5000)
    check("small log untouched", R.rotate_log(10_000_000) is False)
    check("big log rotated", R.rotate_log(1000) is True)
    check("rotated copy exists", big.with_suffix(".log.1").exists())
    big.with_suffix(".log.1").unlink(missing_ok=True)
    big.unlink(missing_ok=True)

    print("\n[4] restart plumbing")
    calls = {}
    real_execv, real_sleep = os.execv, time.sleep

    def fake_execv(path, argv):
        calls["path"], calls["argv"] = path, argv

    os.execv = fake_execv
    time.sleep = lambda s: None
    try:
        R.restart_soon(delay=0)
        for _ in range(100):
            if "argv" in calls:
                break
            real_sleep(0.01)
    finally:
        os.execv, time.sleep = real_execv, real_sleep
    check("restart calls execv", "argv" in calls)
    check("same interpreter reused", calls.get("path") == sys.executable)
    check("relaunches as 'python -m nickbot'", calls.get("argv") == [sys.executable, "-m", "nickbot"])

    print("\n[5] diagnostics")
    rows = dict(R.diagnostics(3725))
    check("has host row", "Host" in rows)
    check("has python row", "Python" in rows)
    check("uptime rendered", rows.get("Uptime") == "1h 2m")
    check("commit row present", "Commit" in rows)
    check("deps row lists httpx", "httpx" in rows.get("Deps", ""))
    check("where_am_i is text", isinstance(R.where_am_i(), str) and R.where_am_i() != "")
    check("autostart unknown off-Termux", R.autostart_installed() is None)

    print("\n[6] cards")
    card = F.diag_card(list(rows.items()), "plain shell", False)
    check("diag card shows labels", "Uptime" in card and "1h 2m" in card)
    check("diag card explains autostart", "termux-boot.sh" in card)
    evil = F.diag_card([("<x>", "a&b")], "plain shell", None)
    check("diag card escapes html", "&lt;x&gt;" in evil and "a&amp;b" in evil)
    check("diag card handles no-autostart", "not applicable" in evil)
    check("empty log card is friendly", "data/bot.log" in F.logs_card("", 30))
    check("log card wraps in pre", "<pre>" in F.logs_card("boom", 1))
    check("help lists the new commands",
          all(c in F.help_text(True) for c in ("/diag", "/restart", "/update", "/logs")))
    check("help hides them from users", "/restart" not in F.help_text(False))

    print("\n[7] owner gate")
    stranger = FakeEvent(uid=99)
    asyncio.run(H.cmd_restart(stranger))
    check("stranger cannot restart", stranger.said and "owner" in stranger.said[0].lower())

    logs_ev = FakeEvent(uid=1, args=["7"])
    asyncio.run(H.cmd_logs(logs_ev))
    check("owner gets a log answer", logs_ev.said and len(logs_ev.said) == 1)

    diag_ev = FakeEvent(uid=1)
    asyncio.run(H.cmd_diag(diag_ev))
    check("owner gets a diag answer", diag_ev.said and "Machine" in diag_ev.said[0])

    print(f"\nResult: {ok} passed / {fail} failed")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
