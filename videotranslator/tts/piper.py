"""Offline Piper TTS adapter for installed Russian voices."""
from __future__ import annotations

from pathlib import Path
import importlib.metadata
import importlib.util
import json
import threading
import wave


class PiperVoice:
    name = "piper"

    def __init__(self):
        self._voices = {}
        self._lock = threading.RLock()

    @staticmethod
    def _runtime_installed() -> bool:
        if importlib.util.find_spec("piper") is None:
            return False
        try:
            return bool(importlib.metadata.version("piper-tts"))
        except importlib.metadata.PackageNotFoundError:
            return False

    @staticmethod
    def _valid_config(model: str | Path) -> bool:
        config_path = Path(str(Path(model)) + ".json")
        try:
            if not config_path.is_file() or config_path.stat().st_size <= 1:
                return False
            with config_path.open("r", encoding="utf-8") as stream:
                return isinstance(json.load(stream), dict)
        except (OSError, UnicodeError, ValueError, TypeError):
            return False

    def available(self, model: str | Path | None = None) -> bool:
        if not self._runtime_installed():
            return False
        if model is None:
            return True
        model_path = Path(model)
        return model_path.is_file() and model_path.stat().st_size > 0 and self._valid_config(model_path)

    @staticmethod
    def _model_revision(model: str | Path) -> tuple[int, int, int, int]:
        model_path = Path(model)
        model_stat = model_path.stat()
        config_path = Path(str(model_path) + ".json")
        try:
            config_stat = config_path.stat()
            return model_stat.st_size, model_stat.st_mtime_ns, config_stat.st_size, config_stat.st_mtime_ns
        except OSError:
            return model_stat.st_size, model_stat.st_mtime_ns, 0, 0

    def _load(self, model: str | Path):
        model_path = str(Path(model))
        revision = self._model_revision(model_path)
        with self._lock:
            cached = self._voices.get(model_path)
            if cached is None or cached[0] != revision:
                from piper import PiperVoice as PiperRuntimeVoice
                voice = PiperRuntimeVoice.load(model_path)
                self._voices[model_path] = (revision, voice)
            else:
                voice = cached[1]
            return voice

    def synthesize(self, text: str, model: str | Path, output: str | Path):
        if not self.available(model):
            raise RuntimeError("Piper TTS или выбранный локальный голос не установлен")
        voice = self._load(model)
        output = str(output)
        # Piper writes PCM WAV.  The caller/ffmpeg detects the container from bytes,
        # even if the historical raw TTS path happens to end in .mp3.
        with self._lock, wave.open(output, "wb") as wav_file:
            voice.synthesize_wav(str(text or ""), wav_file)
        return output
