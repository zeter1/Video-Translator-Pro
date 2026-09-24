"""Platform-specific subprocess flags shared by GUI-owned child processes."""

from __future__ import annotations

import os
import subprocess


def no_console_creationflags() -> int:
    """Hide console-subsystem child windows when launched from the Windows GUI."""
    if os.name != "nt":
        return 0
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
