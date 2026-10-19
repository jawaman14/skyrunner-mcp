"""A clock and timers for agents that cannot see the time themselves.

An agent that is told "Codex is out of usage until 4:11 PM" has no way to know how long that is. `now` gives the local time and zone; a timer stores a
due time on the shared board database, so either agent can set one ("come back at 16:14") and either can ask what is due. A timer may name the agent that
is away (`agent="codex"`), which turns it into an availability note: "codex is back at ...". Nothing here wakes anyone: it only answers "what time is it" and
"is it time yet".
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import re
import sqlite3
import time
from zoneinfo import ZoneInfo

AGENTS = ("claude", "codex", "owner", "")
MAX_WAIT_S = 540


def _tz(name):
    if not name or name == "local":
        return datetime.now().astimezone().tzinfo
    if name.upper() == "UTC":
        return timezone.utc
    try:
        return ZoneInfo(name)
    except Exception as e:
        raise ValueError(f"Unknown timezone {name!r}; use local, UTC or an IANA name like Australia/Sydney.") from e


def now(tz_name="local", at=None):
    """The current time as a dict. `at` (epoch seconds) is for tests."""
    zone = _tz(tz_name)
    t = datetime.fromtimestamp(at if at is not None else time.time(), zone)
    return {"iso": t.isoformat(timespec="seconds"), "date": t.date().isoformat(), "time": t.strftime("%H:%M:%S"), "weekday": t.strftime("%A"),
            "timezone": t.tzname() or str(zone), "utc_offset": t.strftime("%z"), "epoch": int(t.timestamp())}


def parse_when(at, in_minutes, tz_name="local", at_now=None):
    """Epoch seconds for 'HH:MM' (the next time the clock reads that), an ISO date-time, or `in_minutes` from now."""
    zone = _tz(tz_name)
    now_s = at_now if at_now is not None else time.time()
    if bool(at) == bool(in_minutes):
        raise ValueError("Give exactly one of at (HH:MM or an ISO date-time) or in_minutes.")
    if in_minutes:
        if not 0 < in_minutes <= 60 * 24 * 14:
            raise ValueError("in_minutes must be between 1 and 20160 (two weeks).")
        return now_s + in_minutes * 60
    base = datetime.fromtimestamp(now_s, zone)
    m = re.fullmatch(r"(\d{1,2}):(\d{2})\s*([AaPp][Mm])?", at.strip())
    if m:
        hour, minute, ampm = int(m.group(1)), int(m.group(2)), (m.group(3) or "").lower()
        if ampm:
            if not 1 <= hour <= 12:
                raise ValueError("12-hour times run 1-12.")
            hour = hour % 12 + (12 if ampm == "pm" else 0)
        if not (0 <= hour < 24 and 0 <= minute < 60):
            raise ValueError("Not a time of day.")
        target = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= base:
            target += timedelta(days=1)
        return target.timestamp()
    try:
        target = datetime.fromisoformat(at.strip())
    except ValueError as e:
        raise ValueError("at must be HH:MM, h:MM am/pm or an ISO date-time such as 2026-10-12T16:11.") from e
    if target.tzinfo is None:
        target = target.replace(tzinfo=zone)
    return target.timestamp()


class Clock:
    """Timers kept in the board's database file (a table of their own)."""

    def __init__(self, path):
        self.path = path
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS timers (id INTEGER PRIMARY KEY AUTOINCREMENT, owner TEXT, label TEXT, agent TEXT, due REAL, created REAL, cleared REAL)")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def set(self, owner, label, due, agent="", at_now=None):
        if owner not in ("claude", "codex", "owner") or agent not in AGENTS:
            raise ValueError("owner must be claude, codex or owner; agent must be claude, codex, owner or empty.")
        label = label.strip()
        if not label or len(label) > 300:
            raise ValueError("A timer needs a label of 1-300 characters.")
        now_s = at_now if at_now is not None else time.time()
        if due <= now_s:
            raise ValueError("That time has already passed.")
        with self.connect() as db:
            cur = db.execute("INSERT INTO timers (owner, label, agent, due, created) VALUES (?,?,?,?,?)", (owner, label, agent, due, now_s))
            return cur.lastrowid

    def rows(self, include_cleared=False, at_now=None, tz_name="local"):
        now_s = at_now if at_now is not None else time.time()
        zone = _tz(tz_name)
        with self.connect() as db:
            found = db.execute("SELECT * FROM timers " + ("" if include_cleared else "WHERE cleared IS NULL ") + "ORDER BY due, id").fetchall()
        out = []
        for r in found:
            left = r["due"] - now_s
            out.append({"id": r["id"], "owner": r["owner"], "label": r["label"], "agent": r["agent"] or None,
                        "due": datetime.fromtimestamp(r["due"], zone).isoformat(timespec="minutes"),
                        "seconds_left": int(left) if left > 0 else 0, "status": "cleared" if r["cleared"] else ("due" if left <= 0 else "pending")})
        return out

    def clear(self, timer_id, at_now=None):
        with self.connect() as db:
            cur = db.execute("UPDATE timers SET cleared=? WHERE id=? AND cleared IS NULL", (at_now if at_now is not None else time.time(), timer_id))
            if cur.rowcount != 1:
                raise ValueError(f"No active timer {timer_id}.")

    def remaining(self, timer_id, at_now=None):
        now_s = at_now if at_now is not None else time.time()
        with self.connect() as db:
            r = db.execute("SELECT due, cleared FROM timers WHERE id=?", (timer_id,)).fetchone()
        if r is None:
            raise ValueError(f"No timer {timer_id}.")
        return 0.0 if r["cleared"] else max(0.0, r["due"] - now_s)
