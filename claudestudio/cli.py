"""Command-line interface for ClaudeStudio."""

from __future__ import annotations

import argparse
import contextlib
import datetime as _dt
import json
import os
import pathlib
import sys
import textwrap
import time

from . import __version__, api, fixtures, index, selftest, server, wrapped
from . import parser as _parser

_LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def _warn_if_public_host(host: str) -> None:
    """Warn loudly when the operator binds to anything but loopback.

    ClaudeStudio is single-user and local-first; a non-loopback bind exposes the
    full session history (prompts, code, tool output) to everyone on the network.
    """
    if (host or "").strip().lower() not in _LOOPBACK:
        print(
            f"  ⚠  WARNING: binding to {host!r} exposes your session history to "
            f"your whole network.\n"
            f"     Use the default 127.0.0.1 unless you specifically need remote "
            f"access — and trust the network.",
            file=sys.stderr,
        )


def _safe_out_path(out: str, default_name: str) -> pathlib.Path:
    """Resolve a user-supplied --out into a concrete, contained file path.

    `default_name` is a server-side slug with no path separators. If `out` names
    a directory the export lands inside it under that slug; otherwise `out` is
    taken as the target file. The result is fully resolved so `..` segments are
    collapsed rather than silently followed.
    """
    if os.sep in default_name or (os.altsep and os.altsep in default_name):
        raise ValueError(f"unsafe export filename: {default_name!r}")
    base = pathlib.Path(out).expanduser() if out else pathlib.Path(default_name)
    if out and (base.is_dir() or out.endswith(("/", os.sep))):
        base = base / default_name
    # os.path.abspath first: it forces an absolute, `..`-collapsed path on every
    # platform, including Python 3.9 on Windows where Path.resolve() leaves a
    # non-existent relative path relative (fixed in 3.10). resolve() then folds
    # any symlinks in the (existing) parents.
    return pathlib.Path(os.path.abspath(base)).resolve()

BANNER = rf"""
   ___ _                _      ___ _            _ _
  / __| |__ _ _  _ __| |___ / __| |_ _  _ __| (_)___
 | (__| / _` | || / _` / -_)\__ \  _| || / _` | / _ \
  \___|_\__,_|\_,_\__,_\___||___/\__|\_,_\__,_|_\___/
  the desktop workspace for Claude Code  ·  v{__version__}
"""


def _add_common(p):
    p.add_argument("--db", default=index.default_db_path(), help="index database path")
    p.add_argument(
        "--root", default=None,
        help="Claude projects root(s) to scan. Pass several with your platform's "
             f"path separator ({os.pathsep!r}), e.g. --root pathA{os.pathsep}pathB",
    )


def _split_roots(raw):
    """Parse a --root value into a list of roots (or None for the default).

    Multiple roots are separated by ``os.pathsep`` — ``;`` on Windows, ``:`` on
    POSIX — which never collides with a Windows drive letter (``C:\\…``) the way a
    hard-coded ``:`` split would. Returns None when no root was given so the
    indexer falls back to the default projects root.
    """
    if not raw:
        return None
    parts = [p for p in str(raw).split(os.pathsep) if p]
    return parts or None


def _progress(done, total):
    if total:
        bar = int(30 * done / total)
        sys.stdout.write(f"\r  indexing  [{'█'*bar}{'·'*(30-bar)}] {done}/{total}")
        sys.stdout.flush()


def cmd_index(args):
    conn = index.connect(args.db)
    print(BANNER)
    t0 = time.time()
    stats = index.reindex(conn, _split_roots(args.root), force=args.force, progress=_progress)
    print()
    dt = time.time() - t0
    print(f"  scanned {stats['files']} files in {dt:.1f}s")
    print(f"  added {stats['added']}  updated {stats['updated']}  "
          f"skipped {stats['skipped']}  removed {stats['removed']}")
    s = index.session_summary(conn)
    print(f"  → {s['sessions']:,} sessions · {s['messages']:,} messages · "
          f"{s['tool_calls']:,} tool calls · ${s['cost_usd']:,.2f}")
    conn.close()
    if stats["files"] == 0:
        root = args.root or _parser.default_projects_root()
        print(f"\n  No .jsonl sessions found under {root}")
        print("  Tip: run `claudestudio demo` to explore with synthetic data.")
    return 0


def cmd_serve(args):
    roots = _split_roots(args.root)
    if not os.path.exists(args.db):
        print("  No index yet — building one first…")
        conn = index.connect(args.db)
        index.reindex(conn, roots, progress=_progress)
        print()
        conn.close()
    print(BANNER)
    _warn_if_public_host(args.host)
    server.serve(args.db, roots, host=args.host, port=args.port,
                 open_browser=not args.no_browser)
    return 0


def cmd_wrapped(args):
    conn = index.connect(args.db)
    data = wrapped.generate(conn, args.year)
    wrapped.print_text(data)
    conn.close()
    return 0


def _export_all(conn, args) -> int:
    """Export every indexed session into a directory, with progress + skip logic."""
    fmt = args.format
    out_dir = pathlib.Path(args.out_dir or "claudestudio-export").expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    ids: list[str] = []
    offset = 0
    while True:
        page = api.list_sessions(conn, {"archived": "all", "limit": 500, "offset": offset})
        ids.extend(s["session_id"] for s in page["sessions"])
        offset += 500
        if offset >= page.get("total", 0) or not page["sessions"]:
            break
    total = len(ids)
    written = skipped = 0
    for i, sid in enumerate(ids, 1):
        out = api.export_session(conn, sid, fmt)
        if out is None:
            continue
        dest = out_dir / f"{sid[:8]}-{out['filename']}"
        if dest.exists() and not args.force:
            skipped += 1
        else:
            with open(dest, "w", encoding="utf-8") as fh:
                fh.write(out["text"])
            written += 1
        sys.stdout.write(f"\r  exporting  {i}/{total}  ({written} written, {skipped} skipped)")
        sys.stdout.flush()
    conn.close()
    print(f"\n  → {written} session(s) exported to {out_dir}"
          + (f" ({skipped} already present)" if skipped else ""))
    return 0


def cmd_export(args):
    conn = index.connect(args.db)
    want_zip = bool(getattr(args, "zip", False)) or bool(
        args.out and str(args.out).lower().endswith(".zip")
    )
    if want_zip:
        if getattr(args, "all", False):
            ids = _collect_session_ids(conn, getattr(args, "project", None))
        elif args.session_id:
            ids = [args.session_id]
        else:
            conn.close()
            print("  For a .zip, pass --all (optionally --project) or a session id.")
            return 1
        fmt = "html" if args.format == "html" else ("json" if args.format == "json" else "md")
        out = api.export_batch(conn, ids, fmt, include_index=True)
        conn.close()
        dest = _safe_out_path(args.out or "claudestudio-export.zip", "claudestudio-export.zip")
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "wb") as fh:
            fh.write(out["bytes"])
        print(f"  archive → {dest}  ({out['count']} session(s), {len(out['bytes']):,} bytes)")
        return 0
    if getattr(args, "all", False):
        return _export_all(conn, args)
    if not args.session_id:
        conn.close()
        print("  Provide a session id, or use --all to export every session.")
        return 1
    out = api.export_session(conn, args.session_id, args.format)
    conn.close()
    if out is None:
        print(f"  No session with id {args.session_id!r} in the index.")
        print("  Tip: run `claudestudio index` first, or copy an id from `serve`.")
        return 1
    if args.out == "-":
        sys.stdout.write(out["text"])
        return 0
    dest = _safe_out_path(args.out, out["filename"])
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "w", encoding="utf-8") as fh:
        fh.write(out["text"])
    print(f"  exported → {dest}  ({len(out['text']):,} bytes)")
    return 0


def _collect_session_ids(conn, project=None, archived="all") -> list[str]:
    """Every indexed session id (optionally one project), paged out of the index."""
    ids: list[str] = []
    offset = 0
    while True:
        params = {"archived": archived, "limit": 500, "offset": offset}
        if project:
            params["project"] = project
        page = api.list_sessions(conn, params)
        ids.extend(s["session_id"] for s in page["sessions"])
        offset += 500
        if offset >= page.get("total", 0) or not page["sessions"]:
            break
    return ids


def cmd_budget(args):
    """Show spend vs budget, or set/clear a spend ceiling."""
    from . import budget as budgetmod
    conn = index.connect(args.db)
    if args.clear:
        budgetmod.clear_budget(conn)
        print("  budget cleared.")
    elif args.set is not None:
        rec = budgetmod.set_budget(conn, args.period, args.set)
        print(f"  ✓ budget set: ${rec['ceiling_usd']:,.2f} / {rec['period']}")
    st = budgetmod.budget_status(conn)
    conn.close()
    print(BANNER)
    if not st["has_budget"]:
        print(f"  No budget set. Spend this {st['period'][:-2]}: "
              f"${st['spent_usd']:,.2f} across {st['sessions_this_period']} session(s).")
        print("  Set one:  claudestudio budget --set 50 --period monthly")
        return 0
    pct = st["percent"]
    width = 28
    filled = min(width, int(round(width * pct / 100.0)))
    bar = "█" * filled + "·" * (width - filled)
    flag = " ⚠ OVER BUDGET" if pct >= 100 else (" ⚠" if st["alert"] else "")
    print(f"  {st['period'].title()} budget  ${st['ceiling_usd']:,.2f}{flag}")
    print(f"  [{bar}] {pct:.0f}%")
    print(f"  spent   ${st['spent_usd']:,.2f}   remaining ${st['remaining_usd']:,.2f}")
    print(f"  {st['sessions_this_period']} session(s) · {st['days_remaining']} day(s) left in period")
    return 0


def cmd_generate_claude_md(args):
    """Generate a CLAUDE.md from a project's indexed history."""
    from . import generate_claude_md
    conn = index.connect(args.db)
    project = args.project
    if not project:
        row = conn.execute(
            "SELECT project_name FROM sessions WHERE project_name<>'' "
            "GROUP BY project_name ORDER BY COUNT(*) DESC LIMIT 1"
        ).fetchone()
        project = row[0] if row else None
    if not project:
        conn.close()
        print("  No indexed projects. Run `claudestudio index` (or `demo`) first.")
        return 1
    profile = generate_claude_md.analyse_project(conn, project)
    markdown = generate_claude_md.render_claude_md(profile)
    conn.close()
    if args.dry_run or args.out == "-" or not args.out:
        sys.stdout.write(markdown)
        if not markdown.endswith("\n"):
            sys.stdout.write("\n")
        if args.dry_run:
            print(f"\n  (dry run — {profile['sessions']} session(s) analysed for "
                  f"{profile['project_name']!r}; pass --out CLAUDE.md to write)")
        return 0
    dest = _safe_out_path(args.out, "CLAUDE.md")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "w", encoding="utf-8") as fh:
        fh.write(markdown)
    print(f"  CLAUDE.md → {dest}  ({len(markdown):,} bytes, "
          f"{profile['sessions']} session(s) analysed)")
    return 0


