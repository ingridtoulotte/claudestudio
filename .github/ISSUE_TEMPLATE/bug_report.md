---
name: Bug report
about: Something did not work the way you expected
title: "[bug] "
labels: bug
---

**What happened**
A clear description of the bug.

**Steps to reproduce**
1.
2.
3.

**Expected**
What you expected to happen instead.

**Environment**
- OS:
- `python --version`:
- Release tag or commit (`git rev-parse --short HEAD`):

**Self-test**
Does `python -m claudestudio --selftest` print `ALLPASS`?  yes / no

**Notes**
ClaudeStudio is local-only — please do not paste real session contents if they
are sensitive. Repros against `python -m claudestudio demo --serve` (synthetic
data) are ideal.
