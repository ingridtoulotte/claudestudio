#!/usr/bin/env python3
"""ClaudeStudio launcher.

Thin wrapper so you can run `python cs.py …` from a checkout without installing.
Equivalent to `python -m claudestudio …`.
"""

import sys

from claudestudio.cli import main

if __name__ == "__main__":
    sys.exit(main())
