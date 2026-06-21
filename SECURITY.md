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

## Supported versions

ClaudeStudio is pre-1.0 and ships from `main`. Security fixes land on the latest
release line.

| Version | Supported |
|---------|-----------|
| 0.4.x   | ✅        |
| < 0.4   | ❌ — please upgrade |

## Reporting a vulnerability

If you find a security issue — for example a way to make ClaudeStudio reach the
network, escape `127.0.0.1`, or write outside its own index file — please report
it **privately** rather than opening a public issue.

- **Preferred:** open a [GitHub Security Advisory](https://github.com/ingridtoulotte/claudestudio/security/advisories/new).
- **Or email** `ingridtoulotte@gmail.com` with `SECURITY` in the subject line.

Please include:

- steps to reproduce,
- the release tag or commit you are on (`git rev-parse --short HEAD`),
- your OS and `python --version`.

I aim to acknowledge reports within a few days. Thanks for helping keep the
project trustworthy.