def cmd_watch(args):
    """Poll the projects root(s) and reindex whenever a session file changes.

    A foreground companion to `serve`: pair them (in two terminals) for live,
    hands-free updates. Exits cleanly on Ctrl-C. Pure polling (no inotify) so it
    behaves identically on every OS.
    """
    roots = _split_roots(args.root)
    interval = max(1.0, float(args.interval))
    if not os.path.exists(args.db):
        print("  No index yet — building one first…")
        conn = index.connect(args.db)
        index.reindex(conn, roots, progress=_progress)
        print()
        conn.close()
    print(BANNER)
    shown = [index.normalize_roots(roots)]
    print(f"  watching {', '.join(shown[0])}")
    print(f"  polling every {interval:.0f}s · Ctrl-C to stop\n")
    last_seen = index.newest_source_mtime(roots)
    try:
        while True:
            time.sleep(interval)
            newest = index.newest_source_mtime(roots)
            if newest <= last_seen:
                continue
            last_seen = newest
            conn = index.connect(args.db)
            stats = index.reindex(conn, roots)
            conn.close()
            stamp = _dt.datetime.now().strftime("%H:%M:%S")
            print(f"  [{stamp}] reindexed — +{stats['added']} new, "
                  f"{stats['updated']} updated, {stats['skipped']} unchanged")
    except KeyboardInterrupt:
        print("\n  Stopped watching.")
    return 0


def cmd_report(args):
    """Generate a shareable HTML/Markdown activity report for a date range."""
    from . import report
    conn = index.connect(args.db)
    params = {}
    if args.since:
        params["since"] = args.since
    if args.until:
        params["until"] = args.until
    since, until, title = api.report_range(params)
    fmt = "md" if args.format in ("md", "markdown") else "html"
    text = report.generate_report(conn, since, until, title, fmt)
    conn.close()
    if args.out == "-":
        sys.stdout.write(text)
        return 0
    if args.out:
        dest = _safe_out_path(args.out, f"claudestudio_report.{fmt}")
    else:
        date = _dt.date.today().isoformat()
        dest = pathlib.Path(os.path.expanduser("~")) / f"claudestudio_report_{date}.{fmt}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "w", encoding="utf-8") as fh:
        fh.write(text)
    print(f"  report → {dest}  ({len(text):,} bytes)")
    print(f"  range  : {_fmt_day(since)} → {_fmt_day(until)}")
    return 0


def cmd_mcp(args):
    """Launch the MCP server (JSON-RPC 2.0 over stdio).

    No banner / no stdout chatter — stdout is the JSON-RPC channel and any stray
    print would corrupt the protocol stream.
    """
    from . import mcp
    if not os.path.exists(args.db):
        print("  No index yet — building one first…", file=sys.stderr)
        conn = index.connect(args.db)
        index.reindex(conn, _split_roots(args.root))
        conn.close()
    return mcp.serve_stdio(args.db)


def cmd_highlights(args):
    conn = index.connect(args.db)
    from . import highlights
    data = highlights.generate(conn)
    conn.close()
    if args.json:
        print(json.dumps(data, default=str, indent=2))
        return 0
    print(BANNER)
    labels = {
        "breakthroughs": "✦ Breakthrough moments",
        "cost_spikes": "⚡ Cost spikes",
        "marathons": "🏃 Marathon sessions",
        "revisited_files": "📌 Most revisited files",
        "recurring_prompts": "🔁 Recurring prompts",
        "abandoned": "🌱 Abandoned sessions",
        "model_migrations": "🔀 Model migrations",
    }
    any_shown = False
    for key, label in labels.items():
        items = data.get(key) or []
        if not items:
            continue
        any_shown = True
        print(f"\n  {label}")
        for it in items[:8]:
            who = it.get("session_id") or it.get("file") or it.get("date") or ""
            reason = it.get("reason", "")
            title = (it.get("title") or "")[:40]
            print(f"    {str(who)[:36]:<36}  {title:<40}  {reason}")
    if not any_shown:
        print("\n  No highlights yet — index more sessions, or run `claudestudio demo`.")
    return 0


def _doctor_recommendation(*, hooks, hook_installed, error_rate, stale_pricing,
                           n_sessions) -> str:
    """One sentence: the single most impactful thing the user could do next.

    Pure function of the gathered signals (deterministic) so the self-test can
    pin its behaviour on known inputs."""
    if n_sessions == 0:
        return "Run `claudestudio index` (or `claudestudio demo`) to populate your index."
    if error_rate.get("change_pct", 0) >= 30 and error_rate.get("errors", 0) > 0:
        return (f"Your error rate rose {error_rate['change_pct']:.0f}% this week — "
                f"open the Errors dashboard to see what's failing.")
    if not hook_installed:
        return ("Install the post-session hook (`claudestudio hook install`) so your "
                "index stays fresh automatically.")
    if stale_pricing:
        return "Upgrade ClaudeStudio to refresh the bundled pricing table for accurate costs."
    if not hooks:
        return ("Wire a local webhook (`claudestudio webhook --add …`) to get alerts in "
                "your own tools.")
    return "Everything looks healthy — try `claudestudio resume --last` to jump back in."


def cmd_doctor(args):
    """Diagnose environment & index health. Exit 0 healthy, 1 warnings, 2 critical."""
    print(BANNER)
    warnings: list[str] = []   # (message, fix-hint) printed inline; counted for exit code
    criticals: list[str] = []

    roots = _split_roots(args.root) or [_parser.default_projects_root()]
    total_files = 0
    for r in roots:
        exists = os.path.isdir(r)
        n = len(list(_parser.iter_session_files(r))) if exists else 0
        total_files += n
        print(f"  projects root : {r}  ({'exists' if exists else 'missing'}, {n} files)")
    print(f"  session files : {total_files}")
    print(f"  index db      : {args.db}")
    print(f"  index exists  : {os.path.exists(args.db)}")
    try:
        import sqlite3
        c = sqlite3.connect(":memory:")
        c.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        print("  sqlite FTS5   : available ✓")
    except sqlite3.OperationalError:
        print("  sqlite FTS5   : MISSING ✗ (search will be degraded)")
        criticals.append("sqlite FTS5 missing — install a Python with FTS5 support")
    print(f"  python        : {sys.version.split()[0]}")
    from . import pricing
    age = pricing.price_table_age_days()
    if pricing.is_price_table_stale():
        print(f"  pricing data  : STALE ⚠ (updated {pricing.PRICE_TABLE_DATE}, {age}d ago)")
        warnings.append("pricing table is stale — upgrade ClaudeStudio for current prices")
    else:
        print(f"  pricing data  : ok ✓ (updated {pricing.PRICE_TABLE_DATE}, {age}d ago)")
    from . import hook, mcp
    print(f"  mcp server    : {len(mcp.TOOLS)} tools (run `claudestudio mcp`)")
    hst = hook.hook_status(db_path=args.db)
    if hst["installed"]:
        print(f"  hook          : installed ✓ ({hook.HOOK_EVENT} → {hook.HOOK_COMMAND})")
    else:
        print("  hook          : not installed ⚠")
        warnings.append("hook not installed — run `claudestudio hook install` to auto-reindex")

    # plugin status (v0.6.1)
    from . import plugin_loader
    plugins = plugin_loader.load_plugins()
    ok_p = [p for p in plugins if p.ok]
    bad_p = [p for p in plugins if not p.ok]
    if not plugins:
        print("  plugins       : none (drop .py files in ~/.claudestudio/plugins/)")
    else:
        print(f"  plugins       : {len(ok_p)} loaded, {len(bad_p)} failed")
        for p in ok_p:
            print(f"                  ✓ {p.name}  ({', '.join(p.hooks) or 'no hooks'})")
        for p in bad_p:
            print(f"                  ✗ {p.name}  — {p.error}")
            warnings.append(f"plugin {p.name!r} failed to load — fix or remove "
                            f"~/.claudestudio/plugins/{p.name}.py")

    if os.path.exists(args.db):
        conn = index.connect(args.db)
        try:
            s = index.session_summary(conn)
            stored = index.stored_schema_version(conn)
            flag = "✓" if stored == index.SCHEMA_VERSION else "⚠"
            print(f"  schema ver    : {stored} (current {index.SCHEMA_VERSION}) {flag}")
            print(f"  indexed       : {s['sessions']:,} sessions, "
                  f"{s['messages']:,} messages")
            rc = index.root_counts(conn)
            if len(rc) > 1:
                for r in rc:
                    print(f"  indexed root  : {r['root']}  ({r['sessions']} sessions)")

            # index freshness (v0.6.1)
            ind = s.get("indexed_at")
            if ind:
                hrs = max(0.0, (time.time() - ind) / 3600.0)
                fresh = "✓" if hrs <= 24 else "⚠"
                print(f"  index fresh   : {hrs:.1f}h old {fresh}")
                if hrs > 24 and not hst["installed"]:
                    warnings.append("index >24h old and no hook — run `claudestudio index` "
                                    "or install the hook")

            # preferences (v0.6.1)
            theme = index.get_preference(conn, "theme", "dark")
            budget = conn.execute(
                "SELECT period, ceiling_usd FROM budgets ORDER BY id DESC LIMIT 1"
            ).fetchone()
            bud = (f"${budget['ceiling_usd']:.0f}/{budget['period']}"
                   if budget else "none")
            print(f"  preferences   : theme={theme}, budget={bud}")

            # hottest file (v0.6.1)
            from . import file_heatmap
            top = file_heatmap.top_files(conn, limit=1)["files"]
            if top:
                f = top[0]
                print(f"  hottest file  : {f['path']} ({f['edit_count']} edits)")

            # benchmark quick verdict (v0.6.1)
            from . import benchmark
            verdict = benchmark.compute_benchmark(conn, "week")["verdict"]
            print(f"  benchmark     : {verdict}")

            # --- v0.6.2: Insight Engine status ---
            from . import error_taxonomy, webhooks
            hooks = webhooks.list_webhooks(conn)
            print(f"  webhooks      : {len(hooks)} configured")

            # resume readiness — a recent session means `resume --last` works
            recent = conn.execute(
                "SELECT session_id FROM sessions ORDER BY last_epoch DESC LIMIT 1"
            ).fetchone()
            n_sessions = s["sessions"]
            print(f"  resume ready  : {'yes' if recent else 'no'} "
                  f"(compare needs ≥2 — have {n_sessions})")

            er = error_taxonomy.error_rate(conn, days=7)
            print(f"  error rate 7d : {er['errors']} errors over {er['sessions']} "
                  f"sessions ({er['per_session']}/session, {er['change_pct']:+.0f}% vs prior)")

            # one-line "biggest lever" recommendation
            rec = _doctor_recommendation(
                hooks=hooks, hook_installed=hst["installed"], error_rate=er,
                stale_pricing=pricing.is_price_table_stale(), n_sessions=n_sessions)
            print(f"\n  ➤ Top recommendation: {rec}")
        finally:
            conn.close()
    else:
        warnings.append("no index yet — run `claudestudio index` (or `claudestudio demo`)")

    # summary + exit code
    print()
    if criticals:
        for m in criticals:
            print(f"  ❌ {m}")
    for m in warnings:
        print(f"  ⚠  {m}")
    if criticals:
        print("\n  doctor: critical issues found.")
        return 2
    if warnings:
        print(f"\n  doctor: {len(warnings)} warning(s) — see fixes above.")
        return 1
    print("  doctor: all healthy ✓")
    return 0


