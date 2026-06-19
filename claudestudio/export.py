"""Export a single session to Markdown or a self-contained, shareable HTML file.

Both renderers take the dict returned by `api.get_session` (a session plus its
`timeline`) and return a string — no I/O, no dependencies — so the server, the
CLI, and the self-test all share one faithful representation. The HTML is a
single standalone file (inline CSS, no scripts, no network) you can open
anywhere or attach to an issue.
"""

from __future__ import annotations

import datetime as dt
import html
import json

_ROLE_LABEL = {"user": "You", "assistant": "Claude"}


def _fmt_ts(epoch) -> str:
    try:
        if not epoch:
            return ""
        return dt.datetime.fromtimestamp(float(epoch)).strftime("%Y-%m-%d %H:%M")
    except (ValueError, OSError, TypeError):
        return ""


def _fmt_cost(c) -> str:
    c = float(c or 0)
    if c == 0:
        return "$0"
    if c < 0.01:
        return f"${c:.4f}"
    return f"${c:,.2f}"


def _fmt_int(n) -> str:
    return f"{int(n or 0):,}"


def _is_empty(m: dict) -> bool:
    # tool-result-only user turns carry no prompt text — skip them so the
    # exported artifact reads as a clean conversation, not raw log noise.
    return not m.get("text") and not m.get("thinking") and not m.get("tools")


def _arg_text(inp: dict) -> str:
    parts = []
    for k, v in (inp or {}).items():
        sval = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
        parts.append(f"{k}: {sval}")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------

def to_markdown(session: dict) -> str:
    s = session
    out: list[str] = []
    title = s.get("title") or "Untitled session"
    out.append(f"# {title}")
    out.append("")
    meta = [
        f"**Project:** `{s.get('project','')}`",
        f"**Models:** {', '.join(s.get('models') or []) or '—'}",
        f"**When:** {_fmt_ts(s.get('first_epoch'))} → {_fmt_ts(s.get('last_epoch'))}",
    ]
    if s.get("git_branch"):
        meta.append(f"**Branch:** `{s['git_branch']}`")
    out.append(" · ".join(meta))
    out.append("")
    out.append(
        f"**{_fmt_int(s.get('msg_count'))}** messages · "
        f"**{_fmt_int(s.get('user_msgs'))}** prompts · "
        f"**{_fmt_int(s.get('tool_calls'))}** tool calls · "
        f"**{_fmt_cost(s.get('cost_usd'))}** est. cost"
    )
    out.append("")
    out.append("---")
    out.append("")

    for m in s.get("timeline", []):
        if _is_empty(m):
            continue
        who = _ROLE_LABEL.get(m.get("role"), m.get("role", "?"))
        stamp = _fmt_ts(m.get("epoch"))
        head = f"### {who}"
        if stamp:
            head += f"  ·  _{stamp}_"
        out.append(head)
        out.append("")
        if m.get("thinking"):
            out.append("> **✦ thinking**")
            for line in str(m["thinking"]).splitlines():
                out.append(f"> {line}")
            out.append("")
        if m.get("text"):
            out.append(str(m["text"]))
            out.append("")
        for t in m.get("tools", []):
            flag = "⚠️ error" if t.get("is_error") else "ok"
            out.append(f"**🛠 {t.get('name','?')}** ({flag})")
            arg = _arg_text(t.get("input"))
            if arg:
                out.append("")
                out.append("```")
                out.append(arg)
                out.append("```")
            if t.get("result_preview"):
                out.append("")
                out.append("```text")
                out.append(str(t["result_preview"]))
                out.append("```")
            out.append("")
        out.append("---")
        out.append("")

    out.append(
        f"<sub>Exported from ClaudeStudio · session `{s.get('session_id','')}`</sub>"
    )
    out.append("")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# HTML (single self-contained file)
# ---------------------------------------------------------------------------

