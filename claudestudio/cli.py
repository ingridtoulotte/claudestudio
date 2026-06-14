"""Command-line interface for ClaudeStudio."""

from __future__ import annotations

import argparse
import os
import sys
import time

from . import __version__, index, parser as _parser, server, wrapped, fixtures, selftest

BANNER = r"""
   ___ _                _      ___ _            _ _
  / __| |__ _ _  _ __| |___ / __| |_ _  _ __| (_)___
 | (__| / _` | || / _` / -_)\__ \  _| || / _` | / _ \
  \___|_\__,_|\_,_\__,_\___||___/\__|\_,_\__,_|_\___/
  the desktop workspace for Claude Code  ·  v%s
""" % __version__


def _add_common(p):
    p.add_argument("--db", default=index.default_db_path(), help="index database path")
    p.add_argument("--root", default=None, help="Claude projects root to scan")


def _progress(done, total):
    if total:
        bar = int(30 * done / total)
        sys.stdout.write(f"\r  indexing  [{'█'*bar}{'·'*(30-bar)}] {done}/{total}")
        sys.stdout.flush()


def cmd_index(args):
    conn = index.connect(args.db)
    print(BANNER)
    t0 = time.time()
    stats = index.reindex(conn, args.root, force=args.force, progress=_progress)
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
    if not os.path.exists(args.db):
        print("  No index yet — building one first…")
        conn = index.connect(args.db)
        index.reindex(conn, args.root, progress=_progress)
        print()
        conn.close()
    print(BANNER)
    server.serve(args.db, args.root, host=args.host, port=args.port,
                 open_browser=not args.no_browser)
    return 0


def cmd_wrapped(args):
    conn = index.connect(args.db)
    data = wrapped.generate(conn, args.year)
    wrapped.print_text(data)
    conn.close()
    return 0


def cmd_doctor(args):
    print(BANNER)
    root = args.root or _parser.default_projects_root()
    print(f"  projects root : {root}")
    print(f"  exists        : {os.path.isdir(root)}")
    files = list(_parser.iter_session_files(root)) if os.path.isdir(root) else []
    print(f"  session files : {len(files)}")
    print(f"  index db      : {args.db}")
    print(f"  index exists  : {os.path.exists(args.db)}")
    try:
        import sqlite3
        c = sqlite3.connect(":memory:")
        c.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        print("  sqlite FTS5   : available ✓")
    except sqlite3.OperationalError:
        print("  sqlite FTS5   : MISSING ✗ (search will be degraded)")
    print(f"  python        : {sys.version.split()[0]}")
    if os.path.exists(args.db):
        conn = index.connect(args.db)
        s = index.session_summary(conn)
        print(f"  indexed       : {s['sessions']:,} sessions, "
              f"{s['messages']:,} messages")
        conn.close()
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
        try:
            os.remove(demo_db)
        except OSError:
            pass
    conn = index.connect(demo_db)
    stats = index.reindex(conn, demo_root, force=True, progress=_progress)
    print()
    s = index.session_summary(conn)
    print(f"  demo index → {s['sessions']} sessions · {s['messages']:,} messages · ${s['cost_usd']:,.2f}")
    conn.close()
    if args.serve:
        server.serve(demo_db, demo_root, host=args.host, port=args.port,
                     open_browser=not args.no_browser)
    else:
        print(f"\n  Explore it:  claudestudio serve --db \"{demo_db}\" --root \"{demo_root}\"")
    return 0


def build_parser():
    ap = argparse.ArgumentParser(
        prog="claudestudio",
        description="The desktop workspace for Claude Code.",
    )
    ap.add_argument("--version", action="version", version=f"ClaudeStudio {__version__}")
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

    p = sub.add_parser("wrapped", help="print your Claude Wrapped summary")
    _add_common(p)
    p.add_argument("--year", type=int, default=None)
    p.set_defaults(func=cmd_wrapped)

    p = sub.add_parser("doctor", help="diagnose environment & index health")
    _add_common(p)
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("stats", help="print headline numbers")
    _add_common(p)
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("demo", help="generate synthetic data and explore it")
    p.add_argument("--count", type=int, default=48)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--serve", action="store_true")
    p.add_argument("--port", type=int, default=8787)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--no-browser", action="store_true")
    p.set_defaults(func=cmd_demo)

    return ap


def _force_utf8():
    # Windows consoles default to cp1252 and choke on the progress bar / emoji.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


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