def cmd_hook(args):
    """Install / inspect / remove the Claude Code post-session reindex hook."""
    from . import hook
    action = args.action or "status"
    if action == "install":
        res = hook.install_hook()
        print(BANNER)
        if res["changed"]:
            print(f"  ✓ Installed the post-session hook into {res['path']}")
        else:
            print(f"  ✓ Hook already installed in {res['path']} (no change)")
        print(f"\n  Claude Code will now run `{hook.HOOK_COMMAND}` on `{hook.HOOK_EVENT}`.")
        print("  Exact settings.json content:\n")
        for line in json.dumps(res["settings"], indent=2).splitlines():
            print("    " + line)
        print("\n  Tip: pair it with `claudestudio watch` for live in-app updates.")
        return 0
    if action == "uninstall":
        res = hook.uninstall_hook()
        print(BANNER)
        print(f"  ✓ Removed the hook from {res['path']}" if res["changed"]
              else f"  Hook was not installed in {res['path']} (nothing to do)")
        return 0
    # status
    st = hook.hook_status(db_path=args.db)
    print(BANNER)
    print(f"  settings.json : {st['settings_path']}")
    print(f"  installed     : {st['installed']}")
    print(f"  event/command : {st['event']} → {st['command']}")
    if st["installed"]:
        when = _fmt_day(st["last_run_epoch"]) if st["last_run_epoch"] else "never"
        print(f"  last index run: {when}")
    else:
        print("  install it    : claudestudio hook install")
    return 0


def _configured_roots(conn) -> list[str]:
    """All projects roots recorded in the index (multi-root aware).

    Reads the `roots` meta key written by reindex; falls back to the single
    `root` key (and finally the default) so an older index still reports one.
    """
    row = conn.execute("SELECT value FROM meta WHERE key='roots'").fetchone()
    if row and row[0]:
        try:
            roots = json.loads(row[0])
            if isinstance(roots, list) and roots:
                return [str(r) for r in roots]
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    row = conn.execute("SELECT value FROM meta WHERE key='root'").fetchone()
    return [row[0]] if row and row[0] else [_parser.default_projects_root()]


def _mcp_snippet() -> str:
    return json.dumps(
        {"mcpServers": {"claudestudio": {"command": "claudestudio-mcp", "args": []}}},
        indent=2,
    )


def cmd_info(args):
    """Full environment summary — paste this into a bug report."""
    import platform as _platform

    from . import hook, mcp
    print(f"  claudestudio  {__version__}")
    print(f"  python        {sys.version.split()[0]}  ({_platform.python_implementation()})")
    print(f"  platform      {_platform.platform()}")
    print(f"  index db      {args.db}")
    exists = os.path.exists(args.db)
    print(f"  index exists  {exists}")
    if exists:
        size = os.path.getsize(args.db)
        print(f"  index size    {size:,} bytes")
        conn = index.connect(args.db)
        s = index.session_summary(conn)
        print(f"  sessions      {s['sessions']:,}  ({s['messages']:,} messages)")
        print(f"  schema ver    {index.stored_schema_version(conn)} "
              f"(current {index.SCHEMA_VERSION})")
        for r in _configured_roots(conn):
            n = conn.execute(
                "SELECT COUNT(DISTINCT session_id) n FROM sources WHERE root=?", (r,)
            ).fetchone()
            cnt = n["n"] if n and n["n"] else 0
            print(f"  root          {r}  ({cnt} sessions)")
        conn.close()
    try:
        import sqlite3
        cx = sqlite3.connect(":memory:")
        cx.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        print("  fts5          available")
    except sqlite3.OperationalError:
        print("  fts5          MISSING (search degraded)")
    st = hook.hook_status()
    print(f"  hook          {'installed' if st['installed'] else 'not installed'}"
          + (f" (last ran {_fmt_day(st['last_run_epoch'])})" if st.get("last_run_epoch") else ""))
    print(f"  mcp tools     {len(mcp.TOOLS)} (run `claudestudio mcp`)")
    print("\n  Register the MCP server with Claude Code (~/.claude.json):")
    for line in _mcp_snippet().splitlines():
        print("    " + line)
    return 0


def cmd_stats(args):
    conn = index.connect(args.db)
    s = index.session_summary(conn)
    print(f"\n  sessions   {s['sessions']:>12,}")
    print(f"  projects   {s['projects']:>12,}")
    print(f"  messages   {s['messages']:>12,}")
    print(f"  tool calls {s['tool_calls']:>12,}")
    print(f"  tokens     {s['tokens']:>12,}")
    print(f"  cost       ${s['cost_usd']:>11,.2f}\n")
    conn.close()
    return 0


def cmd_demo(args):
    demo_root = os.path.join(os.path.dirname(index.default_db_path()), "demo-projects")
    demo_db = os.path.join(os.path.dirname(index.default_db_path()), "demo.db")
    print(BANNER)
    print(f"  generating {args.count} synthetic sessions…")
    # start clean so --count is exact and stale fixtures don't accumulate
    import shutil
    shutil.rmtree(demo_root, ignore_errors=True)
    fixtures.build_corpus(demo_root, count=args.count, seed=args.seed)
    # fresh db each time
    if os.path.exists(demo_db):
        with contextlib.suppress(OSError):
            os.remove(demo_db)
    conn = index.connect(demo_db)
    index.reindex(conn, demo_root, force=True, progress=_progress)
    print()
    s = index.session_summary(conn)
    print(f"  demo index → {s['sessions']} sessions · {s['messages']:,} messages · ${s['cost_usd']:,.2f}")
    conn.close()
    if args.serve:
        _warn_if_public_host(args.host)
        server.serve(demo_db, demo_root, host=args.host, port=args.port,
                     open_browser=not args.no_browser)
    else:
        print(f"\n  Explore it:  claudestudio serve --db \"{demo_db}\" --root \"{demo_root}\"")
    return 0


def _fmt_day(epoch) -> str:
    try:
        return _dt.datetime.fromtimestamp(float(epoch)).strftime("%Y-%m-%d %H:%M")
    except (ValueError, OSError, TypeError):
        return "—"


def cmd_list(args):
    conn = index.connect(args.db)
    params = {"limit": args.limit, "sort": args.sort}
    if args.query:
        params["q"] = args.query
    if args.project:
        params["project"] = args.project
    if args.model:
        params["model"] = args.model
    if args.since:
        params["since"] = args.since
    if args.until:
        params["until"] = args.until
    if args.favorite:
        params["favorite"] = "1"
    params["archived"] = args.archived
    res = api.list_sessions(conn, params)
    conn.close()
    if args.json:
        print(json.dumps(res, default=str, indent=2))
        return 0
    rows = res["sessions"]
    if not rows:
        print("  No sessions match.")
        return 0
    for s in rows:
        star = "★" if s.get("favorite") else " "
        title = (s.get("title") or "Untitled").replace("\n", " ")[:48]
        print(f"  {star} {s['session_id']}  {_fmt_day(s.get('last_epoch')):16}  "
              f"{int(s.get('msg_count') or 0):>4} msg  {(s.get('project_name') or ''):<14}  {title}")
    print(f"\n  {len(rows)} of {res.get('total', len(rows))} session(s)")
    return 0


def cmd_search(args):
    conn = index.connect(args.db)
    params = {"q": args.query, "limit": args.limit}
    for k in ("kind", "project", "session", "since", "until"):
        v = getattr(args, k, None)
        if v:
            params[k] = v
    res = api.search(conn, params)
    conn.close()
    if args.json:
        print(json.dumps(res, default=str, indent=2))
        return 0
    if res.get("error"):
        print(f"  Bad query: {args.query!r}")
        return 1
    results = res.get("results", [])
    if not results:
        print(f"  No matches for {args.query!r}.")
        return 0
    for r in results:
        snip = (r.get("snip") or "").replace("\n", " ").strip()
        print(f"  {r['session_id']}  #{r.get('seq'):<4} [{r.get('kind','?'):<9}] "
              f"{(r.get('title') or 'Untitled')[:40]}")
        if snip:
            print(f"      {snip}")
    print(f"\n  {len(results)} match(es). Export one with "
          f"`claudestudio export <id>`, or `serve` and open ?seq=<n>.")
    return 0


def _print_ask(res):
    title = res.get("title", "")
    print()
    print(f"  {title}")
    print("  " + "─" * min(max(len(title), 4), 64))
    for b in res.get("blocks", []):
        label, kind = b.get("label"), b.get("type")
        if label:
            print(f"\n  {label}:")
        if kind == "text":
            for line in textwrap.wrap(str(b.get("text", "")), 76) or [""]:
                print(f"    {line}")
        elif kind in ("list", "steps"):
            for it in b.get("items", []):
                print(f"    • {it}")
        elif kind == "decisions":
            for it in b.get("items", []):
                print(f"    • {it.get('text', '')}  [#{it.get('seq')}]")
        elif kind == "stats":
            cells = [f"{it.get('value')} {it.get('label')}" for it in b.get("items", [])]
            print("    " + "  ·  ".join(cells))
        elif kind == "files":
            for it in b.get("items", []):
                ops = it.get("ops")
                ops = "+".join(ops) if isinstance(ops, (list, tuple)) else str(ops or "")
                print(f"    {it.get('name', ''):<28} {ops:<14} ×{it.get('count', 0)}"
                      + (f"  ({it.get('errors')} err)" if it.get("errors") else ""))
        elif kind == "sessions":
            for it in b.get("items", []):
                print(f"    {it.get('session_id', '')}  "
                      f"{(it.get('title') or 'Untitled')[:34]:<34}  — {it.get('reason', '')}")
        else:  # compare / unknown — degrade gracefully
            for it in b.get("items", []) or []:
                print(f"    • {it}")
    g = res.get("grounding")
    if g:
        print(f"\n  {g}\n")


