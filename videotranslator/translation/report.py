"""Human readable translation statistics."""
from __future__ import annotations
import json
from pathlib import Path
from videotranslator.core.io import atomic_write_text

def save_report(path, stats):
    atomic_write_text(
        Path(path),
        json.dumps({"translation_report":stats}, ensure_ascii=False, indent=2),
    )
