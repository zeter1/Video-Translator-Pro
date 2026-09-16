**Язык / Language:** [Русский](README.md) · **English**

# Video Translator Pro

**Video Translator Pro** is a Windows application for automatic speech recognition, translation, and video voice-over. It combines OpenAI Whisper, machine translation, Edge TTS / gTTS, and FFmpeg into a resilient media pipeline.

A key feature is **Pause Sync**: when translated speech is longer than the original phrase, the application prioritizes natural speech instead of aggressively speeding it up or cutting it off.

## What this project demonstrates

- a multi-stage pipeline: audio extraction → speech recognition → translation → TTS → video assembly;
- Whisper, Edge TTS, gTTS, FFmpeg, and FFprobe integration in one desktop application;
- checkpoints/recovery and reuse of intermediate results;
- bounded retries and network-storm protection;
- validation of the final artifact rather than relying only on process return codes;
- synchronization of translated speech with video through Pause Sync;
- a modular `videotranslator/` package, regression tests, and AI-friendly `AGENTS.md`.

## Features

- OpenAI Whisper speech recognition;
- translation through `deep-translator`;
- Edge TTS with gTTS fallback;
- Pause Sync with configurable speech-speed limits;
- FFmpeg / FFprobe media processing;
- optional original-audio mixing;
- multiple target languages;
- persistent TTS cache;
- checkpoint/recovery;
- final MP4 validation;
- structured diagnostics.

## Installation

1. Install Python 3.10–3.13 for Windows.
2. Install FFmpeg and FFprobe and add them to `PATH`, or place the executables next to `video_translator.py`.
3. Download the project:

```bash
git clone https://github.com/zeter1/Video-Translator-Pro.git
cd Video-Translator-Pro
```

4. Create an environment and install dependencies:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install --upgrade pip
py -m pip install -r requirements.txt
```

Whisper may download the selected model on first use.

## Running

```powershell
py video_translator.py
```

You can also use `Запустить.bat`.

## Usage

1. Select a source video.
2. Choose the source and target languages.
3. Configure Whisper and TTS settings.
4. Set the maximum speech-speed adjustment and original-audio mix.
5. Start translation.
6. The application performs recognition, translation, TTS, and FFmpeg assembly.
7. The final MP4 is validated before the job is reported as successful.

If a job is interrupted, keep recovery/checkpoint data and the TTS cache so completed work can be reused.

## Pause Sync

1. Available natural pauses after source speech are used first.
2. Translation is moderately accelerated within the configured limit when necessary.
3. If the phrase still does not fit, the video is temporarily paused until translated speech finishes.

## Architecture

```text
video_translator.py        # compatibility launcher
videotranslator/
├── pipeline/              # orchestration, translation, TTS, timeline
├── media/                 # FFmpeg/FFprobe, audio/video operations
├── speech/                # Whisper loading
├── network/               # retries / network-storm protection
├── recovery/              # checkpoints and batch recovery
├── diagnostics/           # structured diagnostics
├── sync/                  # Pause Sync
├── reports/
├── tts/
└── ui/
tests/                     # regression tests
tools/                     # project tooling
docs/                      # documentation
legacy/                    # historical implementation
```

## Reliability

- atomic checkpoints;
- interrupted-job recovery;
- reuse of completed TTS fragments;
- bounded retries;
- network-storm protection;
- heartbeat reporting for long Whisper/FFmpeg stages;
- final MP4 validation;
- bounded cache and diagnostics storage.

## Verification

```powershell
python -m compileall -q video_translator.py videotranslator tests tools
python -m unittest discover -s tests -v
```

GitHub Actions runs compilation and the offline regression suite. Full end-to-end translation with real Whisper/TTS/FFmpeg execution requires Windows and the corresponding runtime environment.

## Limitations

- translation quality depends on source speech recognition quality;
- heavily obscured speech may be missed by Whisper;
- Edge TTS and translation require network access;
- the complete media pipeline depends on FFmpeg/FFprobe;
- CI does not download real Whisper models and does not replace end-to-end runtime verification.

## Diagnostics and support

- [Support and diagnostics](SUPPORT.md)
- [Security / privacy](SECURITY.md)
- `docs/` — architecture and operational documentation;
- `.github/ISSUE_TEMPLATE/bug_report.yml` — structured bug report form.

## License

The project is published for portfolio review, architecture study, and source-code inspection. No separate open-source license is currently granted.