def cmd_ask(args):
    conn = index.connect(args.db)
    res = api.ask(conn, args.question, args.session)
    conn.close()
    if args.json:
        print(json.dumps(res, default=str, indent=2))
        return 0
    _print_ask(res)
    return 0


def cmd_sync(args):
    """Push/pull the ~/.claudestudio index across machines via git or rsync."""
    from . import sync as syncmod
    state_dir = os.path.dirname(os.path.abspath(args.db))
    print(BANNER)
    if args.status:
        st = syncmod.status(state_dir)
        print(f"  state dir   : {st['state_dir']}")
        print(f"  git repo    : {st['is_git_repo']}")
        print(f"  method      : {st['method'] or '—'}")
        print(f"  remote      : {st['remote'] or '—'}")
        print(f"  last push   : {_fmt_day(st['last_push']) if st['last_push'] else 'never'}")
        print(f"  last pull   : {_fmt_day(st['last_pull']) if st['last_pull'] else 'never'}")
        print(f"  bytes       : {st['bytes'] or 0:,}")
        print(f"  conflict    : {st['conflict']}")
        return 0
    action = "push" if args.push else ("pull" if args.pull else None)
    if not action:
        print("  Pick one of:  --push | --pull | --status")
        return 1
    res = syncmod.sync(action, state_dir=state_dir, remote=args.remote,
                       method=args.method, dry_run=args.dry_run)
    if res.get("dry_run"):
        print(f"  dry run · {action} via {res['method']}"
              + (f" → {args.remote}" if args.remote else ""))
        for c in res["commands"]:
            print(f"    $ {c}")
        print("\n  (no commands were executed)")
        return 0
    if res.get("error"):
        print(f"  ✗ {res['error']}")
        return 1
    if res.get("ok"):
        extra = " (nothing new to commit)" if res.get("no_changes") else ""
        print(f"  ✓ {action} via {res['method']} complete{extra}")
        if res.get("bytes"):
            print(f"    {res['bytes']:,} bytes under {state_dir}")
        return 0
    print(f"  ✗ {action} failed"
          + (" — conflict detected (resolve manually)" if res.get("conflict") else ""))
    for line in res.get("output", [])[-6:]:
        print(f"    {line}")
    return 1


def cmd_changelog_draft(args):
    """Draft a CHANGELOG entry from the git log since the last version tag."""
    from . import changelog_draft
    res = changelog_draft.generate(version=args.version, date=args.date or None)
    if not res["available"]:
        print("  git is not available here — cannot read the commit log. "
              "Install git or run inside a repo.")
        return 0
    if args.out and args.out != "-":
        dest = _safe_out_path(args.out, "CHANGELOG.draft.md")
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "w", encoding="utf-8") as fh:
            fh.write(res["draft"])
        print(f"  draft → {dest}  ({res['count']} commit(s) since "
              f"{res['tag'] or 'the start'})")
        return 0
    sys.stdout.write(res["draft"])
    return 0


def _stdin_feed():
    while True:
        try:
            yield input()
        except EOFError:
            return


def cmd_init(args):
    """Interactive onboarding wizard (use --yes for all-defaults, non-interactive)."""
    from . import init_wizard
    actions = init_wizard.WizardActions(args.db, _split_roots(args.root))
    print(BANNER)
    init_wizard.run_wizard(actions, assume_yes=args.yes,
                           inputs=None if args.yes else _stdin_feed())
    if args.open:
        actions.open_app()
    return 0


def cmd_feed(args):
    """Print the local RSS feed URL (and a subscribing tip), or write the feed."""
    from . import feed
    if args.out:
        conn = index.connect(args.db)
        xml = feed.build_atom(conn) if args.atom else feed.build_rss(conn)
        conn.close()
        if args.out == "-":
            sys.stdout.write(xml)
            return 0
        dest = _safe_out_path(args.out, "claudestudio-feed." + ("atom" if args.atom else "rss"))
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "w", encoding="utf-8") as fh:
            fh.write(xml)
        print(f"  feed → {dest}")
        return 0
    print(BANNER)
    base = f"http://{args.host}:{args.port}"
    print("  Your sessions, as a feed (served while `claudestudio serve` is running):")
    print(f"    RSS  : {base}/api/feed.rss")
    print(f"    Atom : {base}/api/feed.atom")
    print("\n  Filters: ?project=<name>  ?since=YYYY-MM-DD  ?limit=N")
    print("  Tip: subscribe in any RSS reader that can reach localhost, or pipe it "
          "into a Slack/email bot.")
    return 0


def _last_session_id(conn) -> str | None:
    row = conn.execute(
        "SELECT session_id FROM sessions ORDER BY last_epoch DESC, session_id ASC LIMIT 1"
    ).fetchone()
    return row["session_id"] if row else None


def cmd_tag(args):
    """Manage session tags — the freeform organisational layer."""
    from .tags import TagManager
    conn = index.connect(args.db)
    try:
        if args.add:
            try:
                tag = TagManager.create_tag(conn, args.add, args.colour)
            except ValueError as exc:
                print(f"  ✗ {exc}", file=sys.stderr)
                return 1
            print(f"  ✓ tag “{tag['name']}”  {tag['colour']}  "
                  f"({tag['session_count']} sessions)")
            return 0
        tags = TagManager.list_tags(conn)
        print(BANNER)
        if not tags:
            print("  No tags yet. Add one:  claudestudio tag --add \"bug-fix\"")
            return 0
        print(f"  {len(tags)} tag(s):\n")
        for t in tags:
            print(f"    {t['colour']}  {t['name']:<24} {t['session_count']:>4} sessions")
        return 0
    finally:
        conn.close()


def cmd_narrative(args):
    """Print a deterministic, human-readable narrative of a session."""
    from . import narrative
    conn = index.connect(args.db)
    try:
        sid = _last_session_id(conn) if args.last else args.session_id
        if not sid:
            print("  No session specified. Pass a <session_id> or --last.",
                  file=sys.stderr)
            return 1
        n = narrative.narrative_for_session(conn, sid)
    finally:
        conn.close()
    if n.get("error"):
        print(f"  ✗ {n['error']}: {sid}", file=sys.stderr)
        return 1
    print(f"\n  {n['headline']}\n")
    print(f"  Goal      {n['goal'] or '—'}")
    print(f"  Approach  {n['approach']}")
    print(f"  Outcome   {n['outcome']}")
    if n.get("files_changed"):
        print(f"  Files     {', '.join(n['files_changed'][:8])}")
    if n.get("recovery"):
        print(f"  Recovery  {n['recovery']}")
    if n.get("next_steps"):
        print(f"  Next      {n['next_steps']}")
    print(f"  Quality   {n['quality']}  ({n['word_count']} words)\n")
    return 0


def cmd_digest(args):
    """Standup-ready summary of a day's Claude Code sessions."""
    from . import digest
    date = args.date
    if args.yesterday:
        date = (_dt.date.today() - _dt.timedelta(days=1)).strftime("%Y-%m-%d")
    conn = index.connect(args.db)
    try:
        if args.html:
            html = digest.digest_html(conn, date=date, project_id=args.project)
            dest = _safe_out_path(args.out, "claudestudio-digest.html")
            dest.parent.mkdir(parents=True, exist_ok=True)
            with open(dest, "w", encoding="utf-8") as fh:
                fh.write(html)
            print(f"  digest → {dest}")
            return 0
        d = digest.generate_digest(conn, date=date, project_id=args.project)
    finally:
        conn.close()
    print(d["markdown"])
    return 0


def cmd_share(args):
    """Export a session as a self-contained, offline-renderable HTML file."""
    from . import share
    conn = index.connect(args.db)
    try:
        sid = _last_session_id(conn) if args.last else args.session_id
        if not sid:
            print("  No session specified. Pass a <session_id> or --last.",
                  file=sys.stderr)
            return 1
        html = share.build_share_pack(
            conn, sid, include_annotations=not args.no_annotations)
    finally:
        conn.close()
    if not html:
        print(f"  ✗ no session: {sid}", file=sys.stderr)
        return 1
    if not args.out or args.out == "-":
        sys.stdout.write(html)
        return 0
    dest = _safe_out_path(args.out, f"{sid[:12]}-share.html")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "w", encoding="utf-8") as fh:
        fh.write(html)
    print(f"  share → {dest}")
    return 0


def cmd_benchmark(args):
    """Week/month/quarter efficiency comparison with trend arrows."""
    from . import benchmark
    conn = index.connect(args.db)
    try:
        b = benchmark.compute_benchmark(conn, args.mode)
    finally:
        conn.close()
    if args.json:
        print(json.dumps(b, indent=2, default=str))
        return 0
    arrow = {"improving": "↑", "declining": "↓", "stable": "→"}[b["trend"]]
    print(BANNER)
    print(f"  {b['verdict']}\n")
    cur, delta = b["current"], b["delta"]

    def line(label, key, fmt="{:,}"):
        c = fmt.format(cur[key]) if isinstance(cur[key], (int, float)) else cur[key]
        d = delta[key]
        a = "↑" if d > 0 else ("↓" if d < 0 else "→")
        print(f"    {label:<20} {c:>14}   {a} {d:+.1f}%")

    line("sessions", "sessions")
    line("output tokens", "tokens_output")
    line("cost (USD)", "cost_usd", "${:,.2f}")
    line("output / dollar", "output_per_dollar", "{:,.0f}")
    line("tool success", "tool_success_rate", "{:.0%}")
    line("avg health", "avg_health_score", "{:.0f}")
    line("files touched", "files_touched")
    print(f"\n  trend: {arrow} {b['trend']}")
    return 0


