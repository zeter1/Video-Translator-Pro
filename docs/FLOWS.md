# FLOWS

## 1. Batch GUI flow

```text
App.start
 → create/load batch recovery state
 → background App._worker
 → for each pending file
 → VideoTranslator(...)
 → VideoTranslator.process(...)
 → persist per-file result
 → final batch status
```

Owners: `ui/batch_recovery.py`, `ui/batch_start.py`, `ui/batch_worker.py`, `recovery/batch.py`.

Resume compares saved settings before overwriting them. Changed settings create a new batch for the remaining selected files with empty output references, preserving previous files. Identical settings retain recovery. This prevents a previous language/voice result from satisfying a new request.

## 2. Per-video translation flow

```text
VT1  ffprobe duration → extract mono 16 kHz WAV
VT2  load Whisper → transcribe → merge short segments
VT3  restore translation checkpoint → batch translate → retry failures → save checkpoint
VT4  optional manual review window
VT5  Edge/gTTS preparation → timing slots → Pause Sync → mix/master voice WAV
VT6  ffmpeg final MP4 → video+audio validation
VT7  translated text + *_segments.txt
VT8  temp/client/cache cleanup
```

Owner: `pipeline/process.py`.

`media/audio.py:ffmpeg_has_encoder` caches a successfully parsed encoder inventory per executable string. Heavy assembly reuses it for NVIDIA/AMD/Intel candidates. Failed or empty probes are not cached; cancellation propagates. Availability is parsed from encoder names, not descriptions. This reports build support, not whether a GPU can actually initialize; runtime encoder fallback remains necessary.

Final video selection uses FFmpeg `0:V:0` in stream copy, reencoding and all Pause Sync fragments. This selects the first video stream excluding attached pictures/thumbnails/cover art. Multiple ordinary video tracks still use the first one; no new default-track policy is introduced.

Final assembly distinguishes encoding failures from destination failures. A validated candidate that cannot be saved raises `FinalVideoSaveError`; encoder fallback must not run for this exception. The pipeline preserves and detaches that job's temporary directory, reports the recovery path, and returns failure. Candidate validation occurs once before moving; the destination is checked after moving. A moved file that fails the destination check is retained for inspection, not reported as successful.

Publication uses a no-overwrite hard link when supported on the same filesystem. Otherwise it copies to a unique `.vt-video-*.partial.mp4` in the destination directory, validates its size and media streams, then publishes with a no-overwrite rename on Windows (hard link on POSIX). The original candidate remains available until destination validation succeeds. Failed copies never occupy the final pathname; simultaneous destination creation is rejected.

Speech slots stop before the next phrase starts, even when recognition intervals overlap. A deficit creates a freeze at that boundary rather than at the current phrase's later end. Nested short-segment merging retains the largest end time.

Source audio policy: `media/probe.py:select_audio_stream` chooses the first audio stream marked default, otherwise the first audio stream. `process.py` selects it once and passes its absolute stream index to Whisper extraction and final assembly (including Pause Sync). Failed probing or no audio stops the pipeline; it must not silently guess a different language. Existing helper callers may omit the new optional index, retaining their previous behavior. Checkpoint keys still include the recognized source text and timing; the source-file signature invalidates checkpoints when its size/mtime changes.

Audio extraction preserves a delayed audio start with `aresample:first_pts=0`, so Whisper timestamps include the leading silence. Original background uses the same normalization; Pause Sync applies it before trimming pieces. This uses FFmpeg's input timeline origin and does not promise synchronization for arbitrary independently offset video streams. Track selection receives the pipeline cancellation event and checks it before launching the probe.

Final assembly supplies `expected_duration` to the probe validator before accepting an encoder candidate or moving it. Selected video (excluding cover art) and audio must have finite positive durations, start near zero and end near the planned duration. Tolerance is 120 ms or one video frame, whichever is larger. Missing timing metadata fails this strict check. Legacy presence-only callers remain supported. Timing metadata does not prove decoded content, complete speech or arbitrary A/V synchronization.

TTS preparation retains the last usable phrase when a tempo result has zero, negative or non-finite duration. Non-finite base duration is rejected before timeline planning. The existing pause mechanism accommodates the retained longer phrase.

## 3. Edge TTS / VPN recovery

```text
Edge request
 → NetworkStormGuard
 → repeated failures
 → preserve selected voice window
 → user can switch VPN + trigger probe
 → successful probes restore Edge
 → timeout/user approval enables gTTS only for remaining segments
```

Owners: `pipeline/network_voice.py`, `pipeline/tts_generate.py`, `network/guard.py`.

## 4. Translation recovery

```text
segment source text
 → stable segment key
 → translated_texts/Контрольные точки/*.json
 → cache hit skips network request
 → batch markers validated strictly
 → broken batch falls back to individual requests
 → incomplete translation blocks TTS/final output
```

Owners: `recovery/translation.py`, `translation/batching.py`, `pipeline/translation.py`.

An individual source phrase can exceed the batch request budget. `split_translation_text` bounds those requests to `TRANSLATION_BATCH_MAX_CHARS`, preferring whitespace boundaries and preserving source characters. `translate_segment` translates the parts in order with independent retries and returns only after every part succeeds. A handled failure/cancellation cannot publish the incomplete phrase. A later deferred pass may still repeat earlier parts because partial parts are not persisted.

Individual fallback results update the shared segment records immediately. If translation is cancelled or fails, `pipeline/process.py` saves those records before cleanup. Interruption saving is enabled only after the complete cached segment plan has been restored, so cancellation while building that plan cannot replace an existing checkpoint with a partial list. This covers handled cancellation/errors, not sudden process termination before the next write.

Without Pause Sync, the final audio mix follows the longest input and is limited by `final_dur`. A shorter original audio track must not truncate translated speech. The semantic synthetic regression is documented in `VERIFICATION.md`.

## 5. Pause Sync

```text
translated phrase
 → prepared TTS duration
 → natural available slot
 → bounded speech speed
 → if phrase still does not fit: add stop-frame duration
 → merge only safe nearby pauses
 → build extended audio timeline
 → final video filter script inserts matching visual pauses
```

Owners: `pipeline/timeline_build.py`, `pipeline/timeline_mix.py`, `sync/pause.py`, `media/final_video.py`.

Intermediate phrase batches retain absolute offsets but stop after their last WAV phrase plus a 100 ms limiter tail. The last batch retains the complete video duration; the final mix remains full length. Unknown audio formats retain the previous full-length batch path. This avoids repeatedly writing silent trailing PCM; regression fixtures compare final decoded samples exactly, including multiple merge rounds.

The NumPy fallback must add every required phrase/intermediate batch: decode failure, empty audio or an invalid starting offset stops mixing, and cancellation propagates. A readable WAV with missing speech is not a successful fallback. Decoder readers still close on every path.

A pause at zero clones the first video frame and prepends equal background silence. Positive intervals inside the first 20 ms are retained instead of discarding the freeze and source prefix. Synthetic content checks are listed in `VERIFICATION.md`.

## 6. Diagnostics

```text
problem/event
 → ProblemLogger.event
 → events.jsonl / problems.jsonl
 → compact summary reducer
 → issue cards / Codex analysis
 → report.md + manifest.json + latest_session.json
```

Owners: `diagnostics/problem_logger.py`, `problem_analysis.py`, `problem_reporting.py`.
