"""Self-contained correctness checks. Run via `python -m claudestudio --selftest`.

No external test framework — just exact assertions over a deterministic fixture,
so CI on every OS/Python combo needs zero dependencies.
"""

from __future__ import annotations

import contextlib
import io
import json
import math
import os
import re
import sqlite3
import tempfile

import claudestudio

from . import analytics, api, ask, cli, export, fixtures, index, parser, pricing, wrapped

_WEEK_RE = re.compile(r"^\d{4}-W\d{2}$")


def re_match_week(s: str) -> bool:
    """True if `s` is an ISO week label like '2026-W23' (selftest helper)."""
    return bool(_WEEK_RE.match(str(s)))


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
    # pricing table staleness signalling
    import datetime as _dts
    c.eq(pricing.price_table_age_days(pricing.PRICE_TABLE_DATE), 0, "price age 0 on table date")
    c.ok(not pricing.is_price_table_stale(), "bundled price table is fresh")
    c.ok(pricing.is_price_table_stale(
        pricing.PRICE_TABLE_DATE + _dts.timedelta(days=pricing.PRICE_TABLE_MAX_AGE_DAYS + 1)),
        "price table goes stale past max age")

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

        # --- schema index + read-only connection -------------------------
        idx_names = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'")]
        c.ok("idx_msg_model" in idx_names, "messages(model) index created")
        ro_conn = index.connect_ro(db)
        c.eq(ro_conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 1,
             "connect_ro reads the index")
        try:
            ro_conn.execute("INSERT INTO meta(key,value) VALUES('x','y')")
            wrote = True
        except sqlite3.OperationalError:
            wrote = False
        c.ok(not wrote, "connect_ro is read-only (writes rejected)")
        ro_conn.close()

        # --- schema version + migration safety ---------------------------
        c.eq(index.stored_schema_version(conn), index.SCHEMA_VERSION,
             "schema version recorded in meta")
        index.maybe_migrate(conn)  # idempotent — must not change anything
        c.eq(index.stored_schema_version(conn), index.SCHEMA_VERSION,
             "maybe_migrate is idempotent")
        fut_db = os.path.join(tmp, "future.db")
        fc = index.connect(fut_db)
        fc.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('schema_version',?)",
                   (str(index.SCHEMA_VERSION + 99),))
        fc.commit()
        fc.close()
        raised = False
        try:
            index.connect(fut_db)
        except RuntimeError:
            raised = True
        c.ok(raised, "opening a newer-schema index raises a clear error")

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

        # --- search filters + deterministic ordering ---------------------
        rt = api.search(conn, {"q": "parser", "kind": "tool"})
        c.ok(rt["results"] and all(r["kind"] == "tool" for r in rt["results"]),
             "search kind=tool restricts to tool rows")
        c.eq(rt["filters"]["kind"], "tool", "search echoes applied kind filter")
        rsc = api.search(conn, {"q": "parser", "session": exp["session_id"]})
        c.ok(all(r["session_id"] == exp["session_id"] for r in rsc["results"]),
             "search session scope restricts to one session")
        c.eq(len(api.search(conn, {"q": "parser", "session": "nope"})["results"]), 0,
             "search unknown session scope → empty")
        c.ok(len(api.search(conn, {"q": "parser", "project": ps.project})["results"]) >= 1,
             "search project filter keeps matching project")
        c.eq(len(api.search(conn, {"q": "parser", "project": "/no/such"})["results"]), 0,
             "search bogus project → empty")
        c.eq(len(api.search(conn, {"q": "parser", "since": "2999-01-01"})["results"]), 0,
             "search since=future → empty")
        c.ok(len(api.search(conn, {"q": "parser", "until": "2999-01-01"})["results"]) >= 1,
             "search until=future → keeps results")
        ord1 = [(r["session_id"], r["seq"]) for r in api.search(conn, {"q": "parser"})["results"]]
        ord2 = [(r["session_id"], r["seq"]) for r in api.search(conn, {"q": "parser"})["results"]]
        c.eq(ord1, ord2, "search ordering is deterministic across calls")

        lst = api.list_sessions(conn, {})
        c.eq(lst["total"], 1, "list_sessions total=1")
        lst_q = api.list_sessions(conn, {"q": "Known fixture"})
        c.eq(lst_q["total"], 1, "list filters by title query")
        lst_none = api.list_sessions(conn, {"q": "zzzznotpresentzzz"})
        c.eq(lst_none["total"], 0, "no false matches")
        c.eq(api.list_sessions(conn, {"since": "2026-05-01"})["total"], 1,
             "list since=past → keeps session")
        c.eq(api.list_sessions(conn, {"since": "2026-07-01"})["total"], 0,
             "list since=future → empty")
        c.eq(api.list_sessions(conn, {"until": "2026-05-01"})["total"], 0,
             "list until=past → empty")
        c.eq(api.list_sessions(conn, {"until": "2026-07-01"})["total"], 1,
             "list until=future → keeps session")
        # `until` is inclusive of the whole selected day: picking the session's
        # own active day must keep it (regression — a bare date used to resolve to
        # midnight and drop everything after 00:00). Bound derived from the stored
        # epoch in local time so the check is timezone-independent.
        import datetime as _dt
        _row = conn.execute(
            "SELECT first_epoch, last_epoch FROM sessions WHERE session_id=?",
            (exp["session_id"],),
        ).fetchone()
        _start_day = _dt.datetime.fromtimestamp(_row["first_epoch"]).strftime("%Y-%m-%d")
        _end_day = _dt.datetime.fromtimestamp(_row["last_epoch"]).strftime("%Y-%m-%d")
        c.eq(api.list_sessions(conn, {"until": _end_day})["total"], 1,
             "list until=active-day → keeps session (inclusive end day)")
        c.eq(api.list_sessions(conn, {"since": _start_day, "until": _end_day})["total"], 1,
             "list single-day window → keeps session")

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
        c.ok(any(card["label"] == "Sessions" for card in w["cards"]), "wrapped has Sessions card")
        c.ok(2026 in w["available_years"], "wrapped knows 2026")
        # an unrepresentable year (>= datetime.MAXYEAR) must fall back to all-time,
        # never crash the wrapped endpoint with an OSError/ValueError -> HTTP 500.
        wy = api.wrapped_payload(conn, "9999")
        c.eq(wy["label"], "All time", "wrapped out-of-range year falls back to all-time")
        c.ok(wy["year"] is None, "wrapped out-of-range year normalized to None")

        # A session whose timestamp parses to a far-future instant (valid ISO-8601
        # up to year 9999, or a corrupt millisecond value read as seconds) stores
        # an epoch past `fromtimestamp`'s range -> OSError on Windows, and a silent
        # year-9999 bucket on POSIX. The time-bucketed views must skip it on every
        # OS: never crash, never show an absurd year. Insert, assert, then remove.
        _far = _dt.datetime(9999, 12, 31, tzinfo=_dt.timezone.utc).timestamp()
        conn.execute(
            "INSERT INTO sessions(session_id,project,last_epoch,first_epoch,"
            "msg_count,tool_calls,cost_usd,input_tokens,output_tokens,"
            "cache_write,cache_read,duration_s) "
            "VALUES('__far__','/x',?,?,1,0,0,0,0,0,0,0)", (_far, _far))
        try:
            a2 = analytics.overview(conn)
            c.eq(a2["sessions"], 2, "far-future row still counted in SQL totals")
            c.ok(all(not d["date"].startswith("9999") for d in a2["daily"]),
                 "far-future epoch excluded from daily chart on every OS")
            c.eq(sum(sum(row) for row in a2["heatmap"]), 1,
                 "far-future epoch excluded from heatmap on every OS")
            w2 = wrapped.generate(conn)
            c.ok(any(card["label"] == "Sessions" for card in w2["cards"]),
                 "wrapped survives a far-future epoch")
            c.ok(9999 not in wrapped.available_years(conn),
                 "available_years drops the far-future epoch on every OS")
        finally:
            conn.execute("DELETE FROM sessions WHERE session_id='__far__'")
            conn.commit()

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

        # --- cli: list / search / ask reach the core workflows ----------
        ap = cli.build_parser()

        def _run(argv):
            ns = ap.parse_args(argv)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = ns.func(ns)
            return rc, buf.getvalue()

        rc, out = _run(["list", "--db", db])
        c.eq(rc, 0, "cli list exits 0")
        c.ok(exp["session_id"][:8] in out, "cli list shows the session id")
        rc, out = _run(["search", "parser", "--db", db])
        c.eq(rc, 0, "cli search exits 0")
        c.ok("parser" in out.lower(), "cli search output mentions the match")
        rc, out = _run(["search", "parser", "--db", db, "--kind", "tool", "--json"])
        c.eq(rc, 0, "cli search --json exits 0")
        c.ok('"kind": "tool"' in out, "cli search --json carries the filter")
        rc, out = _run(["ask", "what should I reopen next?", "--db", db])
        c.eq(rc, 0, "cli ask exits 0")
        c.ok("reopen" in out.lower(), "cli ask renders the answer title")
        rc, out = _run(["ask", "summarize this session", "--db", db,
                        "--session", exp["session_id"]])
        c.eq(rc, 0, "cli ask --session exits 0")
        c.ok("digest" in out.lower(), "cli ask scoped → digest")

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

        # reopen stays correct AND avoids the per-session N+1: one batched error
        # lookup + inline citations, regardless of how many sessions it ranks.
        qn = {"n": 0}
        conn2.set_trace_callback(lambda s: qn.__setitem__("n", qn["n"] + 1))
        ro2 = ask.reopen_suggestions(conn2)
        conn2.set_trace_callback(None)
        c.eq(ro2["intent"], "reopen", "corpus reopen intent")
        c.ok(len(ro2["blocks"][-1]["items"]) >= 1, "corpus reopen surfaces sessions")
        c.ok(qn["n"] <= 6, f"reopen avoids N+1 ({qn['n']} SQL stmts for 12 sessions)")

        conn2.close()

        # --- v0.5.0: tool stats / graph / similarity / find-by-file ------
        # A crafted index gives every detector a deterministic, known input.
        from . import highlights, mcp

        def _mk_session(conn, sid, **kw):
            cols = {
                "session_id": sid, "title": "", "project": "/p", "project_name": "p",
                "git_branch": "", "version": "", "first_ts": "", "last_ts": "",
                "first_epoch": 0.0, "last_epoch": 0.0, "duration_s": 0.0,
                "msg_count": 0, "user_msgs": 0, "assistant_msgs": 0, "tool_calls": 0,
                "models": "[]", "primary_model": "", "input_tokens": 0,
                "output_tokens": 0, "cache_write": 0, "cache_read": 0,
                "cost_usd": 0.0, "file_path": "", "preview": "",
            }
            cols.update(kw)
            keys = ",".join(cols)
            ph = ",".join("?" * len(cols))
            conn.execute(f"INSERT OR REPLACE INTO sessions({keys}) VALUES({ph})",
                         list(cols.values()))
            conn.execute("INSERT OR IGNORE INTO user_state(session_id) VALUES(?)", (sid,))

        def _mk_tool(conn, sid, name, *, is_error=0, inp=None, seq=0):
            conn.execute(
                "INSERT INTO tool_calls(session_id,message_uuid,seq,name,ts,is_error,"
                "input_json,result_preview) VALUES(?,?,?,?,?,?,?,?)",
                (sid, f"{sid}:{seq}", seq, name, "", is_error,
                 json.dumps(inp or {}), ""),
            )

        def _mk_msg(conn, sid, text, *, seq=0):
            conn.execute(
                "INSERT INTO messages(uuid,session_id,role,seq,text) VALUES(?,?,?,?,?)",
                (f"{sid}:{seq}", sid, "user", seq, text),
            )

        hdb = os.path.join(tmp, "hl.db")
        hconn = index.connect(hdb)
        _far_day = 1_770_000_000.0  # a fixed, representable epoch (same local day reused)

        # marathon + breakthrough: long session that recovered after tool errors
        _mk_session(hconn, "mara", title="Big refactor", duration_s=7200,
                    msg_count=200, cost_usd=0.5, last_epoch=_far_day,
                    primary_model="claude-opus-4-8", preview="refactor the parser module end to end")
        for i in range(2):
            _mk_tool(hconn, "mara", "Bash", is_error=1, seq=i)
        _mk_tool(hconn, "mara", "Edit", is_error=0,
                 inp={"file_path": "/p/core/engine.py"}, seq=2)
        _mk_tool(hconn, "mara", "Edit", is_error=0,
                 inp={"file_path": "/p/core/engine.py"}, seq=3)

        # cost spike: one session far above the mean
        for i in range(5):
            _mk_session(hconn, f"cheap{i}", cost_usd=0.05, msg_count=10,
                        last_epoch=_far_day, primary_model="claude-haiku-4-5")
        _mk_session(hconn, "spike", title="Marathon debug", cost_usd=50.0,
                    msg_count=80, last_epoch=_far_day, primary_model="claude-opus-4-8")

        # abandoned: tiny sessions
        _mk_session(hconn, "ab1", msg_count=1, last_epoch=_far_day)
        _mk_session(hconn, "ab2", msg_count=2, last_epoch=_far_day)

        # recurring prompts: two near-identical openers (>60% trigram overlap)
        _mk_session(hconn, "rec1", preview="run the tests and fix all the failing tests one by one",
                    msg_count=12, last_epoch=_far_day)
        _mk_session(hconn, "rec2", preview="run the tests and fix all the failing tests one by one now",
                    msg_count=12, last_epoch=_far_day)

        # similarity: rec1/rec2 share prompt words; unrelated does not
        _mk_msg(hconn, "rec1", "run the tests and fix all the failing assertions in the parser")
        _mk_msg(hconn, "rec2", "run the tests and fix all the failing assertions in the parser quickly")
        _mk_msg(hconn, "spike", "design a brand new billing dashboard with charts and exports")
        # most-edited file appears across sessions for revisited-files
        _mk_tool(hconn, "rec1", "Edit", inp={"file_path": "/p/core/engine.py"}, seq=0)
        _mk_tool(hconn, "rec2", "Read", inp={"file_path": "/p/core/engine.py"}, seq=0)
        hconn.commit()

        # tools_stats
        tstats = api.tools_stats(hconn)
        names = {t["name"]: t for t in tstats["leaderboard"]}
        c.ok("Edit" in names and "Bash" in names, "tools_stats leaderboard lists used tools")
        c.eq(names["Bash"]["errors"], 2, "tools_stats counts Bash errors")
        c.close(names["Bash"]["success_rate"], 0.0, "tools_stats Bash success_rate = 0")
        c.ok(any(f["file"] == "engine.py" for f in tstats["most_edited_files"]),
             "tools_stats surfaces the most-edited file")
        c.ok(tstats["total_calls"] >= 6, "tools_stats total_calls aggregates")
        c.ok(tstats["distinct_tools"] >= 2, "tools_stats counts distinct tools")

        # graph
        g = api.graph(hconn, {})
        ids = {n["id"]: n for n in g["nodes"]}
        c.ok("s:mara" in ids and ids["s:mara"]["type"] == "session", "graph has a session node")
        c.ok(any(n["type"] == "project" for n in g["nodes"]), "graph has a project node")
        c.ok(any(n["type"] == "file" and n["label"] == "engine.py" for n in g["nodes"]),
             "graph has the edited file node")
        c.ok(g["stats"]["edges"] == len(g["edges"]), "graph stats.edges matches edge list")
        c.ok({"source": "s:mara", "target": "f:engine.py", "rel": "touched"} in g["edges"],
             "graph links session → file it touched")
        gp = api.graph(hconn, {"project": "nope"})
        c.eq(gp["stats"]["session"], 0, "graph project filter excludes non-matches")

        # similarity
        sim = api.similar_sessions(hconn, "rec1", 3)
        c.ok(sim["similar"], "similar_sessions returns ranked neighbours")
        c.eq(sim["similar"][0]["session_id"], "rec2", "similar_sessions ranks the near-duplicate first")
        c.ok(sim["similar"][0]["score"] > 0.3, "similar_sessions score is meaningful")
        c.eq(api.similar_sessions(hconn, "does-not-exist")["similar"], [],
             "similar_sessions on unknown id → empty")

        # find sessions by file
        byf = api.sessions_by_file(hconn, "engine.py")
        found = {s["session_id"] for s in byf["sessions"]}
        c.ok({"mara", "rec1", "rec2"} <= found, "sessions_by_file finds every toucher")
        c.eq(api.sessions_by_file(hconn, "")["sessions"], [], "sessions_by_file empty path → []")
        # LIKE wildcards in the needle are matched literally, never as patterns
        c.eq(api.sessions_by_file(hconn, "%.py")["sessions"], [],
             "sessions_by_file escapes LIKE wildcards (no pattern injection)")

        # --- v0.5.0: highlights ------------------------------------------
        h = highlights.generate(hconn)
        c.ok(any(x["session_id"] == "spike" for x in h["cost_spikes"]),
             "highlights flags the cost spike")
        c.ok(any(x["session_id"] == "mara" for x in h["marathons"]),
             "highlights flags the marathon session")
        c.ok(any(x["session_id"] == "mara" for x in h["breakthroughs"]),
             "highlights detects the error→recovery breakthrough")
        c.ok({x["session_id"] for x in h["abandoned"]} >= {"ab1", "ab2"},
             "highlights flags abandoned sessions")
        c.ok(any(x.get("file") == "engine.py" for x in h["revisited_files"]),
             "highlights flags the revisited file")
        c.ok(any({p["a"], p["b"]} == {"rec1", "rec2"} for p in h["recurring_prompts"]),
             "highlights detects the recurring prompt pair")
        c.ok(any("claude-opus-4-8" in m["models"] for m in h["model_migrations"]),
             "highlights detects the day with multiple models")

        # --- v0.5.0: MCP server (JSON-RPC 2.0 dispatch) ------------------
        def _rpc(req):
            return mcp.handle_request(hdb, req)

        init = _rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        c.eq(init["result"]["protocolVersion"], mcp.PROTOCOL_VERSION, "mcp initialize returns protocol version")
        c.eq(init["result"]["serverInfo"]["version"], claudestudio.__version__,
             "mcp serverInfo carries the package version")
        tl = _rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        c.eq(len(tl["result"]["tools"]), 20, "mcp exposes 20 tools")
        c.ok(all(t.get("inputSchema") for t in tl["result"]["tools"]), "every mcp tool has an input schema")
        # notification (no id) gets no response
        c.ok(_rpc({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None,
             "mcp notification yields no response")
        # unknown method / bad params / unknown tool
        c.eq(_rpc({"jsonrpc": "2.0", "id": 3, "method": "nope"})["error"]["code"],
             mcp.METHOD_NOT_FOUND, "mcp unknown method → -32601")
        c.eq(_rpc({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {}})["error"]["code"],
             mcp.INVALID_PARAMS, "mcp tools/call without name → -32602")
        unk = _rpc({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                    "params": {"name": "ghost", "arguments": {}}})
        c.ok(unk["result"]["isError"], "mcp unknown tool → isError result")

        def _call(name, arguments):
            r = _rpc({"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                      "params": {"name": name, "arguments": arguments}})
            return json.loads(r["result"]["content"][0]["text"]), r["result"]["isError"]

        sr, err = _call("search_sessions", {"query": "refactor", "limit": 5})
        c.ok(not err and any(s["session_id"] == "mara" for s in sr["sessions"]),
             "mcp search_sessions finds a session")
        gs, err = _call("get_session", {"session_id": "mara"})
        c.ok(not err and gs["session_id"] == "mara" and "by_tool" in gs, "mcp get_session returns detail")
        miss, err = _call("get_session", {"session_id": "ghost"})
        c.ok(err and "error" in miss, "mcp get_session unknown id → isError")
        an, err = _call("get_analytics_summary", {"days": 30})
        c.ok(not err and an["all_time"]["sessions"] >= 1 and an["window"]["days"] == 30,
             "mcp get_analytics_summary returns all-time + window")
        bf, err = _call("find_sessions_by_file", {"file_path": "engine.py"})
        c.ok(not err and any(s["session_id"] == "mara" for s in bf["sessions"]),
             "mcp find_sessions_by_file works")
        rs, err = _call("get_recent_sessions", {"limit": 3})
        c.ok(not err and len(rs["sessions"]) >= 1, "mcp get_recent_sessions works")
        ann, err = _call("get_session_annotations", {"session_id": "mara"})
        c.ok(not err and ann["annotations"] == [], "mcp get_session_annotations empty when no note")
        ps, err = _call("get_project_stats", {"project_name": "p"})
        c.ok(not err and ps.get("sessions", 0) >= 1, "mcp get_project_stats aggregates the project")
        ah, err = _call("ask_history", {"question": "what should I reopen next?"})
        c.ok(not err and ah.get("intent") in ("reopen", "search", "help"), "mcp ask_history routes a question")

        # a stored note is surfaced as an annotation
        api.set_state(hconn, "mara", {"notes": "remember to add a migration"})
        ann2, _ = _call("get_session_annotations", {"session_id": "mara"})
        c.ok(ann2["annotations"] and "migration" in ann2["annotations"][0]["body"],
             "mcp get_session_annotations surfaces a stored note")

        # --- v0.5.0: JSON export -----------------------------------------
        jx = api.export_session(hconn, "rec1", "json")
        c.ok(jx is not None and jx["filename"].endswith(".json"), "json export has .json filename")
        c.ok(jx["content_type"].startswith("application/json"), "json export content type")
        parsed = json.loads(jx["text"])
        c.eq(parsed["session_id"], "rec1", "json export round-trips the session id")
        c.ok("timeline" in parsed, "json export includes the timeline")

        # =================================================================
        # v0.5.1 features
        # =================================================================
        from . import hook as hookmod
        from . import patterns as patmod
        from . import report as reportmod
        from . import server as servermod

        # --- F11: version embedded everywhere ----------------------------
        c.eq(claudestudio.__version__, "0.6.1", "package version bumped to 0.6.1")
        c.eq(init["result"]["serverInfo"]["version"], "0.6.1", "mcp serverInfo is 0.6.1")
        rc, out = _run(["info", "--db", db])
        c.eq(rc, 0, "cli info exits 0")
        c.ok("0.6.1" in out, "cli info prints the version")
        c.ok("mcp tools" in out and "20" in out, "cli info reports the 20 MCP tools")

        # --- F3: inline unified diff (pure tool_diff) --------------------
        d_edit, trunc = api.tool_diff(
            "Edit", {"file_path": "a.py", "old_string": "x = 1\ny = 2",
                     "new_string": "x = 1\ny = 3"})
        c.eq(d_edit, "--- a/a.py\n+++ b/a.py\n@@ -1,2 +1,2 @@\n x = 1\n-y = 2\n+y = 3",
             "tool_diff renders an exact unified diff for an Edit")
        c.ok(d_edit.startswith("---") and "\n+y = 3" in d_edit, "diff has --- header and + line")
        c.eq(trunc, False, "small diff is not truncated")
        d_new, _ = api.tool_diff("Write", {"file_path": "n.py", "content": "a\nb"})
        c.eq(d_new, "--- a/n.py\n+++ b/n.py\n@@ -0,0 +1,2 @@\n+a\n+b",
             "tool_diff renders create (empty→content) for Write")
        c.eq(api.tool_diff("Read", {"path": "z"}), (None, False),
             "tool_diff is None for a non-edit tool")
        c.eq(api.tool_diff("Edit", {"old_string": "same", "new_string": "same"}),
             (None, False), "tool_diff is None when nothing changed")
        # integration: get_session attaches a diff to the edit tool
        _mk_session(hconn, "df", title="diff demo", msg_count=1)
        _mk_msg(hconn, "df", "change a file", seq=0)
        _mk_tool(hconn, "df", "Edit", seq=0,
                 inp={"file_path": "z.py", "old_string": "a\nb", "new_string": "a\nc"})
        hconn.commit()
        dfdetail = api.get_session(hconn, "df")
        dtool = dfdetail["timeline"][0]["tools"][0]
        c.ok(dtool.get("diff", "").startswith("---"), "get_session attaches a diff to the edit")
        c.ok("diff_truncated" in dtool, "get_session flags diff truncation state")

        # --- F2: per-message bookmarks -----------------------------------
        c.eq(index.list_bookmarks(hconn), [], "no bookmarks initially")
        bk = api.add_bookmark(hconn, "mara", {"seq": 2, "note": "look here"})
        c.ok(bk["id"] and bk["seq"] == 2, "add_bookmark returns id + seq")
        c.eq(bk["note"], "look here", "bookmark note round-trips")
        allbk = api.list_bookmarks(hconn)["bookmarks"]
        c.eq(len(allbk), 1, "one bookmark listed")
        c.eq(allbk[0]["session_title"], "Big refactor", "bookmark carries session title")
        c.eq(len(api.list_bookmarks(hconn, "mara")["bookmarks"]), 1, "bookmark filter by session")
        c.eq(api.list_bookmarks(hconn, "nope")["bookmarks"], [], "bookmark filter excludes others")
        c.eq(api.delete_bookmark(hconn, bk["id"])["deleted"], True, "bookmark deleted")
        c.eq(index.list_bookmarks(hconn), [], "bookmarks empty after delete")
        c.eq(api.delete_bookmark(hconn, "ghost")["deleted"], False, "deleting unknown bookmark → False")

        # --- F6: per-tool latency ----------------------------------------
        _mk_session(hconn, "lat", title="latency", msg_count=6, last_epoch=1_700_000_000.0)
        for _k, (_s, _e) in enumerate([(1000.0, 1001.0), (2000.0, 2002.0), (3000.0, 3003.0)]):
            _a, _u = _k * 2, _k * 2 + 1
            hconn.execute("INSERT INTO messages(uuid,session_id,role,seq,epoch,text) "
                          "VALUES(?,?,?,?,?,?)", (f"lat:{_a}", "lat", "assistant", _a, _s, ""))
            hconn.execute("INSERT INTO messages(uuid,session_id,role,seq,epoch,text) "
                          "VALUES(?,?,?,?,?,?)", (f"lat:{_u}", "lat", "user", _u, _e, ""))
            _mk_tool(hconn, "lat", "Bash", seq=_a)
        hconn.commit()
        lat = analytics.tool_latency(hconn)
        c.ok("Bash" in lat, "tool_latency surfaces the timed tool")
        c.eq(lat["Bash"]["count"], 3, "tool_latency counts the timed calls")
        c.close(lat["Bash"]["p50_ms"], 2000.0, "tool_latency p50 within 5%", tol=100.0)
        c.eq(lat["Bash"]["max_ms"], 3000.0, "tool_latency max is exact")
        c.eq(api.tool_latency_payload(hconn)["latency"]["Bash"]["count"], 3,
             "tool_latency_payload wraps the dict")

        # --- F8: prompt patterns -----------------------------------------
        _pbase = "please write unit tests for the auth module now"
        for _i in range(5):
            _sid = f"pat{_i}"
            _mk_session(hconn, _sid, title=f"tests {_i}", msg_count=2,
                        last_epoch=1_700_000_000.0 + _i)
            hconn.execute(
                "INSERT INTO messages(uuid,session_id,role,seq,epoch,text) VALUES(?,?,?,?,?,?)",
                (f"{_sid}:0", _sid, "user", 0, 1_700_000_000.0 + _i,
                 (_pbase + " " + "again " * _i).strip()))
        hconn.commit()
        pats = patmod.extract_patterns(hconn, min_count=3)
        c.ok(pats, "extract_patterns finds a cluster")
        _auth = [p for p in pats if "auth module" in p["canonical_text"]]
        c.ok(_auth, "extract_patterns clusters the auth-tests prompts")
        c.eq(_auth[0]["count"], 5, "the recurring prompt is counted 5 times")
        c.ok(_pbase in _auth[0]["canonical_text"], "canonical text matches the prompt shape")
        c.ok(0.0 < _auth[0]["similarity_score"] <= 1.0, "pattern similarity score is a fraction")
        c.eq(api.prompt_patterns(hconn, {"min_count": 99})["patterns"], [],
             "prompt_patterns honours min_count")

        # --- F4: activity report -----------------------------------------
        _lo, _hi = 0.0, 9_000_000_000.0
        rd = reportmod.report_data(hconn, _lo, _hi, "Sprint Report")
        _exp_sessions = hconn.execute(
            "SELECT COUNT(*) n, COALESCE(SUM(cost_usd),0) c FROM sessions "
            "WHERE last_epoch>=? AND last_epoch<?", (_lo, _hi)).fetchone()
        c.eq(rd["totals"]["sessions"], _exp_sessions["n"], "report session count is exact")
        c.close(rd["totals"]["cost_usd"], _exp_sessions["c"], "report cost is exact")
        rep_html = reportmod.generate_report(hconn, _lo, _hi, "Sprint Report", "html")
        c.ok("Sprint Report" in rep_html, "report HTML carries the title")
        c.ok("Top projects" in rep_html and "Top tools" in rep_html
             and "Notable sessions" in rep_html, "report HTML has its section headings")
        c.ok("@media print" in rep_html, "report HTML is print-optimized")
        rep_md = reportmod.generate_report(hconn, _lo, _hi, "Sprint Report", "md")
        c.ok(rep_md.startswith("# Sprint Report"), "report markdown starts with the title")
        rj = api.report_json(hconn, {"since": "1970-01-01", "until": "2200-01-01"})
        c.ok("totals" in rj and "top_projects" in rj, "report_json returns structured data")

        # --- F9: CSV exports ---------------------------------------------
        scsv = api.sessions_csv(hconn)
        _nsess = hconn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        c.eq(len(scsv.strip().splitlines()), _nsess + 1, "sessions CSV has header + one row each")
        c.ok(scsv.splitlines()[0].startswith("session_id,title"), "sessions CSV header columns")
        acsv = api.analytics_csv(hconn)
        for _sec in ("# Overview", "# By model", "# By tool", "# Daily activity", "# Top projects"):
            c.ok(_sec in acsv, f"analytics CSV has the {_sec!r} section")

        # --- F1: SSE packing + watch mtime scan --------------------------
        c.eq(servermod.sse_pack({"type": "reindex", "ts": 123}),
             'data: {"type": "reindex", "ts": 123}\n\n', "sse_pack frames JSON as one SSE event")
        c.ok(index.index_db_mtime(hdb) > 0, "index_db_mtime reads an existing index")
        c.eq(index.index_db_mtime(os.path.join(tmp, "nope.db")), 0.0,
             "index_db_mtime is 0 for a missing index")
        c.ok(index.newest_source_mtime(root) > 0, "newest_source_mtime finds the fixture files")
        c.eq(index.newest_source_mtime(os.path.join(tmp, "empty-root")), 0.0,
             "newest_source_mtime is 0 for an empty/absent root")

        # --- F5: hook install / status / uninstall (mocked settings) -----
        _hookcfg = os.path.join(tmp, "settings.json")
        r1 = hookmod.install_hook(_hookcfg)
        c.ok(r1["changed"] and r1["installed"], "install_hook writes the hook")
        c.ok(hookmod.is_installed(hookmod._load_settings(_hookcfg)), "hook present after install")
        r2 = hookmod.install_hook(_hookcfg)
        c.eq(r2["changed"], False, "install_hook is idempotent (no duplicate)")
        _g = hookmod._load_settings(_hookcfg)["hooks"][hookmod.HOOK_EVENT]
        c.eq(len(_g), 1, "install_hook leaves exactly one hook group")
        c.ok(hookmod.hook_status(_hookcfg)["installed"], "hook_status reports installed")
        r3 = hookmod.uninstall_hook(_hookcfg)
        c.ok(r3["changed"] and not r3["installed"], "uninstall_hook removes the hook")
        c.eq(hookmod._load_settings(_hookcfg), {}, "uninstall prunes back to a clean settings file")
        # a foreign hook is preserved across install/uninstall
        with open(_hookcfg, "w", encoding="utf-8") as _fh:
            json.dump({"hooks": {"SessionEnd": [{"hooks": [
                {"type": "command", "command": "echo keepme"}]}]}}, _fh)
        hookmod.install_hook(_hookcfg)
        hookmod.uninstall_hook(_hookcfg)
        _after = hookmod._load_settings(_hookcfg)
        c.ok(any(h.get("command") == "echo keepme"
                 for grp in _after["hooks"]["SessionEnd"] for h in grp["hooks"]),
             "uninstall never clobbers a pre-existing hook")

        # --- F8/F2: the two new MCP tools dispatch -----------------------
        names = {t["name"] for t in tl["result"]["tools"]}
        c.ok({"list_bookmarks", "get_prompt_patterns"} <= names,
             "mcp exposes list_bookmarks + get_prompt_patterns")
        lbk, err = _call("list_bookmarks", {})
        c.ok(not err and "bookmarks" in lbk, "mcp list_bookmarks returns bookmarks")
        gpp, err = _call("get_prompt_patterns", {"min_count": 3})
        c.ok(not err and "patterns" in gpp, "mcp get_prompt_patterns returns patterns")

        # =================================================================
        # v0.5.2 features
        # =================================================================
        import zipfile

        from . import budget as budgetmod
        from . import generate_claude_md as genmd
        from . import git_context, health
        from . import prompt_library as plib

        # --- F10: health score (pure function + boundaries + column) ------
        _pj = parser.parse_file(exp["path"])
        _hs = health.compute_health_score(_pj)
        c.ok(0 <= _hs["score"] <= 100, "health score is within 0..100")
        c.ok(_hs["grade"] in ("A", "B", "C", "D", "F"), "health grade is a letter")
        c.eq(set(_hs["components"]),
             {"tool_success", "error_density", "token_efficiency", "completion_signal"},
             "health exposes its four components")
        c.eq(health.grade_for(95), "A", "grade boundary: 95 → A")
        c.eq(health.grade_for(85), "B", "grade boundary: 85 → B")
        c.eq(health.grade_for(70), "C", "grade boundary: 70 → C")
        c.eq(health.grade_for(55), "D", "grade boundary: 55 → D")
        c.eq(health.grade_for(10), "F", "grade boundary: 10 → F")
        # a perfect run scores higher than a flat-out failure
        _good = health.compute(tool_calls=10, tool_errors=0, input_tokens=1000,
                               output_tokens=4000, msg_count=20, completion_signal=1.0)
        _bad = health.compute(tool_calls=10, tool_errors=10, input_tokens=4000,
                              output_tokens=10, msg_count=4, completion_signal=0.0)
        c.ok(_good["score"] > _bad["score"], "healthy session outscores a failed one")
        c.eq(health.completion_signal_for("user", False, False), 0.0,
             "completion: ending on a user prompt = abandoned (0.0)")
        c.eq(health.completion_signal_for("assistant", False, False), 1.0,
             "completion: clean assistant wrap-up = 1.0")

        # health column populated by indexing + sort + get_session breakdown
        hsdb = os.path.join(tmp, "health.db")
        hsconn = index.connect(hsdb)
        index.reindex(hsconn, root)
        _hrow = hsconn.execute(
            "SELECT health_score FROM sessions WHERE session_id=?",
            (exp["session_id"],)).fetchone()
        c.ok(_hrow["health_score"] is not None, "indexing caches a health_score on the row")
        _hl = api.list_sessions(hsconn, {"sort": "health"})
        c.ok(_hl["sessions"] and "health_score" in _hl["sessions"][0],
             "list_sessions sorts by health + carries the score")
        _hd = api.get_session(hsconn, exp["session_id"])
        c.ok(_hd["health"]["grade"] in ("A", "B", "C", "D", "F"),
             "get_session attaches a health breakdown")
        c.ok("git" in _hd, "get_session always carries a git key (may be null)")

        # --- F7: git context (best-effort, never raises) -----------------
        c.eq(git_context.get_git_context(os.path.join(tmp, "no-such-repo"), 1_700_000_000.0),
             None, "git context on a non-repo path is None (no raise)")
        c.eq(git_context.get_current_branch(os.path.join(tmp, "no-such-repo")), None,
             "current branch on a non-repo path is None")
        c.eq(git_context.get_git_context("", 0), None, "git context on empty path is None")
        hsconn.close()

        # --- F3: budget tracker ------------------------------------------
        c.ok(not budgetmod.budget_status(hconn)["has_budget"],
             "budget status reports no budget initially")
        _set = budgetmod.set_budget(hconn, "monthly", 10.0)
        c.eq(_set["ceiling_usd"], 10.0, "set_budget stores the ceiling")
        _bnow = _dt.datetime.fromtimestamp(_far_day)  # month with the seeded spend
        _bs = budgetmod.budget_status(hconn, now=_bnow)
        c.ok(_bs["has_budget"] and _bs["spent_usd"] > 0,
             "budget status computes spend in the active period")
        c.eq(set(_bs) >= {"period", "ceiling_usd", "spent_usd", "percent",
                          "remaining_usd", "sessions_this_period", "alert"}, True,
             "budget status has the full structure")
        c.ok(_bs["alert"], "budget over 75% raises the alert flag (seeded spend > $10)")
        c.ok(budgetmod.clear_budget(hconn)["cleared"], "clear_budget removes it")
        c.ok(not budgetmod.budget_status(hconn)["has_budget"], "budget gone after clear")

        # --- F5: annotations (CRUD + FTS + survive reindex) --------------
        _a1 = index.upsert_annotation(hconn, "mara", -1, "the big scheduler refactor")
        c.ok(_a1["id"] and _a1["message_idx"] == -1, "session-level annotation upserts")
        _a2 = index.upsert_annotation(hconn, "mara", 2, "this edit fixed the race")
        c.eq(len(index.list_annotations(hconn, "mara")), 2,
             "session + message annotations coexist")
        _a1b = index.upsert_annotation(hconn, "mara", -1, "updated note text")
        c.eq(_a1b["id"], _a1["id"], "re-annotating the same target updates in place")
        c.eq(len(index.list_annotations(hconn, "mara")), 2, "no duplicate on update")
        _asr = index.search_annotations(hconn, "scheduler")
        c.eq(_asr, [], "FTS reflects the update (old 'scheduler' text gone)")
        _asr2 = index.search_annotations(hconn, "race")
        c.ok(any(r["session_id"] == "mara" for r in _asr2),
             "FTS finds an annotation by note content")
        c.ok(index.delete_annotation(hconn, _a2["id"])["deleted"], "annotation deletes")
        c.eq(len(index.list_annotations(hconn, "mara")), 1, "one annotation left after delete")
        c.eq(index.search_annotations(hconn, "race"), [], "deleted note drops out of FTS")
        c.eq(api.get_annotations(hconn, "mara")["annotations"][0]["note"], "updated note text",
             "api.get_annotations surfaces the note")
        # annotations survive a reindex (they live in their own table)
        annsurv_db = os.path.join(tmp, "annsurv.db")
        asconn = index.connect(annsurv_db)
        index.reindex(asconn, root)
        index.upsert_annotation(asconn, exp["session_id"], -1, "keep me across reindex")
        index.reindex(asconn, root, force=True)
        c.eq(len(index.list_annotations(asconn, exp["session_id"])), 1,
             "annotation survives a forced reindex")
        asconn.close()

        # --- F8: prompt library (extract + CRUD + search) ----------------
        _ex = api.extract_prompts(hconn, {"min_count": 3})
        c.ok(_ex["extracted"] >= 1, "extract_prompts seeds the library from history")
        c.ok(any("auth module" in p["text"] for p in index.list_prompts(hconn)),
             "extracted library includes the recurring auth-tests prompt")
        c.ok(index.list_prompts(hconn, q="auth"), "prompt library substring search works")
        _man = index.upsert_prompt(hconn, text="Refactor this for readability",
                                   source="manual", starred=True)
        c.ok(_man["starred"], "manual prompt can be starred")
        c.ok(all(p["starred"] for p in index.list_prompts(hconn, starred=True)),
             "starred filter returns only starred prompts")
        c.ok(plib.score_prompt_reusability("write tests for the auth module")
             > plib.score_prompt_reusability("fix /home/dev/x.py line 4231 today"),
             "reusability score rewards templates over one-off references")
        c.ok(index.delete_prompt(hconn, _man["id"])["deleted"], "library prompt deletes")
        # idempotent extraction: same ids, no duplicate rows
        _n1 = len(index.list_prompts(hconn, limit=1000))
        api.extract_prompts(hconn, {"min_count": 3})
        c.eq(len(index.list_prompts(hconn, limit=1000)), _n1,
             "re-extraction is idempotent (stable ids, no duplicates)")

        # --- F4: CLAUDE.md generator -------------------------------------
        _cm = api.project_claude_md(hconn, "p")
        c.ok(_cm["profile"]["found"], "analyse_project finds the 'p' project")
        for _sec in ("## Project Overview", "## Key Files", "## Conventions Observed",
                     "## Common Pitfalls", "## Preferred Patterns"):
            c.ok(_sec in _cm["markdown"], f"generated CLAUDE.md has the {_sec!r} section")
        c.ok("engine.py" in _cm["markdown"], "CLAUDE.md surfaces the most-edited file")
        c.eq(genmd.analyse_project(hconn, "no-such-project")["found"], False,
             "analyse_project returns found=False for an unknown project")

        # --- F6: token-efficiency dashboard ------------------------------
        _eff = api.efficiency(hconn)
        c.eq(set(_eff), {"overall", "by_project", "trend"}, "efficiency has its 3 sections")
        c.eq(set(_eff["overall"]),
             {"output_tokens_per_dollar", "tool_success_rate",
              "avg_messages_per_session", "median_session_duration_s"},
             "efficiency overall has the 4 KPIs")
        c.ok(0.0 <= _eff["overall"]["tool_success_rate"] <= 1.0,
             "tool_success_rate is a fraction")
        _ranks = [p["efficiency_rank"] for p in _eff["by_project"]]
        c.eq(_ranks, sorted(_ranks), "by_project is sorted by efficiency_rank ascending")
        c.ok(all(re_match_week(t["week"]) for t in _eff["trend"]),
             "trend weeks are formatted YYYY-Www")

        # --- F11: batch export + archive ---------------------------------
        _bx = api.export_batch(hconn, ["mara", "rec1"], "md", include_index=True)
        c.ok(_bx["content_type"] == "application/zip", "batch export is a zip")
        c.eq(_bx["count"], 2, "batch export wrote both sessions")
        _zf = zipfile.ZipFile(io.BytesIO(_bx["bytes"]))
        _names = _zf.namelist()
        c.ok("index.md" in _names, "batch zip contains the index.md table of contents")
        c.ok(sum(1 for n in _names if n != "index.md") == 2,
             "batch zip contains one file per session")
        c.ok("| Session |" in _zf.read("index.md").decode("utf-8"),
             "index.md is a session table of contents")
        _bx0 = api.export_batch(hconn, [], "md", include_index=False)
        c.eq(_bx0["count"], 0, "batch export of nothing is an empty (valid) zip")

        # --- F9: cost-by-period + per-session diffs ----------------------
        _cbp = api.cost_by_period(hconn, "monthly", 6)
        c.ok("periods" in _cbp and isinstance(_cbp["periods"], list),
             "cost_by_period returns a periods list")
        if _cbp["periods"]:
            c.eq(set(_cbp["periods"][0]) >= {"period", "sessions", "cost_usd", "tokens"},
                 True, "each period has spend/token/session totals")
        _dfs = api.diffs_for_session(hconn, "df")
        c.ok(_dfs["diffs"] and _dfs["diffs"][0]["diff"].startswith("---"),
             "diffs_for_session returns the session's unified diffs")
        c.ok(api.diffs_for_session(hconn, "df", "z.py")["diffs"],
             "diffs_for_session filters by filename (match)")
        c.eq(api.diffs_for_session(hconn, "df", "nope.py")["diffs"], [],
             "diffs_for_session filters by filename (no match)")

        # --- F9: the four new MCP tools dispatch -------------------------
        _names = {t["name"] for t in tl["result"]["tools"]}
        c.ok({"get_cost_by_period", "get_diff_for_session", "get_annotations",
              "generate_project_brief"} <= _names,
             "mcp exposes the four v0.5.2 tools")
        _gc, err = _call("get_cost_by_period", {"period": "monthly", "n": 3})
        c.ok(not err and "periods" in _gc, "mcp get_cost_by_period returns periods")
        _gd, err = _call("get_diff_for_session", {"session_id": "df"})
        c.ok(not err and _gd["diffs"], "mcp get_diff_for_session returns diffs")
        # upsert a note so the MCP annotations tool has something to surface
        index.upsert_annotation(hconn, "spike", -1, "watch this expensive session")
        _ga, err = _call("get_annotations", {"session_id": "spike"})
        c.ok(not err and _ga["annotations"], "mcp get_annotations surfaces a stored note")
        _gb, err = _call("generate_project_brief", {"project_id": "p"})
        c.ok(not err and _gb["found"] and _gb["sessions"] >= 1,
             "mcp generate_project_brief returns a populated brief")

        hconn.close()

        # --- F7: multi-root indexing (own db so ids stay distinct) -------
        def _write_min(rt, sid, proj):
            d = os.path.join(rt, proj)
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, f"{sid}.jsonl"), "w", encoding="utf-8") as fh:
                fh.write(json.dumps({
                    "type": "user", "uuid": sid + "u", "parentUuid": None,
                    "sessionId": sid, "cwd": "/x", "timestamp": "2026-06-01T10:00:00Z",
                    "message": {"role": "user", "content": "hello " + sid}}) + "\n")

        root_a = os.path.join(tmp, "root-a")
        root_b = os.path.join(tmp, "root-b")
        _write_min(root_a, "mra1", "projA")
        _write_min(root_a, "mra2", "projA")
        _write_min(root_b, "mrb1", "projB")
        mrdb = os.path.join(tmp, "mr.db")
        mrconn = index.connect(mrdb)
        mrstats = index.reindex(mrconn, [root_a, root_b])
        c.eq(mrstats["added"], 3, "multi-root indexes every root's sessions")
        c.eq(set(mrstats["roots"]), {root_a, root_b}, "reindex records both roots")
        c.eq(index.session_summary(mrconn)["sessions"], 3, "combined session count")
        c.eq(api.list_sessions(mrconn, {"root": root_a})["total"], 2,
             "root filter returns only root-a sessions")
        c.eq(api.list_sessions(mrconn, {"root": root_b})["total"], 1,
             "root filter returns only root-b sessions")
        rcounts = {r["root"]: r["sessions"] for r in index.root_counts(mrconn)}
        c.eq(rcounts.get(root_a), 2, "root_counts tallies root-a")
        c.eq(rcounts.get(root_b), 1, "root_counts tallies root-b")
        c.eq(index.stored_schema_version(mrconn), index.SCHEMA_VERSION,
             "multi-root index is at the current schema version")
        mrconn.close()

        # =================================================================
        # v0.6.0 features
        # =================================================================
        from . import changelog_draft, cross_ref, feed, init_wizard, prompt_score
        from . import github_linker as ghl
        from . import patterns as patmod2
        from . import sync as syncmod

        c.eq(index.SCHEMA_VERSION, 5, "schema version is 5 (session tags)")

        # craft a session (real .jsonl → reindex) carrying GitHub refs + a
        # cross-session reference phrase + a scoreable prompt.
        def _wsession(root, proj, sid, ts, msgs):
            d = os.path.join(root, proj)
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, f"{sid}.jsonl"), "w", encoding="utf-8") as fh:
                fh.write(json.dumps({"type": "ai-title", "aiTitle": f"S {sid}",
                                     "sessionId": sid}) + "\n")
                for obj in msgs:
                    obj.setdefault("sessionId", sid)
                    obj.setdefault("cwd", "/proj")
                    obj.setdefault("timestamp", ts)
                    fh.write(json.dumps(obj) + "\n")

        vroot = os.path.join(tmp, "v6root")
        _wsession(vroot, "proj", "old1", "2026-06-01T09:00:00Z", [
            {"type": "user", "uuid": "o1", "parentUuid": None,
             "message": {"role": "user", "content": "set up the auth middleware module"}},
        ])
        _wsession(vroot, "proj", "ghx", "2026-06-10T10:00:00Z", [
            {"type": "user", "uuid": "gu1", "parentUuid": None,
             "message": {"role": "user", "content":
                         "fix the auth bug like we did last time, see #123 and "
                         "acme/repo#7 and https://github.com/acme/repo/pull/9"}},
            {"type": "assistant", "uuid": "ga1", "parentUuid": "gu1",
             "message": {"role": "assistant", "model": "claude-opus-4-8",
                         "content": [
                             {"type": "tool_use", "id": "gt1", "name": "Edit",
                              "input": {"file_path": "/proj/auth.py"}},
                             {"type": "text", "text": "Fixed it."}],
                         "usage": {"input_tokens": 500, "output_tokens": 1200}}},
            {"type": "user", "uuid": "gu2", "parentUuid": "ga1",
             "message": {"role": "user", "content": [
                 {"type": "tool_result", "tool_use_id": "gt1", "is_error": False, "content": "ok"}]}},
        ])
        vdb = os.path.join(tmp, "v6.db")
        vconn = index.connect(vdb)
        index.reindex(vconn, vroot)

        # --- 2.10: github refs stored at index time + searchable -----------
        grefs = index.github_refs_for_session(vconn, "ghx")
        nums = {r["number"] for r in grefs}
        c.ok({123, 7, 9} <= nums, "github refs detected + stored at index time")
        c.ok(any(r["kind"] == "pr" and r["number"] == 9 for r in grefs),
             "the PR url is classified as a pr")
        c.ok(any(r["owner"] == "acme" and r["repo"] == "repo" and r["number"] == 7 for r in grefs),
             "owner/repo#n shorthand captures owner+repo")
        hits = index.search_github_refs(vconn, number=123)
        c.ok(any(h["session_id"] == "ghx" for h in hits), "search_github_refs finds the session by number")
        c.eq(index.search_github_refs(vconn, number=99999), [],
             "search_github_refs returns [] for an unreferenced number")
        gsearch = api.github_refs_search(vconn, {"q": "acme/repo#7"})
        c.ok(gsearch["results"] and gsearch["number"] == 7, "api github_refs_search parses owner/repo#n")
        c.ok("github_refs" in api.get_session(vconn, "ghx"), "get_session attaches github_refs")
        # github refs are derived data — rebuilt (not duplicated) on a forced reindex
        index.reindex(vconn, vroot, force=True)
        c.eq(len(index.github_refs_for_session(vconn, "ghx")), len(grefs),
             "github refs rebuilt without duplication on reindex")

        # pure extractor
        ex = ghl.extract_refs("see #5, foo/bar#6, https://github.com/x/y/issues/7")
        c.eq({r["number"] for r in ex}, {5, 6, 7}, "extract_refs finds all three forms")
        c.eq(ghl.extract_refs("color#fff and id#x"), [], "extract_refs ignores non-issue # noise")

        # --- 2.2: cross-session reference detection ------------------------
        xrefs = cross_ref.find_cross_refs(vconn)
        ghx_ref = next((x for x in xrefs if x["session_id"] == "ghx"), None)
        c.ok(ghx_ref is not None, "cross_ref detects the 'like we did last time' prompt")
        c.eq(ghx_ref["message_index"], 0, "cross_ref reports the prompt's seq")
        c.ok("last time" in ghx_ref["matched_phrase"], "cross_ref surfaces the matched phrase")
        c.ok(any(cand["session_id"] == "old1" for cand in ghx_ref["candidate_sessions"]),
             "cross_ref proposes the earlier same-project session as a candidate")
        c.eq(cross_ref.matched_phrase("just a normal prompt"), None,
             "cross_ref matched_phrase is None for an ordinary prompt")
        c.ok(api.cross_refs(vconn)["cross_refs"], "api.cross_refs returns the references")

        # --- 2.3: prompt effectiveness score -------------------------------
        sc = prompt_score.score_prompt_at(vconn, "ghx", 0)
        c.ok(sc is not None and 0 <= sc["score"] <= 100, "prompt score is within 0..100")
        c.eq(set(sc["components"]), {"tool_success", "continuation", "low_errors"},
             "prompt score exposes its three components")
        c.eq(sc["tool_calls"], 1, "prompt score counts the immediate tool call")
        c.eq(sc["tool_errors"], 0, "prompt score counts zero errors for a clean edit")
        c.ok(prompt_score.score_from_components(
            {"tool_success": 1.0, "continuation": 1.0, "low_errors": 1.0}) == 100,
            "a perfect prompt scores 100")
        c.ok(prompt_score.score_from_components(
            {"tool_success": 0.0, "continuation": 0.0, "low_errors": 0.0}) == 0,
            "a useless prompt scores 0")
        c.ok(prompt_score.score_prompt_at(vconn, "ghx", 1) is None,
             "prompt score is None for a non-user message")
        # a retry-shaped follow-up tanks the low-errors component
        comp_retry = prompt_score.score_components(
            tool_calls=2, tool_errors=0, follow_output_tokens=1000, retry_followup=True)
        comp_clean = prompt_score.score_components(
            tool_calls=2, tool_errors=0, follow_output_tokens=1000, retry_followup=False)
        c.ok(comp_retry["low_errors"] < comp_clean["low_errors"],
             "a retry follow-up lowers the low-errors component")

        # --- 2.6: session pattern mining -----------------------------------
        # craft a debug loop (Bash×4) + a recurring Read→Edit workflow.
        _mk_session(vconn, "loop", title="debug", msg_count=8, last_epoch=1_700_000_000.0)
        for i in range(4):
            _mk_tool(vconn, "loop", "Bash", seq=i)
        for sidw in ("wf1", "wf2", "wf3"):
            _mk_session(vconn, sidw, title="wf", msg_count=4, last_epoch=1_700_000_500.0)
            _mk_tool(vconn, sidw, "Read", seq=0)
            _mk_tool(vconn, sidw, "Edit", seq=1)
        vconn.commit()
        loops = patmod2.debug_loops(vconn)
        c.ok(any(x["session_id"] == "loop" and x["tool"] == "Bash" and x["length"] == 4 for x in loops),
             "debug_loops detects the 4×Bash run")
        wfs = patmod2.recurring_workflows(vconn, min_count=3)
        c.ok(any(w["steps"] == ["Read", "Edit"] and w["count"] >= 3 for w in wfs),
             "recurring_workflows clusters the Read→Edit workflow")
        tod = patmod2.time_of_day(vconn)
        c.ok(tod and all({"hour", "label", "sessions", "emoji"} <= set(t) for t in tod),
             "time_of_day returns ranked hours with emoji")
        mom = patmod2.project_momentum(vconn)
        c.ok(all(m["momentum"] in ("rising", "stalling", "steady") for m in mom),
             "project_momentum labels every project")
        c.ok("workflows" in api.patterns_workflows(vconn), "api.patterns_workflows wraps the list")
        c.ok("debug_loops" in api.patterns_debug_loops(vconn) and
             "time_of_day" in api.patterns_debug_loops(vconn),
             "api.patterns_debug_loops carries loops + hours")
        c.ok("momentum" in api.patterns_momentum(vconn), "api.patterns_momentum wraps the list")

        # --- 2.7: RSS / Atom feed ------------------------------------------
        import xml.etree.ElementTree as _ET
        rss = feed.build_rss(vconn)
        atom = feed.build_atom(vconn)
        root_rss = _ET.fromstring(rss)
        root_atom = _ET.fromstring(atom)
        c.eq(root_rss.tag, "rss", "RSS document root is <rss>")
        n_items = len(root_rss.findall("./channel/item"))
        n_sessions = vconn.execute("SELECT COUNT(*) FROM sessions WHERE last_epoch>0").fetchone()[0]
        c.eq(n_items, min(25, n_sessions), "RSS item count matches the (capped) session count")
        c.ok(root_atom.tag.endswith("feed"), "Atom document root is <feed>")
        c.eq(len(feed.feed_items(vconn, {"limit": 1})), 1, "feed respects the ?limit= filter")
        c.ok(feed.feed_items(vconn, {"project": "proj"}), "feed respects the ?project= filter")
        c.ok(rss.startswith("<?xml"), "RSS carries an XML declaration")

        # --- 2.4: multi-machine sync (mocked runner) -----------------------
        sync_dir = os.path.join(tmp, "syncstate")
        os.makedirs(sync_dir)
        push_plan = syncmod.plan("push", sync_dir, "git@host:repo.git", "git")
        c.ok(["git", "init"] in push_plan and any(p[:2] == ["git", "push"] for p in push_plan),
             "sync push plan initialises + pushes via git")
        pull_plan = syncmod.plan("pull", sync_dir, "host:/path/", "rsync")
        c.eq(pull_plan[0][0], "rsync", "sync rsync pull plan uses rsync")
        dry = syncmod.sync("push", state_dir=sync_dir, remote="r", method="git", dry_run=True)
        c.ok(dry["dry_run"] and dry["commands"], "sync --dry-run returns commands, runs nothing")
        calls = []

        class _R:
            returncode = 0
            stdout = ""
            stderr = ""

        def _fake_run(args, cwd):
            calls.append(args)
            return _R()

        res = syncmod.sync("push", state_dir=sync_dir, remote="r", method="git",
                           run=_fake_run, now=1234.0)
        c.ok(res["ok"] and calls, "sync push runs the planned commands via the injected runner")
        st = syncmod.status(sync_dir)
        c.eq(st["last_push"], 1234.0, "sync status records the push timestamp")
        c.eq(st["method"], "git", "sync status records the method")
        # a 'nothing to commit' is not a hard failure
        def _no_changes(args, cwd):
            calls.append(args)
            r = _R()
            if args[:2] == ["git", "commit"]:
                r.returncode = 1
                r.stdout = "nothing to commit, working tree clean"
            return r
        res2 = syncmod.sync("push", state_dir=sync_dir, remote="r", method="git", run=_no_changes)
        c.ok(res2["ok"] and res2["no_changes"], "sync treats 'nothing to commit' as success")

        # --- 2.11: changelog draft generator -------------------------------
        c.eq(changelog_draft.classify("fix: crash on empty input"), "Fixed", "classify fix → Fixed")
        c.eq(changelog_draft.classify("feat: add the feed"), "Added", "classify feat → Added")
        c.eq(changelog_draft.classify("security: patch path traversal"), "Security",
             "classify security → Security")
        c.eq(changelog_draft.classify("refactor the parser"), "Changed", "classify refactor → Changed")
        draft = changelog_draft.render_draft(
            ["feat: add x", "fix: a crash", "security: harden y", "Merge branch 'z'"],
            version="0.6.0", date="2026-06-26")
        c.ok("## [0.6.0] - 2026-06-26" in draft, "draft has the version header")
        c.ok("### Added" in draft and "### Fixed" in draft and "### Security" in draft,
             "draft groups commits into sections")
        c.ok("Merge branch" not in draft, "draft drops merge commits")
        # git integration via a fully mocked runner
        def _git(args):
            if args[:1] == ["describe"]:
                return 0, "v0.5.2\n"
            if args[:1] == ["log"]:
                return 0, "feat: thing\nfix: bug\n"
            return 0, ""
        gen = changelog_draft.generate(version="0.6.0", run=_git)
        c.ok(gen["available"] and gen["tag"] == "v0.5.2" and gen["count"] == 2,
             "changelog_draft.generate reads the log since the last tag")

        # --- 2.8: init wizard state machine (mock actions + stdin) ---------
        class _FakeActions(init_wizard.WizardActions):
            def __init__(self):
                super().__init__(vdb)
                self.did = []
            def is_indexed(self):
                return True
            def hook_installed(self):
                return False
            def install_hook(self):
                self.did.append("hook")
                return {}
            def write_watch_script(self):
                self.did.append("watch")
                return "/tmp/w.sh"
            def set_budget(self, amount, period="monthly"):
                self.did.append(("budget", amount))
                return {}
            def run_selftest(self):
                self.did.append("selftest")
                return 0
            def open_app(self):
                self.did.append("open")

        fa = _FakeActions()
        out_lines: list = []
        wstate = init_wizard.run_wizard(
            fa, inputs=iter(["y", "y", "25", "y", "n"]), out=out_lines)
        c.eq(wstate["steps"],
             ["doctor", "hook", "watch", "budget", "selftest", "summary", "open"],
             "wizard walks every step in order")
        c.eq(wstate["hook"], "installed", "wizard installs the hook on 'y'")
        c.eq(wstate["budget"], 25.0, "wizard reads the budget value")
        c.eq(wstate["selftest_rc"], 0, "wizard runs the self-test")
        c.ok(("budget", 25.0) in fa.did and "hook" in fa.did, "wizard performs the chosen actions")
        # --yes path takes every default with no input
        fa2 = _FakeActions()
        w2 = init_wizard.run_wizard(fa2, assume_yes=True, out=[])
        c.ok(w2["hook"] == "installed" and w2["opened"] is False,
             "wizard --yes installs the hook and declines the open default")

        # --- mcp tools #15 + #16 dispatch ----------------------------------
        def _vrpc(req):
            return mcp.handle_request(vdb, req)

        vtl = _vrpc({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        vnames = {t["name"] for t in vtl["result"]["tools"]}
        c.ok({"get_cross_refs", "find_sessions_by_github_ref"} <= vnames,
             "mcp exposes get_cross_refs + find_sessions_by_github_ref")

        def _vcall(name, arguments):
            r = _vrpc({"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                       "params": {"name": name, "arguments": arguments}})
            return json.loads(r["result"]["content"][0]["text"]), r["result"]["isError"]

        xr, err = _vcall("get_cross_refs", {"limit": 10})
        c.ok(not err and "cross_refs" in xr, "mcp get_cross_refs returns references")
        gh, err = _vcall("find_sessions_by_github_ref", {"ref": "#123"})
        c.ok(not err and any(r["session_id"] == "ghx" for r in gh["results"]),
             "mcp find_sessions_by_github_ref finds the session")

        # --- new CLI commands smoke-test -----------------------------------
        rc, out = _run(["feed", "--db", vdb])
        c.eq(rc, 0, "cli feed exits 0")
        c.ok("/api/feed.rss" in out, "cli feed prints the RSS URL")
        rc, out = _run(["sync", "--status", "--db", os.path.join(sync_dir, "index.db")])
        c.eq(rc, 0, "cli sync --status exits 0")
        rc, out = _run(["sync", "--push", "--remote", "r", "--dry-run",
                        "--db", os.path.join(sync_dir, "index.db")])
        c.eq(rc, 0, "cli sync --dry-run exits 0")
        c.ok("dry run" in out, "cli sync --dry-run announces a dry run")

        # ===================================================================
        # v0.6.1 — Deep Intelligence & Community: tags, narrative, file
        # heatmap, digest, benchmark, share pack, preferences, plugins.
        # ===================================================================
        from claudestudio import benchmark as _bench
        from claudestudio import digest as _digest
        from claudestudio import file_heatmap as _fh
        from claudestudio import narrative as _narr
        from claudestudio import plugin_loader as _pl
        from claudestudio import share as _share
        from claudestudio.tags import TagManager as _TM
        from claudestudio.tags import normalise_colour as _nc
        from claudestudio.tags import normalise_name as _nn

        f61root = os.path.join(tmp, "f61root")
        os.makedirs(f61root)
        fixtures.build_corpus(f61root, count=12, seed=11)
        f61db = os.path.join(tmp, "f61.db")
        f61 = index.connect(f61db)
        index.reindex(f61, f61root, force=True)
        _r = f61.execute(
            "SELECT session_id, last_epoch FROM sessions "
            "ORDER BY last_epoch DESC LIMIT 1").fetchone()
        f61_sid = _r["session_id"]
        f61_day = parser.local_datetime(_r["last_epoch"]).strftime("%Y-%m-%d")
        empty61 = index.connect(os.path.join(tmp, "empty61.db"))

        # --- schema v5 user-owned tables -----------------------------------
        v5tabs = {r[0] for r in f61.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        c.ok({"available_tags", "session_tags", "preferences"} <= v5tabs,
             "schema v5 creates available_tags, session_tags, preferences")

        # --- tags ----------------------------------------------------------
        c.eq(_nn("Bug Fix!"), "bug-fix", "tag name normalises to lowercase-hyphen")
        c.eq(_nn("  Multiple   Spaces  "), "multiple-spaces", "tag name collapses spaces")
        c.eq(_nn("x" * 50), "x" * 32, "tag name capped at 32 chars")
        c.eq(_nn("!!!"), "", "pure-punctuation name normalises to empty")
        c.eq(_nc("#abcdef"), "#abcdef", "valid hex colour kept")
        c.eq(_nc("nope"), "#9a8cff", "invalid colour falls back to brand purple")
        tg = _TM.create_tag(f61, "Architecture", "#ff8a5b")
        c.eq(tg["name"], "architecture", "create_tag stores normalised name")
        c.eq(tg["colour"], "#ff8a5b", "create_tag stores colour")
        c.eq(tg["session_count"], 0, "new tag has zero sessions")
        c.eq(_TM.create_tag(f61, "architecture", None)["id"], tg["id"],
             "create_tag is idempotent by normalised name")
        try:
            _TM.create_tag(f61, "  !  ")
            c.ok(False, "create_tag rejects an empty name")
        except ValueError:
            c.ok(True, "create_tag rejects an empty name")
        _TM.tag_session(f61, f61_sid, tg["id"])
        _TM.tag_session(f61, f61_sid, tg["id"])  # idempotent re-apply
        st = _TM.get_session_tags(f61, f61_sid)
        c.eq(len(st), 1, "get_session_tags returns the applied tag")
        c.eq(st[0]["name"], "architecture", "applied tag name round-trips")
        c.eq(next(t["session_count"] for t in _TM.list_tags(f61) if t["id"] == tg["id"]), 1,
             "list_tags counts one tagged session")
        c.ok(any(s["session_id"] == f61_sid for s in _TM.get_sessions_by_tag(f61, tg["id"])),
             "get_sessions_by_tag returns the tagged session")
        c.ok(any(t["name"] == "architecture" for t in _TM.search_tags(f61, "arch")),
             "search_tags matches a substring")
        c.eq(len(_TM.search_tags(f61, "")), len(_TM.list_tags(f61)),
             "empty search returns all tags")
        _TM.untag_session(f61, f61_sid, tg["id"])
        c.eq(len(_TM.get_session_tags(f61, f61_sid)), 0, "untag_session removes the tag")
        _TM.delete_tag(f61, tg["id"])
        c.ok(all(t["id"] != tg["id"] for t in _TM.list_tags(f61)),
             "delete_tag removes the tag from the palette")

        # --- preferences ---------------------------------------------------
        c.eq(api.preferences_get(f61)["theme"], "dark", "default theme is dark")
        api.preferences_set(f61, {"theme": "light"})
        c.eq(api.preferences_get(f61)["theme"], "light", "preferences_set persists theme")
        c.eq(index.get_preference(f61, "theme"), "light", "index.get_preference round-trips")
        try:
            api.preferences_set(f61, {"theme": "rainbow"})
            c.ok(False, "invalid theme rejected")
        except api.ApiError as _e:
            c.eq(_e.status, 400, "invalid theme raises ApiError 400")
        c.ok("theme" in index.all_preferences(f61), "all_preferences includes theme")

        # --- narrative (4 quality categories + recovery + next steps) ------
        def _mk(role, text="", thinking="", tools=()):
            m = parser.Message(uuid="u", parent_uuid=None, role=role, ts="", seq=0)
            m.text, m.thinking = text, thinking
            m.tool_calls = [parser.ToolCall(tool_use_id="t", name=n, input=i, ts="",
                                            is_error=e) for (n, i, e) in tools]
            return m

        def _sess(msgs):
            p = parser.ParsedSession(session_id="s", file_path="", file_mtime=0, file_size=0)
            p.first_ts, p.last_ts = "2026-06-01T10:00:00Z", "2026-06-01T10:30:00Z"
            p.messages = msgs
            return p

        _ok_tools = [("Read", {"path": "a.py"}, False)] * 3 + \
                    [("Edit", {"file_path": "auth.py"}, False)] * 3
        succ = _narr.generate_narrative(_sess([
            _mk("user", "Refactor the auth module to use JWT tokens for sessions"),
            _mk("assistant", "done", tools=_ok_tools),
            _mk("assistant", "All set, the tests pass."),
        ]), health_score=85)
        c.eq(succ["quality"], "successful", "narrative: clean high-health run is successful")
        c.ok(succ["headline"].startswith("✅"), "narrative: successful headline emoji")
        c.ok("auth.py" in succ["files_changed"], "narrative: files_changed lists the edited file")
        c.ok(succ["word_count"] > 0, "narrative: word_count populated")
        c.ok(succ["headline"].startswith("✅ Successful:"), "narrative: headline format")
        part = _narr.generate_narrative(_sess([
            _mk("user", "fix the bug"),
            _mk("assistant", "working", tools=_ok_tools),
            _mk("assistant", "partial progress so far"),
        ]), health_score=55)
        c.eq(part["quality"], "partial", "narrative: mid-health run is partial")
        c.ok(part["headline"].startswith("⚠"), "narrative: partial headline emoji")
        aband = _narr.generate_narrative(_sess([
            _mk("user", "do the thing"),
            _mk("assistant", "", tools=[("Bash", {"command": "x"}, False)]),
        ]), health_score=85)
        c.eq(aband["quality"], "abandoned", "narrative: ending mid-tool-call is abandoned")
        c.ok(aband["headline"].startswith("⛔"), "narrative: abandoned headline emoji")
        expl = _narr.generate_narrative(_sess([
            _mk("user", "what do you think about X?"),
            _mk("assistant", "", thinking="long deliberation " * 50,
                tools=[("Read", {"path": "a"}, False)]),
            _mk("assistant", "Here are my thoughts."),
        ]), health_score=85)
        c.eq(expl["quality"], "exploratory", "narrative: thinking-heavy low-tool run is exploratory")
        c.ok(expl["headline"].startswith("🔍"), "narrative: exploratory headline emoji")
        rec = _narr.generate_narrative(_sess([
            _mk("user", "build it"),
            _mk("assistant", "trying", tools=[("Bash", {"command": "x"}, True)]),
            _mk("assistant", "fixed it", tools=[("Bash", {"command": "y"}, False)]),
        ]), health_score=60)
        c.eq(rec["errors_encountered"], 1, "narrative: counts the error")
        c.ok(rec["recovery"] and "Recovered via Bash" in rec["recovery"],
             "narrative: recovery detected after an error")
        nxt = _narr.generate_narrative(_sess([
            _mk("user", "ship it"),
            _mk("assistant", "TODO: wire up the integration tests", tools=_ok_tools),
        ]), health_score=85)
        c.ok(nxt["next_steps"] and "TODO" in nxt["next_steps"],
             "narrative: next_steps picks up a TODO")
        hg = _narr.generate_narrative(_sess([
            _mk("user", "y" * 90), _mk("assistant", "k")]), health_score=85)
        c.ok(hg["headline"].endswith("…"), "narrative: long goal truncated with ellipsis")
        codey = _narr.generate_narrative(_sess([
            _mk("user", "Look at ```python\nsecret_code()\n``` then refactor"),
            _mk("assistant", "k")]), health_score=85)
        c.ok("secret_code" not in codey["goal"], "narrative: goal strips fenced code blocks")
        c.eq(_narr.narrative_for_session(f61, "does-not-exist").get("error"), "not found",
             "narrative_for_session: missing session reports not found")
        c.ok(not _narr.narrative_for_session(f61, f61_sid).get("error"),
             "narrative_for_session: indexed session narrates")

        # --- file heatmap --------------------------------------------------
        hm = _fh.compute_file_heatmap(f61)
        c.ok(hm["total_files"] >= 1, "heatmap finds edited files")
        c.ok(all(0.0 <= f["heat_score"] <= 1.0 for f in hm["files"]),
             "heatmap heat_score normalised to 0..1")
        c.ok(all(hm["files"][i]["heat_score"] >= hm["files"][i + 1]["heat_score"]
                 for i in range(len(hm["files"]) - 1)),
             "heatmap sorted by heat_score descending")
        c.ok(all({"path", "edit_count", "session_count", "heat_score"} <= set(f)
                 for f in hm["files"]), "heatmap file records carry required keys")
        c.eq(_fh.compute_file_heatmap(empty61)["total_files"], 0,
             "heatmap: empty corpus returns no files safely")
        c.eq(_fh.compute_file_heatmap(f61, since="2099-01-01")["total_files"], 0,
             "heatmap: future since-filter excludes everything")
        svg = _fh.heatmap_svg(hm)
        import xml.dom.minidom as _mdom
        _mdom.parseString(svg)
        c.ok(True, "heatmap SVG is valid XML")
        c.ok('role="img"' in svg and "aria-label" in svg, "heatmap SVG has a11y role + label")
        c.ok("data-tip" in svg, "heatmap SVG cells carry data-tip tooltips")
        c.ok(_fh.heatmap_svg(_fh.compute_file_heatmap(empty61)).startswith("<svg"),
             "heatmap SVG of an empty corpus still renders")
        c.ok(len(_fh.top_files(f61, limit=5)["files"]) <= 5, "top_files honours the limit")

        # --- digest --------------------------------------------------------
        dg = _digest.generate_digest(f61, date=f61_day)
        c.ok(dg["session_count"] >= 1, "digest: day with sessions counts them")
        c.ok(dg["markdown"].startswith("## 📅"), "digest markdown has the dated header")
        c.ok("session" in dg["markdown"], "digest markdown names the session count")
        c.ok("$" in dg["markdown"], "digest markdown shows cost")
        c.ok(all(s["health_grade"] in "ABCDEF" for s in dg["session_summaries"]),
             "digest session summaries carry an A–F grade")
        c.ok(isinstance(dg["tools_used"], dict), "digest tools_used is a dict")
        c.ok(f61_day in dg["markdown"], "digest markdown carries the date")
        empty_dg = _digest.generate_digest(f61, date="1999-01-01")
        c.eq(empty_dg["session_count"], 0, "digest: empty day returns zero sessions")
        c.ok("No Claude Code sessions" in empty_dg["markdown"],
             "digest: empty day markdown is friendly")
        c.eq(_digest.generate_digest(f61, date="not-a-date")["date"], _digest._today_str(),
             "digest: an unparseable date falls back to today")
        c.ok("<html" in _digest.digest_html(f61, date=f61_day), "digest_html renders a page")

        # --- benchmark -----------------------------------------------------
        c.close(_bench._pct(110, 100), 10.0, "benchmark delta: +10%")
        c.close(_bench._pct(90, 100), -10.0, "benchmark delta: -10%")
        c.close(_bench._pct(5, 0), 100.0, "benchmark delta: new-from-zero is +100%")
        c.close(_bench._pct(0, 0), 0.0, "benchmark delta: zero over zero is 0%")
        c.eq(_bench._trend(6.0), "improving", "benchmark trend: >+5% improving")
        c.eq(_bench._trend(-6.0), "declining", "benchmark trend: <-5% declining")
        c.eq(_bench._trend(2.0), "stable", "benchmark trend: within ±5% is stable")
        for _mode in ("week", "month", "quarter"):
            b = _bench.compute_benchmark(f61, _mode)
            c.eq(b["mode"], _mode, "benchmark mode " + _mode + " echoed")
            c.ok({"current", "previous", "delta", "trend", "verdict"} <= set(b),
                 "benchmark " + _mode + " returns the full shape")
        be = _bench.compute_benchmark(empty61, "week")
        c.eq(be["trend"], "stable", "benchmark: empty corpus is stable")
        c.ok(isinstance(be["verdict"], str) and be["verdict"], "benchmark: verdict is a string")

        # --- share pack ----------------------------------------------------
        sp = _share.build_share_pack(f61, f61_sid)
        c.ok(sp.startswith("<!doctype html>") and "</html>" in sp, "share pack is an HTML document")
        c.ok('name="claudestudio-share-version"' in sp, "share pack carries the version meta")
        c.ok("not connected to ClaudeStudio" in sp, "share pack shows the offline banner")
        c.ok("cs-share-data" in sp, "share pack inlines the session data block")
        c.ok(len(sp.encode("utf-8")) < 2_000_000, "share pack is under 2 MB")
        c.ok("http://" not in sp and "https://" not in sp, "share pack makes no external calls")
        index.upsert_annotation(f61, f61_sid, -1, "private reviewer note here")
        with_ann = _share.build_share_pack(f61, f61_sid, include_annotations=True)
        without_ann = _share.build_share_pack(f61, f61_sid, include_annotations=False)
        c.ok("private reviewer note here" in with_ann, "share pack embeds annotations when opted in")
        c.ok("private reviewer note here" not in without_ann,
             "share pack omits annotations when opted out")
        c.eq(_share.build_share_pack(f61, "nope"), "", "share pack of a missing session is empty")

        # --- plugin loader -------------------------------------------------
        c.eq(_pl.discover_plugins(os.path.join(tmp, "no-such-dir")), [],
             "discover_plugins: missing dir returns []")
        plug_dir = os.path.join(tmp, "plugins61")
        os.makedirs(plug_dir)
        with open(os.path.join(plug_dir, "good.py"), "w", encoding="utf-8") as fh:
            fh.write("FLAG = []\n"
                     "def register_routes(handler):\n    FLAG.append(handler)\n"
                     "def on_session_indexed(db, sid):\n    pass\n")
        with open(os.path.join(plug_dir, "bad.py"), "w", encoding="utf-8") as fh:
            fh.write("def register_routes(handler)\n    pass\n")  # syntax error
        loaded = _pl.load_plugins(plug_dir)
        c.eq(len(loaded), 2, "load_plugins loads every discovered file")
        good = next(p for p in loaded if p.name == "good")
        bad = next(p for p in loaded if p.name == "bad")
        c.ok(good.ok and "register_routes" in good.hooks,
             "valid plugin loads with its hooks detected")
        c.ok(not bad.ok and bad.error, "plugin with a syntax error is skipped with an error")
        c.eq(len(_pl.get_loaded_plugins()), 2, "get_loaded_plugins returns the singleton set")
        _marker = object()
        _pl.apply_route_hooks(_marker)
        c.ok(good.module.FLAG and good.module.FLAG[-1] is _marker,
             "apply_route_hooks invokes register_routes with the handler")

        # --- MCP tools #17–20 ---------------------------------------------
        def _f61rpc(req):
            return mcp.handle_request(f61db, req)

        f61names = {t["name"] for t in
                    _f61rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]}
        c.ok({"list_tags", "get_session_tags", "get_session_narrative", "get_file_heatmap"}
             <= f61names, "mcp exposes the 4 new v0.6.1 tools")

        def _f61call(name, arguments):
            r = _f61rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                         "params": {"name": name, "arguments": arguments}})
            return json.loads(r["result"]["content"][0]["text"]), r["result"]["isError"]

        _lt, _e = _f61call("list_tags", {})
        c.ok(not _e and "tags" in _lt, "mcp list_tags returns tags")
        _na, _e = _f61call("get_session_narrative", {"session_id": f61_sid})
        c.ok(not _e and "headline" in _na, "mcp get_session_narrative returns a narrative")
        _hf, _e = _f61call("get_file_heatmap", {})
        c.ok(not _e and "files" in _hf, "mcp get_file_heatmap returns hottest files")
        _ts, _e = _f61call("get_session_tags", {"session_id": f61_sid})
        c.ok(not _e and "tags" in _ts, "mcp get_session_tags returns a session's tags")

        # --- new CLI subcommands -------------------------------------------
        rc, out = _run(["tag", "--list", "--db", f61db])
        c.eq(rc, 0, "cli tag --list exits 0")
        rc, out = _run(["tag", "--add", "ship-it", "--db", f61db])
        c.ok(rc == 0 and "ship-it" in out, "cli tag --add creates a tag")
        rc, out = _run(["narrative", f61_sid, "--db", f61db])
        c.eq(rc, 0, "cli narrative exits 0")
        rc, out = _run(["narrative", "--last", "--db", f61db])
        c.ok(rc == 0 and "Quality" in out, "cli narrative --last prints a narrative")
        rc, out = _run(["digest", "--date", f61_day, "--db", f61db])
        c.ok(rc == 0 and "Digest" in out, "cli digest prints the dated digest")
        rc, out = _run(["benchmark", "--mode", "month", "--json", "--db", f61db])
        c.ok(rc == 0 and out.strip().startswith("{"), "cli benchmark --json emits JSON")
        rc, out = _run(["share", "--last", "--db", f61db])
        c.ok(rc == 0 and "not connected" in out, "cli share --last emits the share HTML")

        # --- API route wiring + content types (integration) ----------------
        with open(os.path.join(os.path.dirname(claudestudio.__file__), "server.py"),
                  encoding="utf-8") as fh:
            srv = fh.read()
        c.ok('"/api/tags"' in srv, "server wires /api/tags")
        c.ok('"/api/files/heatmap.svg"' in srv and "image/svg+xml" in srv,
             "server wires heatmap.svg with image/svg+xml")
        c.ok('"/api/files/heatmap"' in srv, "server wires /api/files/heatmap")
        c.ok('"/api/digest.md"' in srv and "text/markdown" in srv,
             "server wires digest.md with text/markdown")
        c.ok('"/api/digest"' in srv, "server wires /api/digest")
        c.ok('"/api/benchmark"' in srv, "server wires /api/benchmark")
        c.ok('"/api/preferences"' in srv and "_send_empty(204)" in srv,
             "server wires /api/preferences with a 204 write")
        c.ok('"/narrative"' in srv and '"/share.html"' in srv,
             "server wires the session narrative + share routes")
        c.ok("except api.ApiError" in srv, "server maps ApiError to its status code")
        for _fn in ("tags_list", "tags_create", "tags_delete", "file_heatmap_payload",
                    "digest_payload", "benchmark_payload", "preferences_get",
                    "preferences_set", "session_narrative", "session_share"):
            c.ok(hasattr(api, _fn), "api exposes " + _fn)
        c.ok(hasattr(api, "ApiError"), "api exposes ApiError")
        c.ok(os.path.isfile(os.path.join(os.path.dirname(claudestudio.__file__),
             "web", "themes.js")), "web/themes.js ships")

        empty61.close()
        f61.close()

        vconn.close()

    # --- web assets: guard the SPA wiring the UI behaviors depend on ------
    # The behaviors themselves are JS/CSS (exercised in a browser), but these
    # checks fail CI if a refactor strips the wiring, on every OS, zero deps.
    web_dir = os.path.join(os.path.dirname(claudestudio.__file__), "web")
    c.ok(os.path.isfile(os.path.join(web_dir, "index.html")), "web/index.html shipped")
    with open(os.path.join(web_dir, "app.js"), encoding="utf-8") as fh:
        app_js = fh.read()
    with open(os.path.join(web_dir, "styles.css"), encoding="utf-8") as fh:
        css = fh.read()

    # 1. virtual scrolling — nodes stay in the DOM, browser skips offscreen layout
    c.ok("content-visibility: auto" in css, "css: turns virtualized via content-visibility")
    c.ok("contain-intrinsic-size" in css, "css: intrinsic size reserved for offscreen turns")
    # 2. keyboard navigation in replay
    c.ok("setViewKey(" in app_js, "app.js: per-view key handler helper exists")
    c.ok("setViewKey(null)" in app_js, "app.js: router tears down previous view's key handler")
    c.ok("'PageDown'" in app_js and "'PageUp'" in app_js and "'Home'" in app_js and "'End'" in app_js,
         "app.js: replay nav handles Home/End/PageUp/PageDown")
    # 3. message grouping
    c.ok("group-cont" in app_js and ".turn.group-cont" in css,
         "grouping: consecutive same-role turns flagged and styled")
    c.ok("m.role === prevRole" in app_js, "grouping: continuation computed from previous role")
    # 4. search-term highlight in replay (escape-then-mark => no HTML injection)
    c.ok("function markTerms(" in app_js, "app.js: markTerms highlights query terms in replay")
    c.ok("esc(txt).replace(re" in app_js, "markTerms escapes before inserting <mark> (XSS-safe)")
    c.ok("mark.hit" in css, "css: matched-term highlight styled")
    # 5. search filter UI wired to the backend filters already proven above
    c.ok("search-filters" in app_js and "search-filters" in css, "search filter toolbar present + styled")
    c.ok("history.replaceState" in app_js, "search updates URL without re-render (keeps input focus)")
    c.ok("search: (q, limit = 30, filters" in app_js, "API.search forwards structured filters")
    for token in ("kind", "project", "since", "until"):
        c.ok(token in app_js, f"search filter UI exposes the {token} filter")
    # polish: focus visibility, error emphasis, disabled-empty submit
    c.ok(":focus-visible" in css, "css: focus-visible ring for keyboard users")
    c.ok(".tool-card.error" in css, "css: tool errors visually emphasized")
    c.ok("sendBtn.disabled" in app_js, "ask: submit disabled when query empty")

    # --- v0.5.1 web wiring -------------------------------------------------
    with open(os.path.join(web_dir, "index.html"), encoding="utf-8") as fh:
        index_html = fh.read()
    # F1: live SSE updates
    c.ok("EventSource('/api/events')" in app_js, "app.js: opens the SSE events stream")
    c.ok("startLiveUpdates" in app_js and "showReindexToast" in app_js,
         "app.js: live reindex toast wired")
    # F2: bookmarks
    c.ok("bookmarkButton(" in app_js and "openBookmarkPopover(" in app_js,
         "app.js: per-message bookmark UI present")
    c.ok("/api/session/' + encodeURIComponent(id) + '/bookmark" in app_js,
         "app.js: bookmark POST endpoint wired")
    c.ok("async function viewBookmarks(" in app_js, "app.js: global bookmarks view exists")
    c.ok(".bm-btn" in css and ".bm-pop" in css, "css: bookmark button + popover styled")
    # F3: inline diff
    c.ok("function diffNode(" in app_js, "app.js: unified-diff renderer exists")
    c.ok("t.diff" in app_js and "diff-toggle" in app_js, "app.js: Diff/Raw toggle wired")
    c.ok(".diff-view" in css and ".dl.add" in css and ".dl.del" in css,
         "css: diff lines styled (add/del)")
    # F4: report
    c.ok("function reportPanel(" in app_js and "API.reportUrl(" in app_js,
         "app.js: report generator wired")
    c.ok(".report-bar" in css, "css: report toolbar styled")
    # F6: latency
    c.ok("function latencyChart(" in app_js and "/api/tools/latency" in app_js,
         "app.js: latency chart wired to the endpoint")
    c.ok(".lat-fill.good" in css and ".lat-fill.bad" in css, "css: latency bars color-banded")
    # F8: patterns
    c.ok("function patternsList(" in app_js and "/api/prompts/patterns" in app_js,
         "app.js: patterns list wired to the endpoint")
    # F9: CSV download
    c.ok("analyticsCsvUrl" in app_js and "sessionsCsvUrl" in app_js,
         "app.js: CSV export buttons wired")
    # F12: accessibility
    c.ok("prefers-reduced-motion" in css, "css: honours prefers-reduced-motion")
    c.ok('role="navigation"' in index_html and 'role="main"' in index_html
         and 'role="complementary"' in index_html, "index.html: landmark roles present")
    c.ok("k === ' '" in app_js and "replay.toggle()" in app_js,
         "app.js: Space toggles replay play/pause")
    c.ok("'ArrowLeft'" in app_js and "'ArrowRight'" in app_js,
         "app.js: arrow keys step the replay")

    # --- v0.5.2 web wiring -------------------------------------------------
    with open(os.path.join(web_dir, "keyboard.js"), encoding="utf-8") as fh:
        kbd_js = fh.read()
    # F10 health: A–F dot in the list, breakdown card in detail
    c.ok("function healthDot(" in app_js, "app.js: health dot renderer exists")
    c.ok("function detailContext(" in app_js, "app.js: detail context (git/health/note) exists")
    c.ok(".health-dot" in css and ".health-card" in css, "css: health dot + card styled")
    # F7 git badge
    c.ok(".git-badge" in css, "css: git context badge styled")
    c.ok("s.git" in app_js, "app.js: detail reads the session git context")
    # F5 annotations
    c.ok("saveAnnotation" in app_js and "function annotationEditor(" in app_js,
         "app.js: annotation editor wired to the save endpoint")
    c.ok(".annotate" in css, "css: annotation editor styled")
    # F3 budget
    c.ok("function checkBudget(" in app_js and "/api/budget" in app_js,
         "app.js: budget banner wired to the endpoint")
    c.ok("function radialArc(" in app_js, "app.js: pure-SVG budget arc exists")
    c.ok(".budget-banner" in css and ".budget-arc" in css, "css: budget banner + arc styled")
    # F6 efficiency
    c.ok("function viewEfficiency(" in app_js and "/api/analytics/efficiency" in app_js,
         "app.js: efficiency view wired to the endpoint")
    c.ok(".eff-kpi" in css and ".eff-fill" in css, "css: efficiency KPIs + bars styled")
    # F8 prompt library
    c.ok("function viewPrompts(" in app_js and "function promptCard(" in app_js,
         "app.js: prompt library view + cards exist")
    c.ok("/api/prompts/extract" in app_js, "app.js: prompt extraction wired")
    c.ok(".prompt-card" in css, "css: prompt cards styled")
    # F4 CLAUDE.md modal
    c.ok("function openClaudeMdModal(" in app_js and "claudeMd" in app_js,
         "app.js: CLAUDE.md modal wired")
    c.ok(".cs-modal" in css, "css: CLAUDE.md modal styled")
    # F12 keyboard navigation system
    c.ok("keyboard.js" in index_html, "index.html: loads the keyboard navigation script")
    c.ok("class KeyboardNavigator" in kbd_js or "function KeyboardNavigator(" in kbd_js,
         "keyboard.js: KeyboardNavigator class exists")
    c.ok("cs:navigate" in kbd_js and "cs:action" in kbd_js,
         "keyboard.js: emits cs:navigate / cs:action intents")
    c.ok("kbd-cheat" in kbd_js and ".kbd-cheat" in css,
         "keyboard.js: '?' cheat-sheet overlay present + styled")
    # nav exposes the two new top-level views
    c.ok("'efficiency'" in app_js and "'prompts'" in app_js,
         "app.js: router registers the efficiency + prompts routes")

    # --- v0.6.0 web wiring -------------------------------------------------
    with open(os.path.join(web_dir, "sw.js"), encoding="utf-8") as fh:
        sw_js = fh.read()
    manifest_path = os.path.join(web_dir, "manifest.json")
    c.ok(os.path.isfile(manifest_path), "web/manifest.json shipped")
    with open(manifest_path, encoding="utf-8") as fh:
        manifest = json.load(fh)
    # 2.5 PWA: valid manifest with the required fields + icons + SW + registration
    for _field in ("name", "short_name", "start_url", "display", "icons", "theme_color"):
        c.ok(_field in manifest, f"manifest.json has required field {_field!r}")
    c.ok(manifest["theme_color"] == "#9a8cff", "manifest uses the brand colour")
    c.ok(len(manifest["icons"]) >= 2 and any("512" in i["sizes"] for i in manifest["icons"]),
         "manifest declares 192 + 512 icons")
    c.ok(os.path.isfile(os.path.join(web_dir, "assets", "icon-192.svg"))
         and os.path.isfile(os.path.join(web_dir, "assets", "icon-512.svg")),
         "pure-SVG PWA icons shipped")
    c.ok('rel="manifest"' in index_html, "index.html links the manifest")
    c.ok("registerServiceWorker" in app_js and "serviceWorker.register('sw.js')" in app_js,
         "app.js: registers the service worker")
    c.ok("/api/" in sw_js and "network-first" in sw_js.lower(),
         "sw.js: API responses are network-first (never cached)")
    c.ok("caches.open" in sw_js and "SHELL" in sw_js, "sw.js: caches the static shell")
    # 2.1 replay: pill speed control (0.5×..∞), typewriter, jump-to-error, summary
    c.ok("REPLAY_SPEEDS" in app_js and "Infinity" in app_js, "app.js: replay has a 0.5×..∞ speed set")
    c.ok("speed-pill" in app_js and ".speed-pill" in css, "replay: pill-segmented speed control styled")
    c.ok("replay-typewriter" in app_js and "@keyframes cs-typewriter" in css,
         "replay: CSS typewriter reveal wired")
    c.ok("function firstErrorIndex(" in app_js and "jumpToError" in app_js,
         "replay: jump-to-first-error wired")
    c.ok("function replaySummaryCard(" in app_js and ".replay-summary" in css,
         "replay: end-of-playback summary card present + styled")
    c.ok("replay.speedUp()" in app_js and "replay.speedDown()" in app_js,
         "replay: '<' / '>' step the speed")
    # 2.6 patterns view + auto-generated SVG flowchart
    c.ok("async function viewPatterns(" in app_js and "'patterns'" in app_js,
         "app.js: Patterns view + route registered")
    c.ok("function workflowFlowchart(" in app_js and ".wf-svg" in css,
         "patterns: pure-SVG workflow flowchart wired")
    c.ok("/api/patterns/workflows" in app_js and "/api/patterns/momentum" in app_js,
         "patterns: workflow + momentum endpoints wired")
    c.ok(".mom-badge.rising" in css, "patterns: momentum badges styled")
    # 2.10 github refs card + 2.2 cross-reference card
    c.ok("function githubRefsCard(" in app_js and ".gh-refs-card" in css,
         "app.js: GitHub references card present + styled")
    c.ok("function crossRefCard(" in app_js and ".xref-card" in css,
         "app.js: cross-reference card present + styled")
    # 2.12 dev self-test dashboard
    c.ok("async function viewDev(" in app_js and "/api/dev/selftest" in app_js,
         "app.js: developer self-test dashboard wired to the SSE endpoint")
    c.ok("e.key === 'D'" in app_js or "e.key === 'd'" in app_js,
         "app.js: Shift+D opens the developer view")
    c.ok(".dev-selftest-out" in css and ".dev-line.bad" in css, "dev dashboard styled")
    # 2.9 a11y: document title per route + role=status toast host
    c.ok("function docTitleFor(" in app_js and "document.title =" in app_js,
         "a11y: document title updates per route")
    c.ok('role="status"' in index_html, "a11y: live-update toast host announces via role=status")
    c.ok('name="theme-color"' in index_html, "index.html: theme-color meta for installed PWA")

    # every <button> in the shipped shell has an accessible name (text/aria/title)
    import html.parser as _hp

    class _BtnAudit(_hp.HTMLParser):
        def __init__(self):
            super().__init__()
            self.depth = 0
            self.named = True
            self._txt = []
            self._attrs = {}

        def handle_starttag(self, tag, attrs):
            if tag == "button":
                self.depth += 1
                self._attrs = dict(attrs)
                self._txt = []

        def handle_data(self, data):
            if self.depth:
                self._txt.append(data)

        def handle_endtag(self, tag):
            if tag == "button" and self.depth:
                self.depth -= 1
                has_name = ("".join(self._txt).strip()
                            or self._attrs.get("aria-label")
                            or self._attrs.get("title"))
                if not has_name:
                    self.named = False

    audit = _BtnAudit()
    audit.feed(index_html)
    c.ok(audit.named, "a11y: every button in index.html has an accessible name")

    total = c.passed + c.failed
    print(f"\n  selftest: {c.passed}/{total} checks passed")
    if c.failed:
        print(f"  {c.failed} FAILED")
        return 1
    print("  ALLPASS")
    return 0