def cmd_resume(args):
    """Print a copy-paste-ready brief to resume a session in a new window."""
    from . import resume
    conn = index.connect(args.db)
    try:
        sid = _last_session_id(conn) if args.last else args.session_id
        if not sid:
            print("  No session specified. Pass a <session_id> or --last.",
                  file=sys.stderr)
            return 1
        data = resume.build_brief(conn, sid)
    finally:
        conn.close()
    if data.get("error"):
        print(f"  ✗ {data['error']}: {sid}", file=sys.stderr)
        return 1
    brief = data["brief"]
    if args.out:
        dest = _safe_out_path(args.out, "claudestudio-resume.txt")
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "w", encoding="utf-8") as fh:
            fh.write(brief + "\n")
        print(f"  resume brief → {dest}")
    else:
        print("\n" + brief + "\n")
    if args.copy:
        if _copy_to_clipboard(brief):
            print("  ✓ copied to clipboard")
        else:
            print("  (clipboard unavailable — copy the text above)", file=sys.stderr)
    return 0


def _copy_to_clipboard(text: str) -> bool:
    """Best-effort copy via the OS clipboard tool. Never raises; False if absent."""
    import shutil
    import subprocess
    if os.name == "nt":
        candidates = [["clip"]]
    elif sys.platform == "darwin":
        candidates = [["pbcopy"]]
    else:
        candidates = [["wl-copy"], ["xclip", "-selection", "clipboard"], ["xsel", "-b"]]
    for cmd in candidates:
        if not shutil.which(cmd[0]):
            continue
        try:
            subprocess.run(cmd, input=text.encode("utf-8"), check=True)
            return True
        except (OSError, subprocess.SubprocessError):
            continue
    return False


def resolve_open_url(conn, *, session_id=None, last=False, starred=False,
                     query=None, port=8787) -> str | None:
    """Build the localhost URL to open for the requested mode, or None if there is
    nothing to open. Pure (no I/O beyond the index read) so the self-test can pin
    every mode's URL."""
    from urllib.parse import quote
    base = f"http://localhost:{int(port)}"
    if query:
        return f"{base}/?q={quote(str(query))}"
    sid = session_id
    if last and not sid:
        sid = _last_session_id(conn)
    if starred and not sid:
        row = conn.execute(
            "SELECT s.session_id FROM sessions s JOIN user_state u USING(session_id) "
            "WHERE u.favorite=1 ORDER BY s.last_epoch DESC, s.session_id ASC LIMIT 1"
        ).fetchone()
        sid = row["session_id"] if row else None
    if not sid and not (session_id or last or starred):
        sid = _last_session_id(conn)
    if not sid:
        return None
    return f"{base}/session/{quote(str(sid))}"


def cmd_open(args):
    """Open a session (or a pre-filled search) in the browser; start the server
    if it isn't already running."""
    import webbrowser
    conn = index.connect(args.db)
    try:
        url = resolve_open_url(
            conn, session_id=args.session_id, last=args.last,
            starred=args.starred, query=args.query, port=args.port)
    finally:
        conn.close()
    if url is None:
        print("  No matching session to open.", file=sys.stderr)
        return 1
    # Start the server in the background if the port looks closed.
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        running = sock.connect_ex(("127.0.0.1", args.port)) == 0
    if not running:
        import threading
        roots = _split_roots(args.root)
        conn2 = index.connect(args.db)
        try:
            index.reindex(conn2, roots)
        finally:
            conn2.close()
        t = threading.Thread(
            target=server.serve,
            kwargs={"db_path": args.db, "projects_root": roots,
                    "port": args.port, "open_browser": False},
            daemon=True)
        t.start()
        time.sleep(1.0)
    print(f"  opening {url}")
    webbrowser.open(url)
    if not running:
        print("  (server started in this process — press Ctrl+C to stop)")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            print("\n  Stopped.")
    return 0


