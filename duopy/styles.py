"""
Стили и темная тема DuoPy в стиле современных редакторов кода (Dark Theme / VS Code & JetBrains style).
Глубокая палитра, гармоничные скругления, акцентные кнопки и продуманная эргономика.
"""

DARK_THEME_QSS = """
/* Базовые параметры окна */
QMainWindow, QDialog {
    background-color: #1e1e1e;
    color: #cccccc;
    font-family: 'Segoe UI', -apple-system, BlinkMacSystemFont, Arial, sans-serif;
}

QWidget {
    background-color: #1e1e1e;
    color: #cccccc;
    font-size: 13px;
    font-family: 'Segoe UI', -apple-system, BlinkMacSystemFont, Arial, sans-serif;
}

/* Верхняя панель инструментов (Toolbar) */
QToolBar {
    background-color: #252526;
    border-bottom: 1px solid #333333;
    padding: 4px 8px;
    spacing: 6px;
}

QToolBar::separator {
    background-color: #3e3e42;
    width: 1px;
    margin: 4px 6px;
}

/* Кнопки тулбара и общие кнопки */
QPushButton {
    background-color: #2d2d30;
    color: #e0e0e0;
    border: 1px solid #3e3e42;
    border-radius: 4px;
    padding: 5px 12px;
    font-weight: 500;
    font-size: 12px;
}

QPushButton:hover {
    background-color: #38383c;
    border-color: #007acc;
    color: #ffffff;
}

QPushButton:pressed {
    background-color: #007acc;
    border-color: #007acc;
    color: #ffffff;
}

QPushButton:disabled {
    background-color: #252526;
    color: #555555;
    border-color: #2d2d30;
}

/* Акцентная кнопка запуска ▶ */
QPushButton#btn_run {
    background-color: #238636;
    color: #ffffff;
    border: 1px solid #2ea043;
    font-weight: bold;
    padding: 5px 16px;
    border-radius: 4px;
}

QPushButton#btn_run:hover {
    background-color: #2ea043;
    border-color: #3fb950;
}

QPushButton#btn_run:pressed {
    background-color: #196127;
}

QPushButton#btn_run:disabled {
    background-color: #252526;
    color: #555555;
    border-color: #2d2d30;
}

/* Кнопка остановки ⏹ */
QPushButton#btn_stop {
    background-color: #b91c1c;
    color: #ffffff;
    border: 1px solid #dc2626;
    font-weight: bold;
    padding: 5px 14px;
    border-radius: 4px;
}

QPushButton#btn_stop:hover {
    background-color: #dc2626;
}

QPushButton#btn_stop:pressed {
    background-color: #991b1b;
}

QPushButton#btn_stop:disabled {
    background-color: #252526;
    color: #555555;
    border-color: #2d2d30;
}

/* Кнопка создания комнаты 🌐 */
QPushButton#btn_create_room {
    background-color: #0d4429;
    color: #4ec9b0;
    border: 1px solid #1a7f37;
    font-weight: 600;
}

QPushButton#btn_create_room:hover {
    background-color: #125e38;
    color: #7ee787;
}

/* Поля ввода текста */
QLineEdit, QSpinBox {
    background-color: #252526;
    border: 1px solid #3c3c3c;
    border-radius: 4px;
    color: #f1f1f1;
    padding: 5px 8px;
    selection-background-color: #264f78;
    selection-color: #ffffff;
    font-size: 12px;
}

QLineEdit:focus, QSpinBox:focus {
    border: 1px solid #007acc;
    background-color: #1e1e1e;
}

/* Разделители (Splitters) */
QSplitter::handle {
    background-color: #252526;
}

QSplitter::handle:hover {
    background-color: #007acc;
}

/* Вкладки файлов (Tabs) */
QTabBar {
    background-color: #181818;
    border: none;
}

QTabBar::tab {
    background-color: #181818;
    color: #8c8c8c;
    border: none;
    border-right: 1px solid #252526;
    padding: 7px 14px;
    font-family: 'Segoe UI', Consolas, sans-serif;
    font-size: 12px;
}

QTabBar::tab:hover:!selected {
    background-color: #1f1f1f;
    color: #cccccc;
}

QTabBar::tab:selected {
    background-color: #1e1e1e;
    color: #ffffff;
    border-top: 2px solid #007acc;
    font-weight: 500;
}

QTabBar::close-button {
    image: none;
    subcontrol-position: right;
    margin-left: 6px;
    padding: 2px;
}

/* Дерево файлов проводника */
QTreeView {
    background-color: #222223;
    color: #cccccc;
    border: none;
    outline: none;
    font-size: 12px;
}

QTreeView::item {
    padding: 4px 6px;
    border-radius: 3px;
    margin: 1px 4px;
}

QTreeView::item:hover {
    background-color: #2a2d2e;
    color: #ffffff;
}

QTreeView::item:selected {
    background-color: #04395e;
    color: #ffffff;
}

/* Полоса вкладок файлов (Tabs) */
/* Второй набор правил для QTabBar убран: он перекрывал первый и затирал
   настройку close-button (image: none), из-за чего крестик закрытия вкладки
   рисовался системной иконкой вместо стиля темы. */

QTabBar::close-button {
    subcontrol-position: right;
    margin-left: 6px;
    padding: 2px;
    border-radius: 2px;
    image: none;
}

QTabBar::close-button:hover {
    background-color: #c42b1c;
    color: #ffffff;
}

/* Окно вывода терминала */
QTextEdit#console_output {
    background-color: #141414;
    color: #d4d4d4;
    font-family: 'Consolas', 'Cascadia Code', monospace;
    font-size: 13px;
    line-height: 1.4;
    border: 1px solid #2d2d30;
    border-radius: 4px;
    padding: 8px;
    selection-background-color: #264f78;
}

/* Чат и список участников */
QListWidget {
    background-color: #222223;
    border: 1px solid #333333;
    border-radius: 4px;
    color: #cccccc;
    padding: 4px;
    outline: none;
}

QListWidget::item {
    padding: 6px 8px;
    border-radius: 4px;
    margin-bottom: 2px;
}

QListWidget::item:hover {
    background-color: #2a2d2e;
}

QListWidget::item:selected {
    background-color: #094771;
    color: #ffffff;
}

/* Окно истории сообщений чата */
QTextEdit#chat_history {
    background-color: #222223;
    border: 1px solid #333333;
    border-radius: 4px;
    font-size: 12px;
    padding: 6px;
}

/* Нижняя панель состояния (Status Bar) */
QStatusBar {
    background-color: #007acc;
    color: #ffffff;
    font-size: 12px;
    font-weight: 500;
    padding: 2px 8px;
}

QStatusBar::item {
    border: none;
}

/* Скроллбары */
QScrollBar:vertical {
    border: none;
    background-color: #1e1e1e;
    width: 10px;
    margin: 0px;
}

QScrollBar::handle:vertical {
    background-color: #3e3e42;
    min-height: 25px;
    border-radius: 5px;
    margin: 2px;
}

QScrollBar::handle:vertical:hover {
    background-color: #555559;
}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    border: none;
    background: none;
    height: 0px;
}

QScrollBar:horizontal {
    border: none;
    background-color: #1e1e1e;
    height: 10px;
    margin: 0px;
}

QScrollBar::handle:horizontal {
    background-color: #3e3e42;
    min-width: 25px;
    border-radius: 5px;
    margin: 2px;
}

QScrollBar::handle:horizontal:hover {
    background-color: #555559;
}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    border: none;
    background: none;
    width: 0px;
}

/* Контекстные меню */
QMenu {
    background-color: #252526;
    border: 1px solid #3c3c3c;
    border-radius: 4px;
    padding: 4px;
}

QMenu::item {
    padding: 5px 22px 5px 12px;
    border-radius: 3px;
    color: #cccccc;
}

QMenu::item:hover, QMenu::item:selected {
    background-color: #04395e;
    color: #ffffff;
}

QMenu::separator {
    height: 1px;
    background-color: #3c3c3c;
    margin: 4px 6px;
}
"""
