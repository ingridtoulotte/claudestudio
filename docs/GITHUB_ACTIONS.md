# ClaudeStudio in GitHub Actions

> ClaudeStudio v0.6.3+ — post a session digest as a PR comment when Claude Code
> runs in CI.

Many teams run Claude Code in CI. ClaudeStudio can turn the resulting session
JSONL into a compact Markdown summary — cost, tokens, tool success rate, health
score, top files changed, first/last prompt — and you can post that as a PR
comment.

## The summary command

```bash
claudestudio github-summary --session path/to/session.jsonl   # explicit file
claudestudio github-summary --last                            # most recent indexed session
```

Both print Markdown to stdout, so you can capture it into `$GITHUB_OUTPUT` or a
comment step. The module (`claudestudio.github_action`) is pure standard library
and makes **no model calls and no network calls** — it only parses the session
file and computes the same deterministic metrics as the rest of the app.

Example output:

```markdown
### 🧠 ClaudeStudio session summary

**Refactor the parser to stream tokens**

| Metric | Value |
| --- | --- |
| Cost | $0.4120 |
| Tokens | 184,230 |
| Messages | 38 |
| Tool calls | 21 |
| Tool success | 95% |
| Health | 82/100 (B) |

**Top files changed**

- `src/parser.py` (6 edits)
- `tests/test_parser.py` (3 edits)

**First prompt:** Refactor the parser to stream tokens instead of buffering …
```

## Reusable workflow

A reusable workflow ships at `.github/workflows/session-summary.yml`. Call it from
your own workflow after a Claude Code step has produced a session JSONL:

```yaml
jobs:
  summarize:
    uses: ingridtoulotte/claudestudio/.github/workflows/session-summary.yml@main
    with:
      session-path: ${{ steps.claude.outputs.session-path }}
    secrets:
      GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

## Posting to a PR comment yourself

If you'd rather wire it up directly:

```yaml
- name: ClaudeStudio summary
  id: summary
  run: |
    pipx run --spec git+https://github.com/ingridtoulotte/claudestudio claudestudio \
      github-summary --session "$SESSION_PATH" > summary.md
    {
      echo 'body<<EOF'
      cat summary.md
      echo EOF
    } >> "$GITHUB_OUTPUT"

- name: Comment on the PR
  uses: actions/github-script@v7
  with:
    script: |
      const fs = require('fs');
      const body = fs.readFileSync('summary.md', 'utf8');
      await github.rest.issues.createComment({
        owner: context.repo.owner,
        repo: context.repo.repo,
        issue_number: context.issue.number,
        body,
      });
```

<!-- screenshot: github_pr_comment.png -->

## Notes

- The summary runs in CI, **not** inside the ClaudeStudio server.
- `--last` requires an index; in CI prefer `--session <path>` with the JSONL the
  Claude Code step emitted.
- A missing or unreadable session degrades to a one-line note, never a hard
  failure — it won't break your build.
