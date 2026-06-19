"""ClaudeStudio — the desktop workspace for Claude Code.

Local-first, zero-dependency engine that indexes, searches, replays, and
analyzes every Claude Code session on your machine.

Public API for other builders — import the parser instead of reverse-engineering
the Claude Code session wire format yourself (see ``docs/FORMAT.md``):

    >>> from claudestudio import parse_session, iter_session_files
    >>> for path in iter_session_files(default_projects_root()):
    ...     session = parse_session(path)
"""

from .parser import (
    Message,
    ParsedSession,
    ToolCall,
    default_projects_root,
    iter_session_files,
    parse_file,
    parse_session,
)

__version__ = "0.2.0"
__all__ = [
    "__version__",
    "ParsedSession",
    "Message",
    "ToolCall",
    "parse_session",
    "parse_file",
    "iter_session_files",
    "default_projects_root",
]
