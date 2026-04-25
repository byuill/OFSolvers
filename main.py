import sys
from PyQt6.QtWidgets import QApplication

from case_model import CaseModel
from main_window import MainWindow
from case_controller import CaseController

def main():
    app = QApplication(sys.argv)

    model = CaseModel()
    view = MainWindow()
    controller = CaseController(model, view)

    view.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()