def cmd_compare(args):
    """Print a structured diff of two sessions."""
    from . import compare as compare_mod
    conn = index.connect(args.db)
    try:
        data = compare_mod.compare_sessions(conn, args.session_a, args.session_b)
    finally:
        conn.close()
    if data.get("error"):
        print(f"  ✗ {data['error']}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(data, indent=2, default=str))
        return 0
    a, b = data["a"], data["b"]
    print(BANNER)
    print(f"  A  {a['title'][:54]:<54}  ${a['cost_usd']:.4f}  health {a['health_score']}")
    print(f"  B  {b['title'][:54]:<54}  ${b['cost_usd']:.4f}  health {b['health_score']}\n")
    print(f"  cost Δ    ${data['cost_delta_usd']:+.4f}")
    print(f"  tokens Δ  {data['token_delta']:+,}")
    print(f"  health Δ  {data['health_delta']:+d}")
    if data["shared_files"]:
        print(f"  shared    {', '.join(data['shared_files'][:8])}")
    print(f"\n  {data['verdict']}\n")
    return 0


def cmd_verify_claude_md(args):
    """Verify a project's CLAUDE.md against its session history."""
    from . import verify_claude_md as vmod
    conn = index.connect(args.db)
    try:
        project = args.project
        if not project:
            row = conn.execute(
                "SELECT project_name FROM sessions ORDER BY last_epoch DESC LIMIT 1"
            ).fetchone()
            project = row["project_name"] if row else ""
        data = vmod.verify(conn, project)
    finally:
        conn.close()
    if args.json:
        print(json.dumps(data, indent=2, default=str))
        return 0
    print(BANNER)
    if not data.get("claude_md_found"):
        print(f"  {data.get('note', 'no CLAUDE.md found')}: {project}")
        return 0
    icon = {"verified": "✅", "stale": "⚠️ ", "unverifiable": "❓"}
    print(f"  CLAUDE.md for {project}  —  score {data['overall_score']:.0%} "
          f"({data['verified']}/{data['total']} verified)\n")
    for c in data["claims"]:
        print(f"  {icon.get(c['status'], '·')} {c['text'][:70]}")
        print(f"        {c['evidence']}")
    return 0


def cmd_webhook(args):
    """Manage local/LAN webhook notifications."""
    from . import webhooks
    conn = index.connect(args.db)
    try:
        if args.add:
            try:
                hook = webhooks.add_webhook(conn, args.add, args.events)
            except ValueError as exc:
                print(f"  ✗ {exc}", file=sys.stderr)
                return 1
            print(f"  ✓ webhook {hook['id'][:8]} → {hook['url']}  "
                  f"[{', '.join(hook['events'])}]")
            return 0
        if args.remove:
            res = webhooks.remove_webhook(conn, args.remove)
            print("  ✓ removed" if res["removed"] else "  (no matching webhook)")
            return 0 if res["removed"] else 1
        hooks = webhooks.list_webhooks(conn)
        print(BANNER)
        if not hooks:
            print("  No webhooks. Add one:  claudestudio webhook --add "
                  "http://localhost:9000/hook --events session_indexed")
            return 0
        print(f"  {len(hooks)} webhook(s):\n")
        for h in hooks:
            print(f"    {h['id'][:8]}  {h['url']}  [{', '.join(h.get('events', []))}]")
        return 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# v0.6.3 — Community & Clarity
# ---------------------------------------------------------------------------

def cmd_tour(args):
    """Print the first-run guided tour as plain text (no curses)."""
    from . import onboarding
    print(onboarding.terminal_tour(), end="")
    return 0


def cmd_plugins(args):
    """Discover and install community plugins from the curated registry."""
    from . import plugin_registry as pr
    action = getattr(args, "action", None) or "list"

    if action == "update":
        try:
            reg = pr.fetch_registry()
        except pr.RegistryError as exc:
            print(f"  ✗ {exc}", file=sys.stderr)
            return 1
        print(f"  ✓ registry refreshed — {len(reg.get('plugins', []))} plugins cached")
        return 0

    if action == "list":
        data = pr.list_plugins()
        print(BANNER)
        plugins = data.get("plugins", [])
        if not plugins:
            print("  No plugins cached yet. Run `claudestudio plugins update` "
                  "to fetch the registry.")
            return 0
        print(f"  {len(plugins)} plugin(s) in the registry:\n")
        for p in plugins:
            mark = "●" if p["installed"] else "○"
            tags = ("  [" + ", ".join(p.get("tags", [])) + "]") if p.get("tags") else ""
            print(f"   {mark} {p['name']:<16} {p.get('description', '')[:52]}{tags}")
        print("\n   ● installed   ○ available · "
              "install with `claudestudio plugins install <name>`")
        return 0

    if action == "info":
        if not args.name:
            print("  Usage: claudestudio plugins info <name>", file=sys.stderr)
            return 1
        info = pr.plugin_info(args.name)
        if info.get("error"):
            print(f"  ✗ {info['error']}", file=sys.stderr)
            return 1
        print(BANNER)
        print(f"  {info.get('name')}  v{info.get('version', '?')}  "
              f"by {info.get('author', 'community')}")
        print(f"  {info.get('description', '')}")
        print(f"  tags : {', '.join(info.get('tags', [])) or '—'}")
        print(f"  url  : {info.get('url', '')}")
        print(f"  state: {'installed' if info.get('installed') else 'not installed'}")
        return 0

    if action == "remove":
        if not args.name:
            print("  Usage: claudestudio plugins remove <name>", file=sys.stderr)
            return 1
        res = pr.remove_plugin(args.name)
        if res["status"] == "removed":
            print(f"  ✓ removed {args.name}")
            return 0
        print(f"  (no installed plugin named {args.name!r})")
        return 1

    if action == "install":
        if not args.name:
            print("  Usage: claudestudio plugins install <name>", file=sys.stderr)
            return 1

        def _confirm(url: str) -> bool:
            try:
                ans = input(f"  Download plugin source from\n    {url}\n  Proceed? [y/N] ")
            except EOFError:
                return False
            return ans.strip().lower() in ("y", "yes")

        try:
            res = pr.install_plugin(args.name, yes=args.yes, confirm=_confirm)
        except pr.RegistryError as exc:
            print(f"  ✗ {exc}", file=sys.stderr)
            return 1
        status = res.get("status")
        if status == "installed":
            vf = " (checksum verified)" if res.get("verified") else ""
            print(f"  ✓ installed {args.name} → {res['path']}{vf}")
            return 0
        if status == "already_installed":
            print(f"  {args.name} is already installed ({res['path']}).")
            return 0
        if status == "cancelled":
            print("  Cancelled.")
            return 1
        print(f"  ✗ {status}", file=sys.stderr)
        return 1

    print(f"  Unknown action: {action}", file=sys.stderr)
    return 1


def cmd_search_history(args):
    """List (or clear) the persistent search history."""
    from . import search_history
    conn = index.connect(args.db)
    try:
        if args.clear:
            search_history.clear(conn)
            print("  ✓ search history cleared")
            return 0
        items = search_history.recent(conn, args.limit)
    finally:
        conn.close()
    if args.json:
        print(json.dumps(items, indent=2, default=str))
        return 0
    print(BANNER)
    if not items:
        print("  No searches recorded yet.")
        return 0
    print(f"  {len(items)} recent search(es):\n")
    for it in items:
        kind = f" [{it['kind']}]" if it.get("kind") else ""
        print(f"   {_fmt_day(it['searched_at']):<12} "
              f"{(it['query'] or '')[:44]:<44}{kind}  · {it.get('result_count', 0)} hits")
    return 0


def cmd_github_summary(args):
    """Print a Markdown session summary for use in GitHub Actions."""
    from . import github_action
    path = args.session
    if not path and args.last:
        conn = index.connect(args.db)
        try:
            row = conn.execute(
                "SELECT file_path FROM sessions ORDER BY last_epoch DESC LIMIT 1"
            ).fetchone()
        finally:
            conn.close()
        if not row or not row["file_path"]:
            print("_No indexed sessions to summarize._")
            return 1
        path = row["file_path"]
    if not path:
        print("  Pass --session <path> or --last", file=sys.stderr)
        return 1
    print(github_action.summarize_path(path), end="")
    return 0


def cmd_template(args):
    """List, render, or create session templates."""
    from . import templates
    action = getattr(args, "action", None) or "list"

    if action == "list":
        items = templates.list_templates()
        print(BANNER)
        print(f"  {len(items)} template(s):\n")
        for t in items:
            v = ("  {" + ", ".join(t["vars"]) + "}") if t["vars"] else ""
            print(f"   {t['name']:<14} [{t['source']}]{v}")
        print("\n   render one:  claudestudio template use <name> --file … --goal …")
        return 0

    if action == "create":
        if not args.name:
            print("  Usage: claudestudio template create <name>", file=sys.stderr)
            return 1
        try:
            res = templates.create_template(args.name)
        except ValueError as exc:
            print(f"  ✗ {exc}", file=sys.stderr)
            return 1
        print(f"  ✓ template at {res['path']} — edit it in your $EDITOR")
        return 0

    if action == "use":
        if not args.name:
            print("  Usage: claudestudio template use <name> [--file …] [--goal …]",
                  file=sys.stderr)
            return 1
        variables: dict = {}
        for k in ("file", "goal", "error", "project", "feature"):
            v = getattr(args, k, None)
            if v:
                variables[k] = v
        for kv in (args.var or []):
            if "=" in kv:
                key, val = kv.split("=", 1)
                variables[key.strip()] = val
        conn = index.connect(args.db)
        try:
            out = templates.render(conn, args.name, variables,
                                   include_context=not args.no_context)
        finally:
            conn.close()
        if out.get("error"):
            print(f"  ✗ {out['error']}", file=sys.stderr)
            return 1
        print(out["rendered"])
        if out.get("missing"):
            print(f"\n  ⚠  unfilled: {{{', '.join(out['missing'])}}} "
                  f"— pass them with --var key=value", file=sys.stderr)
        return 0

    print(f"  Unknown action: {action}", file=sys.stderr)
    return 1


# ---------------------------------------------------------------------------
# v0.7.0 — Intelligence Layer
# ---------------------------------------------------------------------------

def _emit_text(text, args):
    """Print text and honour --copy / --out when present on the namespace."""
    print(text)
    if getattr(args, "copy", False):
        if _copy_to_clipboard(text):
            print("  ✓ copied to clipboard", file=sys.stderr)
        else:
            print("  (clipboard unavailable)", file=sys.stderr)
    out = getattr(args, "out", None)
    if out:
        path = _safe_out_path(out, "claudestudio-output.txt")
        path.write_text(text, encoding="utf-8")
        print(f"  ✓ wrote {path}", file=sys.stderr)


def cmd_ai_summary(args):
    from . import ai_analysis
    conn = index.connect(args.db)
    try:
        sid = _last_session_id(conn) if args.last else args.session_id
        if not sid:
            print("  No session specified. Pass a <session_id> or --last.",
                  file=sys.stderr)
            return 1
        res = ai_analysis.summarize_session(conn, sid)
    finally:
        conn.close()
    if res.get("status") == 402 or res.get("error") == "ANTHROPIC_API_KEY not set":
        print("  ✗ AI features are opt-in. Set ANTHROPIC_API_KEY to enable them.",
              file=sys.stderr)
        return 2
    if res.get("error"):
        print(f"  ✗ {res['error']}", file=sys.stderr)
        return 1
    if args.json:
        _emit_text(json.dumps(res, indent=2, default=str), args)
        return 0
    lines = [f"\n  {res['summary']}\n"]
    if res.get("coaching_tips"):
        lines.append("  Coaching tips:")
        lines += [f"    • {t}" for t in res["coaching_tips"]]
    if res.get("improvement_suggestions"):
        lines.append("  Improvement suggestions:")
        lines += [f"    {i + 1}. {s}" for i, s in enumerate(res["improvement_suggestions"])]
    lines.append(f"\n  ({res['model_used']} · {res['tokens_used']} tokens · "
                 f"${res['cost_usd']:.4f}{' · cached' if res.get('cached') else ''})")
    _emit_text("\n".join(lines), args)
    return 0


def cmd_ai_coach(args):
    from . import ai_analysis
    conn = index.connect(args.db)
    try:
        res = ai_analysis.coach(conn, getattr(args, "n", 20))
    finally:
        conn.close()
    if res.get("status") == 402:
        print("  ✗ AI features are opt-in. Set ANTHROPIC_API_KEY to enable them.",
              file=sys.stderr)
        return 2
    _emit_text(res.get("report", ""), args)
    return 0


def cmd_ai_prompt(args):
    from . import ai_analysis
    conn = index.connect(args.db)
    try:
        res = ai_analysis.improve_prompt(conn, args.prompt)
    finally:
        conn.close()
    if res.get("status") == 402:
        print("  ✗ AI features are opt-in. Set ANTHROPIC_API_KEY to enable them.",
              file=sys.stderr)
        return 2
    out = (f"\n  Original:\n    {res['original']}\n\n  Improved:\n    {res['improved']}\n"
           f"\n  Projected effectiveness delta: +{res['projected_delta']:.2f}")
    _emit_text(out, args)
    return 0


def cmd_similar(args):
    from . import semantic
    conn = index.connect(args.db)
    try:
        sid = _last_session_id(conn) if args.last else args.session_id
        if not sid:
            print("  No session specified. Pass a <session_id> or --last.",
                  file=sys.stderr)
            return 1
        semantic.build_vectors(conn)  # persist so later runs are instant
        res = semantic.similar(conn, sid, top=args.top)
    finally:
        conn.close()
    if args.json:
        print(json.dumps(res, indent=2, default=str))
        return 0
    if not res:
        print("  No similar sessions found (try indexing more sessions).")
        return 0
    print(f"\n  Sessions similar to {sid[:12]}:\n")
    for r in res:
        print(f"  {r['score']:.3f}  {r['title'][:50]:50}  {r['reason']}")
    print()
    return 0


def cmd_clusters(args):
    from . import clustering
    conn = index.connect(args.db)
    try:
        res = clustering.cluster_sessions(conn, args.k, refresh=True)
    finally:
        conn.close()
    if args.json:
        print(json.dumps(res, indent=2, default=str))
        return 0
    print(f"\n  {len(res['clusters'])} topic clusters:\n")
    for cl in res["clusters"]:
        print(f"  ▸ {cl['label']}  ({cl['count']} sessions · "
              f"avg ${cl['avg_cost']:.3f} · health {cl['avg_health']})")
        for s in cl["sessions"]:
            print(f"      {s['title'][:54]:54}  health {s['health']}")
    print()
    return 0


def cmd_watch_session(args):
    from . import live_session
    conn = index.connect(args.db)
    try:
        sid = _last_session_id(conn) if args.last else args.session_id
        if not sid:
            print("  No session specified. Pass a <session_id> or --last.",
                  file=sys.stderr)
            return 1
        path = live_session.resolve_session_path(conn, sid)
    finally:
        conn.close()
    if not path:
        print(f"  ✗ no session file for {sid}", file=sys.stderr)
        return 1
    res = live_session.tail_events(path, since_line=0)
    for ev in res["events"]:
        print("  " + live_session.format_event(ev))
    live = "LIVE" if live_session.is_live(path) else "idle"
    print(f"\n  [{live}] {len(res['events'])} events through line {res['next_line']}.")
    return 0


def cmd_context_analysis(args):
    from . import context_analyzer
    conn = index.connect(args.db)
    try:
        sid = _last_session_id(conn) if args.last else args.session_id
        if not sid:
            print("  No session specified. Pass a <session_id> or --last.",
                  file=sys.stderr)
            return 1
        res = context_analyzer.analyze_session(conn, sid)
    finally:
        conn.close()
    if res.get("error"):
        print(f"  ✗ {res['error']}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(res, indent=2, default=str))
        return 0
    print(f"\n  Context utilization — {res['model'] or 'unknown model'} "
          f"(limit {res['model_limit']:,} tokens)\n")
    print(context_analyzer.ascii_chart(res["turns"]))
    flag = "  ⚠ waste indicator: many turns use <10% of the window" if res["waste_indicator"] else ""
    print(f"\n  avg {res['avg_utilization_pct']}%  peak {res['peak_utilization_pct']}%{flag}\n")
    return 0


def cmd_model_stats(args):
    from . import model_analytics
    conn = index.connect(args.db)
    try:
        res = model_analytics.models_payload(conn)
    finally:
        conn.close()
    if args.json:
        print(json.dumps(res, indent=2, default=str))
        return 0
    print(f"\n  {'model':28} {'sessions':>8} {'total $':>10} {'avg $':>8} "
          f"{'health':>7} {'tool ok':>8}")
    for m in res["models"]:
        print(f"  {m['model'][:28]:28} {m['session_count']:>8} "
              f"{m['total_cost_usd']:>10.4f} {m['avg_cost_usd']:>8.4f} "
              f"{m['avg_health_score']:>7} {m['tool_success_rate']:>8.3f}")
    print(f"\n  {res['recommendation']}\n")
    return 0


def cmd_annotations(args):
    from . import collab_annotations
    conn = index.connect(args.db)
    try:
        if args.action == "export":
            data = collab_annotations.export_annotations(conn)
            text = json.dumps(data, indent=2, default=str)
            if args.out:
                path = _safe_out_path(args.out, "annotations.json")
                path.write_text(text, encoding="utf-8")
                print(f"  ✓ exported {len(data['annotations'])} annotations to {path}")
            else:
                print(text)
            return 0
        if args.action == "import":
            if not args.file:
                print("  Pass the annotations JSON file to import.", file=sys.stderr)
                return 1
            with open(args.file, encoding="utf-8") as fh:
                data = json.load(fh)
            strategy = "replace" if args.replace else "merge"
            res = collab_annotations.import_annotations(conn, data, strategy)
            print(f"  Imported {res['imported']} annotations, "
                  f"skipped {res['skipped']} (strategy: {strategy}).")
            return 0
    finally:
        conn.close()
    print(f"  Unknown action: {args.action}", file=sys.stderr)
    return 1


def cmd_completions(args):
    from . import completions
    if args.action == "install":
        info = completions.install(args.shell)
        print(f"  ✓ installed {info['shell']} completions to {info['path']} "
              f"({info['bytes']} bytes)")
        print("  Restart your shell or source the file to activate.")
        return 0
    shell = args.action  # bash | zsh | fish
    try:
        print(completions.render(shell))
    except ValueError as exc:
        print(f"  ✗ {exc}", file=sys.stderr)
        return 1
    return 0


def build_parser():
    ap = argparse.ArgumentParser(
        prog="claudestudio",
        description="The desktop workspace for Claude Code.",
    )
    ap.add_argument("-V", "--version", action="version",
                    version=f"claudestudio {__version__}")
    ap.add_argument("--selftest", action="store_true", help="run built-in correctness checks")
    sub = ap.add_subparsers(dest="command")

    p = sub.add_parser("index", help="scan sessions and (incrementally) build the index")
    _add_common(p)
    p.add_argument("--force", action="store_true", help="reindex every file")
    p.set_defaults(func=cmd_index)

    p = sub.add_parser("serve", help="launch the desktop app (local web UI)")
    _add_common(p)
    p.add_argument("--port", type=int, default=8787)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--no-browser", action="store_true")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("list", help="list indexed sessions (most recent first)")
    _add_common(p)
    p.add_argument("-q", "--query", default=None, help="filter by full-text / title match")
    p.add_argument("--project", default=None, help="restrict to a project path")
    p.add_argument("--model", default=None, help="restrict to a model substring")
    p.add_argument("--since", default=None, help="only sessions active on/after this date (YYYY-MM-DD)")
    p.add_argument("--until", default=None, help="only sessions started on/before this date (YYYY-MM-DD)")
    p.add_argument("--sort", default="recent",
                   choices=sorted(api.SORT_COLUMNS.keys()), help="sort order (default: recent)")
    p.add_argument("--favorite", action="store_true", help="only favorited sessions")
    p.add_argument("--archived", default="exclude", choices=["exclude", "only", "all"],
                   help="archived sessions: exclude (default), only, or all")
    p.add_argument("--limit", type=int, default=40)
    p.add_argument("--json", action="store_true", help="emit raw JSON for scripting")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("search", help="full-text search across all sessions (BM25)")
    _add_common(p)
    p.add_argument("query", help="words to search for")
    p.add_argument("--kind", default=None, choices=["user", "assistant", "tool"],
                   help="restrict to a message kind")
    p.add_argument("--project", default=None, help="restrict to a project path/name")
    p.add_argument("--session", default=None, help="scope to one session id")
    p.add_argument("--since", default=None, help="only messages on/after this date (YYYY-MM-DD)")
    p.add_argument("--until", default=None, help="only messages on/before this date (YYYY-MM-DD)")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--json", action="store_true", help="emit raw JSON for scripting")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("ask", help="ask your history a question (grounded, no model calls)")
    _add_common(p)
    p.add_argument("question", help="e.g. \"what should I reopen next?\"")
    p.add_argument("--session", default=None, help="scope the question to one session id")
    p.add_argument("--json", action="store_true", help="emit raw JSON for scripting")
    p.set_defaults(func=cmd_ask)

    p = sub.add_parser("wrapped", help="print your Claude Wrapped summary")
    _add_common(p)
    p.add_argument("--year", type=int, default=None)
    p.set_defaults(func=cmd_wrapped)

    p = sub.add_parser("export", help="export a session (or all) to Markdown / HTML / JSON")
    _add_common(p)
    p.add_argument("session_id", nargs="?", default=None,
                   help="session id (see `serve` or `index`); omit with --all")
    p.add_argument("--format", choices=["md", "markdown", "html", "json", "ipynb"], default="md")
    p.add_argument("--out", default=None, help="output file ('-' for stdout)")
    p.add_argument("--all", action="store_true", help="export every indexed session")
    p.add_argument("--project", default=None,
                   help="with --all, restrict to one project (path or name)")
    p.add_argument("--zip", action="store_true",
                   help="bundle into a .zip archive (implied when --out ends in .zip)")
    p.add_argument("--out-dir", default=None,
                   help="destination directory for --all (default: ./claudestudio-export)")
    p.add_argument("--force", action="store_true",
                   help="with --all, overwrite files that already exist")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("report", help="generate a shareable HTML/Markdown activity report")
    _add_common(p)
    p.add_argument("--since", default=None, help="range start (YYYY-MM-DD); default: this week")
    p.add_argument("--until", default=None, help="range end (YYYY-MM-DD); default: this week")
    p.add_argument("--format", choices=["html", "md", "markdown"], default="html")
    p.add_argument("--out", default=None, help="output file ('-' for stdout)")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("watch", help="auto-reindex as sessions change (live mode)")
    _add_common(p)
    p.add_argument("--interval", "--poll-interval", type=float, default=5.0,
                   dest="interval", help="seconds between polls (default 5)")
    p.set_defaults(func=cmd_watch)

    p = sub.add_parser("budget", help="track spend against a monthly/weekly ceiling")
    _add_common(p)
    p.add_argument("--set", type=float, default=None, metavar="USD",
                   help="set the ceiling in dollars, e.g. --set 50")
    p.add_argument("--period", default="monthly", choices=["monthly", "weekly"],
                   help="budget period (default monthly)")
    p.add_argument("--clear", action="store_true", help="remove the budget")
    p.set_defaults(func=cmd_budget)

    p = sub.add_parser("generate-claude-md",
                       help="generate a CLAUDE.md from a project's session history")
    _add_common(p)
    p.add_argument("--project", default=None,
                   help="project path or name (default: your most active project)")
    p.add_argument("--out", default=None, help="write here ('-' for stdout); default prints")
    p.add_argument("--dry-run", action="store_true", help="print without writing a file")
    p.set_defaults(func=cmd_generate_claude_md)

    p = sub.add_parser("mcp", help="run the MCP server (JSON-RPC 2.0 over stdio)")
    _add_common(p)
    p.set_defaults(func=cmd_mcp)

    p = sub.add_parser("init", help="zero-friction onboarding wizard (hook, watch, budget)")
    _add_common(p)
    p.add_argument("--yes", "-y", action="store_true",
                   help="accept every default, non-interactive")
    p.add_argument("--open", action="store_true", help="open the app when finished")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("sync", help="sync the index across machines via git/rsync (no cloud)")
    _add_common(p)
    p.add_argument("--push", action="store_true", help="push the local index to the remote")
    p.add_argument("--pull", action="store_true", help="pull the index from the remote")
    p.add_argument("--status", action="store_true", help="show last push/pull + conflict state")
    p.add_argument("--remote", default="", help="git URL or rsync host:path target")
    p.add_argument("--method", default="auto", choices=["auto", "git", "rsync"],
                   help="sync backend (default: auto-detect)")
    p.add_argument("--dry-run", action="store_true", help="print commands without running them")
    p.set_defaults(func=cmd_sync)

    p = sub.add_parser("feed", help="print the local RSS/Atom activity-feed URL")
    _add_common(p)
    p.add_argument("--atom", action="store_true", help="with --out, emit Atom instead of RSS")
    p.add_argument("--out", default=None, help="write the feed to a file ('-' for stdout)")
    p.add_argument("--port", type=int, default=8787)
    p.add_argument("--host", default="127.0.0.1")
    p.set_defaults(func=cmd_feed)

    p = sub.add_parser("changelog-draft",
                       help="draft a CHANGELOG entry from the git log since the last tag")
    p.add_argument("--version", default="Unreleased", help="version header (default: Unreleased)")
    p.add_argument("--date", default=None, help="release date for the header (YYYY-MM-DD)")
    p.add_argument("--out", default=None, help="write here ('-' or omit for stdout)")
    p.set_defaults(func=cmd_changelog_draft)

    p = sub.add_parser("highlights", help="surface interesting moments from your history")
    _add_common(p)
    p.add_argument("--json", action="store_true", help="emit raw JSON for scripting")
    p.set_defaults(func=cmd_highlights)

    p = sub.add_parser("doctor", help="diagnose environment & index health")
    _add_common(p)
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("stats", help="print headline numbers")
    _add_common(p)
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("info", help="full environment summary (for bug reports)")
    _add_common(p)
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("hook", help="auto-reindex when Claude Code finishes a session")
    _add_common(p)
    p.add_argument("action", nargs="?", default="status",
                   choices=["install", "status", "uninstall"],
                   help="install (default: status)")
    p.set_defaults(func=cmd_hook)

    p = sub.add_parser("demo", help="generate synthetic data and explore it")
    p.add_argument("--count", type=int, default=48)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--serve", action="store_true")
    p.add_argument("--port", type=int, default=8787)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--no-browser", action="store_true")
    p.set_defaults(func=cmd_demo)

    # --- v0.6.1 subcommands ---
    p = sub.add_parser(
        "tag", help="manage session tags",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="example:\n  claudestudio tag --add \"bug-fix\" --colour \"#ff8a5b\"\n"
               "  claudestudio tag --list")
    _add_common(p)
    p.add_argument("--add", metavar="NAME", help="create a tag")
    p.add_argument("--colour", "--color", dest="colour", default=None,
                   help="hex colour for --add (default #9a8cff)")
    p.add_argument("--list", action="store_true", help="list all tags")
    p.set_defaults(func=cmd_tag)

    p = sub.add_parser(
        "narrative", help="generate a session narrative",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="example:\n  claudestudio narrative 1a2b3c4d\n"
               "  claudestudio narrative --last")
    _add_common(p)
    p.add_argument("session_id", nargs="?", help="session id to narrate")
    p.add_argument("--last", action="store_true", help="most recently indexed session")
    p.set_defaults(func=cmd_narrative)

    p = sub.add_parser(
        "digest", help="standup-ready daily summary",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="example:\n  claudestudio digest\n  claudestudio digest --yesterday\n"
               "  claudestudio digest --date 2026-06-20 --html --out digest.html")
    _add_common(p)
    p.add_argument("--date", default=None, help="calendar date YYYY-MM-DD (default today)")
    p.add_argument("--yesterday", action="store_true", help="yesterday's digest")
    p.add_argument("--project", default=None, help="scope to one project")
    p.add_argument("--html", action="store_true", help="export a full HTML page")
    p.add_argument("--out", default=None, help="output file for --html")
    p.set_defaults(func=cmd_digest)

    p = sub.add_parser(
        "share", help="export a session as shareable HTML",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="example:\n  claudestudio share 1a2b3c4d --out session.html\n"
               "  claudestudio share --last")
    _add_common(p)
    p.add_argument("session_id", nargs="?", help="session id to share")
    p.add_argument("--last", action="store_true", help="most recently indexed session")
    p.add_argument("--out", default=None, help="output file (default: stdout)")
    p.add_argument("--no-annotations", action="store_true",
                   help="omit personal notes from the pack")
    p.set_defaults(func=cmd_share)

    p = sub.add_parser(
        "benchmark", help="week/month/quarter efficiency report",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="example:\n  claudestudio benchmark\n"
               "  claudestudio benchmark --mode month\n  claudestudio benchmark --json")
    _add_common(p)
    p.add_argument("--mode", choices=["week", "month", "quarter"], default="week",
                   help="comparison period (default week)")
    p.add_argument("--json", action="store_true", help="raw JSON output")
    p.set_defaults(func=cmd_benchmark)

    # --- v0.6.2 commands (Insight Engine) ---
    p = sub.add_parser("resume",
                       help="copy-paste brief to resume a session in a new window")
    _add_common(p)
    p.add_argument("session_id", nargs="?", help="session id (or use --last)")
    p.add_argument("--last", action="store_true", help="resume the most recent session")
    p.add_argument("--copy", action="store_true", help="copy the brief to the clipboard")
    p.add_argument("--out", default=None, help="write the brief to a file")
    p.set_defaults(func=cmd_resume)

    p = sub.add_parser("open", help="open a session (or search) in the browser")
    _add_common(p)
    p.add_argument("session_id", nargs="?", help="session id to open")
    p.add_argument("--last", action="store_true", help="open the most recent session")
    p.add_argument("--starred", action="store_true", help="open the most recent starred session")
    p.add_argument("--query", default=None, help="open search pre-filled with this text")
    p.add_argument("--port", type=int, default=8787, help="server port (default 8787)")
    p.set_defaults(func=cmd_open)

    p = sub.add_parser("compare", help="structured diff between two sessions")
    _add_common(p)
    p.add_argument("session_a", help="first session id")
    p.add_argument("session_b", help="second session id")
    p.add_argument("--json", action="store_true", help="raw JSON output")
    p.set_defaults(func=cmd_compare)

    p = sub.add_parser("verify-claude-md",
                       help="check a project's CLAUDE.md against its session history")
    _add_common(p)
    p.add_argument("--project", default=None, help="project name/path (default: most recent)")
    p.add_argument("--json", action="store_true", help="raw JSON output")
    p.set_defaults(func=cmd_verify_claude_md)

    p = sub.add_parser("webhook", help="manage local/LAN webhook notifications")
    _add_common(p)
    p.add_argument("--add", default=None, metavar="URL",
                   help="register a local/LAN webhook URL")
    p.add_argument("--events", default=None,
                   help="comma-separated events (session_indexed,budget_alert,"
                        "health_alert,watch_new)")
    p.add_argument("--remove", default=None, metavar="URL_OR_ID",
                   help="remove a webhook by URL or id")
    p.add_argument("--list", action="store_true", help="list webhooks (default)")
    p.set_defaults(func=cmd_webhook)

    # --- v0.6.3: Community & Clarity ---
    p = sub.add_parser("tour", help="print the first-run guided tour (plain text)")
    p.set_defaults(func=cmd_tour)

    p = sub.add_parser("plugins", help="discover & install community plugins from the registry")
    _add_common(p)
    p.add_argument("action", nargs="?", default="list",
                   choices=["list", "install", "remove", "info", "update"],
                   help="list (default) | install | remove | info | update")
    p.add_argument("name", nargs="?", default=None,
                   help="plugin name (for install/remove/info)")
    p.add_argument("--yes", action="store_true",
                   help="skip the download confirmation (for CI)")
    p.set_defaults(func=cmd_plugins)

    p = sub.add_parser("search-history", help="show (or clear) your recent searches")
    _add_common(p)
    p.add_argument("--limit", type=int, default=10, help="how many to show (default 10)")
    p.add_argument("--clear", action="store_true", help="delete all history")
    p.add_argument("--json", action="store_true", help="emit raw JSON for scripting")
    p.set_defaults(func=cmd_search_history)

    p = sub.add_parser("github-summary",
                       help="print a Markdown session summary (for GitHub Actions)")
    _add_common(p)
    p.add_argument("--session", default=None, metavar="PATH",
                   help="path to a Claude Code session JSONL")
    p.add_argument("--last", action="store_true",
                   help="use the most recent indexed session")
    p.set_defaults(func=cmd_github_summary)

    p = sub.add_parser("template", help="session starter templates with auto-context")
    _add_common(p)
    p.add_argument("action", nargs="?", default="list",
                   choices=["list", "use", "create"],
                   help="list (default) | use | create")
    p.add_argument("name", nargs="?", default=None, help="template name")
    p.add_argument("--file", default=None, help="value for {file}")
    p.add_argument("--goal", default=None, help="value for {goal}")
    p.add_argument("--error", default=None, help="value for {error}")
    p.add_argument("--project", default=None, help="value for {project}")
    p.add_argument("--feature", default=None, help="value for {feature}")
    p.add_argument("--var", action="append", metavar="KEY=VALUE",
                   help="set any template variable (repeatable)")
    p.add_argument("--no-context", action="store_true",
                   help="don't fill {auto-context} from history")
    p.set_defaults(func=cmd_template)

    # --- v0.7.0: Intelligence Layer ---
    p = sub.add_parser("ai-summary",
                       help="AI summary of a session (opt-in; needs ANTHROPIC_API_KEY)")
    _add_common(p)
    p.add_argument("session_id", nargs="?", default=None)
    p.add_argument("--last", action="store_true", help="use the most recent session")
    p.add_argument("--copy", action="store_true", help="copy the output to the clipboard")
    p.add_argument("--out", default=None, metavar="FILE", help="also write to FILE")
    p.add_argument("--json", action="store_true", help="emit raw JSON")
    p.set_defaults(func=cmd_ai_summary)

    p = sub.add_parser("ai-coach",
                       help="AI coaching report on your recent sessions (opt-in)")
    _add_common(p)
    p.add_argument("-n", type=int, default=20, help="sessions to analyze (default 20)")
    p.add_argument("--copy", action="store_true", help="copy the output to the clipboard")
    p.add_argument("--out", default=None, metavar="FILE", help="also write to FILE")
    p.set_defaults(func=cmd_ai_coach)

    p = sub.add_parser("ai-prompt",
                       help="rewrite a prompt for better Claude Code results (opt-in)")
    _add_common(p)
    p.add_argument("prompt", help="the raw prompt to improve")
    p.add_argument("--copy", action="store_true", help="copy the output to the clipboard")
    p.add_argument("--out", default=None, metavar="FILE", help="also write to FILE")
    p.set_defaults(func=cmd_ai_prompt)

    p = sub.add_parser("similar", help="find sessions semantically similar to one (local TF-IDF)")
    _add_common(p)
    p.add_argument("session_id", nargs="?", default=None)
    p.add_argument("--last", action="store_true", help="use the most recent session")
    p.add_argument("--top", type=int, default=10, help="how many results (default 10)")
    p.add_argument("--json", action="store_true", help="emit raw JSON")
    p.set_defaults(func=cmd_similar)

    p = sub.add_parser("clusters", help="auto-group your sessions into topic clusters (k-means)")
    _add_common(p)
    p.add_argument("--k", type=int, default=8, help="number of clusters (default 8)")
    p.add_argument("--json", action="store_true", help="emit raw JSON")
    p.set_defaults(func=cmd_clusters)

    p = sub.add_parser("watch-session", help="stream a session's events as they're written")
    _add_common(p)
    p.add_argument("session_id", nargs="?", default=None)
    p.add_argument("--last", action="store_true", help="use the most recent session")
    p.set_defaults(func=cmd_watch_session)

    p = sub.add_parser("context-analysis",
                       help="per-turn context-window utilization for a session")
    _add_common(p)
    p.add_argument("session_id", nargs="?", default=None)
    p.add_argument("--last", action="store_true", help="use the most recent session")
    p.add_argument("--json", action="store_true", help="emit raw JSON")
    p.set_defaults(func=cmd_context_analysis)

    p = sub.add_parser("model-stats", help="cost/health/tool-success breakdown by model")
    _add_common(p)
    p.add_argument("--json", action="store_true", help="emit raw JSON")
    p.set_defaults(func=cmd_model_stats)

    p = sub.add_parser("annotations", help="export/import your annotation layer (team sharing)")
    _add_common(p)
    p.add_argument("action", choices=["export", "import"], help="export or import")
    p.add_argument("file", nargs="?", default=None, help="annotations JSON file (for import)")
    p.add_argument("--out", default=None, metavar="FILE", help="output file (for export)")
    p.add_argument("--replace", action="store_true",
                   help="import strategy: upsert newest (default is merge)")
    p.add_argument("--merge", action="store_true", help="import strategy: fill gaps (default)")
    p.set_defaults(func=cmd_annotations)

    p = sub.add_parser("completions", help="print or install shell tab-completions")
    p.add_argument("action", choices=["bash", "zsh", "fish", "install"],
                   help="which shell, or 'install' to auto-detect and write")
    p.add_argument("--shell", default=None, choices=["bash", "zsh", "fish"],
                   help="shell to install for (with 'install')")
    p.set_defaults(func=cmd_completions)

    return ap


def _force_utf8():
    # Windows consoles default to cp1252 and choke on the progress bar / emoji.
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(AttributeError, ValueError):
            stream.reconfigure(encoding="utf-8", errors="replace")


def main(argv=None) -> int:
    _force_utf8()
    ap = build_parser()
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest.run()
    if not getattr(args, "command", None):
        # no subcommand → serve (build index if needed)
        ns = ap.parse_args(["serve"])
        return ns.func(ns)
    return args.func(args)
