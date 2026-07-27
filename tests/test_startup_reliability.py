import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import gui
import main as app_main
from core.reliability import CrashReporter, install_global_exception_handler
from core.reliability.startup import StartupTrace, show_fatal_startup_error


class _Trace:
    def __init__(self, path):
        self.path = Path(path)
        self.messages = []

    def write(self, message):
        self.messages.append(message)
        return True

    def exception(self, context, error):
        self.messages.append(f"FATAL {context}: {error}")


class _Application:
    def __init__(self, argv):
        self.argv = argv
        self.quit_on_last_window = None
        self.exec_called = False
        self.window_seen_during_exec = None

    def setQuitOnLastWindowClosed(self, enabled):
        self.quit_on_last_window = enabled

    def exec(self):
        self.exec_called = True
        self.window_seen_during_exec = gui._active_window
        return 0


class _Window:
    def __init__(self):
        self.shown = False

    def show(self):
        self.shown = True


class StartupReliabilityTest(unittest.TestCase):
    def test_handler_accepts_startup_user_notifier(self):
        reporter = install_global_exception_handler(user_notifier=lambda _: None)
        self.assertIsInstance(reporter, CrashReporter)
        self.assertIsNotNone(reporter.user_notifier)

    def test_gui_creates_shows_and_enters_event_loop_with_strong_window_ref(self):
        with tempfile.TemporaryDirectory() as directory:
            trace = _Trace(Path(directory) / "startup.log")
            application = _Application([])
            window = _Window()
            result = gui.start_gui(
                application_factory=lambda _: application,
                window_factory=lambda: window,
                exception_installer=lambda **_: None,
                trace=trace,
                fatal_handler=lambda *args, **kwargs: None,
            )
        self.assertEqual(result, 0)
        self.assertTrue(window.shown)
        self.assertTrue(application.exec_called)
        self.assertIs(application.window_seen_during_exec, window)
        self.assertTrue(application.quit_on_last_window)
        self.assertIsNone(gui._active_window)
        self.assertIn("14 event loop starting", trace.messages)
        self.assertIn("15 event loop returned: 0", trace.messages)

    def test_early_exception_is_written_and_uses_fatal_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            trace = StartupTrace(Path(directory) / "startup.log")
            shown = []

            class BrokenLogging:
                def configure(self):
                    raise RuntimeError("logging kapot")

            result = app_main.run_application(
                cli_main=lambda: 0,
                logging_factory=BrokenLogging,
                trace=trace,
                fatal_handler=lambda error, path: shown.append((error, path)),
            )
            content = trace.path.read_text(encoding="utf-8")
        self.assertEqual(result, 1)
        self.assertIn("RuntimeError: logging kapot", content)
        self.assertIn("Traceback", content)
        self.assertEqual(len(shown), 1)

    def test_unwritable_primary_startup_log_uses_temp_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            blocked = Path(directory) / "not-a-directory"
            blocked.write_text("bestand", encoding="utf-8")
            trace = StartupTrace()
            trace.path = blocked / "startup.log"
            with patch("core.reliability.startup.tempfile.gettempdir", return_value=directory):
                self.assertTrue(trace.write("01 process started"))
            self.assertEqual(trace.path, Path(directory) / "MegamanRecoveryTool-startup.log")
            self.assertIn("01 process started", trace.path.read_text(encoding="utf-8"))

    def test_exception_after_qapplication_uses_qmessagebox(self):
        class MessageBox:
            calls = []

            @classmethod
            def critical(cls, *args):
                cls.calls.append(args)

        result = show_fatal_startup_error(
            RuntimeError("venster kapot"), "startup.log",
            qapplication=object(), messagebox=MessageBox,
        )
        self.assertEqual(result, "qt")
        self.assertEqual(len(MessageBox.calls), 1)
        self.assertIn("startup.log", MessageBox.calls[0][2])

    def test_startup_remains_safe_without_stdout_or_stderr(self):
        with tempfile.TemporaryDirectory() as directory:
            trace = _Trace(Path(directory) / "startup.log")
            with patch.object(sys, "stdout", None), patch.object(sys, "stderr", None):
                result = app_main.run_application(
                    cli_main=lambda: 0,
                    logging_factory=lambda: type(
                        "Logger", (), {"configure": lambda self: None}
                    )(),
                    exception_installer=lambda: None,
                    trace=trace,
                )
        self.assertEqual(result, 0)

    def test_frozen_no_argument_cli_selects_gui(self):
        from cli import main

        with patch.object(sys, "frozen", True, create=True), \
                patch.object(sys, "stdout", None), \
                patch.object(sys, "argv", ["MegamanRecoveryTool.exe"]), \
                patch("gui.start_gui", return_value=0) as start:
            self.assertEqual(main(), 0)
        start.assert_called_once_with()

    def test_spec_is_windowed_and_collects_windows_platform_plugin(self):
        root = Path(__file__).resolve().parents[1]
        spec = (root / "megaman_recovery.spec").read_text(encoding="utf-8")
        self.assertIn("console=False", spec)
        self.assertIn("exclude_binaries=True", spec)
        self.assertIn("COLLECT(", spec)
        try:
            from PySide6.QtCore import QLibraryInfo
        except ImportError:
            self.skipTest("PySide6 niet geïnstalleerd")
        plugin = Path(QLibraryInfo.path(QLibraryInfo.PluginsPath)) / "platforms" / "qwindows.dll"
        if os.name == "nt":
            self.assertTrue(plugin.is_file(), plugin)


if __name__ == "__main__":
    unittest.main()
