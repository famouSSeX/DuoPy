"""
Виджет панели поиска и замены (Find and Replace) в стиле VS Code.
Поддерживает поиск вперед/назад, учет регистра, замену одного или всех вхождений,
а также горячие клавиши Ctrl+F, Ctrl+H и Esc.
"""

from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLineEdit, QPushButton,
    QLabel, QToolButton, QCheckBox
)
from PyQt6.QtCore import Qt, pyqtSignal, QEvent


class FindReplaceBar(QWidget):
    """Компактная стильная панель поиска и замены над редактором кода."""

    find_next_requested = pyqtSignal(str, bool)       # (pattern, case_sensitive)
    find_prev_requested = pyqtSignal(str, bool)       # (pattern, case_sensitive)
    replace_requested = pyqtSignal(str, str, bool)    # (pattern, replacement, case_sensitive)
    replace_all_requested = pyqtSignal(str, str, bool) # (pattern, replacement, case_sensitive)
    closed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_ui()
        self.hide()

    def _init_ui(self):
        self.setStyleSheet("""
            FindReplaceBar {
                background-color: #252526;
                border: 1px solid #3c3c3c;
                border-radius: 6px;
            }
            QLineEdit {
                background-color: #1e1e1e;
                color: #d4d4d4;
                border: 1px solid #3c3c3c;
                border-radius: 3px;
                padding: 4px 8px;
                font-family: 'Consolas', monospace;
                font-size: 12px;
            }
            QLineEdit:focus {
                border: 1px solid #007acc;
            }
            QPushButton, QToolButton {
                background-color: #333333;
                color: #cccccc;
                border: 1px solid #3c3c3c;
                border-radius: 3px;
                padding: 3px 8px;
                font-size: 11px;
                font-weight: 500;
            }
            QPushButton:hover, QToolButton:hover {
                background-color: #3e3e42;
                color: #ffffff;
                border-color: #555555;
            }
            QPushButton:pressed, QToolButton:pressed {
                background-color: #007acc;
                border-color: #007acc;
            }
            QCheckBox {
                color: #cccccc;
                font-size: 11px;
            }
            QCheckBox::indicator {
                width: 14px;
                height: 14px;
            }
            QLabel {
                color: #858585;
                font-size: 11px;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)

        # Строка 1: Поиск
        find_row = QHBoxLayout()
        find_row.setSpacing(6)

        lbl_icon = QLabel("🔍")
        find_row.addWidget(lbl_icon)

        self.find_input = QLineEdit()
        self.find_input.setPlaceholderText("Поиск в коде...")
        self.find_input.textChanged.connect(self._on_find_text_changed)
        self.find_input.returnPressed.connect(self.on_find_next)
        self.find_input.installEventFilter(self)
        find_row.addWidget(self.find_input, 1)

        self.lbl_match_count = QLabel("0 совпадений")
        self.lbl_match_count.setMinimumWidth(85)
        find_row.addWidget(self.lbl_match_count)

        self.chk_case = QCheckBox("Aa")
        self.chk_case.setToolTip("Учитывать регистр")
        self.chk_case.stateChanged.connect(self._on_find_text_changed)
        find_row.addWidget(self.chk_case)

        self.btn_prev = QToolButton()
        self.btn_prev.setText("▲")
        self.btn_prev.setToolTip("Предыдущее (Shift+Enter)")
        self.btn_prev.clicked.connect(self.on_find_prev)
        find_row.addWidget(self.btn_prev)

        self.btn_next = QToolButton()
        self.btn_next.setText("▼")
        self.btn_next.setToolTip("Следующее (Enter)")
        self.btn_next.clicked.connect(self.on_find_next)
        find_row.addWidget(self.btn_next)

        self.btn_toggle_replace = QToolButton()
        self.btn_toggle_replace.setText("⇄")
        self.btn_toggle_replace.setToolTip("Показать/скрыть замену (Ctrl+H)")
        self.btn_toggle_replace.clicked.connect(self.toggle_replace_mode)
        find_row.addWidget(self.btn_toggle_replace)

        self.btn_close = QToolButton()
        self.btn_close.setText("✕")
        self.btn_close.setToolTip("Закрыть (Esc)")
        self.btn_close.clicked.connect(self.close_bar)
        find_row.addWidget(self.btn_close)

        layout.addLayout(find_row)

        # Строка 2: Замена (по умолчанию скрыта)
        self.replace_widget = QWidget()
        replace_row = QHBoxLayout(self.replace_widget)
        replace_row.setContentsMargins(0, 2, 0, 0)
        replace_row.setSpacing(6)

        lbl_rep_icon = QLabel("✏️")
        replace_row.addWidget(lbl_rep_icon)

        self.replace_input = QLineEdit()
        self.replace_input.setPlaceholderText("Заменить на...")
        self.replace_input.returnPressed.connect(self.on_replace)
        self.replace_input.installEventFilter(self)
        replace_row.addWidget(self.replace_input, 1)

        self.btn_replace = QPushButton("Заменить")
        self.btn_replace.clicked.connect(self.on_replace)
        replace_row.addWidget(self.btn_replace)

        self.btn_replace_all = QPushButton("Заменить все")
        self.btn_replace_all.clicked.connect(self.on_replace_all)
        replace_row.addWidget(self.btn_replace_all)

        layout.addWidget(self.replace_widget)
        self.replace_widget.hide()

    def show_find(self, initial_text: str = ""):
        """Показ панели в режиме поиска."""
        self.show()
        if initial_text:
            self.find_input.setText(initial_text)
        self.find_input.selectAll()
        self.find_input.setFocus()
        # Счётчик пересчитываем всегда: иначе после очистки запроса оставалась
        # надпись «Не найдено» от предыдущего поиска.
        self._on_find_text_changed()

    def show_replace(self, initial_text: str = ""):
        """Показ панели в режиме замены."""
        self.show()
        self.replace_widget.show()
        if initial_text:
            self.find_input.setText(initial_text)
        self.find_input.selectAll()
        self.find_input.setFocus()
        self._on_find_text_changed()

    def keyPressEvent(self, event):
        """
        Esc закрывает панель.

        Раньше обработчик стоял только здесь и не срабатывал: фокус находится
        в дочерних QLineEdit, и их события до панели не доходили.
        """
        if event.key() == Qt.Key.Key_Escape:
            self.close_bar()
            return
        super().keyPressEvent(event)

    def eventFilter(self, obj, event):
        """Esc из полей поиска и замены тоже закрывает панель."""
        if event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
            self.close_bar()
            return True
        return super().eventFilter(obj, event)

    def toggle_replace_mode(self):
        """Переключение видимости строки замены."""
        if self.replace_widget.isVisible():
            self.replace_widget.hide()
        else:
            self.replace_widget.show()
            self.replace_input.setFocus()

    def close_bar(self):
        """Закрытие панели поиска."""
        self.hide()
        self.closed.emit()

    def update_match_status(self, current_idx: int, total_count: int):
        """Обновление счетчика найденных совпадений."""
        if total_count == 0:
            if self.find_input.text().strip():
                self.lbl_match_count.setText("Не найдено")
                self.lbl_match_count.setStyleSheet("color: #f48771; font-size: 11px;")
            else:
                self.lbl_match_count.setText("0 совпадений")
                self.lbl_match_count.setStyleSheet("color: #858585; font-size: 11px;")
        else:
            self.lbl_match_count.setText(f"{current_idx} из {total_count}")
            self.lbl_match_count.setStyleSheet("color: #4ec9b0; font-size: 11px; font-weight: bold;")

    def _on_find_text_changed(self):
        pattern = self.find_input.text()
        case_sensitive = self.chk_case.isChecked()
        self.find_next_requested.emit(pattern, case_sensitive)

    def on_find_next(self):
        pattern = self.find_input.text()
        case_sensitive = self.chk_case.isChecked()
        self.find_next_requested.emit(pattern, case_sensitive)

    def on_find_prev(self):
        pattern = self.find_input.text()
        case_sensitive = self.chk_case.isChecked()
        self.find_prev_requested.emit(pattern, case_sensitive)

    def on_replace(self):
        pattern = self.find_input.text()
        replacement = self.replace_input.text()
        case_sensitive = self.chk_case.isChecked()
        self.replace_requested.emit(pattern, replacement, case_sensitive)

    def on_replace_all(self):
        pattern = self.find_input.text()
        replacement = self.replace_input.text()
        case_sensitive = self.chk_case.isChecked()
        self.replace_all_requested.emit(pattern, replacement, case_sensitive)
