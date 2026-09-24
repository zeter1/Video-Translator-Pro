"""Extended Hybrid AI v9 translation report."""
from __future__ import annotations
import json
from pathlib import Path
from datetime import datetime
from videotranslator.core.io import atomic_write_text

def save_full_report(path, data):
    payload = {
        'schema_version': 1,
        'created_at': datetime.now().astimezone().isoformat(timespec='seconds'),
        **data,
    }
    atomic_write_text(Path(path), json.dumps(payload, ensure_ascii=False, indent=2))
