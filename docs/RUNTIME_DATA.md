# RUNTIME DATA

These paths are mutable runtime evidence and should not be treated as source code:

- `logs/` — text application logs;
- `Логи проблем/` — structured session diagnostics for Codex;
- `translated_texts/` — translated text/checkpoints;
- `tts_cache/` — persistent recovery cache;
- `work/` — runtime work files if present;
- `__pycache__/` — generated Python cache.
- `~/.video_translator_models/` — постоянное пользовательское хранилище локальных Marian/Argos/Piper данных и manifest/maintenance state. Оно специально находится вне каталога версии приложения, чтобы обновление/распаковка нового архива не требовали повторной установки голосов. Можно явно перенести через `VIDEO_TRANSLATOR_MODELS_DIR`.

Do not package live user logs/cache/output into a source refactor artifact unless explicitly requested.

Final publication may create an owned `.vt-video-*.partial.mp4` in the output directory. Handled failures remove this staging copy while retaining the original completed candidate; sudden termination can leave the staging copy. It is not a published result and must not be treated as one. No automatic scan/deletion of old staging files is introduced.

If saving a completed MP4 fails, the pipeline retains the system-temp `video_translator_*` job directory and prints the candidate path. This exception to ordinary cleanup preserves recovery material and detaches the directory from later jobs. Recovery is manual; copy the candidate to a safe location before OS temp cleanup. Retained directories may contain large WAV intermediates. A candidate moved successfully but rejected by the destination check remains at the destination for inspection and is not claimed to be valid.

## Writable runtime location (pass 6)

Mutable runtime directories keep the existing portable behavior **when the application
directory is really writable**. Before choosing that location, the program now proves write
access with a small create/delete probe instead of relying on the current working directory
or `os.access`.

If the application/EXE is installed into a protected location (for example `Program Files`),
`logs/`, `Логи проблем/`, `translated_texts/` and `tts_cache/` automatically move to a
per-user writable root:

- Windows: `%LOCALAPPDATA%\VideoTranslatorPRO\...`;
- non-Windows source/testing fallback: `~/.video_translator/...`.

`VIDEO_TRANSLATOR_RUNTIME_DIR` explicitly overrides that root. The persistent local model
store remains independent and continues to use `~/.video_translator_models/` (or
`VIDEO_TRANSLATOR_MODELS_DIR`). This split prevents an EXE installed under a read-only
directory from failing while creating diagnostics, checkpoints or TTS recovery cache.

Corrupt persistent JSON is no longer silently disposable: settings and glossary loading
preserve a timestamped `*.corrupt.*.json` copy before a later successful save can replace
the original. User-facing text/report exports changed in this pass use atomic temp + replace
so an interrupted/failed commit does not truncate an already existing output.

## Packaging hygiene (pass 5)

Source/refactor ZIP artifacts are produced without live `logs/`, `Логи проблем/`,
`translated_texts/`, `tts_cache/`, `work/`, `__pycache__/` and `.pytest_cache/` content.
Those directories are recreated by the application/tests when needed. This prevents an
upgrade archive from redistributing stale user diagnostics, checkpoints or cache as if they
were program source.

Normal `logs/video_translator*.txt` files are now maintained with bounded retention by the
application. Cleanup is restricted to application-owned filenames; unrelated `.txt` files
in the directory are not part of that policy.
