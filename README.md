**Язык / Language:** **Русский** · [English](README_EN.md)

# Video Translator Pro

**Video Translator Pro** — Windows-приложение для автоматического распознавания речи, перевода и озвучивания видео. Проект объединяет OpenAI Whisper, машинный перевод, Edge TTS / gTTS и FFmpeg в один устойчивый медиаконвейер.

Главная особенность проекта — режим **Pause Sync**: если перевод получается длиннее исходной фразы, программа старается сохранить естественную речь вместо агрессивного ускорения или обрезки перевода.

## Скачать готовую Windows-версию

Готовый **Windows `.exe`** публикуется на странице [GitHub Releases](https://github.com/zeter1/Video-Translator-Pro/releases). Бинарная сборка включает FFmpeg и ffprobe, поэтому отдельно устанавливать Python и медиатулы для запуска EXE не нужно.

При первом выборе Whisper-модели программа может скачать её из интернета. Перевод и Edge TTS также зависят от сетевого доступа. Перед публикацией GitHub Actions запускает regression tests и packaged self-test, а рядом с EXE публикуется SHA-256.

> Бинарник пока не подписан коммерческим code-signing сертификатом, поэтому Windows SmartScreen может показать предупреждение для нового/редко скачиваемого файла.

## Что демонстрирует проект

- многоэтапный pipeline: audio extraction → speech recognition → translation → TTS → video assembly;
- Whisper, Edge TTS, gTTS, FFmpeg и FFprobe в одном desktop-приложении;
- checkpoints/recovery и повторное использование промежуточных результатов;
- ограниченные retry и защиту от каскада сетевых ошибок;
- проверку итогового артефакта, а не только return code;
- синхронизацию переведённой речи с видео через Pause Sync;
- модульную структуру `videotranslator/`, regression tests и AI-friendly `AGENTS.md`.

## Возможности

- распознавание речи через OpenAI Whisper;
- перевод через `deep-translator`;
- Edge TTS с gTTS fallback;
- Pause Sync и настраиваемый предел ускорения речи;
- FFmpeg / FFprobe media pipeline;
- подмешивание оригинальной дорожки;
- несколько языков;
- TTS-cache;
- checkpoint/recovery;
- финальная проверка MP4;
- структурированная диагностика.

## Установка

1. Установите Python 3.10–3.13 для Windows.
2. Установите FFmpeg и FFprobe и добавьте их в `PATH` либо положите рядом с `video_translator.py`.
3. Скачайте проект:

```bash
git clone https://github.com/zeter1/Video-Translator-Pro.git
cd Video-Translator-Pro
```

4. Создайте окружение и установите зависимости:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install --upgrade pip
py -m pip install -r requirements.txt
```

При первом использовании Whisper может загрузить выбранную модель.

## Запуск

```powershell
py video_translator.py
```

Также можно использовать `Запустить.bat`.

## Как пользоваться

1. Выберите видеофайл.
2. Укажите язык исходной речи и язык перевода.
3. Выберите Whisper и TTS-настройки.
4. Настройте максимальное ускорение и подмешивание оригинального звука.
5. Запустите перевод.
6. Программа выполнит распознавание, перевод, TTS и сборку через FFmpeg.
7. Итоговый MP4 проверяется перед успешным завершением.

Если задача прервалась, сохраните recovery/checkpoint и TTS-cache — уже выполненная работа может быть использована повторно.

## Pause Sync

1. Используется естественная пауза после исходной реплики.
2. При необходимости перевод умеренно ускоряется в пределах лимита.
3. Если фраза всё ещё не помещается, видеоряд временно останавливается до завершения перевода.

## Архитектура

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

## Надёжность

- атомарные checkpoints;
- восстановление незавершённых задач;
- повторное использование TTS-фрагментов;
- ограниченные retry;
- network-storm protection;
- heartbeat длительных Whisper/FFmpeg этапов;
- проверка финального MP4;
- ограничение cache/diagnostics.

## Проверка

```powershell
python -m compileall -q video_translator.py videotranslator tests tools
python -m unittest discover -s tests -v
```

GitHub Actions выполняет compile и offline regression suite. Полный end-to-end перевод с реальными Whisper/TTS/FFmpeg сценариями требует Windows и соответствующего runtime-окружения.

## Ограничения

- качество перевода зависит от распознавания исходной речи;
- сильно заглушённые слова могут быть пропущены Whisper;
- Edge TTS и перевод требуют сетевого доступа;
- полный медиапайплайн зависит от FFmpeg/FFprobe;
- CI не загружает реальные модели Whisper и не заменяет end-to-end runtime проверку.

## Диагностика и поддержка

- [Поддержка и диагностика](SUPPORT.md)
- [Security / privacy](SECURITY.md)
- `docs/` — архитектура и эксплуатационная документация;
- `.github/ISSUE_TEMPLATE/bug_report.yml` — структурированный bug report.

## Лицензия

Проект опубликован для портфолио, изучения архитектуры и просмотра исходного кода. Отдельная open-source лицензия в настоящее время не предоставляется.
