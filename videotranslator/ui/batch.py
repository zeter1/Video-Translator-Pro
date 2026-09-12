"""Small composition facade for batch UI behavior.

Codex routing:
- batch_recovery.py: startup recovery and persisted settings
- batch_start.py: validation/start contract
- batch_worker.py: long-running per-file worker state machine
"""
from __future__ import annotations

from videotranslator.ui.batch_recovery import UIBatchRecoveryMixin
from videotranslator.ui.batch_start import UIBatchStartMixin
from videotranslator.ui.batch_worker import UIBatchWorkerMixin


class UIBatchMixin(UIBatchRecoveryMixin, UIBatchStartMixin, UIBatchWorkerMixin):
    """Public mixin facade preserving the historical App composition contract."""

    pass
