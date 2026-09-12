"""Target-language catalog and voice lookup helpers.

Kept separate so most Codex tasks do not need to read the large language table.
"""
from __future__ import annotations

MODELS_MAP = {
    "tiny  — быстро, низкая точность": "tiny",
    "base  — баланс (рекомендуется)": "base",
    "small — медленнее, лучше качество": "small",
    "medium — высокая точность (GPU желательно)": "medium",
}

TARGET_LANGUAGES = {
    "Английский (США)": {
        "code": "en",
        "gtts": "en",
        "suffix": "_EN",
        "name": "английский",
        "voices": {
            "Мужской — Guy": "en-US-GuyNeural",
            "Женский — Jenny": "en-US-JennyNeural",
            "Женский — Aria": "en-US-AriaNeural",
        },
    },
    "Английский (Великобритания)": {
        "code": "en",
        "gtts": "en",
        "suffix": "_EN_GB",
        "name": "английский (Великобритания)",
        "voices": {
            "Мужской — Ryan": "en-GB-RyanNeural",
            "Женский — Sonia": "en-GB-SoniaNeural",
            "Женский — Libby": "en-GB-LibbyNeural",
        },
    },
    "Испанский": {
        "code": "es",
        "gtts": "es",
        "suffix": "_ES",
        "name": "испанский",
        "voices": {
            "Мужской — Alvaro": "es-ES-AlvaroNeural",
            "Женский — Elvira": "es-ES-ElviraNeural",
        },
    },
    "Немецкий": {
        "code": "de",
        "gtts": "de",
        "suffix": "_DE",
        "name": "немецкий",
        "voices": {
            "Мужской — Conrad": "de-DE-ConradNeural",
            "Женский — Katja": "de-DE-KatjaNeural",
        },
    },
    "Французский": {
        "code": "fr",
        "gtts": "fr",
        "suffix": "_FR",
        "name": "французский",
        "voices": {
            "Мужской — Henri": "fr-FR-HenriNeural",
            "Женский — Denise": "fr-FR-DeniseNeural",
        },
    },
    "Итальянский": {
        "code": "it",
        "gtts": "it",
        "suffix": "_IT",
        "name": "итальянский",
        "voices": {
            "Мужской — Diego": "it-IT-DiegoNeural",
            "Женский — Elsa": "it-IT-ElsaNeural",
        },
    },
    "Польский": {
        "code": "pl",
        "gtts": "pl",
        "suffix": "_PL",
        "name": "польский",
        "voices": {
            "Мужской — Marek": "pl-PL-MarekNeural",
            "Женский — Zofia": "pl-PL-ZofiaNeural",
        },
    },
    "Украинский": {
        "code": "uk",
        "gtts": "uk",
        "suffix": "_UK",
        "name": "украинский",
        "voices": {
            "Мужской — Ostap": "uk-UA-OstapNeural",
            "Женский — Polina": "uk-UA-PolinaNeural",
        },
    },
    "Турецкий": {
        "code": "tr",
        "gtts": "tr",
        "suffix": "_TR",
        "name": "турецкий",
        "voices": {
            "Мужской — Ahmet": "tr-TR-AhmetNeural",
            "Женский — Emel": "tr-TR-EmelNeural",
        },
    },
    "Японский": {
        "code": "ja",
        "gtts": "ja",
        "suffix": "_JA",
        "name": "японский",
        "voices": {
            "Женский — Nanami": "ja-JP-NanamiNeural",
            "Мужской — Keita": "ja-JP-KeitaNeural",
        },
    },
    "Корейский": {
        "code": "ko",
        "gtts": "ko",
        "suffix": "_KO",
        "name": "корейский",
        "voices": {
            "Женский — SunHi": "ko-KR-SunHiNeural",
            "Мужской — InJoon": "ko-KR-InJoonNeural",
        },
    },
    "Китайский": {
        "code": "zh-CN",
        "gtts": "zh-CN",
        "suffix": "_ZH",
        "name": "китайский",
        "voices": {
            "Женский — Xiaoxiao": "zh-CN-XiaoxiaoNeural",
            "Мужской — Yunxi": "zh-CN-YunxiNeural",
        },
    },
    "Русский": {
        "code": "ru",
        "gtts": "ru",
        "suffix": "_RU",
        "name": "русский",
        "voices": {
            "Дмитрий (мужской)": "ru-RU-DmitryNeural",
            "Светлана (женский)": "ru-RU-SvetlanaNeural",
            "Дарья (живой женский)": "ru-RU-DariyaNeural",
        },
    },
}

DEFAULT_TARGET_LANGUAGE = "Русский"

def get_target_language(label: str | None) -> dict:
    base = TARGET_LANGUAGES.get(label or "") or TARGET_LANGUAGES[DEFAULT_TARGET_LANGUAGE]
    info = dict(base)
    info["label"] = label if label in TARGET_LANGUAGES else DEFAULT_TARGET_LANGUAGE
    return info

def get_target_language_by_code(code: str | None) -> str:
    code = (code or "").strip().lower()
    for label, info in TARGET_LANGUAGES.items():
        if str(info.get("code", "")).lower() == code:
            return label
    return DEFAULT_TARGET_LANGUAGE

def get_voice_options(language_label: str | None) -> dict:
    return dict(get_target_language(language_label).get("voices") or {})

def get_language_labels() -> list[str]:
    labels = [label for label in TARGET_LANGUAGES.keys() if label != DEFAULT_TARGET_LANGUAGE]
    return [DEFAULT_TARGET_LANGUAGE] + labels
