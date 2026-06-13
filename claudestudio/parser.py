"""Parse Claude Code session logs (`~/.claude/projects/**/*.jsonl`).

Each `.jsonl` file is one session: a stream of newline-delimited JSON records.
We normalize the records we care about into a `ParsedSession` — plain dataclasses,
no SQL, no I/O policy — so the index, analytics, and tests can all share one
faithful representation of the wire format.

Record types seen in the wild:
  user / assistant   -> message with `message.content` (str or block list)
  ai-title           -> session title
  system             -> system events (durationMs, subtype)
  attachment, mode, permission-mode, last-prompt, file-history-snapshot -> metadata
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Iterable

from . import pricing


# ---------------------------------------------------------------------------
# data model
# ---------------------------------------------------------------------------

@dataclass
class ToolCall:
    tool_use_id: str
    name: str
    input: dict
    ts: str
    is_error: bool = False
    result_preview: str = ""


@dataclass
class Message:
    uuid: str
    parent_uuid: str | None
    role: str
    ts: str
    seq: int
    model: str | None = None
    text: str = ""
    thinking: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0
    is_meta: bool = False
    is_sidechain: bool = False
    skill: str | None = None
    plugin: str | None = None

    @property
    def cost_usd(self) -> float:
        return pricing.cost_for_usage(
            self.model,
            self.input_tokens,
            self.output_tokens,
            self.cache_write_tokens,
            self.cache_read_tokens,
        )


@dataclass
class ParsedSession:
    session_id: str
    file_path: str
    file_mtime: float
    file_size: int
    title: str = ""
    cwd: str = ""
    git_branch: str = ""
    version: str = ""
    entrypoint: str = ""
    first_ts: str = ""
    last_ts: str = ""
    messages: list[Message] = field(default_factory=list)
    models: list[str] = field(default_factory=list)

    # ---- derived aggregates -------------------------------------------------
    @property
    def project(self) -> str:
        return self.cwd or "(unknown)"

    @property
    def user_msgs(self) -> int:
        # Count real prompts only — a user turn that carries nothing but
        # tool_result blocks has no text and is not a prompt the user typed.
        return sum(
            1 for m in self.messages
            if m.role == "user" and not m.is_meta and m.text
        )

    @property
    def assistant_msgs(self) -> int:
        return sum(1 for m in self.messages if m.role == "assistant")

    @property
    def tool_call_count(self) -> int:
        return sum(len(m.tool_calls) for m in self.messages)

    @property
    def total_input(self) -> int:
        return sum(m.input_tokens for m in self.messages)

    @property
    def total_output(self) -> int:
        return sum(m.output_tokens for m in self.messages)

    @property
    def total_cache_write(self) -> int:
        return sum(m.cache_write_tokens for m in self.messages)

    @property
    def total_cache_read(self) -> int:
        return sum(m.cache_read_tokens for m in self.messages)

    @property
    def cost_usd(self) -> float:
        return sum(m.cost_usd for m in self.messages)

    @property
    def duration_seconds(self) -> float:
        a, b = _parse_ts(self.first_ts), _parse_ts(self.last_ts)
        if a is None or b is None:
            return 0.0
        return max(0.0, b - a)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _parse_ts(ts: str | None) -> float | None:
    """ISO-8601 -> epoch seconds. Tolerant of trailing Z and fractional secs."""
    if not ts:
        return None
    import datetime as _dt

    s = ts.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return _dt.datetime.fromisoformat(s).timestamp()
    except (ValueError, OSError):
        return None


def _blocks(content: Any) -> Iterable[dict]:
    if isinstance(content, list):
        for b in content:
            if isinstance(b, dict):
                yield b


def _text_of_result(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict):
                if b.get("type") == "text":
                    parts.append(b.get("text", ""))
                elif "content" in b and isinstance(b["content"], str):
                    parts.append(b["content"])
        return "\n".join(parts)
    return ""


# ---------------------------------------------------------------------------
# main entry
# ---------------------------------------------------------------------------

def parse_file(path: str) -> ParsedSession | None:
    """Parse one `.jsonl` session file. Returns None if it has no messages."""
    try:
        st = os.stat(path)
    except OSError:
        return None

    session_id = os.path.splitext(os.path.basename(path))[0]
    ps = ParsedSession(
        session_id=session_id,
        file_path=path,
        file_mtime=st.st_mtime,
        file_size=st.st_size,
    )

    seq = 0
    models_seen: dict[str, None] = {}
    # tool_use_id -> ToolCall, so a later tool_result can attach its outcome.
    pending_tools: dict[str, ToolCall] = {}

    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            rtype = rec.get("type")

            if rtype == "ai-title":
                ps.title = rec.get("aiTitle", "") or ps.title
                continue

            if rtype in ("user", "assistant"):
                _ingest_message(rec, rtype, ps, seq, models_seen, pending_tools)
                seq += 1
                continue

            # carry session-level metadata from any record that has it
            if not ps.cwd and rec.get("cwd"):
                ps.cwd = rec["cwd"]
            if not ps.git_branch and rec.get("gitBranch"):
                ps.git_branch = rec["gitBranch"]
            if not ps.version and rec.get("version"):
                ps.version = rec["version"]
            if not ps.entrypoint and rec.get("entrypoint"):
                ps.entrypoint = rec["entrypoint"]

    if not ps.messages:
        return None

    ps.models = list(models_seen.keys())
    ts_values = [m.ts for m in ps.messages if m.ts]
    if ts_values:
        ps.first_ts = min(ts_values)
        ps.last_ts = max(ts_values)
    if not ps.title:
        ps.title = _fallback_title(ps)
    return ps


def _ingest_message(rec, rtype, ps, seq, models_seen, pending_tools):
    msg = rec.get("message")
    if not isinstance(msg, dict):
        return
    if not ps.cwd and rec.get("cwd"):
        ps.cwd = rec["cwd"]
    if not ps.git_branch and rec.get("gitBranch"):
        ps.git_branch = rec["gitBranch"]

    m = Message(
        uuid=rec.get("uuid", f"{ps.session_id}:{seq}"),
        parent_uuid=rec.get("parentUuid"),
        role=msg.get("role", rtype),
        ts=rec.get("timestamp", ""),
        seq=seq,
        model=msg.get("model"),
        is_meta=bool(rec.get("isMeta")),
        is_sidechain=bool(rec.get("isSidechain")),
        skill=rec.get("attributionSkill"),
        plugin=rec.get("attributionPlugin"),
    )
    if m.model:
        models_seen.setdefault(m.model, None)

    usage = msg.get("usage")
    if isinstance(usage, dict):
        m.input_tokens = int(usage.get("input_tokens", 0) or 0)
        m.output_tokens = int(usage.get("output_tokens", 0) or 0)
        m.cache_write_tokens = int(usage.get("cache_creation_input_tokens", 0) or 0)
        m.cache_read_tokens = int(usage.get("cache_read_input_tokens", 0) or 0)

    content = msg.get("content")
    texts, thinks = [], []
    if isinstance(content, str):
        texts.append(content)
    else:
        for b in _blocks(content):
            bt = b.get("type")
            if bt == "text":
                texts.append(b.get("text", ""))
            elif bt == "thinking":
                thinks.append(b.get("thinking", ""))
            elif bt == "tool_use":
                tc = ToolCall(
                    tool_use_id=b.get("id", ""),
                    name=b.get("name", "?"),
                    input=b.get("input") or {},
                    ts=m.ts,
                )
                m.tool_calls.append(tc)
                if tc.tool_use_id:
                    pending_tools[tc.tool_use_id] = tc
            elif bt == "tool_result":
                tid = b.get("tool_use_id", "")
                target = pending_tools.get(tid)
                if target is not None:
                    target.is_error = bool(b.get("is_error"))
                    target.result_preview = _text_of_result(b.get("content"))[:2000]

    m.text = "\n".join(t for t in texts if t).strip()
    m.thinking = "\n".join(t for t in thinks if t).strip()
    ps.messages.append(m)


def _fallback_title(ps: ParsedSession) -> str:
    for m in ps.messages:
        if m.role == "user" and not m.is_meta and m.text:
            first = m.text.strip().splitlines()[0]
            return (first[:80] + "…") if len(first) > 80 else first
    return f"Session {ps.session_id[:8]}"


def iter_session_files(root: str) -> Iterable[str]:
    """Yield every `.jsonl` file under the Claude projects root."""
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            if name.endswith(".jsonl"):
                yield os.path.join(dirpath, name)


def default_projects_root() -> str:
    return os.path.join(os.path.expanduser("~"), ".claude", "projects")
