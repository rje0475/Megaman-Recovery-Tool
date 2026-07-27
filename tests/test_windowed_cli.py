import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from cli import _safe_write, main


class FalseyStream(io.StringIO):
    def __bool__(self):
        return False


class BrokenStream:
    def write(self, _text):
        raise OSError("geen console")

    def flush(self):
        raise OSError("geen console")


class WindowedCliTests(unittest.TestCase):
    def test_safe_write_accepts_none_and_broken_stream(self):
        self.assertFalse(_safe_write(None, "tekst"))
        self.assertFalse(_safe_write(BrokenStream(), "tekst"))

    def test_explicit_falsey_stream_is_preserved(self):
        stream = FalseyStream()
        with patch("tools.create_demo_recovery_test.voer_demo_uit") as demo:
            self.assertEqual(main(["--demo"], uitvoer=stream), 0)
        self.assertIn("Megaman Recovery Tool", stream.getvalue())
        demo.assert_called_once()
        self.assertIs(demo.call_args.kwargs["uitvoer"], stream)

    def test_gui_starts_with_stdout_none_stderr_none_or_both(self):
        combinations = ((None, io.StringIO()), (io.StringIO(), None), (None, None))
        for stdout, stderr in combinations:
            with self.subTest(stdout=stdout, stderr=stderr), \
                    patch.object(sys, "stdout", stdout), patch.object(sys, "stderr", stderr), \
                    patch("gui.start_gui", return_value=0):
                self.assertEqual(main(["--gui"]), 0)

    def test_argumentloze_windowed_exe_start_gui(self):
        with patch.object(sys, "stdout", None), patch.object(sys, "stderr", None), \
                patch.object(sys, "frozen", True, create=True), \
                patch.object(sys, "argv", ["MegamanRecoveryTool.exe"]), \
                patch("gui.start_gui", return_value=0) as start_gui:
            self.assertEqual(main(), 0)
            start_gui.assert_called_once()

    def test_normal_cli_error_with_stdout_none_does_not_crash(self):
        missing = Path("does-not-exist-windowed-test")
        with patch.object(sys, "stdout", None), patch.object(sys, "stderr", None):
            self.assertEqual(main(["--analyze", str(missing)]), 1)

    def test_unexpected_exception_with_stdout_none_does_not_mask_original(self):
        with patch.object(sys, "stdout", None), patch.object(sys, "stderr", None), \
                patch("cli.voer_analyse", side_effect=RuntimeError("boom")):
            self.assertEqual(main(["--analyze", "."]), 1)

    def test_help_and_version_with_missing_global_streams(self):
        for argument in ("--help", "--version"):
            with self.subTest(argument=argument), patch.object(sys, "stdout", None), \
                    patch.object(sys, "stderr", None), self.assertRaises(SystemExit) as raised:
                main([argument])
            self.assertEqual(raised.exception.code, 0)

    def test_demo_remains_operational_without_console(self):
        with patch.object(sys, "stdout", None), patch.object(sys, "stderr", None), \
                patch("tools.create_demo_recovery_test.voer_demo_uit") as demo:
            self.assertEqual(main(["--demo"]), 0)
            demo.assert_called_once()


if __name__ == "__main__":
    unittest.main()
