# Multi-Machine Sync (zero cloud)

Keep your ClaudeStudio index in sync across machines — a work laptop and a
personal desktop, say — without any cloud service. Sync only ever touches
`~/.claudestudio/` (the index + your saved searches, bookmarks, annotations,
budgets). It **never** touches your original `.jsonl` session files.

Two backends, both over tools you already have:

- **git** (default) — version `~/.claudestudio/` and push/pull to any remote git
  can reach: a private GitHub repo, a NAS, another box over SSH. You get history
  and conflict detection for free.
- **rsync** — a plain mirror when you don't want git.

## Commands

```bash
# Push the local index to a remote
claudestudio sync --push --remote git@github.com:you/claudestudio-index.git

# Pull it on the other machine
claudestudio sync --pull --remote git@github.com:you/claudestudio-index.git

# rsync instead of git
claudestudio sync --push --method rsync --remote nas:/backups/claudestudio/

# See exactly what would run, without running it
claudestudio sync --push --remote <url> --dry-run

# Status: last push / pull, bytes, conflict state
claudestudio sync --status
```

`--method auto` (the default) picks **git** when `~/.claudestudio/` is already a
git repo or git is installed, otherwise **rsync**.

## Dry run

`--dry-run` prints the exact command sequence and runs nothing:

```
$ claudestudio sync --push --remote r --dry-run
  dry run · push via git → r
    $ git init
    $ git remote add origin r
    $ git add -A
    $ git commit -m claudestudio sync
    $ git push origin HEAD
  (no commands were executed)
```

## Conflicts

Pulls use `git pull --ff-only`: if the two machines diverged, the sync stops and
reports a conflict rather than guessing. Resolve it in `~/.claudestudio/` with
normal git, then sync again. `--status` shows `conflict: true` until it's clear.

## Notes

- A `git commit` with nothing to commit is treated as success ("nothing new").
- Sync state (last push/pull, bytes, method, remote, conflict) is stored in
  `~/.claudestudio/.claudestudio-sync.json`.
- 100% local: the only network access is the git/rsync transport *you* configure.
