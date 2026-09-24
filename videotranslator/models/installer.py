"""Backward-compatible facade for the Hybrid AI runtime model manager."""
from __future__ import annotations
from videotranslator.models.manager_v8 import RuntimeModelManager


class ModelInstaller(RuntimeModelManager):
    def install_folder(self, model_id):
        return self.install_prepare(model_id)
