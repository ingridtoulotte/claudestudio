"""Deterministic synthetic session generator.

Used by two callers:
  * `--selftest`  — builds a tiny known fixture and asserts exact numbers.
  * `demo`        — builds a realistic, larger corpus so the UI looks alive in
                    screenshots without touching any real (private) session.

Everything is seeded, so two runs produce byte-identical files.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import random
import uuid

PROJECTS = [
    ("/home/dev/orbit-api", "main"),
    ("/home/dev/orbit-web", "feat/checkout"),
    ("/home/dev/datalake-etl", "main"),
    ("/home/dev/mobile-app", "release/3.2"),
    ("/home/dev/infra", "main"),
    ("/home/dev/ml-pipeline", "experiment/v4"),
]
MODELS = ["claude-opus-4-8", "claude-sonnet-4-6", "claude-haiku-4-5", "claude-fable-5"]
TOOLS = ["Read", "Edit", "Write", "Bash", "Grep", "Glob", "Task", "WebSearch"]
TITLES = [
    "Fix flaky checkout integration test", "Refactor auth middleware to async",
    "Add Stripe webhook handler", "Debug N+1 query in orders endpoint",
    "Migrate ETL job to incremental loads", "Wire up dark mode toggle",
    "Optimize image pipeline with worker pool", "Set up GitHub Actions matrix",
    "Trace memory leak in websocket server", "Implement Reed-Solomon codec",
    "Port path tracer to web workers", "Tune retrieval prompt for accuracy",
    "Rotate leaked API key and audit logs", "Add pagination to search results",
    "Write property-based tests for parser", "Containerize the inference service",
]
PROMPTS = [
    "The checkout test fails intermittently in CI. Can you find the race?",
    "Refactor this middleware to use async/await throughout.",
    "Add a webhook handler that verifies the signature and is idempotent.",
    "Orders endpoint is slow under load — profile and fix the query.",
    "Make the ETL job incremental instead of full reloads each run.",
    "Add a dark mode toggle that persists across reloads.",
]
REPLIES = [
    "Found it — the test shares a fixture across cases. I'll isolate state per test and re-run.",
    "Done. Converted the callback chain to async/await and added error propagation.",
    "Added HMAC verification and a dedupe table keyed on the event id.",
    "It was an N+1; I added a join + eager load. p95 dropped from 1.8s to 120ms.",
    "Switched to a watermark column and upserts. Re-run is now ~12x faster.",
    "Toggle wired to a Web Audio-free localStorage flag; respects prefers-color-scheme.",
]


def _usage(model, rng):
    inp = rng.randint(800, 6000)
    out = rng.randint(120, 2200)
    cw = rng.randint(0, 9000)
    cr = rng.randint(0, 40000)
    return {
        "input_tokens": inp, "output_tokens": out,
        "cache_creation_input_tokens": cw, "cache_read_input_tokens": cr,
    }


def _write_session(path, *, seed, base_time, n_turns, project, branch, model, title):
    rng = random.Random(seed)
    sid = str(uuid.UUID(int=seed))
    lines = []
    lines.append({"type": "ai-title", "aiTitle": title, "sessionId": sid})
    t = base_time
    parent = None
    for turn in range(n_turns):
        t += dt.timedelta(seconds=rng.randint(20, 600))
        uid = str(uuid.UUID(int=seed * 1000 + turn * 2))
        prompt = rng.choice(PROMPTS) if turn else PROMPTS[seed % len(PROMPTS)]
        lines.append({
            "type": "user", "uuid": uid, "parentUuid": parent,
            "sessionId": sid, "cwd": project, "gitBranch": branch,
            "version": "2.1.0", "entrypoint": "cli", "isMeta": False,
            "timestamp": t.isoformat() + "Z",
            "message": {"role": "user", "content": prompt},
        })
        parent = uid
        t += dt.timedelta(seconds=rng.randint(5, 90))
        aid = str(uuid.UUID(int=seed * 1000 + turn * 2 + 1))
        content = [{"type": "thinking", "thinking": "Let me inspect the relevant files and reason about the cause."}]
        # a couple of tool calls
        toolblocks = []
        resultblocks = []
        for _ in range(rng.randint(1, 4)):
            tname = rng.choice(TOOLS)
            tuid = "toolu_" + uuid.UUID(int=rng.randint(0, 2**60)).hex[:16]
            toolblocks.append({"type": "tool_use", "id": tuid, "name": tname,
                               "input": {"command": "…", "path": f"{project}/src/x.py"}})
            resultblocks.append({"type": "tool_result", "tool_use_id": tuid,
                                 "is_error": rng.random() < 0.06,
                                 "content": "ok"})
        content.extend(toolblocks)
        content.append({"type": "text", "text": rng.choice(REPLIES)})
        lines.append({
            "type": "assistant", "uuid": aid, "parentUuid": parent,
            "sessionId": sid, "cwd": project, "gitBranch": branch,
            "requestId": "req_" + uuid.UUID(int=rng.randint(0, 2**60)).hex[:12],
            "timestamp": t.isoformat() + "Z",
            "message": {"role": "assistant", "model": model,
                        "content": content, "usage": _usage(model, rng)},
        })
        parent = aid
        # tool results delivered as a following user turn
        if resultblocks:
            t += dt.timedelta(seconds=rng.randint(1, 20))
            ruid = str(uuid.UUID(int=seed * 7000 + turn))
            lines.append({
                "type": "user", "uuid": ruid, "parentUuid": parent,
                "sessionId": sid, "cwd": project, "gitBranch": branch,
                "timestamp": t.isoformat() + "Z",
                "message": {"role": "user", "content": resultblocks},
            })
            parent = ruid

    with open(path, "w", encoding="utf-8") as fh:
        for obj in lines:
            fh.write(json.dumps(obj) + "\n")
    return sid


def build_corpus(root: str, *, count: int = 48, seed: int = 7) -> str:
    """Write `count` synthetic sessions under root/<encoded-project>/<sid>.jsonl."""
    rng = random.Random(seed)
    now = dt.datetime(2026, 6, 13, 18, 0, 0)
    for i in range(count):
        project, branch = PROJECTS[i % len(PROJECTS)]
        model = MODELS[i % len(MODELS)] if i % 5 else rng.choice(MODELS)
        title = TITLES[i % len(TITLES)]
        days_ago = rng.randint(0, 180)
        base = now - dt.timedelta(days=days_ago, hours=rng.randint(0, 14))
        enc = project.replace("/", "-").replace("\\", "-")
        d = os.path.join(root, enc)
        os.makedirs(d, exist_ok=True)
        sid = str(uuid.UUID(int=1000 + i))
        path = os.path.join(d, f"{sid}.jsonl")
        _write_session(
            path, seed=1000 + i, base_time=base,
            n_turns=rng.randint(4, 22), project=project, branch=branch,
            model=model, title=title,
        )
    return root


def build_known(root: str) -> dict:
    """A tiny, fully-deterministic fixture with hand-computable expectations."""
    os.makedirs(os.path.join(root, "-home-dev-known"), exist_ok=True)
    sid = str(uuid.UUID(int=42))
    path = os.path.join(root, "-home-dev-known", f"{sid}.jsonl")
    lines = [
        {"type": "ai-title", "aiTitle": "Known fixture session", "sessionId": sid},
        {"type": "user", "uuid": "u1", "parentUuid": None, "sessionId": sid,
         "cwd": "/home/dev/known", "gitBranch": "main", "isMeta": False,
         "timestamp": "2026-06-01T10:00:00Z",
         "message": {"role": "user", "content": "please fix the parser bug"}},
        {"type": "assistant", "uuid": "a1", "parentUuid": "u1", "sessionId": sid,
         "cwd": "/home/dev/known", "timestamp": "2026-06-01T10:00:30Z",
         "message": {"role": "assistant", "model": "claude-opus-4-8",
                     "content": [
                         {"type": "thinking", "thinking": "inspect parser"},
                         {"type": "tool_use", "id": "t1", "name": "Read",
                          "input": {"path": "parser.py"}},
                         {"type": "tool_use", "id": "t2", "name": "Edit",
                          "input": {"path": "parser.py"}},
                         {"type": "text", "text": "Fixed the off-by-one in tokenizer."},
                     ],
                     "usage": {"input_tokens": 1000, "output_tokens": 500,
                               "cache_creation_input_tokens": 2000,
                               "cache_read_input_tokens": 10000}}},
        {"type": "user", "uuid": "u2", "parentUuid": "a1", "sessionId": sid,
         "cwd": "/home/dev/known", "timestamp": "2026-06-01T10:00:35Z",
         "message": {"role": "user", "content": [
             {"type": "tool_result", "tool_use_id": "t1", "is_error": False, "content": "ok"},
             {"type": "tool_result", "tool_use_id": "t2", "is_error": True, "content": "boom"},
         ]}},
    ]
    with open(path, "w", encoding="utf-8") as fh:
        for o in lines:
            fh.write(json.dumps(o) + "\n")

    # expected cost: opus 4.8 = $5 in / $25 out per MTok; cache write 1.25x, read 0.10x
    inp, out, cw, cr = 1000, 500, 2000, 10000
    cost = (inp * 5 + out * 25 + cw * 5 * 1.25 + cr * 5 * 0.10) / 1_000_000
    return {
        "session_id": sid, "path": path,
        "messages": 3, "user_msgs": 1, "assistant_msgs": 1,
        "tool_calls": 2, "tool_errors": 1,
        "input": inp, "output": out, "cache_write": cw, "cache_read": cr,
        "cost": cost,
    }
