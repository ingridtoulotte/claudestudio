# Shell completions

First-class tab completion for **bash**, **zsh** and **fish** — one command,
permanent. Pure shell, no dependencies.

## Print a script

```console
$ claudestudio completions bash   # → ~/.bash_completion.d/claudestudio or eval in .bashrc
$ claudestudio completions zsh    # → a _claudestudio function for your $fpath
$ claudestudio completions fish   # → ~/.config/fish/completions/claudestudio.fish
```

## Install automatically

```console
$ claudestudio completions install
  ✓ installed bash completions to ~/.bash_completion.d/claudestudio (1234 bytes)
```

`install` auto-detects your shell (override with `--shell bash|zsh|fish`) and writes
the file to the conventional location.

## What's completed

- Every sub-command and its flags.
- Dynamic `<session_id>` completion — reads ids from `claudestudio list --json`
  (capped at the first 200 rows so it stays fast).

<!-- TODO screenshot: docs/screenshots/v070_completions.png -->
