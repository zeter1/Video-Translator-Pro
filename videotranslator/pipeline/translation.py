"""Method owner for VideoTranslator: _close_translation_client, _get_translation_client, _save_checkpoint, translate_segment, translate_records_batch."""

from __future__ import annotations

from pathlib import Path
import hashlib
import time
from videotranslator.core.cancel import CancelledError
from videotranslator.core.diagnostics import classify_exception, compact_exception, exception_chain
from videotranslator.network.guard import ReusableGoogleTranslator
from videotranslator.network.http import install_requests_default_timeout, progressive_retry_delay
from videotranslator.recovery.translation import save_translation_checkpoint
from videotranslator.translation.batching import build_translation_batch, parse_translation_batch, split_translation_text


class TranslationMixin:
    """Behavior-preserving methods extracted from the legacy monolith."""

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
            # Старое поле оставлено как совместимое представление для тестов/расширений.
            self.translator = self.translation_client
            self._problem(
                "translation_http_session_started",
                message="Создан общий HTTP-сеанс перевода с повторным использованием TLS-соединения.",
                target_language=self.target_info.get("code"),
            )
        return self.translation_client


    def _save_checkpoint(self, path: Path, input_path: str, source_lang: str, segments: list):
        try:
            save_translation_checkpoint(
                path,
                input_path,
                source_lang,
                self.target_info.get("code") or "target",
                segments,
            )
            return True
        except Exception as exc:
            self.log(f"   ⚠️ Не удалось сохранить контрольную точку перевода: {compact_exception(exc)}")
            self._problem(
                "translation_checkpoint_save_failed",
                level="warning",
                message="Перевод продолжается, но контрольную точку записать не удалось.",
                checkpoint_path=str(path),
                exception={
                    "type": type(exc).__name__,
                    "category": classify_exception(exc),
                    "message": compact_exception(exc, max_len=1000),
                },
            )
            return False


    def translate_segment(self, text: str, index: int = 0, attempts: int = 3) -> str:
        text = (text or "").strip()
        if not text:
            return ""

        parts = split_translation_text(text)
        if len(parts) > 1:
            # Each part owns its retries; never retry already completed parts
            # because a later request has a transient failure.
            translated_parts = [self.translate_segment(part, index=index, attempts=attempts)
                                for part in parts]
            return " ".join(translated_parts)

        last_error = None
        attempts = max(1, int(attempts))
        text_hash = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()
        for attempt in range(1, attempts + 1):
            self._check_cancel()
            self.translation_network_guard.before_request(self._sleep_or_cancel)
            started_at = time.monotonic()
            try:
                translated = self._get_translation_client().translate(text)
                if translated and translated.strip():
                    self.translation_network_guard.record_success("translation")
                    if attempt > 1:
                        self._problem(
                            "translation_retry_recovered",
                            message="Повторная попытка перевода успешно завершилась.",
                            segment_index=index,
                            attempt=attempt,
                            elapsed_sec=round(time.monotonic() - started_at, 3),
                            source_length=len(text),
                            source_sha256=text_hash,
                        )
                    return translated.strip()
                raise RuntimeError("переводчик вернул пустой ответ")
            except CancelledError:
                raise
            except Exception as exc:
                last_error = exc
                report_detail = self.translation_network_guard.record_failure("translation", exc)
                if report_detail:
                    self.log(
                        f"      ⚠️ Перевод сегмента {index}, попытка {attempt}/{attempts}: "
                        f"{compact_exception(exc)}"
                    )
                    self._problem(
                        "translation_retry",
                        level="warning",
                        message="Запрос перевода не удался; попытка ограничена тайм-аутом.",
                        segment_index=index,
                        attempt=attempt,
                        attempts_total=attempts,
                        elapsed_sec=round(time.monotonic() - started_at, 3),
                        source_length=len(text),
                        source_sha256=text_hash,
                        target_language=self.target_info.get("code"),
                        exception={
                            "type": type(exc).__name__,
                            "category": classify_exception(exc),
                            "message": compact_exception(exc, max_len=1000),
                            "chain": exception_chain(exc),
                        },
                    )
                if attempt < attempts:
                    self._sleep_or_cancel(progressive_retry_delay(attempt))

        raise RuntimeError(f"Не удалось перевести сегмент {index}: {compact_exception(last_error)}") from last_error


    def translate_records_batch(self, records: list[tuple[int, dict]], attempts: int = 2
                                ) -> tuple[dict[int, str], list[tuple[int, dict, Exception]]]:
        """Переводит пачку одним запросом; при любой неоднозначности безопасно откатывается на поштучный режим."""
        if not records:
            return {}, []
        if len(records) == 1:
            index, record = records[0]
            try:
                value = self.translate_segment(record["source"], index, attempts=attempts)
                record["translated"] = value
                return {index: value}, []
            except CancelledError:
                raise
            except Exception as exc:
                return {}, [(index, record, exc)]

        items = [(index, str(record.get("source") or "")) for index, record in records]
        payload, markers = build_translation_batch(items)
        first_index = records[0][0]
        last_index = records[-1][0]
        batch_label = f"{first_index}-{last_index}"
        fallback_reason = ""
        try:
            translated_payload = self.translate_segment(payload, index=batch_label, attempts=attempts)
            parsed = parse_translation_batch(translated_payload, markers)
            return parsed, []
        except CancelledError:
            raise
        except ValueError as exc:
            fallback_reason = compact_exception(exc)
            self._problem(
                "translation_batch_validation_failed",
                level="warning",
                message="Пакетный ответ отклонён: разделители не прошли строгую проверку; сегменты будут переведены по одному.",
                first_segment=first_index,
                last_segment=last_index,
                segments_count=len(records),
                reason=fallback_reason,
            )
        except Exception as exc:
            fallback_reason = compact_exception(exc)
            self._problem(
                "translation_batch_request_failed",
                level="warning",
                message="Пакетный запрос не удался; сегменты будут переведены по одному.",
                first_segment=first_index,
                last_segment=last_index,
                segments_count=len(records),
                exception={
                    "type": type(exc).__name__,
                    "category": classify_exception(exc),
                    "message": fallback_reason,
                },
            )

        self.log(
            f"      ↩️ Пачка {batch_label} не принята ({fallback_reason}); "
            "безопасный перевод по одному"
        )
        results = {}
        failures = []
        for index, record in records:
            try:
                results[index] = self.translate_segment(record["source"], index, attempts=attempts)
                # Keep completed fallback requests reachable if the next one is cancelled.
                record["translated"] = results[index]
            except CancelledError:
                raise
            except Exception as exc:
                failures.append((index, record, exc))
        return results, failures
