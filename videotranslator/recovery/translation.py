"""Owner module for: translation_segment_key, translation_checkpoint_path, load_translation_checkpoint, save_translation_checkpoint."""

from __future__ import annotations

from pathlib import Path
from datetime import datetime
import hashlib
import json
import os
from videotranslator.core.diagnostics import safe_log_filename
from videotranslator.core.io import atomic_write_json
from videotranslator.core.paths import get_translation_checkpoints_dir


def translation_segment_key(start: float, end: float, source_text: str) -> str:
    payload = f"{float(start):.3f}\n{float(end):.3f}\n{(source_text or '').strip()}"
    return hashlib.sha256(payload.encode("utf-8", errors="replace")).hexdigest()


def translation_checkpoint_path(input_path: str, target_code: str) -> Path:
    absolute = os.path.normcase(os.path.abspath(input_path))
    try:
        stat = os.stat(input_path)
        signature = f"{absolute}\n{stat.st_size}\n{stat.st_mtime_ns}\n{target_code}"
    except OSError:
        signature = f"{absolute}\n{target_code}"
    digest = hashlib.sha256(signature.encode("utf-8", errors="replace")).hexdigest()[:16]
    base = safe_log_filename(Path(input_path).stem, max_len=48)
    target = safe_log_filename(str(target_code).replace("-", "_"), max_len=16)
    return get_translation_checkpoints_dir() / f"{base}_{target}_{digest}.json"


def load_translation_checkpoint(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8-sig") as file:
        data = json.load(file)
    if int(data.get("schema_version", 0)) != 1:
        return {}
    result = {}
    for item in data.get("segments", []):
        key = str(item.get("key") or "")
        translated = str(item.get("translated") or "").strip()
        if key and translated:
            result[key] = translated
    return result


def save_translation_checkpoint(path: Path, input_path: str, source_lang: str,
                                target_code: str, segments: list):
    """Атомарно сохраняет уже готовые сегменты; исходное видео не затрагивается."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        stat = os.stat(input_path)
        input_info = {
            "path": os.path.abspath(input_path),
            "size": stat.st_size,
            "modified_ns": stat.st_mtime_ns,
        }
    except OSError:
        input_info = {"path": os.path.abspath(input_path)}

    items = []
    for seg in segments or []:
        source = (seg.get("source") or "").strip()
        start = float(seg.get("start", 0.0))
        end = float(seg.get("end", 0.0))
        items.append({
            "key": translation_segment_key(start, end, source),
            "start": start,
            "end": end,
            "source": source,
            "translated": (seg.get("translated") or "").strip(),
        })

    data = {
        "schema_version": 1,
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "input": input_info,
        "source_language": source_lang,
        "target_language": target_code,
        "segments": items,
    }
    atomic_write_json(path, data)
