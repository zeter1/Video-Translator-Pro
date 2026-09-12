"""Method owner for VideoTranslator: _edge_tts_async, _run_edge_tts, generate_tts, _tts_segment_log, _get_prepared_tts_audio, _prepare_tts_segment."""

from __future__ import annotations

import hashlib
import math
import os
from videotranslator.config import MAX_NATURAL_TEMPO, MIN_INSERTED_PAUSE, SAMPLE_RATE, TTS_PREPARED_CACHE_SCHEMA_VERSION
from videotranslator.media.audio import get_audio_duration, polish_tts_audio, time_stretch_audio
from videotranslator.sync.pause import edge_rate_from_speed

class TTSPrepareMixin:
    """Focused behavior-preserving mixin extracted for AI-local navigation."""

    def _get_prepared_tts_audio(self, text: str, voice: str, index: int,
                                rate_pct: int, tag: str) -> str | None:
        """Восстанавливает готовый WAV или создаёт и атомарно сохраняет его в постоянный кэш."""
        target_code = self.target_info.get("code") or "target"
        prepared_path = os.path.join(self.temp_dir, f"seg_{index:05d}_{tag}.wav")
        prepared_extra = {
            "audio_settings": self.audio_settings,
            "sample_rate": SAMPLE_RATE,
            "prepared_cache_schema": TTS_PREPARED_CACHE_SCHEMA_VERSION,
        }

        # Кэш Edge и кэш gTTS разделены. Иначе WAV, когда-то созданный женским
        # резервным gTTS, мог выдаваться за выбранный мужской Edge-голос.
        cache_providers = ["edge_tts"]
        if self._tts_allow_gtts_fallback and rate_pct == 0:
            cache_providers.append("gtts")

        for source_provider in cache_providers:
            cache_key = self.tts_cache.make_key(
                text,
                voice,
                rate_pct,
                f"prepared_wav_{source_provider}",
                target_code,
                extra={**prepared_extra, "source_provider": source_provider},
            )
            with self.tts_cache.key_lock(cache_key):
                if self.tts_cache.restore(cache_key, prepared_path, suffix=".wav"):
                    self._increment_tts_stat("cache_hits")
                    self._increment_tts_stat("prepared_cache_hits")
                    if source_provider == "gtts":
                        self._increment_tts_stat("gtts_prepared_cache_hits")
                        self._mark_gtts_fallback(index)
                    return prepared_path

        raw_path = os.path.join(self.temp_dir, f"seg_{index:05d}_{tag}.mp3")
        source_provider = self.generate_tts(
            text,
            raw_path,
            voice,
            rate_pct=rate_pct,
            segment_index=index,
        )
        if not source_provider:
            return None

        cache_key = self.tts_cache.make_key(
            text,
            voice,
            rate_pct,
            f"prepared_wav_{source_provider}",
            target_code,
            extra={**prepared_extra, "source_provider": source_provider},
        )
        with self.tts_cache.key_lock(cache_key):
            # Пока ожидали блокировку, другой поток мог уже подготовить тот же WAV.
            if self.tts_cache.restore(cache_key, prepared_path, suffix=".wav"):
                self._increment_tts_stat("cache_hits")
                self._increment_tts_stat("prepared_cache_hits")
                if source_provider == "gtts":
                    self._increment_tts_stat("gtts_prepared_cache_hits")
                    self._mark_gtts_fallback(index)
                return prepared_path

            if polish_tts_audio(
                self.ffmpeg,
                raw_path,
                prepared_path,
                lambda message: self._tts_segment_log(index, message),
                cancel_event=self.cancel,
                audio_settings=self.audio_settings,
            ):
                cache_stored = self.tts_cache.store(
                    cache_key,
                    prepared_path,
                    {
                        "provider": f"prepared_wav_{source_provider}",
                        "source_provider": source_provider,
                        "target_language": target_code,
                        "requested_voice": voice,
                        "voice_selection_supported": source_provider == "edge_tts",
                        "rate_pct": rate_pct,
                        "audio_settings": self.audio_settings,
                        "sample_rate": SAMPLE_RATE,
                        "text_length": len(text),
                        "text_sha256": hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest(),
                    },
                    suffix=".wav",
                )
                if not cache_stored:
                    self._increment_tts_stat("prepared_cache_skipped_size_limit")
                return prepared_path
            return raw_path

    def _prepare_tts_segment(self, text: str, voice: str, index: int,
                             target_slot: float, hard_slot: float) -> tuple[str, float, dict] | tuple[None, float, dict]:
        """
        Готовит один сегмент без обрезки слов:
          1) Edge TTS обычным темпом;
          2) чистка/ресэмпл в WAV;
          3) если длинно — повторная генерация Edge TTS с нативным rate;
          4) если всё ещё длинно — tempo stretch без изменения pitch;
          5) если для попадания нужен темп выше выбранного лимита — не режем речь, а вставляем паузу видео.
        """
        stats = {
            "native_rate": 0,
            "tempo": 1.0,
            "tempo_method": "",
            "video_pause_needed": False,
            "total_speed": 1.0,
        }

        current_path = self._get_prepared_tts_audio(text, voice, index, 0, "edge_0")
        if not current_path:
            return None, 0.0, stats
        segment_log = lambda message: self._tts_segment_log(index, message)
        current_dur = get_audio_duration(self.ffprobe, current_path, segment_log)
        if not math.isfinite(current_dur) or current_dur <= 0:
            return None, 0.0, stats

        base_dur = current_dur
        speed_needed_total = base_dur / target_slot if target_slot > 0 else 1.0

        # Часть ускорения отдаём самому Edge TTS, но общий темп не должен превышать выбранный лимит.
        # Даже если фраза сильно длинная, мы просим TTS ускориться только в пределах качественного лимита,
        # а остаток компенсируем паузой видео.
        native_rate = edge_rate_from_speed(
            min(speed_needed_total, self.speech_speed_limit),
            speed_limit=self.speech_speed_limit,
        )
        if native_rate > 0:
            self._check_cancel()
            candidate = self._get_prepared_tts_audio(
                text, voice, index, native_rate, f"edge_{native_rate}"
            )
            if candidate:
                candidate_dur = get_audio_duration(self.ffprobe, candidate, segment_log)
                if 0 < candidate_dur < current_dur * 0.995:
                    current_path = candidate
                    current_dur = candidate_dur
                    stats["native_rate"] = native_rate

        # Оцениваем достигнутое ускорение и разрешаем post-tempo только до выбранного общего лимита.
        effective_speed = max(1.0, base_dur / current_dur) if current_dur > 0 else 1.0
        remaining_tempo_cap = max(1.0, self.speech_speed_limit / effective_speed)
        speed_needed_now = current_dur / target_slot if target_slot > 0 else 1.0

        if speed_needed_now > 1.025 and remaining_tempo_cap > 1.025:
            self._check_cancel()
            tempo = min(speed_needed_now, remaining_tempo_cap, MAX_NATURAL_TEMPO)
            if tempo > 1.025:
                stretched = os.path.join(self.temp_dir, f"seg_{index:05d}_tempo.wav")
                ok, method = time_stretch_audio(
                    self.ffmpeg, current_path, stretched, tempo, log=segment_log, cancel_event=self.cancel
                )
                if ok:
                    stretched_dur = get_audio_duration(self.ffprobe, stretched, segment_log)
                    if math.isfinite(stretched_dur) and stretched_dur > 0:
                        current_path = stretched
                        current_dur = stretched_dur
                        stats["tempo"] = tempo
                        stats["tempo_method"] = method
                    else:
                        segment_log("⚠️ Длительность ускоренной фразы не подтверждена; сохранена исходная озвучка.")

        stats["total_speed"] = max(1.0, base_dur / current_dur) if current_dur > 0 else 1.0
        if current_dur > hard_slot + MIN_INSERTED_PAUSE:
            stats["video_pause_needed"] = True

        return current_path, current_dur, stats
