"""Local coordination shared by independent MCP clients; no remote service needed."""
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid
from contextlib import contextmanager


class ProjectLock:
    """Kernel lock held until the job ends. A crashed server releases the handle."""
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = path.open("a+b")
        self.handle.seek(0, 2)
        if self.handle.tell() == 0:
            self.handle.write(b"0")
            self.handle.flush()
        self.handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.handle.close()
            raise RuntimeError("Another MCP client is using this checkout's Godot cache; wait for its job to finish.")

    def close(self):
        if not self.handle.closed:
            if os.name == "nt":
                import msvcrt
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            self.handle.close()


def clean_paths(paths):
    result = []
    for raw in paths:
        p = raw.replace("\\", "/").strip().rstrip("/")
        if not p or p.startswith("/") or ":" in p or any(x in ("", ".", "..") for x in p.split("/")):
            raise ValueError("Claims require relative file/directory paths without traversal.")
        if len(p) > 300:
            raise ValueError("Claim path too long.")
        result.append(p.casefold())
    if not result or len(result) > 50:
        raise ValueError("Claim 1–50 explicit files or directories.")
    return sorted(set(result))


def overlaps(left, right):
    return left == right or left.startswith(right + "/") or right.startswith(left + "/")


class Board:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, owner TEXT, title TEXT, paths TEXT, checkout TEXT, revision TEXT, status TEXT, expires REAL, updated REAL, detail TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY AUTOINCREMENT, author TEXT, recipient TEXT, task TEXT, text TEXT, created REAL)")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def claim(self, owner, title, paths, checkout, revision, minutes=60):
        if not owner.strip() or len(owner) > 120 or not title.strip() or len(title) > 300:
            raise ValueError("Provide a unique agent-session owner and a short task title.")
        if not 5 <= minutes <= 240:
            raise ValueError("Claim duration must be 5–240 minutes.")
        paths = clean_paths(paths)
        now = time.time()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            active = db.execute("SELECT * FROM tasks WHERE status='active' AND expires>?", (now,)).fetchall()
            if len(active) >= 100:
                raise ValueError("100 active claims already exist; release or finish existing tasks first.")
            for task in active:
                if any(overlaps(a, b) for a in paths for b in json.loads(task["paths"])):
                    raise ValueError(f"Claim conflicts with {task['id']} owned by {task['owner']}: {task['title']}")
            task_id = uuid.uuid4().hex
            db.execute("INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?,?)", (task_id, owner, title, json.dumps(paths), checkout, revision, "active", now + minutes * 60, now, ""))
        return task_id

    def update(self, task_id, owner, status, detail, minutes=60):
        if status not in ("active", "blocked", "review", "done", "released"):
            raise ValueError("Unknown task status.")
        if not 5 <= minutes <= 240 or len(detail) > 6000:
            raise ValueError("Invalid renewal duration or detail length.")
        now = time.time()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            task = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if not task or task["owner"] != owner:
                raise ValueError("Task does not belong to this agent session.")
            if task["status"] != "active" or task["expires"] <= now:
                raise ValueError("Claim is no longer active; read the board and obtain a new claim before editing.")
            db.execute("UPDATE tasks SET status=?, detail=?, expires=?, updated=? WHERE id=?", (status, detail, now + minutes * 60, now, task_id))

    def note(self, author, recipient, text, task=""):
        if not author.strip() or len(author) > 120 or recipient not in ("all", "claude", "codex") or not text.strip() or len(text) > 6000:
            raise ValueError("Use a session author, recipient all/claude/codex, and 1–6000 characters of text.")
        with self.connect() as db:
            if task and not db.execute("SELECT 1 FROM tasks WHERE id=?", (task,)).fetchone():
                raise ValueError("Unknown task id.")
            return db.execute("INSERT INTO notes(author,recipient,task,text,created) VALUES (?,?,?,?,?)", (author, recipient, task, text, time.time())).lastrowid

    def read(self, after=0, recipient="all", limit=30):
        if after < 0 or not 1 <= limit <= 100 or recipient not in ("all", "claude", "codex"):
            raise ValueError("Invalid board cursor, recipient or limit.")
        with self.connect() as db:
            tasks = [dict(x) for x in db.execute("SELECT * FROM tasks ORDER BY updated DESC LIMIT ?", (limit,))]
            active_claims = [dict(x) for x in db.execute("SELECT id,owner,title,paths,checkout,revision,expires FROM tasks WHERE status='active' AND expires>? ORDER BY updated DESC", (time.time(),))]
            if recipient == "all":
                notes = db.execute("SELECT * FROM notes WHERE id>? ORDER BY id LIMIT ?", (after, limit)).fetchall()
            else:
                notes = db.execute("SELECT * FROM notes WHERE id>? AND recipient IN ('all',?) ORDER BY id LIMIT ?", (after, recipient, limit)).fetchall()
            high = db.execute("SELECT COALESCE(MAX(id),0) FROM notes").fetchone()[0]
        for task in tasks:
            task["paths"] = json.loads(task["paths"])
            if task["status"] == "active" and task["expires"] <= time.time():
                task["status"] = "expired"
        for task in active_claims:
            task["paths"] = json.loads(task["paths"])
        return {"active_claims": active_claims, "tasks": tasks, "notes": [dict(x) for x in notes], "next_cursor": notes[-1]["id"] if len(notes) == limit else max(after, high), "shared_store": str(self.path)}
