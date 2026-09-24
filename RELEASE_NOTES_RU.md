# Изменения Windows-сборки

- Добавлена готовая Windows-сборка `Video-Translator-Pro.exe` для запуска без установленного Python.
- FFmpeg и ffprobe включаются внутрь EXE и доступны приложению из PyInstaller runtime-каталога.
- Добавлен frozen-runtime path contract: пользовательские логи, checkpoints и cache по-прежнему создаются рядом с EXE, а bundled media tools не требуют системного PATH.
- Добавлен packaged self-test, который проверяет импорты `numpy`, PyTorch, OpenAI Whisper, Edge TTS, gTTS, deep-translator и MoviePy без загрузки Whisper-модели.
- Packaged self-test также запускает встроенные `ffmpeg.exe` и `ffprobe.exe` перед публикацией.
- Бинарник публикуется вместе с отдельным SHA-256 только после успешных regression tests и packaged smoke-test.

- Исправлен Windows regression test восстановления пакета: короткий DOS-путь (`RUNNER~1`) и длинный путь теперь сравниваются как идентичность одного файла через `os.path.samefile()`, а не как буквальные строки.

## Проверка

CI подтверждает сборку и запуск packaged runtime на GitHub-hosted Windows runner. Реальный end-to-end перевод видео, загрузка Whisper-модели, сетевой перевод/TTS и аппаратное ускорение остаются внешними runtime-сценариями и требуют проверки в пользовательской среде.
