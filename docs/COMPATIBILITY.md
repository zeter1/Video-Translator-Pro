# COMPATIBILITY

## Legacy module

The original application exposed every symbol from a single `video_translator.py`. The new root file is a facade and launcher; implementation lives under `videotranslator/`.

The following remain valid:

```python
import video_translator as vt
vt.VideoTranslator
vt.App
vt.ProblemLogger
vt.find_ffmpeg
vt.assemble_final_video
```

## Legacy monkeypatch bridge

Historical tests patch public names on `video_translator` rather than the new owner module. `videotranslator/core/compat_bridge.py` preserves these seams for the dependency points used by the regression suite.

This is deliberate compatibility code, not the preferred pattern for new tests. New tests should patch the direct owner module when possible.

## Path semantics

After modularization `__file__` moved into package directories. `core/paths.get_program_dir()` explicitly returns the project/launcher root, so logs, TTS cache, translated texts and sibling ffmpeg binaries remain where the old monolith expected them.
