"""Static shareable export — one self-contained ``.html`` file that *is* a session.

Unlike the existing Markdown/HTML export, a **share pack** inlines the full
session data as JSON in a ``<script>`` block and renders a read-only replay view
from the file itself: inline CSS, inline JS, no external resources, no server, no
upload. Send the file, the receiver opens it in any browser, done. The file is
the session.

Local-first to the core: building it never touches the network, and the result
makes zero network calls to render.

Usage::

    from claudestudio.share import build_share_pack
    html = build_share_pack(conn, session_id)            # with annotations
    html = build_share_pack(conn, session_id, include_annotations=False)
"""

from __future__ import annotations

import html as _html
import json

from . import api, index

SHARE_VERSION = "1"
_BANNER = "Opened from share file · not connected to ClaudeStudio"


def build_share_pack(conn, session_id: str, include_annotations: bool = True) -> str:
    """Return a fully self-contained HTML string for one session, or '' if missing.

    The session detail (metadata, timeline, tool calls, diffs, health) is inlined
    as JSON and rendered client-side from the file. When `include_annotations` is
    true the user's personal notes are embedded too (opt-in); otherwise they are
    stripped before serialisation so the pack carries no private notes.
    """
    detail = api.get_session(conn, session_id)
    if detail is None:
        return ""

    payload = {
        "share_version": SHARE_VERSION,
        "session": _trim_for_share(detail),
    }
    if include_annotations:
        payload["annotations"] = index.list_annotations(conn, session_id)
        payload["notes"] = detail.get("notes", "")
    else:
        payload["annotations"] = []
        payload["notes"] = ""

    data_json = json.dumps(payload, default=str, ensure_ascii=False)
    # Never let the inlined data terminate the surrounding <script> element.
    data_json = data_json.replace("</", "<\\/")

    title = detail.get("title") or "Claude Code session"
    health = (detail.get("health") or {})
    grade = health.get("grade", "—")
    cost = detail.get("cost_usd") or 0.0
    dur = _human_duration(detail.get("duration_s") or 0.0)
    return _HTML_TEMPLATE.format(
        title=_html.escape(title),
        grade=_html.escape(str(grade)),
        cost=f"{cost:.2f}",
        duration=_html.escape(dur),
        banner=_BANNER,
        share_version=SHARE_VERSION,
        data=data_json,
        css=_SHARE_CSS,
        js=_SHARE_JS,
    )


def _trim_for_share(detail: dict) -> dict:
    """Keep the fields the share viewer renders; drop heavy/duplicated internals."""
    keep = {
        k: detail.get(k) for k in (
            "session_id", "title", "project_name", "project", "git_branch",
            "primary_model", "models", "first_ts", "last_ts", "duration_s",
            "msg_count", "tool_calls", "input_tokens", "output_tokens",
            "cost_usd", "health",
        )
    }
    timeline = []
    for m in detail.get("timeline", []):
        timeline.append({
            "role": m.get("role"),
            "seq": m.get("seq"),
            "text": m.get("text", ""),
            "thinking": m.get("thinking", ""),
            "model": m.get("model"),
            "tools": [
                {"name": t.get("name"), "is_error": t.get("is_error"),
                 "diff": t.get("diff"), "result_preview": (t.get("result_preview") or "")[:600]}
                for t in m.get("tools", [])
            ],
        })
    keep["timeline"] = timeline
    return keep


def build_for_last_session(conn) -> tuple[str, str]:
    """(session_id, html) for the most recently indexed session, or ('', '')."""
    row = conn.execute(
        "SELECT session_id FROM sessions ORDER BY last_epoch DESC, session_id ASC LIMIT 1"
    ).fetchone()
    if not row:
        return "", ""
    sid = row["session_id"]
    return sid, build_share_pack(conn, sid)


def _human_duration(seconds: float) -> str:
    s = int(max(0, seconds))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    h, rem = divmod(s, 3600)
    return f"{h}h{rem // 60:02d}m"


