"""Output helpers shared by claude-tap command-line modules."""

from __future__ import annotations

import builtins
import sys
from typing import TextIO


def print_status(
    *values: str | int | float | bool | None,
    sep: str | None = " ",
    end: str | None = "\n",
    file: TextIO | None = None,
    flush: bool = False,
) -> None:
    """Print operational output to stderr unless a stream is explicit."""
    builtins.print(*values, sep=sep, end=end, file=file or sys.stderr, flush=flush)
