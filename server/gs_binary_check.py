"""Ghostscript is found by the Windows name, not only ``gs``."""

from __future__ import annotations

import os
import unittest
from unittest import mock

import gs_binary


class GhostscriptLookup(unittest.TestCase):
    def test_windows_console_name_when_gs_is_missing(self):
        windows_exe = r"C:\Program Files\gs\gs10.02.1\bin\gswin64c.exe"

        def which(name):
            if name == "gswin64c":
                return windows_exe
            return None

        with mock.patch.object(gs_binary.shutil, "which", side_effect=which):
            with mock.patch.object(gs_binary, "_windows_install_paths", return_value=[]):
                with mock.patch.dict(os.environ, {}, clear=False):
                    for key in gs_binary.ENV_KEYS:
                        os.environ.pop(key, None)
                    self.assertEqual(gs_binary.find_gs_binary(), windows_exe)

    def test_program_files_when_nothing_is_on_path(self):
        installed = r"C:\Program Files\gs\gs10.05.0\bin\gswin32c.exe"
        with mock.patch.object(gs_binary.shutil, "which", return_value=None):
            with mock.patch.object(gs_binary, "_windows_install_paths", return_value=[installed]):
                with mock.patch.object(gs_binary.os.path, "isfile", return_value=True):
                    for key in gs_binary.ENV_KEYS:
                        os.environ.pop(key, None)
                    self.assertEqual(gs_binary.find_gs_binary(), installed)

    def test_env_path_wins(self):
        custom = r"D:\tools\gswin64c.exe"
        with mock.patch.dict(os.environ, {"GS_PATH": custom}):
            with mock.patch.object(gs_binary.os.path, "isfile", return_value=True):
                with mock.patch.object(gs_binary.os, "access", return_value=True):
                    self.assertEqual(gs_binary.find_gs_binary(), custom)

    def test_press_engine_uses_the_shared_lookup(self):
        import press_ready_engine

        with mock.patch("gs_binary.find_gs_binary", return_value="gswin64c") as lookup:
            # The engine imports the function at call time.
            self.assertEqual(press_ready_engine._gs_bin(), "gswin64c")
            lookup.assert_called()


if __name__ == "__main__":
    unittest.main()
