"""Local translation facade used by the hybrid local-first pipeline."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import importlib.util

from videotranslator.models.manager_v8 import RuntimeModelManager
from .argos_engine import ArgosEngine
from .marian_engine import MarianEngine


@dataclass
class LocalResult:
    text: str
    engine: str
    confidence: float


class LocalTranslationManager:
    def __init__(self, root: str | Path | None = None, auto_install_pairs: bool = False, log=None, problem_cb=None):
        self.model_manager = RuntimeModelManager(root)
        self.auto_install_pairs = bool(auto_install_pairs)
        self.log = log
        self.problem_cb = problem_cb
        self.backends = []
        # A deterministic local model failure (OOM, corrupt weights, tokenizer
        # mismatch) must not be retried hundreds of times for the same video.
        self._disabled_backends: set[str] = set()
        self.refresh()

    def refresh(self):
        marian_path = self.model_manager.root / "translation" / "marian-en-ru"
        # Prefer Marian for EN→RU when explicitly installed; use Argos for broad coverage.
        self.backends = [MarianEngine(marian_path), ArgosEngine()]
        return [engine.name for engine in self.backends]

    def available_backends(self, source_lang: str, target_lang: str) -> list[str]:
        names = []
        for engine in self.backends:
            try:
                if engine.name in self._disabled_backends:
                    continue
                if engine.available(source_lang, target_lang):
                    names.append(engine.name)
            except Exception:
                continue
        return names

    def has_local_route(self, source_lang: str, target_lang: str) -> bool:
        source = str(source_lang or "").lower().split("-")[0]
        target = str(target_lang or "").lower().split("-")[0]
        if source == target:
            return True
        return bool(self.available_backends(source, target))

    def ensure_pair(self, source_lang: str, target_lang: str, *, progress_cb=None) -> bool:
        source = str(source_lang or "").lower().split("-")[0]
        target = str(target_lang or "").lower().split("-")[0]
        if not source or not target or source == target:
            return True
        if self.has_local_route(source, target):
            return True
        if not self.auto_install_pairs:
            return False
        # Auto-install only the language model.  The heavy Python engine itself
        # is installed explicitly from the Models tab, never during a video job.
        if importlib.util.find_spec("argostranslate") is None:
            return False
        try:
            if progress_cb:
                progress_cb(f"Устанавливаю локальный языковой пакет {source}→{target}…")
            ok = self.model_manager.ensure_argos_pair(source, target, progress_cb=progress_cb)
            self.refresh()
            return bool(ok)
        except Exception as exc:
            if self.log:
                self.log(f"   ⚠️ Локальный пакет {source}→{target} не установлен: {type(exc).__name__}: {exc}")
            return False

    def translate(self, text: str, source_lang: str, target_lang: str) -> LocalResult:
        return self.translate_many([text], source_lang, target_lang)[0]

    def translate_many(self, texts: list[str], source_lang: str, target_lang: str) -> list[LocalResult]:
        source = str(source_lang or "").lower().split("-")[0]
        target = str(target_lang or "").lower().split("-")[0]
        values = [str(text or "").strip() for text in texts]
        if not values:
            return []
        if source == target:
            return [LocalResult(value, "identity", 1.0) for value in values]

        results: list[LocalResult | None] = [
            LocalResult("", "none", 1.0) if not value else None for value in values
        ]
        pending = [index for index, value in enumerate(values) if value]
        errors = []

        for engine in self.backends:
            if not pending:
                break
            if engine.name in self._disabled_backends:
                continue
            try:
                if not engine.available(source, target):
                    continue
                pending_values = [values[index] for index in pending]
                batch_method = getattr(engine, "translate_many", None)
                if callable(batch_method):
                    translated_values = list(batch_method(pending_values, source, target))
                else:
                    translated_values = [engine.translate(value, source, target) for value in pending_values]
                if len(translated_values) != len(pending):
                    raise RuntimeError(
                        f"{engine.name} вернул {len(translated_values)} результатов для {len(pending)} сегментов"
                    )
                next_pending = []
                confidence = 0.96 if engine.name == "marian" else 0.90
                for index, translated in zip(pending, translated_values):
                    translated = str(translated or "").strip()
                    if translated:
                        results[index] = LocalResult(translated, engine.name, confidence)
                    else:
                        next_pending.append(index)
                pending = next_pending
            except Exception as exc:
                errors.append(f"{engine.name}:{type(exc).__name__}")
                self._disabled_backends.add(engine.name)
                if self.log:
                    self.log(
                        f"   ⚠️ Локальный backend {engine.name} отключён до конца видео после "
                        f"{type(exc).__name__}: {exc}"
                    )
                if self.problem_cb:
                    try:
                        self.problem_cb(
                            "local_translation_backend_disabled",
                            level="warning",
                            message="Локальный переводчик отключён до конца текущего видео после устойчивой ошибки.",
                            backend=engine.name,
                            source_language=source,
                            target_language=target,
                            exception={"type": type(exc).__name__, "message": str(exc)[:1000]},
                        )
                    except Exception:
                        pass

        if pending and errors:
            raise RuntimeError("Локальные переводчики не справились: " + ", ".join(errors))
        if pending:
            raise RuntimeError(f"Нет установленного локального переводчика {source}→{target}")
        return [result if result is not None else LocalResult("", "none", 0.0) for result in results]
