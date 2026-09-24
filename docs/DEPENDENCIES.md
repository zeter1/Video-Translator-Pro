# DEPENDENCIES AND EXTERNAL BOUNDARIES

## Python packages

From `requirements.txt`:

| Dependency | Owner / purpose |
|---|---|
| `openai-whisper>=20250625` | `speech/whisper.py` — speech recognition; word timestamps + hallucination silence guard |
| `edge-tts>=7.2.8` | `pipeline/tts_generate.py` — preferred voice provider |
| `gTTS` | `pipeline/tts_generate.py` — controlled fallback provider |
| `deep-translator` | `network/guard.py`, `pipeline/translation.py` — Google translation client |
| `moviepy<2` | fallback media duration/reading paths |
| `numpy` | `pipeline/timeline_mix.py` — fallback WAV mixing |

`torch` is normally pulled by Whisper and is detected lazily for CUDA support.

Optional local AI runtime is listed separately in `requirements-optional-ai.txt`:
`argostranslate`, `transformers<6`, `sentencepiece`, `huggingface_hub`, and
`piper-tts`. Model/voice data is not bundled with source or EXE; it is managed
from the local Models tab.

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

## Frozen Windows boundary

Python modules are part of the PyInstaller artifact and cannot be made importable
inside an already frozen EXE by installing them into some external Python later.
`build_exe.bat` therefore installs optional AI runtime before freezing, and
`tools/build_exe.py` explicitly collects dynamically imported packages. Runtime
downloads are limited to model/language/voice data.


## FFmpeg dependency bootstrap

Windows setup/build uses `tools/ensure_ffmpeg_windows.py`. Existing local/PATH ffmpeg+ffprobe are reused; otherwise the portable release-essentials ZIP is downloaded and SHA-256 checked. PyInstaller then bundles the exact pair. This keeps the delivered EXE independent of the target machine PATH.
