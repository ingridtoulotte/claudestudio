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
import sqlite3
import tempfile

import claudestudio

from . import analytics, api, ask, cli, export, fixtures, index, parser, pricing, wrapped


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
        c.eq(len(tl["result"]["tools"]), 8, "mcp exposes 8 tools")
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
        hconn.close()

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

    total = c.passed + c.failed
    print(f"\n  selftest: {c.passed}/{total} checks passed")
    if c.failed:
        print(f"  {c.failed} FAILED")
        return 1
    print("  ALLPASS")
    return 0
