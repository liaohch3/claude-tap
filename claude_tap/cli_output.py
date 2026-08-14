"""Output helpers shared by claude-tap command-line modules."""

from __future__ import annotations

import builtins
import sys
from typing import Any


def print_status(*values: object, **kwargs: Any) -> None:
    """Print operational output to stderr unless a stream is explicit."""
    kwargs.setdefault("file", sys.stderr)
    builtins.print(*values, **kwargs)
