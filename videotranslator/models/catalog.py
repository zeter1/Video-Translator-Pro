"""Curated local AI catalog used by the runtime model manager.

Only models that are actually wired into the application are listed here.
Sizes are approximate download / installed sizes and are intentionally shown in
UI before any large network operation starts.
"""
from __future__ import annotations

from typing import Iterable

PIPER_VOICE_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main"

# Single source of truth for the managed Marian EN→RU directory. Both the
# installer/integrity manager and runtime adapter import this tuple so their
# definition of a complete model cannot silently drift apart.
MARIAN_MODEL_FILES = (
    "config.json", "generation_config.json", "pytorch_model.bin", "source.spm",
    "target.spm", "tokenizer_config.json", "vocab.json",
)

MODELS = [
    {
        "id": "argos-en-ru",
        "name": "Argos Translate — English → Russian",
        "type": "translation",
        "engine": "argos",
        "source_lang": "en",
        "target_lang": "ru",
        "size_download_mb": 187,
        "size_disk_mb": 500,
        "size_note": "≈187 МБ языковой пакет; зависимости Argos могут добавить ещё ~100–300 МБ",
        "offline": True,
        "quality": "Хорошее / быстрое",
        "recommended": True,
        "license_note": "Open-source; языковой пакет хранится локально",
        "url": "https://argos-net.com/v1/translate-en_ru-1_9.argosmodel",
        "filename": "translate-en_ru-1_9.argosmodel",
    },
    {
        "id": "marian-en-ru",
        "name": "MarianMT OPUS — English → Russian",
        "type": "translation",
        "engine": "marian",
        "source_lang": "en",
        "target_lang": "ru",
        "size_download_mb": 315,
        "size_disk_mb": 380,
        "size_note": "≈315 МБ модель; PyTorch уже нужен Whisper и обычно повторно не скачивается",
        "offline": True,
        "quality": "Очень хорошее EN→RU",
        "recommended": True,
        "license_note": "Apache-2.0 (модель Helsinki-NLP)",
        "model_repo": "Helsinki-NLP/opus-mt-en-ru",
    },
    {
        "id": "piper-engine",
        "name": "Piper TTS 1.8 — локальный движок",
        "type": "voice_engine",
        "engine": "piper",
        "size_download_mb": 34.1,
        "size_disk_mb": 85,
        "offline": True,
        "quality": "Быстрый локальный TTS",
        "recommended": True,
        "license_note": "GPL-3.0 движок; голоса имеют свои лицензии",
        "pip": "piper-tts==1.8.0",
    },
]

# The four maintained Russian medium Piper voices are all ~63.3 MB including
# their tiny JSON config.  SHA256 values are for the ONNX model files.
_PIPER_RU = [
    ("dmitri", "Дмитрий", "f073356ebc4bd0f80c5af58df2953a5988bd5bdab1eb38635ce960b071fbefcb"),
    ("denis", "Денис", "15fab56e11a097858ee115545d0f697fc2a316c41a291a5362349fb870411b0a"),
    ("irina", "Ирина", "8ff38212d23da300bbe3705c645e6e5b9475f0bfde01558eb17813e22acaaaaa"),
    ("ruslan", "Руслан", "72a5f88e0b20928064eb45d88e1daa21f8af62d18613580d32cbb4aed48dcf7f"),
]

for slug, display, sha256 in _PIPER_RU:
    filename = f"ru_RU-{slug}-medium.onnx"
    rel = f"ru/ru_RU/{slug}/medium"
    MODELS.append({
        "id": f"piper-{slug}-ru",
        "name": f"Piper {display} — русский medium",
        "type": "voice",
        "engine": "piper",
        "voice_code": f"ru_RU-{slug}-medium",
        "language": "ru",
        "size_download_mb": 63.3,
        "size_disk_mb": 64,
        "size_note": "63.3 МБ голос; +34.1 МБ Piper Engine при первой установке",
        "offline": True,
        "quality": "Хорошее локальное нейроголосовое",
        "recommended": slug in {"dmitri", "irina"},
        "license_note": "MIT (rhasspy/piper-voices)",
        "filename": filename,
        "config_filename": f"{filename}.json",
        "url": f"{PIPER_VOICE_BASE}/{rel}/{filename}?download=true",
        "config_url": f"{PIPER_VOICE_BASE}/{rel}/{filename}.json?download=true",
        "sha256": sha256,
    })


def get_catalog() -> list[dict]:
    return [dict(item) for item in MODELS]


def get_model(model_id: str) -> dict:
    for item in MODELS:
        if item["id"] == model_id:
            return dict(item)
    raise KeyError(model_id)


def iter_piper_voices(language: str = "ru") -> Iterable[dict]:
    for item in MODELS:
        if item.get("type") == "voice" and item.get("language") == language:
            yield dict(item)


def get_recommended_for_ram(ram_gb: float):
    if ram_gb < 8:
        return ["argos-en-ru", "piper-engine", "piper-dmitri-ru"]
    return ["argos-en-ru", "marian-en-ru", "piper-engine", "piper-dmitri-ru"]
