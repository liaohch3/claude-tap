"""Subprocess helpers shared by CLI background tasks."""

from __future__ import annotations

import subprocess
import sys
from typing import Any

_CREATE_NO_WINDOW = 0x08000000
_CREATE_NEW_PROCESS_GROUP = 0x00000200
_STARTF_USESHOWWINDOW = 0x00000001
_SW_HIDE = 0


def windows_no_console_subprocess_kwargs() -> dict[str, Any]:
    """Return Windows-only Popen kwargs for hidden background processes."""
    if sys.platform != "win32":
        return {}

    kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", _CREATE_NO_WINDOW)
        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", _CREATE_NEW_PROCESS_GROUP),
    }
    startupinfo_cls = getattr(subprocess, "STARTUPINFO", None)
    if startupinfo_cls is not None:
        startupinfo = startupinfo_cls()
        startupinfo.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", _STARTF_USESHOWWINDOW)
        startupinfo.wShowWindow = getattr(subprocess, "SW_HIDE", _SW_HIDE)
        kwargs["startupinfo"] = startupinfo
    return kwargs
