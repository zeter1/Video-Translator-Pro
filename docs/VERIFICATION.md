# VERIFICATION

## Local task

```bash
python tools/verify_task.py "symptom or task"
```

Runs compile/import and only exact regression selectors mapped by the chosen task-card. If the card has no exact test, the tool says so instead of implying coverage.

## Full gate

```bash
python tools/verify_project.py
```

Checks compile, legacy API/program directory, recovery startup guard, CODE_MAP v3 canonical ownership, deterministic task routing, hard source budgets, targeted-test routing, context benchmark and the complete discovered regression suite (minimum baseline: 43 tests).

## Safe execution and synthetic media

Imports do not launch the GUI. Do not call `bootstrap.main()` or instantiate `App` for offline verification: those use real settings and diagnostics. `VideoTranslator` creates a TTS cache in its constructor; offline tests patch `videotranslator.tts.cache.get_tts_cache_dir` to a temporary directory before constructing it. Pipeline tests also mock Whisper, extraction, network requests and checkpoint paths. Changing cwd/TEMP alone does not isolate program-relative state.

`verify_project.py` regenerates navigation documents under `docs/` and Python bytecode. Run it only in an authorized source workspace. On Windows use UTF-8 for the tools and their children:

```powershell
$env:PYTHONUTF8 = '1'
python tools/verify_project.py
```

Cancellation, partial fallback and restart checks live in `tests/test_pipeline_regressions.py`. Real FFmpeg checks are opt-in and generate only temporary synthetic media:

```powershell
$env:PYTHONUTF8 = '1'
$env:VT_RUN_MEDIA_TESTS = '1'
python tools/verify_project.py
```

The synthetic checks require FFmpeg/ffprobe on PATH and libx264. They cover short original audio with stream-copy and forced software reencode, plus translation-only output. The oracle checks selected stream durations and decodes a late translated signal, including sample count and signal energy. Without the opt-in flag these media tests are explicitly skipped.

`tests/test_mix_integrity.py` exercises NumPy fallback with controlled decoders: failed required phrases/intermediate batches, empty inputs, invalid offsets and cancellation must fail without returning an incomplete mix. Valid inputs are checked for samples at the planned positions. NumPy is required; MoviePy decoding is mocked and readers must close.

`tests/test_pause_media.py` uses real FFmpeg and a synthetic red-to-blue video transition to verify freeze insertion at zero, inside the first frame, in the middle and at the end. It checks selected stream duration, decoded frame colours and background silence/tone around the pause. Frame quantization is allowed within the fixture's frame tolerance; this does not prove arbitrary frame rates or every near-coincident pause plan.

`tests/test_runtime_reliability_pass6.py` covers the protected-install runtime fallback,
portable writable-path compatibility, corrupt settings/glossary preservation, atomic auxiliary
report replacement and the GUI close guard while a local-model operation is active. These are
offline/simulated boundaries: they do not prove real Windows `Program Files` ACL behavior,
Task Manager termination during pip/model installation or live model/network downloads.

## Runtime-only evidence

`test_encoder_inventory.py` checks one shared probe, transient failure followed by success, unparseable output, cancellation and misleading description text. Opt-in execution queries installed FFmpeg and verifies reuse for present/absent names without encoding or touching runtime state. These checks do not establish GPU availability or hardware encode speed.

`test_video_stream_selection.py` asserts cover-excluding selection in copy, reencode and Pause Sync graphs. Its opt-in real FFmpeg fixture contains a red attached cover and blue video; decoded output is blue in ordinary and paused assembly. The MP4 muxer may reorder cover streams, so the real fixture alone does not reproduce cover-first ordering: command checks and the documented FFmpeg `V` selector establish that boundary. Existing Pause Sync content tests verify unaffected timing.

`test_atomic_video_publish.py` covers same-volume publication without a data copy, injected cross-device copy interruption, invalid copied media, concurrent destination creation, and validated-copy publication. All fixtures are temporary. Cross-volume I/O errors are simulated; actual disconnect/power loss and filesystem durability are not proved. Existing real FFmpeg final-assembly tests exercise successful publication and selected content after publication.

`test_final_save_failure.py` verifies that destination errors stop all encoder fallback paths and that the real pipeline exception/finally path retains the candidate through later cleanup. With `VT_RUN_MEDIA_TESTS=1`, it creates an actual MP4, checks an existing destination remains unchanged, probes the retained candidate and decodes its audio. Locked-destination errors are injected; real Windows locks, cross-volume copy interruption and sudden process termination are not simulated by these checks.

`test_long_translation.py` uses a fake request client without application construction or network. It checks long singleton requests, exact source character preservation, Unicode/boundaries, later-part retry without repeating the first part, cancellation and rejection of incomplete results. It does not prove live provider availability or translation quality across split boundaries.

`test_mix_workload.py` checks shortened intermediate WAV batches and the unknown-format fallback. With `VT_RUN_MEDIA_TESTS=1`, real FFmpeg tests compare the old and new final PCM byte for byte for direct and multi-round merging. `repair_evidence/benchmark_mix_speed.py` uses only temporary synthetic tones and reports stage timing, intermediate bytes and sample equality; it does not measure Whisper or network TTS speed.

`test_recovery_settings.py` runs App.start with GUI/log/save/thread boundaries mocked: changed language/voice creates new pending output references, identical settings retains recovery, old files remain unchanged. `test_overlapping_speech.py` uses known speech durations to check nonoverlapping placement, freeze boundaries, regular gaps and nested segment merging. These do not prove live GUI/Whisper behavior.

`test_audio_start_offset.py` verifies real synthetic delayed audio and zero-offset extraction, decoded background silence/signal in ordinary and Pause Sync output, and controlled cancellation propagation during track selection. Only temporary fixtures are used. Cancellation tests mock the subprocess boundary; they do not measure real ffprobe shutdown latency.

Additional offline guards: `test_tts_duration_integrity.py` checks rejected base durations and preservation of the source phrase after an unmeasurable tempo result; `test_output_duration.py` covers invalid selected-stream timing and rounding tolerance. No decoder/network/model is started by these tests.

Opt-in `test_audio_selection.py` creates two tones in separate tracks, verifies default/first selection and measures the chosen frequency after extraction, ordinary mux and Pause Sync (including silence inside the pause). `test_final_audio_integration.py` also verifies that candidates with truncated video or audio are not published. All fixtures remain temporary.

Unit/static checks do not prove live Whisper/GPU, Edge TTS/gTTS/VPN, NVENC or long real FFmpeg jobs on Windows. Those require actual runtime evidence.

## Hybrid AI v9 Pass 9 — terminology / subtitles / ducking / Pause Sync

`tests/test_quality_pass9.py` covers bounded glossary prompts, exact Whisper keyword-compatibility fallback, source-gated glossary cleanup, Pause Sync-aware SRT output and adaptive-ducking graph construction. With `VT_RUN_MEDIA_TESTS=1` it also renders a real FFmpeg WAV and verifies that the 440 Hz original is attenuated only while the translated sidechain voice is active.

`tests/test_pause_media.py` now passes source FPS into Pause Sync so the regression fixture exercises the production frame-quantized `tpad` path. On the same FFmpeg 7.1.5 environment, the unmodified Pass 8 fixture produced ~3.92–3.96 s video for a 5 s expected timeline; Pass 9 produces the expected padded timeline and preserves post-pause content.

`tests/test_final_audio_integration.py` additionally proves that a source whose selected video stream is severely truncated relative to the media timeline is rejected instead of being published as a successful translation.
