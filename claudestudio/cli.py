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
    p.add_argument("--format", choices=["md", "markdown", "html", "json"], default="md")
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
