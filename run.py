"""
Точка входа для запуска приложения DuoPy.
"""

import sys
import os

# Добавляем текущую директорию в sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt6.QtWidgets import QApplication
from duopy.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("DuoPy")
    app.setOrganizationName("DuoPyTeam")

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


import multiprocessing

if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
