"""Community plugin registry (Feature 2, v0.6.3).

The plugin *system* (v0.6.1) lets users drop a ``.py`` file into
``~/.claudestudio/plugins/``. The *registry* lets them discover and install
curated community plugins without copying files by hand.

Security model (also documented in ``docs/PLUGIN_REGISTRY.md``):

* The registry JSON and every plugin source are fetched over **HTTPS only**.
* Fetches are restricted to a **hardcoded host allowlist** (``raw.githubusercontent.com``)
  — arbitrary URLs are refused, even if they appear in a registry entry.
* The user is shown the full URL and must **confirm** before any download
  (``--yes`` skips this for CI).
* If a registry entry carries a ``sha256``, the downloaded bytes are
  **checksum-verified** before anything is written to disk; a mismatch aborts.

Network only happens on an explicit, user-initiated ``plugins update``/``install``.
The fetcher is injectable so the self-test never touches the network.
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.request
from urllib.parse import urlparse

# Where the curated registry lives. Hardcoded — not user-configurable — so a
# malicious config can't redirect installs to an attacker-controlled host.
REGISTRY_URL = (
    "https://raw.githubusercontent.com/ingridtoulotte/claudestudio/main/registry/plugins.json"
)
# The ONLY host plugin content may be fetched from. Enforced on every URL.
ALLOWED_HOSTS = frozenset({"raw.githubusercontent.com"})

_FETCH_TIMEOUT = 15
_MAX_BYTES = 1_000_000  # a plugin is a small .py file; cap the download hard


class RegistryError(Exception):
    """A registry operation failed (bad URL, checksum mismatch, network, …)."""


def _safe_name(name: str) -> str:
    """Return `name` iff it is a bare plugin name; else raise.

    The registry JSON is fetched from an allowlisted host but its *contents* are
    not signed, so a hostile entry could carry ``name: "../../evil"``. Since the
    name becomes a filename (`<name>.py`) under the plugins dir, reject anything
    with a path separator, a parent ref, a leading dot, or other surprises —
    defence in depth so an install can never write outside the plugins dir.
    """
    n = str(name or "").strip()
    if (not n or "/" in n or "\\" in n or n.startswith(".")
            or os.path.basename(n) != n or n != os.path.normpath(n)):
        raise RegistryError(f"unsafe plugin name: {name!r}")
    return n


def cache_path() -> str:
    return os.path.join(os.path.expanduser("~"), ".claudestudio", "registry_cache.json")


def plugins_dir() -> str:
    from . import plugin_loader
    return plugin_loader.default_plugins_dir()


# ---------------------------------------------------------------------------
# URL safety — the heart of the security model
# ---------------------------------------------------------------------------

def validate_url(url: str) -> str:
    """Return `url` iff it is HTTPS and on the host allowlist; else raise.

    No redirects are followed by the default fetcher (``urllib`` does follow
    redirects, so we also re-validate the *final* URL after a fetch is not
    possible here — instead we forbid redirects by using a plain opener and
    checking the response URL in :func:`_default_fetch`)."""
    try:
        p = urlparse(str(url))
    except (ValueError, TypeError) as exc:
        raise RegistryError(f"invalid URL: {url!r}") from exc
    if p.scheme != "https":
        raise RegistryError(f"refusing non-HTTPS URL: {url!r}")
    if (p.hostname or "").lower() not in ALLOWED_HOSTS:
        raise RegistryError(
            f"refusing URL outside the allowlist {sorted(ALLOWED_HOSTS)}: {url!r}")
    return str(url)


def _default_fetch(url: str) -> bytes:
    validate_url(url)
    req = urllib.request.Request(url, headers={"User-Agent": "ClaudeStudio-registry"})
    with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT) as resp:  # noqa: S310 — validated https
        # Re-validate the *final* URL: a redirect could have moved us off-host.
        final = getattr(resp, "url", url)
        validate_url(final)
        return resp.read(_MAX_BYTES + 1)


# ---------------------------------------------------------------------------
# registry fetch / cache
# ---------------------------------------------------------------------------

def fetch_registry(*, fetcher=None, url: str | None = None) -> dict:
    """Download + cache the registry JSON. `fetcher(url)->bytes` is injectable."""
    fetch = fetcher or _default_fetch
    src = validate_url(url or REGISTRY_URL)
    try:
        raw = fetch(src)
    except RegistryError:
        raise
    except OSError as exc:  # URLError, timeouts, DNS, etc. — surface as RegistryError
        raise RegistryError(f"could not fetch the registry: {exc}") from exc
    if len(raw) > _MAX_BYTES:
        raise RegistryError("registry JSON exceeds the size cap")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise RegistryError(f"registry JSON is not valid: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("plugins"), list):
        raise RegistryError("registry JSON missing a 'plugins' list")
    _write_cache(data)
    return data


def _write_cache(data: dict) -> None:
    try:
        os.makedirs(os.path.dirname(cache_path()), exist_ok=True)
        with open(cache_path(), "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
    except OSError:
        pass  # caching is best-effort; never fail an install over it


def cached_registry() -> dict:
    """The last cached registry, or an empty registry when nothing is cached."""
    try:
        with open(cache_path(), encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and isinstance(data.get("plugins"), list):
            return data
    except (OSError, ValueError):
        pass
    return {"version": 1, "plugins": []}


def load_registry(*, fetcher=None, refresh=False) -> dict:
    """Cached registry, fetching once if absent (or when `refresh=True`)."""
    if not refresh:
        cached = cached_registry()
        if cached.get("plugins"):
            return cached
    try:
        return fetch_registry(fetcher=fetcher)
    except RegistryError:
        return cached_registry()


# ---------------------------------------------------------------------------
# installed-state + listing
# ---------------------------------------------------------------------------

def installed_names(pdir: str | None = None) -> set:
    d = pdir or plugins_dir()
    if not os.path.isdir(d):
        return set()
    return {n[:-3] for n in os.listdir(d) if n.endswith(".py") and not n.startswith("_")}


def _find(registry: dict, name: str) -> dict | None:
    for p in registry.get("plugins", []):
        if isinstance(p, dict) and p.get("name") == name:
            return p
    return None


def list_plugins(registry: dict | None = None, *, pdir: str | None = None,
                 fetcher=None) -> dict:
    """Registry plugins annotated with installed status."""
    reg = registry if registry is not None else load_registry(fetcher=fetcher)
    inst = installed_names(pdir)
    out = []
    for p in reg.get("plugins", []):
        if not isinstance(p, dict):
            continue
        out.append({
            "name": p.get("name"),
            "description": p.get("description", ""),
            "author": p.get("author", "community"),
            "version": p.get("version", ""),
            "tags": p.get("tags", []),
            "url": p.get("url", ""),
            "installed": p.get("name") in inst,
        })
    return {"version": reg.get("version", 1), "plugins": out}


def plugin_info(name: str, registry: dict | None = None, *, pdir: str | None = None,
                fetcher=None) -> dict:
    reg = registry if registry is not None else load_registry(fetcher=fetcher)
    p = _find(reg, name)
    if p is None:
        return {"error": f"no plugin named {name!r} in the registry"}
    info = dict(p)
    info["installed"] = name in installed_names(pdir)
    return info


# ---------------------------------------------------------------------------
# install / remove
# ---------------------------------------------------------------------------

def _verify_checksum(entry: dict, raw: bytes) -> None:
    expected = (entry.get("sha256") or "").strip().lower()
    if not expected:
        return  # no checksum recorded — nothing to verify against
    got = hashlib.sha256(raw).hexdigest()
    if got != expected:
        raise RegistryError(
            f"checksum mismatch for {entry.get('name')!r}: "
            f"expected {expected[:12]}…, got {got[:12]}…")


def install_plugin(name: str, *, registry: dict | None = None, fetcher=None,
                   yes: bool = False, confirm=None, pdir: str | None = None,
                   overwrite: bool = False) -> dict:
    """Download a registry plugin into the plugins dir, with all safety checks.

    Flow: locate entry → validate its URL (HTTPS + allowlist) → unless `yes`,
    ask `confirm(url)` (or report ``confirm_required`` when no callback is given)
    → fetch → optional SHA-256 verify → write. A duplicate install is refused
    unless `overwrite=True`.
    """
    reg = registry if registry is not None else load_registry(fetcher=fetcher)
    entry = _find(reg, name)
    if entry is None:
        raise RegistryError(f"no plugin named {name!r} in the registry")
    safe = _safe_name(entry.get("name") or name)
    url = validate_url(entry.get("url") or "")

    d = pdir or plugins_dir()
    dest = os.path.join(d, safe + ".py")
    if os.path.exists(dest) and not overwrite:
        return {"status": "already_installed", "name": name, "path": dest}

    if not yes:
        if confirm is None:
            return {"status": "confirm_required", "name": name, "url": url}
        if not confirm(url):
            return {"status": "cancelled", "name": name, "url": url}

    fetch = fetcher or _default_fetch
    try:
        raw = fetch(url)
    except RegistryError:
        raise
    except OSError as exc:
        raise RegistryError(f"could not download {name!r}: {exc}") from exc
    if len(raw) > _MAX_BYTES:
        raise RegistryError("plugin source exceeds the size cap")
    _verify_checksum(entry, raw)

    os.makedirs(d, exist_ok=True)
    with open(dest, "wb") as fh:
        fh.write(raw)
    return {"status": "installed", "name": name, "path": dest,
            "bytes": len(raw), "verified": bool(entry.get("sha256"))}


def remove_plugin(name: str, *, pdir: str | None = None) -> dict:
    safe = _safe_name(name)
    d = pdir or plugins_dir()
    dest = os.path.join(d, safe + ".py")
    if not os.path.isfile(dest):
        return {"status": "not_installed", "name": name}
    try:
        os.remove(dest)
    except OSError as exc:
        raise RegistryError(f"could not remove {name!r}: {exc}") from exc
    return {"status": "removed", "name": name, "path": dest}
