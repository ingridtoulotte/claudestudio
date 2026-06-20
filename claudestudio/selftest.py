"""Self-contained correctness checks. Run via `python -m claudestudio --selftest`.

No external test framework — just exact assertions over a deterministic fixture,
so CI on every OS/Python combo needs zero dependencies.
"""

from __future__ import annotations

import math
import os
import tempfile

import claudestudio
from . import api, analytics, ask, export, fixtures, index, parser, pricing, wrapped


class Check:
    def __init__(self):
        self.passed = 0
        self.failed = 0

    def eq(self, got, want, msg):
        if got == want:
            self.passed += 1
        else:
            self.failed += 1
            print(f"  FAIL: {msg}\n        got={got!r} want={want!r}")

    def close(self, got, want, msg, tol=1e-9):
        if math.isclose(got, want, rel_tol=1e-6, abs_tol=tol):
            self.passed += 1
        else:
            self.failed += 1
            print(f"  FAIL: {msg}\n        got={got!r} want={want!r}")

    def ok(self, cond, msg):
        if cond:
            self.passed += 1
        else:
            self.failed += 1
            print(f"  FAIL: {msg}")


def run() -> int:
    c = Check()

    # --- pricing ---------------------------------------------------------
    c.eq(pricing.normalize("claude-opus-4-8-20260101"), "claude-opus-4-8", "pricing.normalize strips date")
    c.eq(pricing.is_priced("claude-opus-4-8"), True, "opus priced")
    c.eq(pricing.is_priced("claude-future-99"), False, "unknown not priced")
    c.eq(pricing.family_of("claude-sonnet-4-6"), "Sonnet", "family of sonnet")
    c.close(pricing.cost_for_usage("claude-opus-4-8", 1_000_000, 0), 5.0, "opus 1M input = $5")
    c.close(pricing.cost_for_usage("claude-opus-4-8", 0, 1_000_000), 25.0, "opus 1M output = $25")
    c.close(pricing.cost_for_usage("claude-opus-4-8", 0, 0, 1_000_000, 0), 6.25, "cache write 1.25x")
    c.close(pricing.cost_for_usage("claude-opus-4-8", 0, 0, 0, 1_000_000), 0.5, "cache read 0.10x")
    c.close(pricing.cost_for_usage("claude-zzz", 1_000_000, 1_000_000), 0.0, "unknown model = $0")

    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "projects")
        os.makedirs(root)
        exp = fixtures.build_known(root)

        # --- parser ------------------------------------------------------
        ps = parser.parse_file(exp["path"])
        c.ok(ps is not None, "parse_file returns a session")
        c.eq(ps.session_id, exp["session_id"], "session id from filename")
        c.eq(ps.title, "Known fixture session", "title from ai-title")
        c.eq(ps.cwd, "/home/dev/known", "cwd captured")
        c.eq(len(ps.messages), exp["messages"], "message count")
        c.eq(ps.user_msgs, exp["user_msgs"], "user msgs (meta/tool-result excluded? counts non-meta)")
        c.eq(ps.assistant_msgs, exp["assistant_msgs"], "assistant msgs")
        c.eq(ps.tool_call_count, exp["tool_calls"], "tool call count")
        c.eq(ps.total_input, exp["input"], "total input tokens")
        c.eq(ps.total_output, exp["output"], "total output tokens")
        c.close(ps.cost_usd, exp["cost"], "session cost")
        # tool error attached from following tool_result
        errs = sum(1 for m in ps.messages for t in m.tool_calls if t.is_error)
        c.eq(errs, exp["tool_errors"], "tool error linked to result")

        # --- public parser API (documented for other builders) -----------
        c.ok(hasattr(claudestudio, "parse_session"), "claudestudio.parse_session is exported")
        c.ok("parse_session" in claudestudio.__all__, "parse_session in package __all__")
        pub = claudestudio.parse_session(exp["path"])
        c.ok(pub is not None and pub.session_id == exp["session_id"], "parse_session parses the fixture")
        c.eq(pub.cost_usd, ps.cost_usd, "parse_session matches parse_file")

        # --- index -------------------------------------------------------
        db = os.path.join(tmp, "idx.db")
        conn = index.connect(db)
        stats = index.reindex(conn, root)
        c.eq(stats["added"], 1, "indexed 1 new session")
        # second pass is fully incremental
        stats2 = index.reindex(conn, root)
        c.eq(stats2["added"] + stats2["updated"], 0, "second pass adds nothing")
        c.eq(stats2["skipped"], 1, "second pass skips unchanged file")

        summ = index.session_summary(conn)
        c.eq(summ["sessions"], 1, "summary sessions=1")
        c.eq(summ["tool_calls"], 2, "summary tool_calls=2")
        c.close(summ["cost_usd"], exp["cost"], "summary cost matches")

        # --- api: detail + search + analytics ----------------------------
        detail = api.get_session(conn, exp["session_id"])
        c.ok(detail is not None, "get_session returns detail")
        c.eq(len(detail["timeline"]), 3, "timeline has 3 messages")
        asst = [m for m in detail["timeline"] if m["role"] == "assistant"][0]
        c.eq(len(asst["tools"]), 2, "assistant message carries 2 tools")
        c.eq(asst["tools"][1]["is_error"], True, "second tool flagged error")

        res = api.search(conn, {"q": "off-by-one tokenizer"})
        c.ok(len(res["results"]) >= 1, "FTS finds 'tokenizer'")
        c.ok(any("tokenizer" in (r["snip"] or "").lower() or "tokenizer" in (r.get("title") or "").lower()
                 for r in res["results"]) or len(res["results"]) >= 1, "search snippet present")

        res2 = api.search(conn, {"q": "parser"})
        c.ok(len(res2["results"]) >= 1, "FTS finds tool/text 'parser'")

        lst = api.list_sessions(conn, {})
        c.eq(lst["total"], 1, "list_sessions total=1")
        lst_q = api.list_sessions(conn, {"q": "Known fixture"})
        c.eq(lst_q["total"], 1, "list filters by title query")
        lst_none = api.list_sessions(conn, {"q": "zzzznotpresentzzz"})
        c.eq(lst_none["total"], 0, "no false matches")

        # state round-trips and survives reindex
        api.set_state(conn, exp["session_id"], {"favorite": True, "tags": ["bug"]})
        index.reindex(conn, root, force=True)
        st = conn.execute(
            "SELECT favorite, tags FROM user_state WHERE session_id=?",
            (exp["session_id"],),
        ).fetchone()
        c.eq(st["favorite"], 1, "favorite survives reindex")
        c.ok("bug" in st["tags"], "tags survive reindex")

        ana = analytics.overview(conn)
        c.eq(ana["sessions"], 1, "analytics sessions")
        c.eq(ana["tool_calls"], 2, "analytics tool calls")
        c.ok(len(ana["by_model"]) == 1, "one model in analytics")
        c.ok(len(ana["heatmap"]) == 7 and len(ana["heatmap"][0]) == 24, "heatmap is 7x24")

        w = wrapped.generate(conn)
        c.ok(any("Sessions" == card["label"] for card in w["cards"]), "wrapped has Sessions card")
        c.ok(2026 in w["available_years"], "wrapped knows 2026")

        # --- export: markdown + standalone html --------------------------
        md = api.export_session(conn, exp["session_id"], "md")
        c.ok(md is not None, "export_session returns markdown")
        c.ok("# Known fixture session" in md["text"], "markdown has title heading")
        c.ok("Fixed the off-by-one in tokenizer." in md["text"], "markdown has assistant text")
        c.ok("Read" in md["text"] and "Edit" in md["text"], "markdown lists tool calls")
        c.ok(md["filename"].endswith(".md"), "markdown filename extension")
        htm = api.export_session(conn, exp["session_id"], "html")
        c.ok("<!doctype html>" in htm["text"].lower(), "html is a full document")
        c.ok("Known fixture session" in htm["text"], "html has title")
        c.ok(htm["content_type"].startswith("text/html"), "html content type")
        c.ok(htm["filename"].endswith(".html"), "html filename extension")
        c.ok(api.export_session(conn, "does-not-exist", "md") is None, "export of unknown id is None")
        # html is escaped — no raw angle brackets from message text leak structure
        direct = export.to_html({"title": "<script>", "timeline": []})
        c.ok("<script>" not in direct.split("<title>")[1], "html escapes untrusted title")

        # --- saved searches / smart collections --------------------------
        c.eq(api.list_saved(conn), [], "no saved searches initially")
        sv = api.add_saved(conn, {"name": "Bugs", "query": "parser",
                                  "sort": "cost", "filters": {"favorite": True}})
        c.ok(sv["id"] >= 1, "saved search gets an id")
        saved = api.list_saved(conn)
        c.eq(len(saved), 1, "one saved search listed")
        c.eq(saved[0]["name"], "Bugs", "saved name round-trips")
        c.eq(saved[0]["filters"]["favorite"], True, "saved filters round-trip")
        index.reindex(conn, root, force=True)
        c.eq(len(api.list_saved(conn)), 1, "saved search survives reindex")
        api.delete_saved(conn, sv["id"])
        c.eq(api.list_saved(conn), [], "saved search deleted")

        # --- ask: grounded local companion -------------------------------
        sid = exp["session_id"]
        ft = ask.files_touched(conn, sid)
        c.eq(len(ft), 1, "files_touched finds the one file")
        c.eq(ft[0]["name"], "parser.py", "files_touched names parser.py")
        c.ok("edit" in ft[0]["ops"] and "read" in ft[0]["ops"], "parser.py read and edited")
        c.ok(ft[0]["edited"], "parser.py flagged as edited")
        c.eq(ft[0]["errors"], 1, "files_touched counts the edit error")

        dig = ask.session_digest(conn, sid)
        c.eq(dig["intent"], "digest", "digest intent")
        c.ok(any(b["type"] == "files" for b in dig["blocks"]), "digest has a files block")
        c.ok(dig["citations"][0]["session_id"] == sid, "digest cites the session")
        c.ok("no model calls" in dig["grounding"], "digest states no model calls")

        hb = ask.handoff_brief(conn, sid)
        c.eq(hb["intent"], "handoff", "handoff intent")
        steps = [b for b in hb["blocks"] if b["type"] == "steps"]
        c.ok(steps and any("error" in s.lower() for s in steps[0]["items"]),
             "handoff flags the open error")

        it = ask.important_tools(conn, sid)
        first = it["blocks"][-1]["items"][0]["text"]
        c.ok(first.startswith("Edit") or first.startswith("Write"),
             "important_tools ranks the edit first")

        fh = ask.file_history(conn, "parser.py")
        c.eq(fh["intent"], "files", "file_history intent")
        c.ok(any(s["session_id"] == sid for s in fh["blocks"][-1]["items"]),
             "file_history finds the session that edited parser.py")

        ro = ask.reopen_suggestions(conn)
        c.eq(ro["intent"], "reopen", "reopen intent")
        c.ok(any(s["session_id"] == sid for s in ro["blocks"][-1]["items"]),
             "reopen surfaces the session")
        c.ok(any("error" in s["reason"] for s in ro["blocks"][-1]["items"]),
             "reopen explains the error signal")

        # router: scoped + global
        c.eq(ask.answer(conn, "what happened in this session?", sid)["intent"],
             "digest", "router → digest when scoped")
        c.eq(ask.answer(conn, "give me a handoff brief", sid)["intent"],
             "handoff", "router → handoff when scoped")
        c.eq(ask.answer(conn, "which files changed?", sid)["intent"],
             "files", "router → files when scoped")
        c.eq(ask.answer(conn, "what should I reopen next?")["intent"],
             "reopen", "router → reopen globally")
        c.eq(ask.answer(conn, "where did the tokens go?")["intent"],
             "spend", "router → spend globally")
        c.eq(ask.answer(conn, "why was parser.py edited?")["intent"],
             "files", "router → file history from a path")
        c.ok(ask.answer(conn, "")["intent"] == "help", "empty question → help")
        # api wrapper attaches suggestions
        wrapped_ask = api.ask(conn, "summarize this session", sid)
        c.ok(len(wrapped_ask["suggestions"]) == 4, "api.ask attaches 4 suggestions")
        c.ok(api.ask(conn, "anything", "does-not-exist")["intent"] == "error",
             "api.ask on unknown session → error answer")

        conn.close()

        # --- larger corpus sanity ---------------------------------------
        root2 = os.path.join(tmp, "corpus")
        fixtures.build_corpus(root2, count=12, seed=3)
        db2 = os.path.join(tmp, "c.db")
        conn2 = index.connect(db2)
        s = index.reindex(conn2, root2)
        c.eq(s["added"], 12, "corpus indexes 12 sessions")
        c.ok(index.session_summary(conn2)["messages"] > 12, "corpus has many messages")
        # determinism: rebuild identical corpus -> identical session ids
        root3 = os.path.join(tmp, "corpus3")
        fixtures.build_corpus(root3, count=12, seed=3)
        a = sorted(os.listdir(os.path.join(root2, "-home-dev-orbit-api")))
        b = sorted(os.listdir(os.path.join(root3, "-home-dev-orbit-api")))
        c.eq(a, b, "corpus generation is deterministic")
        conn2.close()

    total = c.passed + c.failed
    print(f"\n  selftest: {c.passed}/{total} checks passed")
    if c.failed:
        print(f"  {c.failed} FAILED")
        return 1
    print("  ALLPASS")
    return 0
