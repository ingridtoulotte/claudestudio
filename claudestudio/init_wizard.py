"""Zero-friction onboarding wizard (Feature 2.8, v0.6.0).

``claudestudio init`` walks a new user from "just installed" to "fully wired" in
a single terminal flow — no curses, just stdin/stdout, so it works in every shell
on every OS. It:

  1. checks whether the index already exists (a ``doctor`` probe),
  2. offers to install the ``SessionEnd`` hook so the index stays fresh,
  3. offers to drop a ``claudestudio watch`` helper script for live updates,
  4. asks for an optional monthly budget ceiling and saves it,
  5. offers to run the self-test inline,
  6. prints a personalised "you're set up" summary,
  7. optionally opens the app.

``--yes`` accepts every default non-interactively. The whole flow is a state
machine driven through injectable I/O + action callables, so the self-test runs
it end-to-end against mock stdin without writing to the real environment.
"""

from __future__ import annotations

import os


class WizardActions:
    """The side-effecting operations the wizard performs, behind one seam.

    The defaults wire to the real modules; the self-test passes a fake subclass to
    drive the state machine without touching the user's settings or filesystem.
    """

    def __init__(self, db_path: str, root=None):
        self.db_path = db_path
        self.root = root

    def is_indexed(self) -> bool:
        return os.path.exists(self.db_path)

    def hook_installed(self) -> bool:
        from . import hook
        return hook.hook_status(db_path=self.db_path)["installed"]

    def install_hook(self) -> dict:
        from . import hook
        return hook.install_hook()

    def write_watch_script(self) -> str:
        """Drop a tiny launcher next to the index so `watch` is one command away."""
        target_dir = os.path.dirname(os.path.abspath(self.db_path))
        os.makedirs(target_dir, exist_ok=True)
        if os.name == "nt":
            path = os.path.join(target_dir, "claudestudio-watch.cmd")
            body = "@echo off\r\nclaudestudio watch\r\n"
        else:
            path = os.path.join(target_dir, "claudestudio-watch.sh")
            body = "#!/bin/sh\nexec claudestudio watch\n"
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body)
        if os.name != "nt":
            with __import__("contextlib").suppress(OSError):
                os.chmod(path, 0o755)
        return path

    def set_budget(self, amount: float, period: str = "monthly") -> dict:
        from . import budget, index
        conn = index.connect(self.db_path)
        try:
            return budget.set_budget(conn, period, amount)
        finally:
            conn.close()

    def run_selftest(self) -> int:
        from . import selftest
        return selftest.run()

    def open_app(self) -> None:  # pragma: no cover - real launch, never in tests
        from . import server
        server._open_app("http://127.0.0.1:8787/")


def _truthy(answer: str, default: bool) -> bool:
    a = (answer or "").strip().lower()
    if not a:
        return default
    return a in ("y", "yes", "1", "true")


def run_wizard(actions: WizardActions, *, assume_yes: bool = False,
               inputs=None, out=None) -> dict:
    """Drive the onboarding flow. Returns a state dict of every decision + result.

    `inputs` is an iterator of answer strings (ignored when `assume_yes`); `out`
    is a list that receives each printed line (defaults to real stdout). The
    returned ``state`` is what the self-test asserts against.
    """
    feed = iter(inputs or [])
    lines: list = [] if out is None else out

    def emit(msg=""):
        if out is None:
            print(msg)
        else:
            lines.append(msg)

    def ask(prompt: str, default: bool) -> bool:
        suffix = " [Y/n] " if default else " [y/N] "
        emit(prompt + suffix)
        if assume_yes:
            return default
        try:
            return _truthy(next(feed), default)
        except StopIteration:
            return default

    def ask_value(prompt: str, default: str = "") -> str:
        emit(prompt)
        if assume_yes:
            return default
        try:
            return (next(feed) or "").strip()
        except StopIteration:
            return default

    state: dict = {"steps": []}

    emit("ClaudeStudio setup")
    emit("==================")

    # 1. indexed?
    indexed = actions.is_indexed()
    state["indexed"] = indexed
    state["steps"].append("doctor")
    emit(f"  index present : {indexed}")

    # 2. hook
    if actions.hook_installed():
        state["hook"] = "already"
        emit("  SessionEnd hook already installed ✓")
    elif ask("Install the SessionEnd hook so the index stays fresh?", True):
        actions.install_hook()
        state["hook"] = "installed"
        emit("  ✓ hook installed")
    else:
        state["hook"] = "skipped"
        emit("  hook skipped")
    state["steps"].append("hook")

    # 3. watch helper
    if ask("Create a `claudestudio watch` helper script for live updates?", True):
        path = actions.write_watch_script()
        state["watch_script"] = path
        emit(f"  ✓ watch helper → {path}")
    else:
        state["watch_script"] = None
        emit("  watch helper skipped")
    state["steps"].append("watch")

    # 4. budget
    raw = ask_value("Monthly budget ceiling in USD? (blank to skip, e.g. 50)", "")
    budget_set = None
    if raw:
        try:
            budget_set = float(raw)
            actions.set_budget(budget_set, "monthly")
            emit(f"  ✓ budget set: ${budget_set:,.2f} / monthly")
        except (ValueError, TypeError):
            emit("  (couldn't read a number — skipping budget)")
            budget_set = None
    else:
        emit("  budget skipped")
    state["budget"] = budget_set
    state["steps"].append("budget")

    # 5. self-test
    if ask("Run a quick self-test now?", True):
        rc = actions.run_selftest()
        state["selftest_rc"] = rc
        emit("  ✓ self-test passed" if rc == 0 else "  ⚠ self-test reported failures")
    else:
        state["selftest_rc"] = None
        emit("  self-test skipped")
    state["steps"].append("selftest")

    # 6. summary
    emit("")
    emit("You're set up! Next:")
    emit("  • claudestudio serve     — open the app")
    emit("  • claudestudio ask \"what should I reopen next?\"")
    if state["hook"] in ("installed", "already"):
        emit("  • the index now refreshes automatically after each Claude Code session")
    state["steps"].append("summary")

    # 7. open
    if ask("Open the app now?", False):
        state["opened"] = True
        actions.open_app()
    else:
        state["opened"] = False
    state["steps"].append("open")

    return state
