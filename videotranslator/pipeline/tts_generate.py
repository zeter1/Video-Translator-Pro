"""Method owner for VideoTranslator: _edge_tts_async, _run_edge_tts, generate_tts, _tts_segment_log, _get_prepared_tts_audio, _prepare_tts_segment."""

from __future__ import annotations

import asyncio
import hashlib
import os
from videotranslator.core.cancel import CancelledError
from videotranslator.core.diagnostics import classify_exception, compact_exception, exception_chain, redact_diagnostic_text
from videotranslator.config import NETWORK_CONNECT_TIMEOUT_SEC, NETWORK_READ_TIMEOUT_SEC
from videotranslator.models.manager_v8 import RuntimeModelManager
from videotranslator.tts.piper import PiperVoice
from videotranslator.network.http import edge_tts_timeout_for_text, install_requests_default_timeout, progressive_retry_delay
from videotranslator.sync.pause import normalize_tts_text


def _piper_model_revision(model_path) -> str:
    """Fast cache revision: changes when the ONNX model or sidecar config changes."""
    model = os.fspath(model_path)
    stat = os.stat(model)
    config = model + ".json"
    try:
        config_stat = os.stat(config)
        config_part = f"{config_stat.st_size}:{config_stat.st_mtime_ns}"
    except OSError:
        config_part = "0:0"
    return f"{stat.st_size}:{stat.st_mtime_ns}:{config_part}"


