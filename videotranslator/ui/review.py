"""Small composition facade for translation review UI.

Codex: review_flow.py owns worker↔UI handoff; review_window.py owns the large Tk window.
"""
from __future__ import annotations

from videotranslator.ui.review_flow import UIReviewFlowMixin
from videotranslator.ui.review_window import UIReviewWindowMixin


class UIReviewMixin(UIReviewFlowMixin, UIReviewWindowMixin):
    pass
