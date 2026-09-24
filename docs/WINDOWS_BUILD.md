# Windows build / EXE

## Recommended path

1. Установить Python и FFmpeg/ffprobe.
2. Запустить `build_exe.bat`.
3. Сначала проверить `dist\VideoTranslatorPRO\VideoTranslatorPRO.exe` (ONEDIR).
4. Проверить: Explorer double-click, другой cwd, локальные модели, Whisper, Edge/Piper, финальный FFmpeg.
5. Только после рабочего ONEDIR при необходимости запускать `build_exe.bat --onefile`.

`build_exe.bat` ставит base + optional AI dependencies и PyInstaller. `tools/build_exe.py` использует явный `--collect-all` для динамически импортируемых Whisper/Edge/gTTS/Argos/Piper/Transformers-компонентов. Модели не вшиваются в artifact.

Если `ffmpeg.exe` и `ffprobe.exe` лежат рядом с исходниками во время сборки, build tool добавит их в artifact. Иначе готовая программа ищет их обычным runtime-механизмом (PATH/рядом с EXE).

## Important

Source-run PASS не равен EXE PASS. После изменения build config или зависимостей старый EXE считается непроверенным. Для диагностики можно запустить `build_exe.bat --console` и получить ONEDIR с видимой консолью.

## FFmpeg bootstrap and artifact ownership

`Установить_зависимости.bat` and `build_exe.bat` call `tools/ensure_ffmpeg_windows.py --install`.
If neither a local pair nor a PATH pair is present on Windows, the helper downloads the Gyan release-essentials ZIP, validates the published SHA-256, and extracts only `ffmpeg.exe` and `ffprobe.exe` next to the source tree. Interrupted ZIP downloads keep a `.part` file and resume on the next run.

`tools/build_exe.py` refuses to produce a distributable artifact without both binaries and always passes the resolved pair to PyInstaller. Runtime resolution prefers binaries next to the application over global PATH, so a user-installed older FFmpeg cannot silently replace the bundled build.

## Protected install directories / runtime state

Representative EXE verification must include a location where the application directory is
not writable by the normal user (for example a normal `Program Files` installation). The EXE
may keep portable `logs/`, `Логи проблем/`, `translated_texts/` and `tts_cache/` beside itself
only when that directory passes a real write probe. Otherwise those mutable directories must
resolve under `%LOCALAPPDATA%\VideoTranslatorPRO`.

Verification checklist:

1. Launch ONEDIR from a protected/read-only application directory without elevation.
2. Confirm startup does not fail while initializing diagnostics/cache/checkpoints.
3. Confirm runtime files appear in `%LOCALAPPDATA%\VideoTranslatorPRO` and survive restart.
4. Confirm bundled FFmpeg/resources still resolve from the application bundle, not from the
   runtime-data fallback.
5. Repeat with `VIDEO_TRANSLATOR_RUNTIME_DIR` set to a writable custom directory.

The automated suite covers path selection with simulated writable/unwritable roots; actual
Windows ACL behavior and the packaged artifact remain a representative-runtime check.
