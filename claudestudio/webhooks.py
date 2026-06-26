"""Local/LAN webhook notifications (v0.6.2 — the "Insight Engine").

A configurable outbound webhook system that POSTs a small JSON payload to a
**local or RFC-1918 LAN** URL when something noteworthy happens — a new session
is indexed, a budget threshold is crossed, a session lands a low health score.
Useful for piping alerts into a local Slack bot, Home Assistant, or a shell
script.

Locality is enforced: a URL whose host is not loopback or a private-range IP
(``127.0.0.0/8``, ``10/8``, ``172.16/12``, ``192.168/16``, or ``localhost``) is
rejected outright, so configuring a webhook can never exfiltrate data to the
public internet. Webhook config lives in the ``preferences`` table (user state,
survives reindexing). Zero third-party dependencies — ``urllib`` does the POST.
"""

from __future__ import annotations

import ipaddress
import json
import urllib.error
import urllib.request
import uuid
from urllib.parse import urlsplit

from . import index

PREF_KEY = "webhooks"
VALID_EVENTS = ("session_indexed", "budget_alert", "health_alert", "watch_new")


def is_local_url(url: str) -> bool:
    """True iff ``url`` is http(s) to a loopback/RFC-1918/``localhost`` host.

    Deterministic and offline: a bare hostname other than ``localhost`` cannot be
    proven private without DNS, so it is rejected rather than resolved. This is
    the gate that keeps webhook data on the local machine / LAN.
    """
    try:
        parts = urlsplit(str(url))
    except ValueError:
        return False
    if parts.scheme not in ("http", "https"):
        return False
    host = (parts.hostname or "").strip().lower()
    if not host:
        return False
    if host == "localhost":
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private


def _load(conn) -> list[dict]:
    raw = index.get_preference(conn, PREF_KEY, "[]")
    try:
        data = json.loads(raw or "[]")
    except (ValueError, TypeError):
        return []
    return [h for h in data if isinstance(h, dict)] if isinstance(data, list) else []


def _save(conn, hooks: list[dict]) -> None:
    index.set_preference(conn, PREF_KEY, json.dumps(hooks))


def list_webhooks(conn) -> list[dict]:
    return _load(conn)


def _clean_events(events) -> list[str]:
    if isinstance(events, str):
        events = [e.strip() for e in events.split(",")]
    out = [e for e in (events or []) if e in VALID_EVENTS]
    return out or list(VALID_EVENTS)


def add_webhook(conn, url: str, events=None) -> dict:
    """Register a webhook. Raises ``ValueError`` if the URL is not local/LAN."""
    if not is_local_url(url):
        raise ValueError(
            f"refusing non-local webhook URL {url!r} — only loopback / RFC-1918 "
            f"hosts (127.x, 10.x, 172.16-31.x, 192.168.x, localhost) are allowed"
        )
    hooks = _load(conn)
    for h in hooks:
        if h.get("url") == url:  # idempotent: update its event set
            h["events"] = _clean_events(events)
            _save(conn, hooks)
            return h
    hook = {"id": uuid.uuid4().hex, "url": str(url), "events": _clean_events(events)}
    hooks.append(hook)
    _save(conn, hooks)
    return hook


def remove_webhook(conn, webhook_id: str) -> dict:
    hooks = _load(conn)
    kept = [h for h in hooks if h.get("id") != webhook_id and h.get("url") != webhook_id]
    _save(conn, kept)
    return {"removed": len(kept) < len(hooks), "id": webhook_id}


def build_payload(event: str, data: dict | None = None) -> dict:
    """The JSON body POSTed to subscribers. Stable shape across every event."""
    return {
        "source": "claudestudio",
        "event": str(event),
        "data": data or {},
    }


def _post(url: str, payload: dict, timeout: float = 2.0) -> dict:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json", "User-Agent": "ClaudeStudio"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — host is gated local
            return {"url": url, "ok": True, "status": getattr(resp, "status", 200)}
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return {"url": url, "ok": False, "error": str(exc)}


def dispatch(conn, event: str, data: dict | None = None, *, timeout: float = 2.0) -> list[dict]:
    """POST the event to every subscribed (and still-local) webhook.

    Never raises — an unreachable endpoint is reported as ``ok: False`` so a dead
    listener can't break indexing. Re-validates locality at send time in case a
    stored URL was tampered with.
    """
    payload = build_payload(event, data)
    results: list[dict] = []
    for h in _load(conn):
        if event not in h.get("events", VALID_EVENTS):
            continue
        url = h.get("url", "")
        if not is_local_url(url):
            results.append({"url": url, "ok": False, "error": "non-local url blocked"})
            continue
        results.append(_post(url, payload, timeout=timeout))
    return results