_HTML_CSS = """
:root{--bg:#0e0f13;--panel:#15171d;--line:#23262f;--text:#e7e9ee;--text-2:#aab0bd;
--text-3:#727887;--accent:#ff8a5b;--violet:#9a8cff;--mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);
font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
.wrap{max-width:860px;margin:0 auto;padding:40px 22px 80px}
h1{font-size:26px;margin:0 0 10px}
.meta{color:var(--text-2);font-size:13px;margin-bottom:6px}
.meta code{font-family:var(--mono);font-size:12px;color:var(--text)}
.stats{display:flex;flex-wrap:wrap;gap:18px;margin:18px 0 8px;padding:14px 16px;
background:var(--panel);border:1px solid var(--line);border-radius:12px}
.stats .s{display:flex;flex-direction:column}
.stats .v{font-size:18px;font-weight:700}
.stats .v.accent{color:var(--accent)}
.stats .k{font-size:11px;color:var(--text-3);text-transform:uppercase;letter-spacing:.04em}
.turn{margin:22px 0;padding:16px 18px;background:var(--panel);
border:1px solid var(--line);border-radius:14px}
.turn.user{border-left:3px solid var(--violet)}
.turn.assistant{border-left:3px solid var(--accent)}
.who{display:flex;align-items:center;gap:8px;font-weight:700;margin-bottom:8px}
.who .tag{font-weight:500;font-size:11px;color:var(--text-3);
font-family:var(--mono);background:#1b1e26;padding:2px 7px;border-radius:6px}
.text{white-space:pre-wrap;word-break:break-word}
.think{margin:8px 0;padding:10px 12px;border-left:2px solid var(--line);
color:var(--text-2);font-size:13px;white-space:pre-wrap}
.tool{margin:10px 0;border:1px solid var(--line);border-radius:10px;overflow:hidden}
.tool-h{display:flex;align-items:center;gap:8px;padding:8px 12px;background:#1b1e26;
font-family:var(--mono);font-size:12px}
.tool-h .err{color:#ff6b6b;margin-left:auto}
.tool-h .ok{color:#5ec98a;margin-left:auto}
.tool pre{margin:0;padding:10px 12px;white-space:pre-wrap;word-break:break-word;
font-family:var(--mono);font-size:12px;color:var(--text-2)}
.tool pre.result{border-top:1px solid var(--line);color:var(--text-3)}
.foot{margin-top:34px;color:var(--text-3);font-size:12px;text-align:center}
.foot a{color:var(--violet);text-decoration:none}
""".strip()


def _tool_html(t: dict) -> str:
    name = html.escape(str(t.get("name", "?")))
    flag = ('<span class="err">● error</span>' if t.get("is_error")
            else '<span class="ok">● ok</span>')
    parts = [f'<div class="tool"><div class="tool-h"><span>🛠 {name}</span>{flag}</div>']
    arg = _arg_text(t.get("input"))
    if arg:
        parts.append(f"<pre>{html.escape(arg)}</pre>")
    if t.get("result_preview"):
        parts.append(f'<pre class="result">{html.escape(str(t["result_preview"]))}</pre>')
    parts.append("</div>")
    return "".join(parts)


def _turn_html(m: dict) -> str:
    role = m.get("role", "?")
    who = html.escape(_ROLE_LABEL.get(role, role))
    tags = []
    if m.get("model"):
        tags.append(f'<span class="tag">{html.escape(str(m["model"]))}</span>')
    if _fmt_ts(m.get("epoch")):
        tags.append(f'<span class="tag">{_fmt_ts(m.get("epoch"))}</span>')
    parts = [f'<div class="turn {html.escape(role)}">',
             f'<div class="who"><span>{who}</span>{"".join(tags)}</div>']
    if m.get("thinking"):
        parts.append(f'<div class="think">{html.escape(str(m["thinking"]))}</div>')
    if m.get("text"):
        parts.append(f'<div class="text">{html.escape(str(m["text"]))}</div>')
    for t in m.get("tools", []):
        parts.append(_tool_html(t))
    parts.append("</div>")
    return "".join(parts)


def to_html(session: dict) -> str:
    s = session
    title = html.escape(s.get("title") or "Untitled session")
    meta_bits = [
        f'<code>{html.escape(s.get("project",""))}</code>',
        html.escape(", ".join(s.get("models") or []) or "—"),
        f'{_fmt_ts(s.get("first_epoch"))} → {_fmt_ts(s.get("last_epoch"))}',
    ]
    if s.get("git_branch"):
        meta_bits.append(f'branch <code>{html.escape(s["git_branch"])}</code>')
    stats = [
        (_fmt_int(s.get("msg_count")), "messages", ""),
        (_fmt_int(s.get("user_msgs")), "prompts", ""),
        (_fmt_int(s.get("tool_calls")), "tool calls", ""),
        (_fmt_cost(s.get("cost_usd")), "est. cost", "accent"),
    ]
    stat_html = "".join(
        f'<div class="s"><span class="v {cls}">{html.escape(v)}</span>'
        f'<span class="k">{html.escape(k)}</span></div>'
        for v, k, cls in stats
    )
    turns = "".join(_turn_html(m) for m in s.get("timeline", []) if not _is_empty(m))
    sid = html.escape(s.get("session_id", ""))
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>{title} · ClaudeStudio</title>
<style>{_HTML_CSS}</style>
</head>
<body>
<div class="wrap">
<h1>{title}</h1>
<div class="meta">{" · ".join(meta_bits)}</div>
<div class="stats">{stat_html}</div>
{turns}
<div class="foot">Exported from <a href="https://github.com/ingridtoulotte/claudestudio">ClaudeStudio</a> · session <code>{sid}</code></div>
</div>
</body>
</html>
"""


def render(session: dict, fmt: str) -> tuple[str, str]:
    """Return (text, content_type) for fmt in {'md','markdown','html'}."""
    f = (fmt or "md").lower().lstrip(".")
    if f in ("html", "htm"):
        return to_html(session), "text/html; charset=utf-8"
    return to_markdown(session), "text/markdown; charset=utf-8"
