"""Method owner for ProblemLogger: _manifest_payload_locked, _write_manifest_locked, _write_latest_index_locked, _markdown_cell, _render_report_locked, _update_summary_locked, _sync_stored_sample_locked, _write_summary_locked."""

from __future__ import annotations

from datetime import datetime
import os
from videotranslator.config import APP_DIAGNOSTIC_VERSION, PROBLEM_INDEX_SCHEMA_VERSION, PROBLEM_LOG_MAX_FILES, PROBLEM_LOG_RETENTION_DAYS, PROBLEM_LOG_SCHEMA_VERSION, PROBLEM_MANIFEST_SCHEMA_VERSION
from videotranslator.core.io import atomic_write_json
from videotranslator.diagnostics.files import problem_log_relative_path

class ProblemReportRenderMixin:
    """Focused behavior-preserving mixin extracted for AI-local navigation."""

    def _manifest_payload_locked(self) -> dict:
        return {
            "schema_version": PROBLEM_MANIFEST_SCHEMA_VERSION,
            "artifact_type": "video_translator_problem_session",
            "diagnostic_schema_version": PROBLEM_LOG_SCHEMA_VERSION,
            "app_version": APP_DIAGNOSTIC_VERSION,
            "session_id": self.session_id,
            "status": self._summary.get("session_status") or self._summary.get("status"),
            "created_from_process": os.getpid(),
            "updated_at": self._summary.get("updated_at") or datetime.now().astimezone().isoformat(timespec="seconds"),
            "encoding": "UTF-8",
            "files": {
                "report.md": "Краткий читаемый отчёт и порядок анализа.",
                "summary.json": "Машиночитаемое текущее состояние, группы проблем и подсказки.",
                "problems.jsonl": "Warning/error; первые примеры и контрольные повторы, одна JSON-запись на строку.",
                "events.jsonl": "Информационная временная шкала без дублирования warning/error; объединяется с problems.jsonl по sequence.",
                "manifest.json": "Описание этой папки и схемы артефактов.",
            },
            "repeat_compaction": {
                "exact_totals_in": "summary.json -> recent_problems[].occurrences_total",
                "stored_samples_in": "summary.json -> recent_problems[].stored_samples",
                "policy": "Первые 3, затем 5/10/20/50/100, каждый 250-й и последний итог в summary/report.",
                "loss_boundary": "Промежуточные одинаковые повторы не имеют отдельных JSONL-строк; первый/последний контекст и точный счётчик сохранены в summary.json.",
            },
            "retention": {
                "days": PROBLEM_LOG_RETENTION_DAYS,
                "max_sessions": PROBLEM_LOG_MAX_FILES,
                "scope": "Удаляются только папки с marker artifact_type=video_translator_problem_session.",
            },
            "validation_boundaries": [
                "Отчёт не доказывает визуальную корректность GUI.",
                "Успешный subprocess не доказывает качество итогового перевода или озвучки без проверки медиа.",
                "Сетевые причины являются подтверждёнными только при наличии соответствующей ошибки в событии.",
            ],
        }

    def _write_manifest_locked(self):
        atomic_write_json(self.manifest_path, self._manifest_payload_locked())

    def _write_latest_index_locked(self):
        files = {
            "report": problem_log_relative_path(self.root_dir, self.report_path),
            "summary": problem_log_relative_path(self.root_dir, self.summary_path),
            "problems": problem_log_relative_path(self.root_dir, self.problem_path),
            "events": problem_log_relative_path(self.root_dir, self.path),
            "manifest": problem_log_relative_path(self.root_dir, self.manifest_path),
        }
        index = {
            "schema_version": PROBLEM_INDEX_SCHEMA_VERSION,
            "artifact_type": "video_translator_latest_session_pointer",
            "updated_at": self._summary.get("updated_at") or "",
            "session_id": self.session_id,
            "status": self._summary.get("session_status") or self._summary.get("status"),
            "session_dir": problem_log_relative_path(self.root_dir, self.session_dir),
            "files": files,
            "start_here": files["report"],
        }
        atomic_write_json(self.latest_index_path, index)

    @staticmethod
    def _markdown_cell(value) -> str:
        return str(value if value is not None else "").replace("|", "\\|").replace("\r", " ").replace("\n", " ")

    def _render_report_locked(self) -> str:
        summary = self._summary
        counters = summary.get("counters") or {}
        batch = summary.get("batch") or {}
        current = summary.get("current_file") or {}
        analysis = summary.get("codex_analysis") or {}
        quality = summary.get("diagnostics_quality") or {}
        lines = [
            "# Диагностический отчёт сессии",
            "",
            f"- Сессия: `{self.session_id}`",
            f"- Статус: `{summary.get('session_status') or summary.get('status')}`",
            f"- Обновлено: `{summary.get('updated_at') or 'ещё нет событий'}`",
            f"- События: {sum(int(value or 0) for value in counters.values())}; предупреждения: {int(counters.get('warning') or 0)}; ошибки: {int(counters.get('error') or 0)}",
            f"- Строк сохранено: {int(quality.get('records_stored_total') or 0)}; компактно объединено повторов: {int(quality.get('repeat_events_compacted') or 0)}; приблизительно сэкономлено: {int(quality.get('estimated_bytes_saved') or 0)} байт",
            f"- Текущий этап: `{summary.get('current_stage') or 'не указан'}`",
            f"- Текущий файл: `{summary.get('current_input_path') or 'не выбран'}`",
            "",
            "## Быстрый вывод",
            "",
        ]
        lines.extend(f"- {item}" for item in analysis.get("confirmed_observations") or [])
        lines.extend([
            "",
            "## Пакет и текущий файл",
            "",
            f"- Пакет: статус `{batch.get('status') or 'idle'}`, всего {int(batch.get('total') or 0)}, успешно {int(batch.get('successful') or 0)}, ошибок {int(batch.get('failed') or 0)}, ожидают {int(batch.get('pending') or 0)}.",
            f"- Файл: статус `{current.get('status') or 'idle'}`, этап `{current.get('stage') or 'не указан'}`, результат `{current.get('output_path') or 'не создан'}`.",
            "",
            "## Сгруппированные проблемы",
            "",
        ])
        problems = summary.get("recent_problems") or []
        if problems:
            lines.extend([
                "| Уровень | Событие | Категория | Всего | Строк | Этап | Сообщение |",
                "|---|---|---|---:|---:|---|---|",
            ])
            for item in problems:
                lines.append(
                    "| {level} | {event} | {category} | {count} | {stored} | {stage} | {message} |".format(
                        level=self._markdown_cell(item.get("level")),
                        event=self._markdown_cell(item.get("event")),
                        category=self._markdown_cell(item.get("category") or item.get("family")),
                        count=int(item.get("occurrences_total") or item.get("count") or 1),
                        stored=int(item.get("stored_samples") or 1),
                        stage=self._markdown_cell(item.get("stage")),
                        message=self._markdown_cell(item.get("message")),
                    )
                )
        else:
            lines.append("Пока предупреждений и ошибок нет.")

        lines.extend(["", "## Карточки исправления проблем", ""])
        issue_cards = analysis.get("issue_cards") or []
        if issue_cards:
            playbooks = analysis.get("remediation_playbooks") or {}
            for index, card in enumerate(issue_cards, 1):
                evidence = card.get("confirmed_evidence") or {}
                playbook_name = card.get("playbook") or "application_exception"
                playbook = playbooks.get(playbook_name) or {}
                context = evidence.get("context") if isinstance(evidence.get("context"), dict) else {}
                tech_parts = []
                if context.get("return_code") is not None or context.get("returncode") is not None:
                    tech_parts.append(f"return_code={context.get('return_code', context.get('returncode'))}")
                if context.get("ffmpeg_completed_ok") is not None:
                    tech_parts.append(f"ffmpeg_completed_ok={context.get('ffmpeg_completed_ok')}")
                if context.get("expected_video_duration_sec") is not None:
                    tech_parts.append(f"expected_video={context.get('expected_video_duration_sec')}с")
                if context.get("expected_audio_duration_sec") is not None:
                    tech_parts.append(f"expected_audio={context.get('expected_audio_duration_sec')}с")
                if context.get("source_video_fps") is not None:
                    tech_parts.append(f"source_fps={context.get('source_video_fps')}")
                if context.get("pause_count") is not None:
                    tech_parts.append(f"pause_count={context.get('pause_count')}")
                if context.get("raw_pause_total_sec") is not None:
                    tech_parts.append(f"raw_pause={context.get('raw_pause_total_sec')}с")
                if context.get("effective_video_pause_sec") is not None:
                    tech_parts.append(f"video_pause={context.get('effective_video_pause_sec')}с")
                if context.get("pause_frame_quantization_delta_sec") is not None:
                    tech_parts.append(f"pause_quant_delta={float(context.get('pause_frame_quantization_delta_sec')):+.3f}с")
                validation = context.get("validation") if isinstance(context.get("validation"), dict) else {}
                if validation.get("reason"):
                    tech_parts.append(f"validation_reason={validation.get('reason')}")
                for check in (validation.get("checks") or [])[:2]:
                    if not isinstance(check, dict):
                        continue
                    stream = check.get("stream") or "stream"
                    if check.get("end_delta_sec") is not None:
                        tech_parts.append(
                            f"{stream}_delta={float(check.get('end_delta_sec')):+.3f}с "
                            f"(tol={float(check.get('tolerance') or 0):.3f}с)"
                        )
                lines.extend([
                    f"### {index}. {card.get('event', 'unknown')} — {card.get('priority', 'medium')}",
                    "",
                    f"- Сигнатура: `{card.get('signature') or 'нет'}`; всего: {int(card.get('occurrences_total') or 0)}; сохранено строк: {int(card.get('stored_samples') or 0)}.",
                    f"- Подтверждено логом: {evidence.get('message') or 'сообщение отсутствует'} Этап: `{evidence.get('stage') or 'не указан'}`.",
                    *( [f"- Технический контекст: `{' | '.join(tech_parts)}`."] if tech_parts else [] ),
                    f"- Сценарий исправления: `{playbook_name}`.",
                    f"- Вероятная причина: {playbook.get('likely_cause') or 'требует анализа'}",
                    f"- Где искать в коде: `{', '.join(playbook.get('code_search') or [])}`.",
                    f"- Как исправлять: {playbook.get('recommended_fix') or ''}",
                    f"- Как проверить: {playbook.get('verification') or ''}",
                    f"- Ограничение вывода: {card.get('do_not_assume') or ''}",
                    "",
                ])
        else:
            lines.append("Карточек нет: в сессии не зарегистрированы warning/error.")
        omitted_cards = int(analysis.get("issue_cards_omitted") or 0)
        if omitted_cards:
            lines.append(
                f"\nЕщё карточек вне компактного отчёта: {omitted_cards}. Их счётчики доступны в "
                "`problem_counts_by_event`/`problem_counts_by_category`, а примеры — в `problems.jsonl`."
            )

        lines.extend(["", "## Что можно улучшить в программе", ""])
        for item in analysis.get("program_improvement_candidates") or []:
            lines.append(f"- **{item.get('priority', 'medium')} / {item.get('area', 'general')}** — {item.get('proposal', '')} Основание: {item.get('basis', '')}")
        lines.extend(["", "## Что можно улучшить в логах", ""])
        for item in analysis.get("logging_improvement_candidates") or []:
            lines.append(f"- **{item.get('priority', 'medium')}** — {item.get('proposal', '')} Основание: {item.get('basis', '')}")
        lines.extend(["", "## Самые частые события", ""])
        event_counts = sorted(
            (summary.get("event_counts_by_event") or {}).items(),
            key=lambda pair: (-int(pair[1] or 0), pair[0]),
        )[:12]
        if event_counts:
            lines.extend(f"- `{event}` — {int(count or 0)}" for event, count in event_counts)
        else:
            lines.append("События ещё не зарегистрированы.")
        lines.extend([
            "",
            "## Порядок дальнейшего анализа",
            "",
        ])
        lines.extend(f"{index}. {item}" for index, item in enumerate(analysis.get("next_checks") or [], 1))
        lines.extend([
            "",
            "## Файлы этой сессии",
            "",
            "- `summary.json` — полная компактная сводка.",
            "- `problems.jsonl` — выборка предупреждений/ошибок; точные итоги повторов находятся в `summary.json`.",
            "- `events.jsonl` — информационная временная шкала без дублей warning/error; обе JSONL объединяются по `sequence`.",
            "- `manifest.json` — схема и границы достоверности.",
            "",
            "> Автоматические предложения — кандидаты для проверки, а не доказанная первопричина.",
            "",
        ])
        return "\n".join(lines)
