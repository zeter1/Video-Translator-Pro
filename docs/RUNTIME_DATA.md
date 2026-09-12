# RUNTIME DATA

These paths are mutable runtime evidence and should not be treated as source code:

- `logs/` — text application logs;
- `Логи проблем/` — structured session diagnostics for Codex;
- `translated_texts/` — translated text/checkpoints;
- `tts_cache/` — persistent recovery cache;
- `work/` — runtime work files if present;
- `__pycache__/` — generated Python cache.

Do not package live user logs/cache/output into a source refactor artifact unless explicitly requested.

Final publication may create an owned `.vt-video-*.partial.mp4` in the output directory. Handled failures remove this staging copy while retaining the original completed candidate; sudden termination can leave the staging copy. It is not a published result and must not be treated as one. No automatic scan/deletion of old staging files is introduced.

If saving a completed MP4 fails, the pipeline retains the system-temp `video_translator_*` job directory and prints the candidate path. This exception to ordinary cleanup preserves recovery material and detaches the directory from later jobs. Recovery is manual; copy the candidate to a safe location before OS temp cleanup. Retained directories may contain large WAV intermediates. A candidate moved successfully but rejected by the destination check remains at the destination for inspection and is not claimed to be valid.
