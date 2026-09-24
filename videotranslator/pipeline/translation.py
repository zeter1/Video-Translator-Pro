"""Translation owner: local-first hybrid routing, Google repair and checkpoints."""
from __future__ import annotations

from pathlib import Path
import hashlib
import time

from videotranslator.config import TRANSLATION_PROVIDER_RETRY_COOLDOWN_SEC, TRANSLATION_RATE_LIMIT_COOLDOWN_SEC
from videotranslator.core.cancel import CancelledError
from videotranslator.core.diagnostics import classify_exception, compact_exception, exception_chain
from videotranslator.network.guard import ReusableGoogleTranslator
from videotranslator.network.http import install_requests_default_timeout, progressive_retry_delay
from videotranslator.network.translation_circuit import TranslationProviderCoolingDownError
from videotranslator.recovery.translation import save_translation_checkpoint
from videotranslator.translation.batching import build_translation_batch, parse_translation_batch, split_translation_text
from videotranslator.translation.local.manager import LocalTranslationManager
from videotranslator.translation.quality import InvalidTranslationResponseError, ensure_valid_translation_response
from videotranslator.translation.quality_checker import inspect as inspect_local_translation


class TranslationMixin:
    """Hybrid local-first translation with selective Google repair."""

    _TEMPORARY_PROVIDER_CATEGORIES = frozenset({
        "rate_limit", "network_timeout", "network_connection", "timeout"
    })

    @classmethod
    def _is_temporary_provider_error(cls, exc: Exception | None) -> bool:
        return isinstance(exc, TranslationProviderCoolingDownError) or classify_exception(exc) in cls._TEMPORARY_PROVIDER_CATEGORIES

    @staticmethod
    def _provider_retry_delay(exc: Exception | None, attempt: int) -> float:
        if isinstance(exc, TranslationProviderCoolingDownError):
            return min(float(TRANSLATION_RATE_LIMIT_COOLDOWN_SEC), 8.0 * max(1, int(attempt)))
        category = classify_exception(exc)
        if category == "rate_limit":
            return min(float(TRANSLATION_RATE_LIMIT_COOLDOWN_SEC), 8.0 * max(1, int(attempt)))
        if category in {"network_timeout", "network_connection", "timeout"}:
            return max(float(TRANSLATION_PROVIDER_RETRY_COOLDOWN_SEC), progressive_retry_delay(attempt))
        return progressive_retry_delay(attempt)

    def _close_translation_client(self):
        client = self.translation_client
        self.translation_client = None
        self.translator = None
        if client is not None:
            try:
                client.close()
            except Exception:
                pass

    def _get_translation_client(self):
        if self.translation_client is None:
            install_requests_default_timeout()
            self.translation_client = ReusableGoogleTranslator(self.target_info["code"])
            self.translator = self.translation_client
            self._problem(
                "translation_http_session_started",
                message=(
                    "Создан устойчивый клиент Google Repair: основной JSON endpoint + "
                    "автоматический резерв на deep-translator/mobile."
                ),
                service="google_translate",
                provider="google_endpoint_router",
                primary_backend="google_json_gtx",
                fallback_backend="deep_translator_google_mobile",
                target_language=self.target_info.get("code"),
            )
        return self.translation_client

    def _get_local_translation_manager(self) -> LocalTranslationManager:
        manager = getattr(self, "local_translation_manager", None)
        settings = getattr(self, "hybrid_translation_settings", {}) or {}
        if manager is None:
            manager = LocalTranslationManager(
                auto_install_pairs=bool(settings.get("auto_install_argos_pairs", True)),
                log=self.log,
                problem_cb=self._problem,
            )
            self.local_translation_manager = manager
        else:
            manager.auto_install_pairs = bool(settings.get("auto_install_argos_pairs", True))
        return manager

    def _save_checkpoint(self, path: Path, input_path: str, source_lang: str, segments: list):
        try:
            save_translation_checkpoint(path, input_path, source_lang, self.target_info.get("code") or "target", segments)
            return True
        except Exception as exc:
            self.log(f"   ⚠️ Не удалось сохранить контрольную точку перевода: {compact_exception(exc)}")
            self._problem(
                "translation_checkpoint_save_failed", level="warning",
                message="Перевод продолжается, но контрольную точку записать не удалось.",
                checkpoint_path=str(path),
                exception={"type": type(exc).__name__, "category": classify_exception(exc), "message": compact_exception(exc, max_len=1000)},
            )
            return False

    def _assert_shared_google_available(self):
        circuit = getattr(self, "shared_translation_circuit", None)
        if circuit is not None:
            circuit.assert_available()

    def _mark_shared_google_failure(self, category: str) -> float:
        circuit = getattr(self, "shared_translation_circuit", None)
        if circuit is None:
            return 0.0
        if category == "rate_limit":
            return circuit.mark_failure(category, TRANSLATION_RATE_LIMIT_COOLDOWN_SEC)
        if category in {"network_timeout", "network_connection", "timeout"}:
            return circuit.mark_failure(category, TRANSLATION_PROVIDER_RETRY_COOLDOWN_SEC)
        return 0.0

    def _mark_shared_google_success(self):
        circuit = getattr(self, "shared_translation_circuit", None)
        if circuit is not None:
            circuit.mark_success()

    def _translate_google_segment(self, text: str, index: int = 0, attempts: int = 3) -> str:
        text = (text or "").strip()
        if not text:
            return ""
        parts = split_translation_text(text)
        if len(parts) > 1:
            return " ".join(self._translate_google_segment(part, index=index, attempts=attempts) for part in parts)

        last_error = None
        attempts = max(1, int(attempts))
        text_hash = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()
        for attempt in range(1, attempts + 1):
            self._check_cancel()
            self._assert_shared_google_available()
            self.translation_network_guard.before_request(self._sleep_or_cancel)
            started_at = time.monotonic()
            try:
                client = self._get_translation_client()
                translated = client.translate(text)
                route_event = None
                consume_route_event = getattr(client, "consume_route_event", None)
                if callable(consume_route_event):
                    try:
                        route_event = consume_route_event()
                    except Exception:
                        route_event = None
                if route_event:
                    self.log(
                        "      🔀 Google Repair: основной endpoint недоступен; "
                        f"автоматически переключено {route_event.get('from')} → {route_event.get('to')}."
                    )
                    self._problem(
                        "translation_provider_endpoint_switched", level="warning",
                        message="Один endpoint Google Translate не ответил; repair продолжен через резервный маршрут.",
                        service="google_translate", provider="google_endpoint_router",
                        provider_from=route_event.get("from"), provider_to=route_event.get("to"),
                        failure_kind=route_event.get("reason") or "endpoint_failure",
                        impact="translation_continues_without_checkpoint_loss",
                        recovery_action="keep_working_on_healthy_endpoint",
                        endpoint_exception=route_event.get("exception") or "", segment_index=index,
                    )
                try:
                    translated = ensure_valid_translation_response(translated, source_text=text)
                except InvalidTranslationResponseError as invalid_exc:
                    self._problem(
                        "translation_provider_response_rejected", level="warning",
                        message="Google Repair вернул страницу/текст ошибки вместо перевода; ответ не сохранён.",
                        service="google_translate", provider="google_endpoint_router", operation="translate",
                        failure_kind="provider_response_invalid", impact="blocked_before_checkpoint_and_tts",
                        recovery_action="retry_same_segment_then_defer", segment_index=index,
                        attempt=attempt, attempts_total=attempts,
                        elapsed_sec=round(time.monotonic() - started_at, 3), source_length=len(text),
                        source_sha256=text_hash, source_preview=" ".join(text.split())[:320],
                        response_validation=invalid_exc.inspection,
                    )
                    raise
                self.translation_network_guard.record_success("translation")
                self._mark_shared_google_success()
                if attempt > 1:
                    self._problem(
                        "translation_retry_recovered", message="Повторная попытка Google Repair успешно завершилась.",
                        segment_index=index, attempt=attempt,
                        elapsed_sec=round(time.monotonic() - started_at, 3), source_length=len(text), source_sha256=text_hash,
                    )
                return translated
            except CancelledError:
                raise
            except TranslationProviderCoolingDownError:
                raise
            except Exception as exc:
                last_error = exc
                category = classify_exception(exc)
                shared_cooldown = self._mark_shared_google_failure(category)
                if category == "rate_limit":
                    self._close_translation_client()
                    self._problem(
                        "translation_rate_limit_detected", level="warning",
                        message="Google Repair ограничил запросы; локальные переводы сохраняются, новые online repair временно остановлены.",
                        service="google_translate", failure_kind="provider_rate_limit",
                        impact="google_repair_paused_local_translation_preserved",
                        recovery_action="batch_circuit_cooldown_then_probe", segment_index=index,
                        attempt=attempt, cooldown_hint_sec=round(shared_cooldown or TRANSLATION_RATE_LIMIT_COOLDOWN_SEC),
                    )
                report_detail = self.translation_network_guard.record_failure("translation", exc)
                if report_detail:
                    self.log(f"      ⚠️ Google Repair сегмента {index}, попытка {attempt}/{attempts}: {compact_exception(exc)}")
                    self._problem(
                        "translation_retry", level="warning",
                        message="Запрос Google Repair не удался; попытка ограничена тайм-аутом.",
                        segment_index=index, attempt=attempt, attempts_total=attempts,
                        elapsed_sec=round(time.monotonic() - started_at, 3), source_length=len(text),
                        source_sha256=text_hash, target_language=self.target_info.get("code"),
                        exception={"type": type(exc).__name__, "category": category, "message": compact_exception(exc, max_len=1000), "chain": exception_chain(exc)},
                    )
                if category == "rate_limit" and shared_cooldown > 0:
                    # The batch circuit is already authoritative. Sleeping eight seconds
                    # and immediately retrying used to hit assert_available() and could
                    # never send a real request while the 60+ second cooldown was active.
                    raise TranslationProviderCoolingDownError(
                        f"Google repair временно отключён ещё на {shared_cooldown:.0f}с после 429."
                    ) from exc
                if attempt < attempts:
                    delay = self._provider_retry_delay(exc, attempt)
                    if category == "rate_limit":
                        self.log(f"      ⏳ Google Repair получил 429; жду {delay:.0f}с перед одной повторной попыткой…")
                    self._sleep_or_cancel(delay)

        raise RuntimeError(f"Не удалось выполнить Google Repair сегмента {index}: {compact_exception(last_error)}") from last_error

    def translate_segment(self, text: str, index: int = 0, attempts: int = 3) -> str:
        """Translate locally first; ask Google only when local quality heuristics flag a segment."""
        text = (text or "").strip()
        if not text:
            return ""
        parts = split_translation_text(text)
        if len(parts) > 1:
            return " ".join(self.translate_segment(part, index=index, attempts=attempts) for part in parts)

        settings = getattr(self, "hybrid_translation_settings", {}) or {}
        source = str(getattr(self, "source_language", "") or "").lower().split("-")[0]
        target = str(self.target_info.get("code") or "ru").lower().split("-")[0]
        if source and target and source == target:
            # Whisper already identified the requested target language. Avoid an
            # unnecessary online translation round-trip that can alter names,
            # punctuation or technical terms for text that needs no translation.
            return text
        local_first = bool(settings.get("local_first", True)) and source and source != target
        local_candidate = ""
        local_engine = ""

        if local_first:
            manager = self._get_local_translation_manager()
            try:
                local = manager.translate(text, source, target)
                local_candidate, local_engine = local.text.strip(), local.engine
                inspection = inspect_local_translation(text, local_candidate, source, target)
                self._problem(
                    "local_translation_completed", message="Сегмент переведён локальным движком.",
                    segment_index=index, local_engine=local_engine, source_language=source,
                    target_language=target, google_repair_needed=not inspection["ok"],
                    quality_reasons=inspection.get("reasons") or [],
                )
                if inspection["ok"]:
                    return local_candidate
                self.log(
                    f"      🧩 [{index}] {local_engine}: нужна точечная Google-проверка "
                    f"({', '.join(inspection.get('reasons') or ['quality'])})"
                )
            except Exception as exc:
                self._problem(
                    "local_translation_unavailable", level="warning",
                    message="Локальный переводчик недоступен для сегмента; online fallback выбран автоматически.",
                    segment_index=index, source_language=source, target_language=target,
                    exception={"type": type(exc).__name__, "message": compact_exception(exc, max_len=800)},
                )

        try:
            repaired = self._translate_google_segment(text, index=index, attempts=attempts)
            if local_candidate:
                self._problem(
                    "translation_google_repair_applied", message="Google использован только для проблемного локального сегмента.",
                    segment_index=index, local_engine=local_engine, source_language=source, target_language=target,
                )
            return repaired
        except Exception as exc:
            if local_candidate:
                self._problem(
                    "translation_google_repair_failed_using_local", level="warning",
                    message="Google Repair недоступен; сохранён локальный перевод вместо остановки всего видео.",
                    segment_index=index, local_engine=local_engine,
                    exception={"type": type(exc).__name__, "category": classify_exception(exc), "message": compact_exception(exc, max_len=1000)},
                )
                return local_candidate
            raise

    def _translate_google_records_batch(self, records: list[tuple[int, dict]], attempts: int = 2,
                                        segment_func=None
                                        ) -> tuple[dict[int, str], list[tuple[int, dict, Exception]]]:
        if not records:
            return {}, []
        segment_func = segment_func or self.translate_segment
        if len(records) == 1:
            index, record = records[0]
            try:
                value = segment_func(record["source"], index, attempts=attempts)
                record["translated"] = value
                return {index: value}, []
            except CancelledError:
                raise
            except Exception as exc:
                return {}, [(index, record, exc)]

        items = [(index, str(record.get("source") or "")) for index, record in records]
        payload, markers = build_translation_batch(items)
        first_index, last_index = records[0][0], records[-1][0]
        batch_label = f"{first_index}-{last_index}"
        fallback_reason = ""
        try:
            translated_payload = segment_func(payload, index=batch_label, attempts=attempts)
            return parse_translation_batch(translated_payload, markers), []
        except CancelledError:
            raise
        except ValueError as exc:
            fallback_reason = compact_exception(exc)
            self._problem(
                "translation_batch_validation_failed", level="warning",
                message="Пакетный Google Repair ответ отклонён; проблемные сегменты будут проверены по одному.",
                first_segment=first_index, last_segment=last_index, segments_count=len(records), reason=fallback_reason,
            )
        except Exception as exc:
            fallback_reason = compact_exception(exc)
            category = "rate_limit" if isinstance(exc, TranslationProviderCoolingDownError) else classify_exception(exc)
            temporary = self._is_temporary_provider_error(exc)
            self._problem(
                "translation_batch_request_failed", level="warning",
                message=("Google Repair пакет отложен без поштучного fan-out." if temporary else "Google Repair пакет не удался; repair будет по одному."),
                first_segment=first_index, last_segment=last_index, segments_count=len(records),
                fallback_individual_suppressed=temporary,
                exception={"type": type(exc).__name__, "category": category, "message": fallback_reason},
            )
            if temporary:
                self._problem(
                    "translation_batch_deferred_provider_unavailable", level="warning",
                    message=(
                        "Провайдер перевода временно недоступен; поштучный fallback подавлен, "
                        "чтобы не усиливать rate-limit/сетевой шторм."
                    ),
                    service="google_translate",
                    first_segment=first_index, last_segment=last_index, segments_count=len(records),
                    failure_kind=category,
                    impact="batch_deferred_checkpoint_safe",
                    recovery_action="cooldown_then_retry_batch",
                )
                return {}, [(index, record, exc) for index, record in records]

        self.log(f"      ↩️ Google Repair пачки {batch_label} не принят ({fallback_reason}); проверяю по одному")
        results, failures = {}, []
        for index, record in records:
            try:
                results[index] = segment_func(record["source"], index, attempts=attempts)
                record["translated"] = results[index]
            except CancelledError:
                raise
            except Exception as exc:
                failures.append((index, record, exc))
        return results, failures

    def translate_records_batch(self, records: list[tuple[int, dict]], attempts: int = 2
                                ) -> tuple[dict[int, str], list[tuple[int, dict, Exception]]]:
        """Local batch first, then one bounded Google request for suspicious local results only."""
        if not records:
            return {}, []
        settings = getattr(self, "hybrid_translation_settings", {}) or {}
        source = str(getattr(self, "source_language", "") or "").lower().split("-")[0]
        target = str(self.target_info.get("code") or "ru").lower().split("-")[0]
        if source and target and source == target:
            results = {}
            for index, record in records:
                value = str(record.get("source") or "").strip()
                record["translated"] = value
                results[index] = value
            return results, []
        manager = self._get_local_translation_manager()
        local_enabled = bool(settings.get("local_first", True)) and source and source != target and manager.has_local_route(source, target)
        if not local_enabled:
            return self._translate_google_records_batch(records, attempts=attempts, segment_func=self.translate_segment)

        results: dict[int, str] = {}
        repair_records: list[tuple[int, dict]] = []
        local_candidates: dict[int, tuple[str, str, dict]] = {}
        local_failures: list[tuple[int, dict, Exception]] = []

        local_batch_method = getattr(manager, "translate_many", None)
        local_batch_results = None
        if callable(local_batch_method):
            try:
                self._check_cancel()
                local_batch_results = list(local_batch_method(
                    [str(record.get("source") or "").strip() for _index, record in records],
                    source,
                    target,
                ))
                if len(local_batch_results) != len(records):
                    raise RuntimeError(
                        f"Локальный batch вернул {len(local_batch_results)} результатов для {len(records)} сегментов"
                    )
            except Exception as exc:
                local_batch_results = None
                self._problem(
                    "local_translation_batch_failed",
                    level="warning",
                    message="Пакетный локальный перевод не удался; используется безопасный поштучный fallback.",
                    segments=len(records), source_language=source, target_language=target,
                    exception={"type": type(exc).__name__, "message": compact_exception(exc, max_len=800)},
                )

        for position, (index, record) in enumerate(records):
            self._check_cancel()
            text = str(record.get("source") or "").strip()
            try:
                local = local_batch_results[position] if local_batch_results is not None else manager.translate(text, source, target)
                inspection = inspect_local_translation(text, local.text, source, target)
                local_candidates[index] = (local.text, local.engine, inspection)
                if inspection["ok"]:
                    results[index] = local.text
                    record["translated"] = local.text
                else:
                    repair_records.append((index, record))
            except Exception as exc:
                local_failures.append((index, record, exc))
                repair_records.append((index, record))

        repaired, repair_failures = self._translate_google_records_batch(
            repair_records, attempts=attempts, segment_func=self._translate_google_segment
        ) if repair_records else ({}, [])
        for index, value in repaired.items():
            results[index] = value
            for row_index, record in repair_records:
                if row_index == index:
                    record["translated"] = value
                    break

        failures: list[tuple[int, dict, Exception]] = []
        failed_by_index = {idx: (record, exc) for idx, record, exc in repair_failures}
        for index, record in repair_records:
            if index in repaired:
                continue
            candidate = local_candidates.get(index)
            if candidate and candidate[0].strip():
                results[index] = candidate[0].strip()
                record["translated"] = results[index]
                _record, exc = failed_by_index.get(index, (record, RuntimeError("Google Repair skipped")))
                self._problem(
                    "translation_google_repair_failed_using_local", level="warning",
                    message="Google Repair недоступен; сохранён локальный перевод сегмента.",
                    segment_index=index, local_engine=candidate[1], quality_reasons=candidate[2].get("reasons") or [],
                    exception={"type": type(exc).__name__, "category": classify_exception(exc), "message": compact_exception(exc, max_len=800)},
                )
            elif index in failed_by_index:
                rec, exc = failed_by_index[index]
                failures.append((index, rec, exc))
            else:
                original = next((row for row in local_failures if row[0] == index), None)
                if original:
                    failures.append(original)

        self._problem(
            "hybrid_translation_batch_summary",
            message="Пачка обработана local-first с выборочным Google Repair.",
            segments=len(records), local_ok=len(records) - len(repair_records),
            google_repair_requested=len(repair_records), google_repair_succeeded=len(repaired),
            local_kept_after_google_failure=sum(1 for idx, _record in repair_records if idx in results and idx not in repaired),
            failed=len(failures), source_language=source, target_language=target,
        )
        return results, failures
