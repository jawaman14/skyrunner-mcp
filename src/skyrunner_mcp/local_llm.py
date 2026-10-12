"""A cheap first-pass helper on the local LM Studio server, for the jobs that do not need Claude or Codex.

The owner's large model lives on that server, and a model that does not fit freezes the machine. So this module only ever
talks to a model that is ALREADY LOADED: it reads /api/v1/models (read-only) first and refuses a model with no loaded instance,
because asking for an unloaded one makes the server load it. It has no load, unload or download call and no MCP-integration
call. One request at a time across every agent's server process (an OS file lock), a pause after each call, modest token
budgets, and reasoning off unless asked for. Everything it returns is a draft for an agent to check; it decides nothing.
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
from pathlib import Path

import httpx

PAUSE_SECONDS = 1.0
DEFAULT_INPUT_CHARS = 24_000
MAX_INPUT_CHARS = 400_000
MAX_CALLS = 30


class LocalLLMError(Exception):
    """Anything that stops a local-model call; its text is shown to the agent."""


def base_url() -> str:
    url = os.environ.get("SKYRUNNER_LOCAL_LLM", "").rstrip("/")
    if not url:
        raise LocalLLMError("Set SKYRUNNER_LOCAL_LLM to the LM Studio address, for example http://169.254.83.107:6456.")
    return url


def loaded_models(data: dict) -> list[str]:
    """Keys of the language models with a loaded instance in a /api/v1/models answer."""
    return [m["key"] for m in data.get("models", []) if m.get("type") == "llm" and m.get("loaded_instances")]


def pick_model(loaded: list[str], requested: str = "") -> str:
    """The loaded model to use. Never one that is not loaded: asking for it would make the server load it."""
    requested = requested or os.environ.get("SKYRUNNER_LOCAL_MODEL", "")
    if requested:
        if requested not in loaded:
            raise LocalLLMError(f"{requested} is not loaded (loaded: {', '.join(loaded) or 'none'}). "
                                "This server never loads a model: load it in LM Studio first.")
        return requested
    if len(loaded) == 1:
        return loaded[0]
    if not loaded:
        raise LocalLLMError("No language model is loaded in LM Studio. This server never loads one: load a model first.")
    raise LocalLLMError(f"Several models are loaded ({', '.join(loaded)}): set SKYRUNNER_LOCAL_MODEL.")


def input_budget(data: dict) -> int:
    """Characters of a log or diff to send: 2 per token of the smallest loaded context window, capped (a long prompt is slow
    to read); SKYRUNNER_LOCAL_MAX_CHARS overrides; an unknown context gives the small default."""
    env = os.environ.get("SKYRUNNER_LOCAL_MAX_CHARS", "")
    if env.isdigit() and int(env) > 0:
        return min(int(env), MAX_INPUT_CHARS)
    sizes = [i.get("config", {}).get("context_length", 0) for m in data.get("models", []) if m.get("type") == "llm"
             for i in m.get("loaded_instances", [])]
    sizes = [x for x in sizes if isinstance(x, int) and x > 0]
    return min(min(sizes) * 2, MAX_INPUT_CHARS) if sizes else DEFAULT_INPUT_CHARS


def clip(text: str, limit: int) -> str:
    """Head and tail of long text, so a log keeps its start and its failure."""
    if len(text) <= limit:
        return text
    half = limit // 2
    return f"{text[:half]}\n...[{len(text) - limit} characters cut]...\n{text[-half:]}"


def tier_of(paths: list[str]) -> int:
    """The review tier of a change from its file paths alone (docs/PIPELINE.md in the game repository): no model involved."""
    t2 = ("scripts/sim/", "scripts/net/", "data/jsbsim/", "tests/fixtures/", "scripts/balance/")
    t1 = ("scripts/", ".github/", "tools/collab/", "tools/mcp/", "assets/", "project.godot")
    if any(p.startswith(t2) for p in paths):
        return 2
    if any(p.startswith(t1) for p in paths):
        return 1
    return 0


def _lock_file(fh) -> bool:
    try:
        if os.name == "nt":
            import msvcrt
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


class Turn:
    """The one-at-a-time lock across every agent's server process on this machine; the OS drops it if the holder dies."""

    def __init__(self, path: str = "", wait: float = 600.0):
        self.path = path or os.environ.get("SKYRUNNER_LOCAL_LOCK") or str(Path(tempfile.gettempdir()) / "skyrunner_local_llm.lock")
        self.wait, self.fh = wait, None

    async def __aenter__(self):
        fh = open(self.path, "a+", encoding="utf-8")
        deadline = time.monotonic() + self.wait
        while not _lock_file(fh):
            if time.monotonic() > deadline:
                fh.close()
                raise LocalLLMError(f"Another job has used the local model for over {int(self.wait)} s; try later.")
            await asyncio.sleep(1.0)
        self.fh = fh
        return self

    async def __aexit__(self, *exc):
        await asyncio.sleep(float(os.environ.get("SKYRUNNER_LOCAL_PAUSE", PAUSE_SECONDS)))
        if self.fh is not None:
            self.fh.close()


