# Video Translator Pro

Windows desktop application for translating and dubbing videos with speech recognition, machine translation, neural text-to-speech and FFmpeg-based media processing.

The project is designed not only to generate a translated audio track, but also to preserve speech quality when the translated phrase is longer than the original timing slot.

## Highlights

- Speech recognition with **OpenAI Whisper**.
- Translation through **deep-translator / Google Translator**.
- Neural dubbing with **Edge TTS**, with controlled **gTTS fallback**.
- **Pause Sync** mode: long translated phrases are not aggressively compressed or cut; the video can insert a short freeze/pause so the phrase can finish naturally.
- Configurable voice-speed limit: **x1.15 / x1.20 / x1.25**.
- FFmpeg/FFprobe pipeline for audio extraction, processing, muxing and final validation.
- Optional original-audio background mix.
- Multi-language target support.
- Persistent TTS cache for recovery after failures or cancellation.
- Translation checkpoints and batch recovery after interrupted processing.
- Verification of completed MP4 files before they are treated as successful.
- Structured diagnostic sessions intended for troubleshooting with **ChatGPT / Codex**.

## Reliability and recovery

The application contains several mechanisms for long-running jobs:

- atomic translation checkpoints;
- recovery of unfinished batches after an abnormal shutdown;
- reuse of successfully generated TTS segments;
- bounded retries and network-storm backoff;
- separate protection for translation, Edge TTS and gTTS network failures;
- periodic heartbeat events during long Whisper and FFmpeg stages;
- validation that the final MP4 contains both video and audio streams;
- cleanup and size limits for recovery/TTS cache data.

## Pause Sync

Translated speech can be substantially longer than the original speech. Instead of forcing every phrase into the original slot with extreme speed-up, Video Translator Pro uses the following strategy:

1. Use available natural silence after the original phrase.
2. Apply moderate TTS/tempo acceleration within the configured limit.
3. If the phrase still does not fit, insert a video freeze/pause until the translated phrase finishes.

This can make the final video slightly longer than the source, but preserves the full translated phrase and reduces robotic-sounding speech.

## Requirements

- Windows 10/11.
- Python **3.10–3.13**.
- FFmpeg and FFprobe available in `PATH`, or `ffmpeg.exe` and `ffprobe.exe` placed next to `video_translator.py`.
- Python dependencies from `requirements.txt`.

## Installation

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install --upgrade pip
py -m pip install -r requirements.txt
```

Then install FFmpeg or place `ffmpeg.exe` and `ffprobe.exe` next to the program.

## Run

```powershell
py video_translator.py
```

On Windows you can also use `Запустить.bat` after installing the dependencies.

## Main Python dependencies

- `openai-whisper`
- `edge-tts`
- `gTTS`
- `deep-translator`
- `moviepy<2`
- `numpy`
- `requests`

## Runtime data

During normal operation the application can create diagnostic logs, translation checkpoints, TTS cache data, temporary media and user settings. These runtime artifacts are excluded from Git through `.gitignore` and should not be committed to the repository.

## Diagnostics

Each diagnostic session is designed to keep useful context for debugging while avoiding huge repetitive logs. The logging system separates informational events from warnings/errors, groups repeated failures by stable signatures, keeps traceback/context for real failures and produces compact summaries for analysis with ChatGPT or Codex.

## Notes

- Whisper can only translate speech that it successfully recognizes; speech hidden by noise/music may be missed.
- Edge TTS is the preferred speech provider. gTTS is used only as a controlled fallback when Edge TTS remains unavailable.
- The final video may be longer than the original when Pause Sync inserts pauses to preserve complete translated speech.

## Documentation

The original Russian documentation and changelog are included in the repository for additional implementation and recovery details.
