"""Start programu: python main.py (z głównego folderu projektu)."""
import sys

from dotenv import load_dotenv
from PySide6.QtWidgets import QApplication

from app.main_window import MainWindow
from app.storage import PROJECT_DIRECTORY


def main() -> None:
    load_dotenv(PROJECT_DIRECTORY / ".env")
    application = QApplication(sys.argv)
    window = MainWindow()
    window.resize(1400, 850)
    window.show()
    sys.exit(application.exec())


if __name__ == "__main__":
    main()
