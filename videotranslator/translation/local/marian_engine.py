"""Optional local MarianMT adapter using a pre-downloaded model directory."""
from __future__ import annotations

from pathlib import Path
import json

from videotranslator.models.catalog import MARIAN_MODEL_FILES


class MarianEngine:
    name = "marian"

    def __init__(self, model_path: str | Path | None = None):
        self.model_path = Path(model_path) if model_path else None
        self._tokenizer = None
        self._model = None

    REQUIRED_FILES = MARIAN_MODEL_FILES

    @staticmethod
    def _valid_json(path: Path) -> bool:
        try:
            if not path.is_file() or path.stat().st_size <= 1:
                return False
            with path.open("r", encoding="utf-8") as stream:
                return isinstance(json.load(stream), dict)
        except (OSError, UnicodeError, ValueError, TypeError):
            return False

    def available(self, source_lang: str | None = None, target_lang: str | None = None) -> bool:
        if source_lang and source_lang.split("-")[0] != "en":
            return False
        if target_lang and target_lang.split("-")[0] != "ru":
            return False
        if not self.model_path:
            return False
        for name in self.REQUIRED_FILES:
            path = self.model_path / name
            if not path.is_file() or path.stat().st_size <= 0:
                return False
            if name.endswith(".json") and not self._valid_json(path):
                return False
        return True

    def _load(self):
        if self._model is not None and self._tokenizer is not None:
            return
        if not self.available("en", "ru"):
            raise RuntimeError("MarianMT EN→RU не установлен")
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
        self._tokenizer = AutoTokenizer.from_pretrained(str(self.model_path), local_files_only=True)
        self._model = AutoModelForSeq2SeqLM.from_pretrained(str(self.model_path), local_files_only=True)
        try:
            self._model.eval()
        except Exception:
            pass

    def translate(self, text: str, source_lang: str, target_lang: str) -> str:
        return self.translate_many([text], source_lang, target_lang)[0]

    def translate_many(self, texts: list[str], source_lang: str, target_lang: str) -> list[str]:
        if not self.available(source_lang, target_lang):
            raise RuntimeError(f"MarianMT не поддерживает {source_lang}→{target_lang}")
        values = [str(text or "").strip() for text in texts]
        if not values:
            return []
        self._load()
        encoded = self._tokenizer(
            values,
            return_tensors="pt",
            padding=True,
            truncation=True,
            max_length=512,
        )
        try:
            import torch
            context = torch.inference_mode()
        except Exception:
            from contextlib import nullcontext
            context = nullcontext()
        with context:
            generated = self._model.generate(**encoded, max_new_tokens=512, num_beams=4)
        if hasattr(self._tokenizer, "batch_decode"):
            result = self._tokenizer.batch_decode(generated, skip_special_tokens=True)
        else:
            result = [self._tokenizer.decode(row, skip_special_tokens=True) for row in generated]
        result = [str(value or "").strip() for value in result]
        if len(result) != len(values):
            raise RuntimeError(
                f"MarianMT вернул {len(result)} переводов для {len(values)} входных сегментов"
            )
        return result
