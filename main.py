import os
import sys

from core.reliability.startup import get_startup_trace, show_fatal_startup_error


def run_application(cli_main=None, logging_factory=None, exception_installer=None,
                    trace=None, fatal_handler=None):
    """Doorloop startup diagnostisch; geschikt voor console en windowed builds."""
    trace = trace or get_startup_trace()
    fatal_handler = fatal_handler or show_fatal_startup_error
    trace.write("01 process started")
    trace.write(f"02 frozen status determined: {bool(getattr(sys, 'frozen', False))}")
    trace.write(f"03 executable and argv determined: executable={sys.executable!r}; argc={len(sys.argv)}")
    trace.write(f"04 runtime paths determined: cwd={os.getcwd()!r}; startup_log={str(trace.path)!r}")
    try:
        from core.reliability import LoggingManager, install_global_exception_handler
        (logging_factory or LoggingManager)().configure()
        trace.write("05 logging configured")
        (exception_installer or install_global_exception_handler)()
        trace.write("06 global exception handler installed")
        if cli_main is None:
            from cli import main as cli_main
        trace.write("07 CLI arguments resolved")
        return cli_main()
    except SystemExit:
        raise
    except BaseException as error:
        trace.exception("startup", error)
        fatal_handler(error, trace.path)
        return 1


if __name__ == "__main__":
    raise SystemExit(run_application())
