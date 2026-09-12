"""Small composition facade for diagnostic artifact rendering and summary state.

Codex: report_render.py owns report/manifest rendering; summary_update.py owns summary state updates.
"""
from __future__ import annotations

from videotranslator.diagnostics.report_render import ProblemReportRenderMixin
from videotranslator.diagnostics.summary_update import ProblemSummaryMixin


class ProblemReportingMixin(ProblemReportRenderMixin, ProblemSummaryMixin):
    pass