class LocalLLM:
    """A client for an LM Studio server. `transport` lets a test supply a fake server."""

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None, turn_path: str = ""):
        self.transport, self.turn_path = transport, turn_path

    def _client(self, timeout: float) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=base_url(), timeout=timeout, transport=self.transport)

    async def models(self) -> dict:
        try:
            async with self._client(20) as c:
                r = await c.get("/api/v1/models")
                r.raise_for_status()
                return r.json()
        except httpx.HTTPError as e:
            raise LocalLLMError(f"Could not read the model list from the local LLM: {e}") from e

    async def chat(self, prompt: str, system: str = "", max_tokens: int = 800, think: bool = False, temperature: float = 0.2,
                   timeout: float = 600.0) -> str:
        async with Turn(self.turn_path):
            model = pick_model(loaded_models(await self.models()))
            body = {"model": model, "input": prompt, "reasoning": "on" if think else "off", "max_output_tokens": max_tokens,
                    "temperature": temperature, "store": False}
            if system:
                body["system_prompt"] = system
            try:
                async with self._client(timeout) as c:
                    r = await c.post("/api/v1/chat", json=body)
                    r.raise_for_status()
                    data = r.json()
            except httpx.HTTPStatusError as e:
                raise LocalLLMError(f"The local LLM refused: {e.response.status_code} {e.response.text[:200]}") from e
            except (httpx.HTTPError, ValueError) as e:
                raise LocalLLMError(f"Could not reach the local LLM: {e}") from e
            text = "".join(str(o.get("content", "")) for o in data.get("output", []) if o.get("type") == "message").strip()
            if not text:
                if think and (data.get("stats") or {}).get("reasoning_output_tokens", 0) >= max_tokens * 0.9:
                    raise LocalLLMError(f"{model} spent all {max_tokens} tokens thinking and answered nothing: raise max_tokens.")
                raise LocalLLMError("The model returned nothing.")
            log = os.environ.get("SKYRUNNER_LOCAL_LOG", "")
            if log:
                with open(log, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps({"model": model, "stats": data.get("stats", {})}) + "\n")
            return text


DIGEST_PROMPT = ("Summarise this {what} for a developer in at most {lines} short lines. Say what failed or changed and where (file and line if "
                 "shown). Quote exact error text. Do not guess.\n\n{text}")
THREAD_PROMPT = ("A reviewer left this comment on {path} line {line}. Do NOT say whether it is right or wrong and do NOT promise any change. "
                 "Write exactly three lines.\nASK: what the reviewer is asking for, in one sentence.\nCHECK: what the author must verify in the "
                 "code before answering, in one sentence.\nREPLY: [TODO: what was actually done]\n\n{body}")
TRIAGE_PROMPT = "One line (under 18 words) saying what this pull request does, from its title and file list only.\nTitle: {title}\nFiles: {files}"
