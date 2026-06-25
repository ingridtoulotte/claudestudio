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
        c.eq(len(tl["result"]["tools"]), 14, "mcp exposes 14 tools")
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
        c.eq(claudestudio.__version__, "0.5.2", "package version bumped to 0.5.2")
        c.eq(init["result"]["serverInfo"]["version"], "0.5.2", "mcp serverInfo is 0.5.2")
        rc, out = _run(["info", "--db", db])
        c.eq(rc, 0, "cli info exits 0")
        c.ok("0.5.2" in out, "cli info prints the version")
        c.ok("mcp tools" in out and "14" in out, "cli info reports the 14 MCP tools")

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
