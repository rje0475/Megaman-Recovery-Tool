from cli import main
from core.reliability import LoggingManager, install_global_exception_handler


if __name__ == "__main__":
    LoggingManager().configure()
    install_global_exception_handler()
    raise SystemExit(main())
