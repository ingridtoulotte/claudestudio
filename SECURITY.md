# Security Policy

## A small attack surface, by design

ClaudeStudio is deliberately boring from a security standpoint:

- **No network egress.** The app makes zero outbound calls. The bundled server
  binds to `127.0.0.1` only — it is never exposed to your LAN or the internet.
- **No dependencies.** Pure Python standard library on the backend, vanilla
  JS/CSS on the frontend. There is no third-party supply chain to compromise —
  which is also why there is **no Dependabot config**: there are no dependencies
  to watch. That is a feature, not an omission.
- **Read-only on your data.** ClaudeStudio only *reads* `~/.claude/projects/*.jsonl`.
  It never writes to, moves, or deletes your session files. Its own state lives in
  a separate SQLite file at `~/.claudestudio/index.db`.
- **No account, no telemetry.** Nothing is collected, transmitted, or phoned home.

## Hardened local server (0.4.0+)

The threat model for a localhost server is a malicious web page open in your
browser that tries to reach `http://127.0.0.1:<port>` (DNS-rebinding / localhost
CSRF). ClaudeStudio defends against it in depth:

- **Host-header validation.** Requests whose `Host` header is not the loopback
  interface (or an explicitly chosen `--host`) are rejected with `421`. A
  rebinding page sending `Host: attacker.example` never reaches the API.
- **Cross-site write protection.** State-mutating requests (favorites, archive,
  tags, saved searches, reindex) require `Sec-Fetch-Site: same-origin` and reject
  a cross-origin `Origin` — so a third-party page cannot change your state.
- **Security response headers** on every response: a strict
  `Content-Security-Policy`, `X-Content-Type-Options: nosniff`,
  `X-Frame-Options: DENY`, and `Referrer-Policy: no-referrer`.
- **`--host` warning.** Binding to anything but loopback prints a clear warning,
  because it exposes your history to the network. The default stays `127.0.0.1`.
- **Contained exports.** `export --out` paths are resolved (collapsing `..`) and
  the server-side download filename is a slug that can never contain a separator.

## Plugin registry & webhooks (0.6.3+)

Two later features can, by explicit user action, touch the network. Both are
hardened:

- **Plugin registry** (`claudestudio plugins …`). The registry JSON and every
  plugin source are fetched over **HTTPS only**, from a **hardcoded host
  allowlist** (`raw.githubusercontent.com`) that is *not* configurable — an
  arbitrary URL in a registry entry is refused. The full URL is shown and must be
  **confirmed** before any download (`--yes` skips this in CI). When a registry
  entry carries a `sha256`, the bytes are **checksum-verified** before anything
  is written to disk; a mismatch aborts. Fetches happen only on an explicit
  `plugins update` / `plugins install`.
- **Webhooks** (`claudestudio webhook …`). Webhook URLs are restricted to
  **loopback and RFC-1918 private ranges** (`is_private` / `is_loopback`),
  enforced both when a webhook is registered and again at send time, so a webhook
  can never be pointed at a public host.

## What's in / out of scope

**In scope:** the local HTTP server (Host/CSRF/CSP, static-path containment), the
SQLite index, plugin loading and the registry's URL/host/checksum gates, and the
webhook URL validation above.

**Out of scope:** the intentional local-only design itself — there is no cloud
component, account, or remote attack surface to report. Binding to a non-loopback
`--host` is an opt-in that prints a warning; exposure that follows from it is the
operator's choice, not a vulnerability.

## Supported versions

ClaudeStudio is pre-1.0 and ships from `main`. Security fixes land on the latest
release line.

| Version | Supported |
|---------|-----------|
| 0.6.x   | ✅        |
| < 0.6   | ❌ — please upgrade |

## Reporting a vulnerability

If you find a security issue — for example a way to make ClaudeStudio reach the
network, escape `127.0.0.1`, write outside its own index file, bypass the plugin
registry's host allowlist or checksum, or point a webhook at a public host —
please report it **privately** rather than opening a public issue.

- **Preferred:** open a [GitHub Security Advisory](https://github.com/ingridtoulotte/claudestudio/security/advisories/new)
  (private vulnerability reporting is enabled on this repository).
- **Or email** `ingridtoulotte@gmail.com` with `SECURITY` in the subject line.

Please include:

- steps to reproduce,
- the release tag or commit you are on (`git rev-parse --short HEAD`),
- your OS and `python --version`.

**Response timeline:** I aim to **acknowledge within 48 hours** and to ship a
patch for a confirmed critical issue **within 14 days**. Thanks for helping keep
the project trustworthy.
