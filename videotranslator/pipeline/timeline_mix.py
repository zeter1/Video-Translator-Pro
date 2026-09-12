"""Method owner for VideoTranslator: build_timeline, _mix_in_batches, _amix_batch, _amix_wavs, _mix_numpy, _create_silence."""

from __future__ import annotations

from videotranslator.core.compat_bridge import call_legacy_override

import os
import math
import wave
from videotranslator.config import BATCH_SIZE, SAMPLE_RATE, VOICE_LIMIT
from videotranslator.core.cancel import CancelledError
from videotranslator.media.audio import calc_audio_work_timeout, master_voice_audio as _legacy_master_voice_audio
from videotranslator.media.process import run_subprocess as _legacy_run_subprocess


def master_voice_audio(*args, **kwargs):
    return call_legacy_override('master_voice_audio', _legacy_master_voice_audio, *args, **kwargs)

def run_subprocess(*args, **kwargs):
    return call_legacy_override('run_subprocess', _legacy_run_subprocess, *args, **kwargs)

class TimelineMixMixin:
    """Focused behavior-preserving mixin extracted for AI-local navigation."""

    def _mix_in_batches(self, segments: list, video_dur: float) -> str:
        if len(segments) <= BATCH_SIZE:
            return self._amix_batch(segments, video_dur, "final")

        batches = [segments[i:i + BATCH_SIZE] for i in range(0, len(segments), BATCH_SIZE)]
        self.log(f"      ℹ️ Разбито на {len(batches)} пачек по ≤{BATCH_SIZE} сегм.")

        batch_wavs = []
        for batch_index, batch in enumerate(batches, 1):
            self._check_cancel()
            self.log(f"      🔄 Пачка {batch_index}/{len(batches)}...")
            batch_duration = video_dur
            if batch_index < len(batches):
                # Intermediate PCM needs only its speech and a limiter tail.
                # Keep the final batch full-length to anchor the final mix.
                try:
                    speech_end = 0.0
                    for delay_ms, path in batch:
                        self._check_cancel()
                        with wave.open(path, "rb") as audio:
                            duration = audio.getnframes() / audio.getframerate()
                        speech_end = max(speech_end, delay_ms / 1000 + duration)
                    batch_duration = min(video_dur, math.ceil((speech_end + 0.1) * 1000) / 1000)
                except (OSError, EOFError, wave.Error, ValueError, ZeroDivisionError):
                    # MP3 fallback/unknown formats keep the previous safe path.
                    batch_duration = video_dur
            batch_wavs.append(self._amix_batch(batch, batch_duration, f"batch_{batch_index:03d}"))

        if len(batch_wavs) == 1:
            return batch_wavs[0]

        round_index = 1
        while len(batch_wavs) > BATCH_SIZE:
            next_round = []
            groups = [batch_wavs[i:i + BATCH_SIZE] for i in range(0, len(batch_wavs), BATCH_SIZE)]
            self.log(f"      🔗 Слияние WAV, круг {round_index}: {len(groups)} пачек...")
            for group_index, group in enumerate(groups, 1):
                self._check_cancel()
                next_round.append(self._amix_wavs(group, video_dur, f"merge_{round_index:02d}_{group_index:03d}"))
            batch_wavs = next_round
            round_index += 1

        self.log(f"      🔗 Финальное слияние {len(batch_wavs)} промежуточных WAV...")
        return self._amix_wavs(batch_wavs, video_dur, "final")

    def _amix_batch(self, segments: list, video_dur: float, tag: str) -> str:
        out_path = os.path.join(self.temp_dir, f"mix_{tag}.wav")

        if not segments:
            self._create_silence(out_path, video_dur)
            return out_path

        inputs = [
            "-f", "lavfi",
            "-i", f"anullsrc=channel_layout=stereo:sample_rate={SAMPLE_RATE}:duration={video_dur:.3f}",
        ]
        filter_parts = ["[0:a]anull[base]"]
        delayed_labels = ["[base]"]

        for idx, (delay_ms, file_path) in enumerate(segments):
            input_index = idx + 1
            inputs += ["-i", file_path]
            label = f"d{idx}"
            filter_parts.append(
                f"[{input_index}:a]aresample={SAMPLE_RATE},"
                "aformat=sample_fmts=fltp:channel_layouts=stereo,"
                f"adelay={delay_ms}|{delay_ms}[{label}]"
            )
            delayed_labels.append(f"[{label}]")

        # Смешиваем пачку одним amix, а не цепочкой amix→amix→amix: меньше накопленных артефактов.
        filter_parts.append(
            "".join(delayed_labels)
            + f"amix=inputs={len(delayed_labels)}:duration=first:dropout_transition=0:normalize=0,"
            + f"alimiter=limit={VOICE_LIMIT:.2f}[out]"
        )

        filter_complex = ";".join(filter_parts)
        cmd = [self.ffmpeg, "-y"] + inputs + [
            "-filter_complex", filter_complex,
            "-map", "[out]",
            "-t", f"{video_dur:.3f}",
            "-ar", str(SAMPLE_RATE),
            "-ac", "2",
            out_path,
        ]

        try:
            mix_timeout = calc_audio_work_timeout(video_dur, min_timeout=360, factor=0.70, extra=180)
            result = run_subprocess(cmd, timeout=mix_timeout, log=self.log, cancel_event=self.cancel)
            if result.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 100:
                return out_path
            self.log(f"      ⚠️ ffmpeg amix ошибка: {(result.stderr or '')[-600:].strip()}")
        except CancelledError:
            raise
        except Exception as exc:
            self.log(f"      ⚠️ ffmpeg amix исключение: {exc}")

        self.log("      ↩️ Переключаемся на numpy-метод для этой пачки...")
        return self._mix_numpy(segments, video_dur, out_path)

    def _amix_wavs(self, wav_files: list, video_dur: float, tag: str = "final") -> str:
        out_path = os.path.join(self.temp_dir, f"final_voice_{tag}.wav")
        inputs = []
        for wav_file in wav_files:
            inputs += ["-i", wav_file]

        n = len(wav_files)
        filter_complex = "".join(f"[{i}:a]" for i in range(n)) + \
            f"amix=inputs={n}:duration=longest:dropout_transition=0:normalize=0,alimiter=limit={VOICE_LIMIT:.2f}[out]"

        cmd = [self.ffmpeg, "-y"] + inputs + [
            "-filter_complex", filter_complex,
            "-map", "[out]",
            "-t", f"{video_dur:.3f}",
            "-ar", str(SAMPLE_RATE),
            "-ac", "2",
            out_path,
        ]

        try:
            mix_timeout = calc_audio_work_timeout(video_dur, min_timeout=360, factor=0.70, extra=240)
            result = run_subprocess(cmd, timeout=mix_timeout, log=self.log, cancel_event=self.cancel)
            if result.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 100:
                return out_path
            self.log(f"      ⚠️ Финальный amix ошибка: {(result.stderr or '')[-600:].strip()}")
        except CancelledError:
            raise
        except Exception as exc:
            self.log(f"      ⚠️ Финальный amix исключение: {exc}")

        self.log("      ↩️ Финальное слияние через numpy...")
        return self._mix_numpy([], video_dur, out_path, extra_wavs=wav_files)

    def _mix_numpy(self, segments: list, video_dur: float, out_path: str, extra_wavs: list = None) -> str:
        import numpy as np

        fps = SAMPLE_RATE
        total_samples = max(1, int(video_dur * fps))
        timeline = np.zeros((total_samples, 2), dtype=np.float32)

        def add_file(file_path: str, offset_samples: int):
            clip = None
            try:
                self._check_cancel()
                from moviepy.editor import AudioFileClip
                if not 0 <= offset_samples < total_samples:
                    raise ValueError("фраза находится за пределами таймлайна")
                clip = AudioFileClip(file_path)
                arr = clip.to_soundarray(fps=fps)
                self._check_cancel()
                if not len(arr):
                    raise ValueError("декодер вернул пустую аудиодорожку")
                if arr.ndim == 1:
                    arr = np.stack([arr, arr], axis=1)
                elif arr.shape[1] == 1:
                    arr = np.repeat(arr, 2, axis=1)
                elif arr.shape[1] > 2:
                    arr = arr[:, :2]

                arr = arr.astype(np.float32)
                end = min(offset_samples + len(arr), total_samples)
                if end <= offset_samples:
                    raise ValueError("фраза не попала в итоговую аудиодорожку")
                timeline[offset_samples:end] += arr[:end - offset_samples]
            except CancelledError:
                raise
            except Exception as exc:
                raise RuntimeError(
                    f"Не удалось добавить обязательную фразу {os.path.basename(file_path)}: {exc}. "
                    "Смешивание остановлено, чтобы не потерять речь."
                ) from exc
            finally:
                if clip:
                    try:
                        clip.close()
                    except Exception:
                        pass

        for delay_ms, file_path in (segments or []):
            self._check_cancel()
            add_file(file_path, int(delay_ms / 1000 * fps))

        for wav_file in (extra_wavs or []):
            self._check_cancel()
            add_file(wav_file, 0)

        self._check_cancel()
        peak = float(np.abs(timeline).max())
        if peak > 0.95:
            timeline = timeline / peak * 0.93

        import wave
        with wave.open(out_path, "w") as wav:
            wav.setnchannels(2)
            wav.setsampwidth(2)
            wav.setframerate(fps)
            wav.writeframes((timeline * 32767).clip(-32768, 32767).astype(np.int16).tobytes())

        return out_path

    def _create_silence(self, out_path: str, duration: float):
        cmd = [
            self.ffmpeg, "-y",
            "-f", "lavfi",
            "-i", f"anullsrc=channel_layout=stereo:sample_rate={SAMPLE_RATE}",
            "-t", f"{duration:.3f}",
            out_path,
        ]
        result = run_subprocess(cmd, timeout=60, log=self.log, cancel_event=self.cancel)
        if result.returncode != 0:
            raise RuntimeError(f"Не удалось создать тишину: {(result.stderr or '')[-400:].strip()}")
