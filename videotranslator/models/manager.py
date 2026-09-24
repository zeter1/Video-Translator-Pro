"""UI-friendly local AI inventory (compatibility facade)."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from videotranslator.models.catalog import get_catalog
from videotranslator.models.manager_v8 import RuntimeModelManager


@dataclass
class ModelInfo:
    name: str
    kind: str
    download_mb: float
    installed_mb: float
    description: str
    model_id: str = ""


def list_models():
    result = []
    for item in get_catalog():
        kind = {"translation": "offline translation", "voice": "offline voice", "voice_engine": "offline voice engine"}.get(item.get("type"), item.get("type", "model"))
        result.append(ModelInfo(
            item["name"], kind, item["size_download_mb"], item["size_disk_mb"],
            item.get("quality") or "", item["id"],
        ))
    return result


def is_installed(name: str, root: Path):
    manager = RuntimeModelManager(root)
    for item in get_catalog():
        if item["name"] == name:
            return bool(manager.status(item["id"])["installed"])
    return False


def installation_report(root: Path):
    return RuntimeModelManager(root).get_status()