class TTSGenerateMixin:
    """Focused behavior-preserving mixin extracted for AI-local navigation."""

    def _resolve_piper_cache_identities(self, *, report_substitution: bool = True) -> list[dict]:
        """Resolve every usable local Piper voice in automatic fallback order."""
        settings = getattr(self, "hybrid_translation_settings", {}) or {}
        preferred_id = str(settings.get("preferred_piper_voice") or "piper-dmitri-ru")
        manager = RuntimeModelManager()
        list_method = getattr(manager, "installed_piper_voices", None)
        if callable(list_method):
            resolved = list(list_method(preferred_id, language="ru"))
        else:  # Compatibility with older test doubles/extensions.
            first = manager.resolve_piper_voice(preferred_id, language="ru")
            resolved = [first] if first else []

        identities = []
        for resolved_voice_id, model_path in resolved:
            identities.append({
                "requested_voice_id": preferred_id,
                "resolved_voice_id": str(resolved_voice_id),
                "model_path": model_path,
                "voice_revision": _piper_model_revision(model_path),
            })

        if report_substitution and identities and identities[0]["resolved_voice_id"] != preferred_id:
            self._problem(
                "tts_piper_voice_substituted",
                level="warning",
                message="Предпочитаемый локальный Piper-голос не установлен; выбран другой установленный локальный голос.",
                preferred_piper_voice=preferred_id,
                actual_piper_voice=identities[0]["resolved_voice_id"],
            )
        return identities

    def _resolve_piper_cache_identity(self, *, report_substitution: bool = True) -> dict:
        """Resolve the actual installed Piper voice and its file revision.

        Prepared WAV cache entries must be tied to the model bytes, otherwise a
        voice update can keep serving audio prepared from the previous model.
        """
        settings = getattr(self, "hybrid_translation_settings", {}) or {}
        preferred_id = str(settings.get("preferred_piper_voice") or "piper-dmitri-ru")
        identities = self._resolve_piper_cache_identities(report_substitution=report_substitution)
        if not identities:
            return {
                "requested_voice_id": preferred_id,
                "resolved_voice_id": "",
                "model_path": None,
                "voice_revision": "",
            }
        return identities[0]

    async def _edge_tts_async(self, text: str, path: str, voice: str, rate: str = "+0%"):
        import edge_tts
        # Pitch специально оставляем 0 Hz: ускоряем темп, а не высоту голоса.
        await asyncio.wait_for(
            edge_tts.Communicate(text, voice, rate=rate, pitch="+0Hz").save(path),
            timeout=edge_tts_timeout_for_text(text),
        )

    def _run_edge_tts(self, text: str, path: str, voice: str, rate: str = "+0%"):
        asyncio.run(self._edge_tts_async(text, path, voice, rate=rate))

    def generate_tts(self, text: str, path: str, voice: str, rate_pct: int = 0,
                     segment_index: int = 0, allow_gtts: bool | None = None) -> str | None:
        self._check_cancel()
        text = normalize_tts_text(text)
        if not text:
            return None

        if allow_gtts is None:
            allow_gtts = bool(self._tts_allow_gtts_fallback)

        rate_pct = int(max(-20, min(35, rate_pct)))
        rate = f"{rate_pct:+d}%"
        target_code = self.target_info.get("code") or "target"
        text_hash = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()
        edge_key = self.tts_cache.make_key(text, voice, rate_pct, "edge_tts", target_code)

        # CODEX-PHASE TG1 EDGE_CACHE — exact selected-voice Edge TTS cache lookup
        with self.tts_cache.key_lock(edge_key):
            if self.tts_cache.restore(edge_key, path):
                self._increment_tts_stat("cache_hits")
                self._increment_tts_stat("edge_cache_hits")
                return "edge_tts"

            # CODEX-PHASE TG2 EDGE_ATTEMPTS — bounded Edge TTS attempts and network-storm handling
            edge_mode = self.edge_network_guard.claim_probe_or_bypass()
            if edge_mode == "bypass":
                self._increment_tts_stat("edge_bypassed")
            else:
                edge_attempts = 1 if edge_mode == "probe" else 3
                for attempt in range(1, edge_attempts + 1):
                    self._check_cancel()
                    parallel_acquired = False
                    probe_acquired = False
                    try:
                        self._acquire_network_gate(self.edge_parallel_gate)
                        parallel_acquired = True
                        if edge_mode == "probe":
                            self._acquire_network_gate(self.edge_probe_gate)
                            probe_acquired = True
                        if os.path.exists(path):
                            os.remove(path)
                        self._run_edge_tts(text, path, voice, rate=rate)
                        if os.path.exists(path) and os.path.getsize(path) > 200:
                            self.edge_network_guard.record_success("edge_tts")
                            self.tts_cache.store(
                                edge_key,
                                path,
                                {
                                    "provider": "edge_tts",
                                    "target_language": target_code,
                                    "voice": voice,
                                    "rate_pct": rate_pct,
                                    "text_length": len(text),
                                    "text_sha256": text_hash,
                                },
                            )
                            self._increment_tts_stat("edge_created")
                            return "edge_tts"
                        raise RuntimeError("TTS создал пустой файл")
                    except CancelledError:
                        raise
                    except Exception as exc:
                        report_detail = self.edge_network_guard.record_failure("edge_tts", exc)
                        if report_detail:
                            self.log(
                                f"        ⚠️ EdgeTTS сегмент {segment_index}, {rate}, "
                                f"попытка {attempt}/{edge_attempts}: {compact_exception(exc)}"
                            )
                            self._problem(
                                "tts_retry",
                                level="warning",
                                message=(
                                    "Сетевая генерация Edge TTS не удалась; будет ограниченный повтор. "
                                    "Дальше программа автоматически выберет Piper или gTTS."
                                ),
                                tts_segment_index=segment_index,
                                attempt=attempt,
                                attempts_total=edge_attempts,
                                request_mode=edge_mode,
                                rate=rate,
                                voice=voice,
                                text_length=len(text),
                                text_sha256=text_hash,
                                exception={
                                    "type": type(exc).__name__,
                                    "category": classify_exception(exc),
                                    "message": compact_exception(exc, max_len=1000),
                                    "chain": exception_chain(exc),
                                },
                            )
                        # После начала шторма не держим VPN тремя повторными WebSocket-запросами.
                        if self.edge_network_guard.is_active():
                            break
                        if attempt < edge_attempts:
                            if probe_acquired:
                                self.edge_probe_gate.release()
                                probe_acquired = False
                            if parallel_acquired:
                                self.edge_parallel_gate.release()
                                parallel_acquired = False
                            self._sleep_or_cancel(progressive_retry_delay(attempt, base=2.0, maximum=12.0))
                    finally:
                        if probe_acquired:
                            self.edge_probe_gate.release()
                        if parallel_acquired:
                            self.edge_parallel_gate.release()

        # CODEX-PHASE TG3 FALLBACK_GATE — local Piper is allowed immediately because
        # it does not touch the network.  Native Edge rate requests still return None;
        # the normal-rate Piper audio can be time-stretched later by the existing pipeline.
        if rate_pct != 0:
            return None

        settings = getattr(self, "hybrid_translation_settings", {}) or {}
        if bool(settings.get("local_piper_fallback", True)) and target_code == "ru":
            preferred_id = str(settings.get("preferred_piper_voice") or "piper-dmitri-ru")
            piper_errors = []
            try:
                identities = self._resolve_piper_cache_identities()
            except Exception as exc:
                identities = []
                piper_errors.append((preferred_id, exc))

            for identity in identities:
                preferred_id = identity["requested_voice_id"]
                resolved_voice_id = identity["resolved_voice_id"]
                model_path = identity["model_path"]
                voice_revision = identity["voice_revision"]
                try:
                    piper_key = self.tts_cache.make_key(
                        text, resolved_voice_id, 0, "piper", target_code,
                        {"voice_revision": voice_revision},
                    )
                    with self.tts_cache.key_lock(piper_key):
                        if self.tts_cache.restore(piper_key, path):
                            self._increment_tts_stat("cache_hits")
                            self._increment_tts_stat("piper_cache_hits")
                            self._last_piper_provider_identity = identity
                            self._set_final_tts_provider(segment_index, "piper")
                            return "piper"
                        if os.path.exists(path):
                            os.remove(path)
                        if getattr(self, "_piper_voice_engine", None) is None:
                            self._piper_voice_engine = PiperVoice()
                        self._piper_voice_engine.synthesize(text, model_path, path)
                        if os.path.exists(path) and os.path.getsize(path) > 1000:
                            self.tts_cache.store(
                                piper_key,
                                path,
                                {
                                    "provider": "piper",
                                    "target_language": target_code,
                                    "voice_model": resolved_voice_id,
                                    "requested_voice_model": preferred_id,
                                    "voice_path": str(model_path),
                                    "voice_revision": voice_revision,
                                    "text_length": len(text),
                                    "text_sha256": text_hash,
                                },
                            )
                            self._last_piper_provider_identity = identity
                            count = self._increment_tts_stat("piper_created")
                            if count <= 3 or count % 50 == 0:
                                self.log(
                                    f"        📴 [{segment_index}] Piper локально ({resolved_voice_id}); "
                                    f"сегментов: {count}"
                                )
                            self._set_final_tts_provider(segment_index, "piper")
                            return "piper"
                        raise RuntimeError("Piper создал пустой WAV")
                except Exception as exc:
                    piper_errors.append((resolved_voice_id, exc))
                    self._increment_tts_stat("piper_failures")
                    self._problem(
                        "tts_piper_voice_failed_trying_next",
                        level="warning",
                        message="Один локальный Piper-голос не сработал; программа автоматически пробует следующий установленный голос.",
                        tts_segment_index=segment_index,
                        preferred_piper_voice=preferred_id,
                        failed_piper_voice=resolved_voice_id,
                        remaining_local_voices=max(0, len(identities) - len(piper_errors)),
                        exception={
                            "type": type(exc).__name__,
                            "category": classify_exception(exc),
                            "message": compact_exception(exc, max_len=1000),
                        },
                    )

            if piper_errors:
                last_voice, last_exc = piper_errors[-1]
                self._problem(
                    "tts_piper_fallback_failed",
                    level="warning",
                    message="Все доступные локальные Piper-голоса исчерпаны; программа автоматически продолжит через сетевой TTS.",
                    tts_segment_index=segment_index,
                    preferred_piper_voice=preferred_id,
                    attempted_piper_voices=[voice_id for voice_id, _exc in piper_errors],
                    exception={
                        "type": type(last_exc).__name__,
                        "category": classify_exception(last_exc),
                        "message": compact_exception(last_exc, max_len=1000),
                        "chain": exception_chain(last_exc),
                    },
                )

        if not allow_gtts:
            self._increment_tts_stat("edge_deferred")
            self._begin_edge_voice_preservation(reason="edge_base_voice_unavailable")
            return None

        # CODEX-PHASE TG4 GTTS_FALLBACK — cache, bounded gTTS attempts and fallback accounting
        # gTTS не умеет такой же аккуратный rate/pitch, поэтому используем его только как резерв.
        gtts_lang = self.target_info.get("gtts") or self.target_info.get("code") or "en"
        gtts_key = self.tts_cache.make_key(text, voice, 0, "gtts", gtts_lang)
        last_gtts_error = None
        with self.tts_cache.key_lock(gtts_key):
            if self.tts_cache.restore(gtts_key, path):
                self._increment_tts_stat("cache_hits")
                self._mark_gtts_fallback(segment_index)
                return "gtts"

            install_requests_default_timeout()
            from gtts import gTTS
            for attempt in range(1, 4):
                gtts_acquired = False
                try:
                    self._check_cancel()
                    self.gtts_network_guard.before_request(self._sleep_or_cancel)
                    self._acquire_network_gate(self.gtts_parallel_gate)
                    gtts_acquired = True
                    if os.path.exists(path):
                        os.remove(path)
                    try:
                        speech = gTTS(
                            text=text,
                            lang=gtts_lang,
                            slow=False,
                            timeout=(NETWORK_CONNECT_TIMEOUT_SEC, NETWORK_READ_TIMEOUT_SEC),
                        )
                    except TypeError:
                        # gTTS < 2.5 did not expose timeout; the requests default patch
                        # remains as compatibility fallback, but current requirements use 2.5+.
                        speech = gTTS(text=text, lang=gtts_lang, slow=False)
                    speech.save(path)
                    if os.path.exists(path) and os.path.getsize(path) > 200:
                        self.gtts_network_guard.record_success("gtts")
                        self.tts_cache.store(
                            gtts_key,
                            path,
                            {
                                "provider": "gtts",
                                "target_language": gtts_lang,
                                "requested_voice": voice,
                                "voice_selection_supported": False,
                                "rate_pct": 0,
                                "text_length": len(text),
                                "text_sha256": text_hash,
                            },
                        )
                        fallback_count = self._mark_gtts_fallback(segment_index)
                        if fallback_count <= 3 or fallback_count % 50 == 0:
                            self.log(
                                f"        ↩️ [{segment_index}] Google TTS (резерв); "
                                f"всего резервных сегментов: {fallback_count}"
                            )
                        return "gtts"
                    raise RuntimeError("Google TTS создал пустой файл")
                except CancelledError:
                    raise
                except Exception as exc:
                    last_gtts_error = exc
                    self._increment_tts_stat("gtts_retries")
                    report_detail = self.gtts_network_guard.record_failure("gtts", exc)
                    if report_detail and attempt < 3:
                        self.log(
                            f"        ⚠️ Google TTS сегмент {segment_index}, "
                            f"попытка {attempt}/3: {compact_exception(exc)}"
                        )
                        self._problem(
                            "tts_gtts_retry",
                            level="warning",
                            message="Резервный Google TTS временно недоступен; будет повторная попытка.",
                            tts_segment_index=segment_index,
                            attempt=attempt,
                            attempts_total=3,
                            text_length=len(text),
                            text_sha256=text_hash,
                            exception={
                                "type": type(exc).__name__,
                                "category": classify_exception(exc),
                                "message": compact_exception(exc, max_len=1000),
                                "chain": exception_chain(exc),
                            },
                        )
                    if gtts_acquired:
                        self.gtts_parallel_gate.release()
                        gtts_acquired = False
                    if attempt < 3:
                        self._sleep_or_cancel(progressive_retry_delay(attempt, base=3.0, maximum=15.0))
                finally:
                    if gtts_acquired:
                        self.gtts_parallel_gate.release()

        # CODEX-PHASE TG5 EXHAUSTED — report exhausted fallback cycle without losing segment recovery state
        if last_gtts_error is not None:
            self.log(
                f"        ⚠️ Google TTS сегмент {segment_index} не сработал после 3 попыток: "
                f"{compact_exception(last_gtts_error)}"
            )
            self._problem(
                "tts_generation_attempts_exhausted",
                level="warning",
                message="Текущий цикл Edge TTS и разрешённого gTTS исчерпан; сегмент будет повторён.",
                tts_segment_index=segment_index,
                voice=voice,
                rate=rate,
                text_length=len(text),
                text_sha256=text_hash,
                exception={
                    "type": type(last_gtts_error).__name__,
                    "category": classify_exception(last_gtts_error),
                    "message": compact_exception(last_gtts_error, max_len=1000),
                    "chain": exception_chain(last_gtts_error),
                },
            )

        return None

    def _tts_segment_log(self, index: int, message: str):
        text = str(message).strip()
        self.log(f"      [TTS-сегмент {index}] {text}")
        lowered = text.lower()
        if any(marker in lowered for marker in ("⚠", "ошибка", "timeout", "stderr")):
            self._problem(
                "tts_processing_warning",
                level="warning",
                message="Ошибка локальной подготовки TTS-аудио.",
                tts_segment_index=index,
                detail=redact_diagnostic_text(text, max_len=3000),
            )
