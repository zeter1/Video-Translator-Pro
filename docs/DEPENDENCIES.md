# DEPENDENCIES AND EXTERNAL BOUNDARIES

## Python packages

From `requirements.txt`:

| Dependency | Owner / purpose |
|---|---|
| `openai-whisper` | `speech/whisper.py` — speech recognition |
| `edge-tts` | `pipeline/tts_generate.py` — preferred voice provider |
| `gTTS` | `pipeline/tts_generate.py` — controlled fallback provider |
| `deep-translator` | `network/guard.py`, `pipeline/translation.py` — Google translation client |
| `moviepy<2` | fallback media duration/reading paths |
| `numpy` | `pipeline/timeline_mix.py` — fallback WAV mixing |

`torch` is normally pulled by Whisper and is detected lazily for CUDA support.

## External executables

- `ffmpeg`
- `ffprobe`

Discovery owner: `media/process.py`.

They may be in `PATH` or next to root `video_translator.py` / the packaged executable.

## Optional ffmpeg capabilities

The program detects capabilities at runtime rather than assuming them:

- hardware H.264 encoders where available;
- `rubberband` filter for higher-quality pitch-preserving tempo changes;
- fallback `atempo` when Rubber Band is unavailable.

Owners: `media/audio.py`, `media/final_video.py`.

## Network boundaries

- Google Translate via `deep-translator`;
- Microsoft Edge TTS;
- Google gTTS fallback.

Network/VPN failures must not be confused with local FFmpeg or Whisper failures. Network storm and selected-voice preservation logic live under `network/` and `pipeline/network_voice.py`.
