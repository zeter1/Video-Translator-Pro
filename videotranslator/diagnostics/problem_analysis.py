"""Method owner for ProblemLogger: _problem_family, _event_signature, _record_artifact_error_locked, _compact_exception_evidence, _compact_problem_context, _remediation_playbooks, _compact_repeat_record, _issue_card, _build_codex_analysis_locked."""

from __future__ import annotations

import hashlib
import json
import re
from videotranslator.core.diagnostics import compact_exception, diagnostic_signature_value, redact_diagnostic_text


class ProblemAnalysisMixin:
    """Behavior-preserving methods extracted from the legacy monolith."""

    @staticmethod
    def _problem_family(event: str, details: dict) -> str:
        exception = details.get("exception") if isinstance(details.get("exception"), dict) else {}
        category = str(exception.get("category") or "")
        if category:
            return category
        event_lower = event.lower()
        command = details.get("command")
        if isinstance(command, dict):
            command = command.get("items") or []
        command_text = " ".join(str(item) for item in command) if isinstance(command, list) else str(command or "")
        if any(word in event_lower for word in ("network", "timeout", "connection", "http")):
            return "network"
        if "validation" in event_lower and any(word in event_lower for word in ("video", "media", "output")):
            return "media_validation"
        if (
            any(word in event_lower for word in ("ffmpeg", "ffprobe", "subprocess", "encoder", "mux"))
            or re.search(r"(?i)(^|[\\/ ])ff(?:mpeg|probe)(?:\.exe)?(?:$| )", command_text)
        ):
            return "external_process"
        if "translation" in event_lower or "translator" in event_lower:
            return "translation"
        if "tts" in event_lower or "voice" in event_lower or "audio" in event_lower:
            return "text_to_speech"
        if "cache" in event_lower or "checkpoint" in event_lower or "recovery" in event_lower:
            return "recovery_and_cache"
        if any(word in event_lower for word in ("file", "path", "batch", "input", "output")):
            return "file_pipeline"
        if any(word in event_lower for word in ("worker", "application", "app_", "session")):
            return "application"
        return "runtime"


    @staticmethod
    def _event_signature(event: str, level: str, family: str, message: str, details: dict) -> str:
        exception = details.get("exception") if isinstance(details.get("exception"), dict) else {}
        basis = {
            "event": event,
            "level": level,
            "family": family,
            "message": diagnostic_signature_value(message),
            "stage": details.get("stage") or "",
            "service": details.get("service") or details.get("component") or "",
            "provider": details.get("provider") or "",
            "exception": diagnostic_signature_value({
                "type": exception.get("type") or "",
                "category": exception.get("category") or "",
                "message": exception.get("message") or "",
            }),
            "command": diagnostic_signature_value(details.get("command") or []),
            "returncode": details.get("returncode", details.get("return_code")),
            "validation_reason": ((details.get("validation") or {}).get("reason")
                                  if isinstance(details.get("validation"), dict) else ""),
        }
        encoded = json.dumps(basis, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8", errors="replace")).hexdigest()[:20]


    def _record_artifact_error_locked(self, artifact: str, exc: Exception):
        errors = self._summary["diagnostics_quality"]["artifact_write_errors"]
        item = {"artifact": artifact, "error": compact_exception(exc)}
        if not errors or errors[-1] != item:
            errors.append(item)
            del errors[:-10]


    @staticmethod
    def _compact_exception_evidence(exception) -> dict:
        if not isinstance(exception, dict):
            return {}
        chain = exception.get("chain") if isinstance(exception.get("chain"), list) else []
        return {
            "type": exception.get("type") or "",
            "category": exception.get("category") or "",
            "message": redact_diagnostic_text(exception.get("message") or "", max_len=1000),
            "chain": [
                {
                    "type": item.get("type") or "",
                    "category": item.get("category") or "",
                    "message": redact_diagnostic_text(item.get("message") or "", max_len=500),
                }
                for item in chain[:5]
                if isinstance(item, dict)
            ],
            "traceback_available_in_problems_jsonl": bool(exception.get("traceback")),
        }


    @staticmethod
    def _compact_problem_context(details: dict) -> dict:
        context = {
            key: details.get(key)
            for key in (
                "service", "operation", "provider", "returncode", "return_code", "file_index", "files_total",
                "segment_index", "tts_segment_index", "attempt", "attempts_total", "max_attempts",
                "elapsed_sec", "timeout_sec", "ffmpeg_completed_ok",
                "expected_video_duration_sec", "expected_audio_duration_sec",
                "source_video_fps", "pause_count", "raw_pause_total_sec",
                "effective_video_pause_sec", "pause_frame_quantization_delta_sec",
            )
            if details.get(key) not in (None, "", [], {})
        }
        command = details.get("command")
        if isinstance(command, list) and command:
            context["command_preview"] = command[:16]
            context["command_items_total"] = len(command)
        stderr = details.get("stderr_tail") or details.get("stderr")
        if stderr:
            context["stderr_tail"] = redact_diagnostic_text(str(stderr)[-1000:], max_len=1000)
        validation = details.get("validation") if isinstance(details.get("validation"), dict) else {}
        if validation:
            context["validation"] = {
                "reason": validation.get("reason") or "",
                "valid": validation.get("valid"),
                "checks": validation.get("checks") or [],
                "timing": validation.get("timing") or {},
            }
        return context


    @staticmethod
    def _remediation_playbooks() -> dict:
        return {
            "network_retry": {
                "likely_cause": "Нестабильный сетевой маршрут/VPN, тайм-аут или ограничение внешнего сервиса.",
                "code_search": ["NetworkStormGuard", "VideoTranslator.translate_segment", "VideoTranslator.generate_tts"],
                "recommended_fix": "Проверить ограниченные повторы, тайм-ауты, паузу шторма и продолжение из контрольной точки; не ослаблять проверку успешного ответа.",
                "verification": "Повторить тот же файл: серия должна либо восстановиться, либо завершиться одной ясной terminal error с сохранённым прогрессом.",
            },
            "media_validation": {
                "likely_cause": "FFmpeg мог завершиться успешно, но фактические A/V-потоки не совпали с ожидаемой временной моделью или файл потерял поток.",
                "code_search": ["validate_output_media", "probe_media_timing", "assemble_final_video", "video_output_validation_failed"],
                "recommended_fix": "Сначала сравнить return_code, expected video/audio duration, фактические stream end и tolerance. Для Pause Sync отдельно проверить fps, число пауз и кадровое округление tpad (raw_pause_total против effective_video_pause). Не считать stderr предупреждение причиной, если FFmpeg вернул 0.",
                "verification": "Повторить тот же вход: корректный исходный A/V-разбег должен приниматься, а реальная обрезка видео/аудио — отклоняться с точным reason/delta.",
            },
            "user_cancel": {
                "likely_cause": "Пользователь запросил остановку во время обработки; это не обязательно баг программы.",
                "code_search": ["VideoTranslator.process", "VideoTranslatorApp._on_close", "save_batch_recovery_state"],
                "recommended_fix": "Не исправлять как ошибку без признаков самопроизвольной отмены; проверить контрольную точку и отсутствие принятого неполного результата.",
                "verification": "Отменить обработку вручную и убедиться, что исходник сохранён, а незавершённый пакет предлагается продолжить.",
            },
            "translation_tts": {
                "likely_cause": "Сбой внешнего сервиса перевода/озвучки либо некорректный ответ для конкретного сегмента.",
                "code_search": ["VideoTranslator.translate_segment", "VideoTranslator.generate_tts", "translation_segments_failed", "tts_summary"],
                "recommended_fix": "Сопоставить сегмент, провайдера и последнюю exception; сохранять готовые сегменты и не собирать неполное видео.",
                "verification": "Проверить повторный запуск, восстановление кэша/контрольной точки и полный итоговый счётчик сегментов.",
            },
            "ffmpeg_process": {
                "likely_cause": "FFmpeg/ffprobe завершился ошибкой, не поддержал параметр или создал неполный результат.",
                "code_search": ["run_subprocess", "run_subprocess_streaming", "output_has_video_and_audio", "file_pipeline_failed"],
                "recommended_fix": "Воспроизвести command, проверить returncode и конец stderr, затем сохранить строгую проверку видео+аудио.",
                "verification": "Запустить команду на проблемном входе и проверить ffprobe итогового MP4; исходник не удалять.",
            },
            "files_paths": {
                "likely_cause": "Файл отсутствует, занят, изменился, недоступен или путь не поддержан внешней программой.",
                "code_search": ["VideoTranslator.process", "VideoTranslatorApp._worker", "make_unique_output_path", "input_file_signature"],
                "recommended_fix": "Проверить существование/доступ/длину пути и конфликт результата; не удалять исходник при сбое.",
                "verification": "Повторить с тем же путём, с кириллицей и пробелами; подтвердить сохранность исходника и проверенный выход.",
            },
            "recovery_cache": {
                "likely_cause": "Контрольная точка или кэш не записались, устарели либо не соответствуют текущему входу.",
                "code_search": ["save_batch_recovery_state", "load_batch_recovery_state", "TTSCache", "input_file_signature"],
                "recommended_fix": "Проверить атомарную запись, сигнатуру входа и очистку только собственных записей.",
                "verification": "Имитировать прерывание и убедиться, что продолжились только незавершённые элементы без перезаписи готовых.",
            },
            "application_exception": {
                "likely_cause": "Причина определяется по первой exception/traceback и предшествующему этапу.",
                "code_search": ["ProblemLogger.write_exception", "VideoTranslator.process", "VideoTranslatorApp._worker"],
                "recommended_fix": "Найти первое место возникновения исключения, исправить первопричину и не заменять её пустым try/except.",
                "verification": "Добавить минимальный воспроизводящий тест и повторить исходный пользовательский сценарий.",
            },
        }


    @staticmethod
    def _compact_repeat_record(record: dict) -> dict:
        """Оставляет в контрольном повторе только изменяемый и поисковый контекст."""
        details = record.get("details") if isinstance(record.get("details"), dict) else {}
        compact_details = ProblemAnalysisMixin._compact_problem_context(details)
        if details.get("stage"):
            compact_details["stage"] = details.get("stage")
        if details.get("input_path"):
            compact_details["input_path"] = details.get("input_path")
        exception = ProblemAnalysisMixin._compact_exception_evidence(details.get("exception"))
        if exception:
            exception.pop("chain", None)
            compact_details["exception"] = exception
        result = dict(record)
        result["details"] = compact_details
        result["diagnostic"] = dict(record.get("diagnostic") or {})
        result["diagnostic"]["sample_kind"] = "repeat_checkpoint"
        return result


    @staticmethod
    def _issue_card(problem: dict) -> dict:
        event = str(problem.get("event") or "unknown")
        family = str(problem.get("category") or problem.get("family") or "runtime")
        level = str(problem.get("level") or "warning")
        occurrences = int(problem.get("occurrences_total") or problem.get("count") or 1)
        is_terminal = level == "error" or event.endswith(("_failed", "_crashed"))
        priority = "critical" if event in {"worker_thread_crashed", "application_startup_failed"} else (
            "high" if is_terminal else ("medium" if occurrences >= 3 else "low")
        )
        card = {
            "signature": problem.get("signature") or "",
            "priority": priority,
            "event": event,
            "family": family,
            "state": "terminal_failure" if is_terminal else "retry_or_warning",
            "occurrences_total": occurrences,
            "stored_samples": int(problem.get("stored_samples") or min(occurrences, 1)),
            "confirmed_evidence": {
                "message": problem.get("message") or "",
                "stage": problem.get("stage") or "",
                "input_path": problem.get("input_path") or "",
                "exception": ProblemAnalysisMixin._compact_exception_evidence(problem.get("exception")),
                "context": problem.get("last_context") or problem.get("context") or {},
                "first_sequence": problem.get("first_sequence"),
                "last_sequence": problem.get("last_sequence"),
                "first_timestamp": problem.get("first_timestamp") or "",
                "last_timestamp": problem.get("last_timestamp") or "",
            },
            "cause_status": "likely_not_proven",
            "do_not_assume": "Предположение становится подтверждённой причиной только после проверки evidence и соседних событий.",
        }
        if family in {"network", "network_timeout", "network_connection", "rate_limit", "timeout"}:
            card["playbook"] = "network_retry"
        elif event == "file_pipeline_cancelled":
            card["playbook"] = "user_cancel"
            card["cause_status"] = "confirmed_user_action_if_app_close_requested_precedes"
        elif family in {"text_to_speech", "translation"}:
            card["playbook"] = "translation_tts"
        elif family == "media_validation":
            card["playbook"] = "media_validation"
        elif family in {"external_process", "subprocess_failed", "ffmpeg"}:
            card["playbook"] = "ffmpeg_process"
        elif family in {"file_pipeline", "file_not_found", "permission_denied", "file_access", "path"}:
            card["playbook"] = "files_paths"
        elif family in {"recovery_and_cache", "cache"}:
            card["playbook"] = "recovery_cache"
        else:
            card["playbook"] = "application_exception"
        return card


    def _build_codex_analysis_locked(self) -> dict:
        counters = self._summary.get("counters") or {}
        warnings = int(counters.get("warning") or 0)
        errors = int(counters.get("error") or 0)
        categories = {
            key for key, count in (self._summary.get("problem_counts_by_category") or {}).items()
            if int(count or 0) > 0
        }
        problems = self._summary.get("recent_problems") or []
        repeated = sum(
            max(0, int(item.get("occurrences_total") or item.get("count") or 1) - 1)
            for item in problems
        )
        quality = self._summary.get("diagnostics_quality") or {}
        all_issue_cards = sorted(
            (self._issue_card(item) for item in problems),
            key=lambda item: (
                {"critical": 0, "high": 1, "medium": 2, "low": 3}.get(item.get("priority"), 4),
                -int(item.get("occurrences_total") or 0),
            ),
        )
        issue_cards = all_issue_cards[:10]

        observations = []
        if errors:
            observations.append(f"Записано ошибок уровня error: {errors}.")
        if warnings:
            observations.append(f"Записано предупреждений: {warnings}.")
        if repeated:
            observations.append(
                f"Повторных срабатываний уже известных сигнатур: {repeated}; "
                f"компактно не записано строк: {int(quality.get('repeat_events_compacted') or 0)}."
            )
        previous = self._summary.get("previous_session") or {}
        if previous.get("status") == "interrupted":
            observations.append("Подтверждено незавершённое закрытие предыдущей сессии.")
        if not observations:
            observations.append("На текущем этапе предупреждения и ошибки не зарегистрированы.")

        program_improvements = []
        if categories & {"network", "network_timeout", "connection", "text_to_speech"}:
            program_improvements.append({
                "priority": "high" if errors else "medium",
                "area": "network_and_tts",
                "proposal": "Проверить тайм-ауты, ограничение повторов, восстановление после сети и сохранение выбранного голоса.",
                "basis": "В сессии есть сетевые или TTS-события; точную причину подтвердить по problems.jsonl.",
            })
        if categories & {"media_validation"}:
            program_improvements.append({
                "priority": "high",
                "area": "media_validation",
                "proposal": "Сопоставить отдельные ожидаемые и фактические длительности video/audio, код FFmpeg и точную причину validation failure.",
                "basis": "Зарегистрирована ошибка проверки уже созданного медиафайла; это не равнозначно падению FFmpeg.",
            })
        if categories & {"external_process", "subprocess", "ffmpeg"}:
            program_improvements.append({
                "priority": "high",
                "area": "ffmpeg_pipeline",
                "proposal": "Проверить команду, код завершения, stderr и валидацию созданного медиафайла до принятия результата.",
                "basis": "Зарегистрирована проблема внешнего процесса или кодека.",
            })
        if categories & {"file_pipeline", "file_access", "permission", "path"}:
            program_improvements.append({
                "priority": "high" if errors else "medium",
                "area": "files_and_paths",
                "proposal": "Проверить существование, доступ, занятость, длинные пути и запрет удаления исходника при сбое.",
                "basis": "Есть проблема файлового конвейера или доступа к пути.",
            })
        if categories & {"recovery_and_cache", "cache"} or previous.get("status") == "interrupted":
            program_improvements.append({
                "priority": "medium",
                "area": "recovery",
                "proposal": "Проверить атомарность контрольных точек и безопасное продолжение только незавершённых элементов.",
                "basis": "Есть событие восстановления/кэша или прерванная прошлая сессия.",
            })
        if categories & {"application", "runtime", "unknown"} and errors:
            program_improvements.append({
                "priority": "high",
                "area": "application_code",
                "proposal": "Начать исправление с первого traceback и его цепочки exception; не подавлять исключение.",
                "basis": "Есть ошибка приложения, не отнесённая к более узкой подсистеме.",
            })
        if not program_improvements:
            program_improvements.append({
                "priority": "low",
                "area": "no_confirmed_change",
                "proposal": "Не менять рабочее поведение без подтверждённой проблемы; дождаться полного сценария или изучить events.jsonl.",
                "basis": "Текущая сессия пока не содержит диагностических оснований для исправления программы.",
            })

        logging_improvements = []
        missing_context = int(quality.get("problems_missing_stage_and_input") or 0)
        if missing_context:
            logging_improvements.append({
                "priority": "high",
                "proposal": "Добавить stage и input_path в события, где сейчас отсутствуют оба поля.",
                "basis": f"Проблем без этапа и входного объекта: {missing_context}.",
            })
        errors_without_traceback = int(quality.get("errors_without_traceback") or 0)
        if errors_without_traceback:
            logging_improvements.append({
                "priority": "high",
                "proposal": "Для исключений использовать write_exception, чтобы сохранялась цепочка и traceback.",
                "basis": f"Error-событий без traceback: {errors_without_traceback}.",
            })
        artifact_errors = quality.get("artifact_write_errors") or []
        if artifact_errors:
            logging_improvements.append({
                "priority": "high",
                "proposal": "Проверить доступ и свободное место для дополнительных диагностических артефактов.",
                "basis": f"Сбоев записи артефактов: {len(artifact_errors)}.",
            })
        if not logging_improvements:
            logging_improvements.append({
                "priority": "low",
                "proposal": "Сохранять текущую структуру; расширять поля только для нового подтверждённого класса проблем.",
                "basis": "Обязательный контекст присутствует, явных пробелов самопроверка не нашла.",
            })

        return {
            "generated_at": self._summary.get("updated_at") or "",
            "nature": "Автоматические подтверждённые счётчики и эвристические кандидаты; предложения требуют проверки по исходному коду и полной хронологии.",
            "result": "errors_detected" if errors else ("warnings_detected" if warnings else "no_problems_detected_yet"),
            "confirmed_observations": observations,
            "problem_categories": sorted(categories),
            "primary_issue_signature": issue_cards[0]["signature"] if issue_cards else "",
            "issue_cards": issue_cards,
            "issue_cards_omitted": max(0, len(all_issue_cards) - len(issue_cards)),
            "remediation_playbooks": self._remediation_playbooks(),
            "program_improvement_candidates": program_improvements,
            "logging_improvement_candidates": logging_improvements,
            "recommended_reading_order": [
                "report.md", "summary.json", "problems.jsonl", "events.jsonl"
            ],
            "next_checks": [
                "Сопоставить первую проблему с предыдущими 10–30 событиями в events.jsonl.",
                "Проверить traceback, команду, returncode и stderr, если эти поля применимы.",
                "Найти место записи event в коде и проверить первопричину, а не подавлять исключение.",
                "После исправления воспроизвести сценарий и сравнить новую отдельную сессию с проблемной.",
            ],
        }