_SHARE_CSS = """
:root{--cs-bg:#0d0d14;--cs-surface:#16161f;--cs-surface2:#1d1d2a;
--cs-border:#2a2a3a;--cs-text:#e7e9f3;--cs-muted:#9aa0b4;--cs-accent:#9a8cff;
--cs-danger:#ff6b6b;--cs-success:#5ec98a;}
*{box-sizing:border-box}
body{margin:0;background:var(--cs-bg);color:var(--cs-text);
font:15px/1.6 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif}
.wrap{max-width:840px;margin:0 auto;padding:24px 20px 80px}
.banner{background:var(--cs-surface2);border:1px solid var(--cs-accent);
color:var(--cs-accent);border-radius:8px;padding:10px 14px;margin-bottom:20px;
font-size:13px;text-align:center}
h1{font-size:22px;margin:.2em 0}
.meta{color:var(--cs-muted);font-size:13px;margin-bottom:8px}
.chips{display:flex;gap:8px;flex-wrap:wrap;margin:14px 0 24px}
.chip{background:var(--cs-surface);border:1px solid var(--cs-border);
border-radius:20px;padding:4px 12px;font-size:12px;color:var(--cs-muted)}
.chip b{color:var(--cs-text)}
.msg{background:var(--cs-surface);border:1px solid var(--cs-border);
border-radius:10px;padding:12px 14px;margin:10px 0}
.msg.user{border-left:3px solid var(--cs-accent)}
.msg.assistant{border-left:3px solid var(--cs-success)}
.role{font-size:11px;text-transform:uppercase;letter-spacing:.06em;
color:var(--cs-muted);margin-bottom:6px}
.text{white-space:pre-wrap;word-break:break-word}
.think{color:var(--cs-muted);font-style:italic;white-space:pre-wrap;
border-left:2px solid var(--cs-border);padding-left:10px;margin:8px 0}
.tool{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12px;
background:var(--cs-surface2);border-radius:6px;padding:6px 10px;margin:6px 0}
.tool.err{border:1px solid var(--cs-danger)}
.tool .nm{color:var(--cs-accent)}
.diff{white-space:pre;overflow:auto;font-size:11px;margin:6px 0 0}
.diff .add{color:var(--cs-success)} .diff .del{color:var(--cs-danger)}
.note{background:var(--cs-surface2);border-radius:8px;padding:10px 14px;
margin:10px 0;font-size:13px}
footer{color:var(--cs-muted);font-size:12px;text-align:center;margin-top:40px}
"""

_SHARE_JS = """
(function(){
  var el=document.getElementById('cs-share-data');
  var data;
  try{data=JSON.parse(el.textContent);}catch(e){
    document.getElementById('app').textContent='Could not read share data.';return;}
  var s=data.session||{};
  var root=document.getElementById('timeline');
  function esc(t){var d=document.createElement('div');d.textContent=t==null?'':String(t);
    return d.innerHTML;}
  function diffHtml(d){return d.split('\\n').map(function(l){
    var c=l[0]==='+'?'add':(l[0]==='-'?'del':'');
    return '<span class="'+c+'">'+esc(l)+'</span>';}).join('\\n');}
  (s.timeline||[]).forEach(function(m){
    var div=document.createElement('div');div.className='msg '+(m.role||'');
    var h='<div class="role">'+esc(m.role)+(m.model?' · '+esc(m.model):'')+'</div>';
    if(m.thinking)h+='<div class="think">'+esc(m.thinking)+'</div>';
    if(m.text)h+='<div class="text">'+esc(m.text)+'</div>';
    (m.tools||[]).forEach(function(t){
      h+='<div class="tool'+(t.is_error?' err':'')+'"><span class="nm">'+esc(t.name)+
        '</span>'+(t.is_error?' ✗':'')+
        (t.diff?'<div class="diff">'+diffHtml(t.diff)+'</div>':'')+'</div>';});
    div.innerHTML=h;root.appendChild(div);
  });
  if(data.notes){var n=document.createElement('div');n.className='note';
    n.innerHTML='<b>Notes:</b> '+esc(data.notes);root.appendChild(n);}
  (data.annotations||[]).forEach(function(a){var n=document.createElement('div');
    n.className='note';n.innerHTML='<b>Annotation:</b> '+esc(a.note);
    root.appendChild(n);});
})();
"""

_HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="claudestudio-share-version" content="{share_version}">
<title>{title} · ClaudeStudio share</title>
<style>{css}</style>
</head>
<body>
<div class="wrap">
  <div class="banner">📦 {banner}</div>
  <h1>{title}</h1>
  <div class="chips">
    <span class="chip">health <b>{grade}</b></span>
    <span class="chip">cost <b>${cost}</b></span>
    <span class="chip">duration <b>{duration}</b></span>
  </div>
  <div id="app"><div id="timeline"></div></div>
  <footer>Generated by ClaudeStudio · share pack v{share_version} · renders offline</footer>
</div>
<script type="application/json" id="cs-share-data">{data}</script>
<script>{js}</script>
</body>
</html>
"""
