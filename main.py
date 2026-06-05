import sys
from PyQt5.QtWidgets import QApplication, QMainWindow
from PyQt5 import uic
from PyQt5.QtCore import Qt, QPoint

from components.tab_students import StudentTabComponent
from components.tab_matrix import MatrixTabComponent 
from components.tab_analytics import AnalyticsTabComponent

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        uic.loadUi('design.ui', self)

        self.setWindowFlags(Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)

        self.tab_buttons = [
            self.btn_tab_students,
            self.btn_tab_matrix,
            self.btn_tab_analytics
        ]

        self.btn_tab_students.clicked.connect(lambda: self.switch_tab(0))
        self.btn_tab_matrix.clicked.connect(lambda: self.switch_tab(1))
        self.btn_tab_analytics.clicked.connect(lambda: self.switch_tab(2))

        self.exit_button.clicked.connect(self.close)
        self.minimize_button.clicked.connect(self.showMinimized)
        if hasattr(self, 'maximize_button'):
            self.maximize_button.clicked.connect(self.toggle_maximize)

        self.oldPos = self.pos()

        # INITIALIZE THE TAB COMPONENTS
        self.student_tab = StudentTabComponent(self) 
        self.matrix_tab = MatrixTabComponent(self) 
        self.analytics_tab = AnalyticsTabComponent(self)
        
        self.switch_tab(0)

    def switch_tab(self, index):
        self.stackedWidget.setCurrentIndex(index)
        
        for i, btn in enumerate(self.tab_buttons):
            btn.setChecked(i == index)
            
        if index == 1: self.matrix_tab.load_matrix_data()
        if index == 2: self.analytics_tab.update_data()

    def toggle_maximize(self):
        if self.windowState() == Qt.WindowMaximized:
            self.setWindowState(Qt.WindowNoState)
        else:
            # Get the screen the app is currently on
            screen = QApplication.desktop().screenNumber(self)
            # Find the available space (which explicitly subtracts the Windows Taskbar)
            available_geom = QApplication.desktop().availableGeometry(screen)
            
            # Restrict the app from growing larger than the space minus the taskbar
            self.setMaximumSize(available_geom.size())
            self.setWindowState(Qt.WindowMaximized)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.oldPos = event.globalPos()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.LeftButton:
            delta = QPoint(event.globalPos() - self.oldPos)
            self.move(self.x() + delta.x(), self.y() + delta.y())
            self.oldPos = event.globalPos()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())