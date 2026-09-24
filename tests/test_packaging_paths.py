import unittest
from pathlib import Path
from unittest import mock

from videotranslator.core import paths


class PackagedPathTests(unittest.TestCase):
    def test_program_dir_uses_executable_parent_when_frozen(self):
        with mock.patch.object(paths.sys, "frozen", True, create=True), \
             mock.patch.object(paths.sys, "executable", "/tmp/portable/Video-Translator-Pro.exe"):
            self.assertEqual(paths.get_program_dir(), Path("/tmp/portable").resolve())

    def test_bundled_resource_dir_uses_module_location(self):
        bundled = Path("/tmp/_MEI123")
        with mock.patch.object(paths, "__file__", str(bundled / "videotranslator" / "core" / "paths.py")):
            self.assertEqual(paths.get_bundled_resource_dir(), bundled.resolve())


if __name__ == "__main__":
    unittest.main()
