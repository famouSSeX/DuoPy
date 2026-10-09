"""
Главное окно приложения DuoPy с поддержкой глобальных онлайн-комнат:
- Создание комнаты и вход по коду (DUO-XXXX) через интернет (из любых стран и сетей)
- Прямое локальное подключение (LAN/IP)
- Синхронизация кода, курсоров, запуска (F5), терминала вывода и чата
- Интерактивный ввод input() в консоли программы
- Автодополнение кода (IntelliSense) по Ctrl+Space
- Быстрый поиск и замена по Ctrl+F / Ctrl+H
- Вкладки файлов (Tabs) в современном стиле
- Премиальный продуманный интерфейс в стиле VS Code & JetBrains
"""

import sys
import os
import html
from datetime import datetime
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QSplitter, QTextEdit, QLineEdit, QListWidget, QListWidgetItem,
    QFileDialog, QMessageBox, QDialog, QSpinBox, QFormLayout, QDialogButtonBox,
    QToolBar, QTreeView, QMenu, QInputDialog, QTabBar, QSizePolicy,
    QTreeWidget, QTreeWidgetItem, QApplication
)
from PyQt6.QtCore import Qt, QDir, QTimer
from PyQt6.QtGui import (
    QAction, QKeySequence, QTextCursor, QTextCharFormat, QColor, QFont,
    QFileSystemModel
)

from duopy.editor import CodeEditor
from duopy.find_replace import FindReplaceBar
from duopy.network import NetworkManager, get_local_ip, generate_room_code
from duopy.runner import (
    CodeRunner, get_python_interpreter, set_custom_python_path, is_valid_python
)
from duopy.styles import DARK_THEME_QSS
from duopy.config import (
    get_user_profile, set_user_profile, PRESET_COLORS, add_recent_room, get_recent_rooms
)
from duopy.updater import AboutAndUpdatesDialog, ConnectionStatsDialog, APP_VERSION


def resolve_inside(path: str, base_dir: str) -> str | None:
    """
    Безопасное преобразование относительного пути в абсолютный внутри base_dir.

    Защищает от обхода каталога проекта напарником: абсолютные пути, переходы
    через '..', символические ссылки и пути в соседние каталоги с общим
    префиксом имени (например D:\\Project\\DuoPy_evil при базе D:\\Project\\DuoPy)
    возвращают None. Проверка идёт по реальным путям через commonpath,
    а не сравнением строк.
    """
    if not path or not isinstance(path, str):
        return None
    if "\x00" in path:
        return None
    if os.path.isabs(path) or os.path.splitdrive(path)[0]:
        return None

    base = os.path.realpath(base_dir)
    candidate = os.path.realpath(os.path.join(base, path))
    try:
        if os.path.commonpath([base, candidate]) != base:
            return None
    except ValueError:
        # Разные диски или некорректный путь
        return None
    return candidate


DEFAULT_CODE_TEMPLATE = '''# Добро пожаловать в DuoPy!
# Совместное написание и запуск Python кода через интернет в реальном времени.

def calculate_stats(numbers: list[int]) -> dict:
    return {
        "count": len(numbers),
        "total": sum(numbers),
        "average": sum(numbers) / len(numbers) if numbers else 0,
        "max": max(numbers) if numbers else None,
        "min": min(numbers) if numbers else None,
    }

if __name__ == "__main__":
    data = [14, 28, 42, 56, 70, 99]
    print(f"Исходные данные: {data}")
    stats = calculate_stats(data)
    for key, value in stats.items():
        print(f"  • {key.capitalize()}: {value}")
'''


class CreateRoomDialog(QDialog):
    """Диалог создания онлайн-комнаты через интернет (Cloud Relay)."""

    def __init__(self, current_name: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Создать онлайн-комнату (Интернет)")
        self.resize(420, 220)

        layout = QFormLayout(self)

        self.name_edit = QLineEdit(current_name)
        layout.addRow("Ваше имя:", self.name_edit)

        self.room_code = generate_room_code()
        self.lbl_code = QLabel(self.room_code)
        self.lbl_code.setStyleSheet("font-size: 20px; font-weight: bold; color: #4ec9b0; letter-spacing: 2px;")

        btn_copy = QPushButton("📋 Скопировать код")
        btn_copy.clicked.connect(self._copy_code)

        h_box = QHBoxLayout()
        h_box.addWidget(self.lbl_code)
        h_box.addWidget(btn_copy)
        layout.addRow("Код комнаты:", h_box)

        lbl_hint = QLabel("💡 Сообщите этот код вашему напарнику. Он сможет подключиться из любого города или страны.")
        lbl_hint.setWordWrap(True)
        lbl_hint.setStyleSheet("color: #858585; font-size: 11px;")
        layout.addRow(lbl_hint)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Запустить комнату")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def _copy_code(self):
        from PyQt6.QtWidgets import QApplication
        QApplication.clipboard().setText(self.room_code)
        QMessageBox.information(self, "Скопировано", f"Код комнаты {self.room_code} скопирован в буфер обмена!")

    def get_data(self):
        return {
            "name": self.name_edit.text().strip() or "Хост",
            "room_code": self.room_code
        }


class JoinRoomDialog(QDialog):
    """Диалог подключения к существующей онлайн-комнате по коду."""

    def __init__(self, current_name: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Войти в комнату по коду (Интернет)")
        self.resize(380, 180)

        layout = QFormLayout(self)

        self.name_edit = QLineEdit(current_name)
        layout.addRow("Ваше имя:", self.name_edit)

        self.code_edit = QLineEdit()
        self.code_edit.setPlaceholderText("DUO-1234")
        self.code_edit.setStyleSheet("font-size: 14px; font-weight: bold; color: #4ec9b0;")
        layout.addRow("Код комнаты:", self.code_edit)

        lbl_hint = QLabel("💡 Введите 8-значный код комнаты, созданной вашим напарником.")
        lbl_hint.setStyleSheet("color: #858585; font-size: 11px;")
        layout.addRow(lbl_hint)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Подключиться")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def get_data(self):
        return {
            "name": self.name_edit.text().strip() or "Гость",
            "room_code": self.code_edit.text().strip().upper()
        }


class LanDialog(QDialog):
    """Диалог прямого подключения в локальной сети (по IP-адресу)."""

    def __init__(self, current_name: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Подключение в локальной сети (LAN / IP)")
        self.resize(420, 240)

        self.selected_action = "host"
        layout = QVBoxLayout(self)

        info_lbl = QLabel(f"Ваш локальный IP: <b>{get_local_ip()}</b>")
        layout.addWidget(info_lbl)

        mode_box = QHBoxLayout()
        self.btn_mode_host = QPushButton("🛡 Создать сервер (Хост)")
        self.btn_mode_client = QPushButton("🔗 Подключиться к хосту")
        self.btn_mode_host.setCheckable(True)
        self.btn_mode_client.setCheckable(True)
        self.btn_mode_host.setChecked(True)

        mode_box.addWidget(self.btn_mode_host)
        mode_box.addWidget(self.btn_mode_client)
        layout.addLayout(mode_box)

        # Секция хоста
        self.host_widget = QWidget()
        host_layout = QFormLayout(self.host_widget)
        self.host_name_edit = QLineEdit(current_name)
        self.host_port = QSpinBox()
        self.host_port.setRange(1024, 65535)
        self.host_port.setValue(8765)
        host_layout.addRow("Имя разработчика:", self.host_name_edit)
        host_layout.addRow("Порт:", self.host_port)
        layout.addWidget(self.host_widget)

        # Секция клиента
        self.client_widget = QWidget()
        client_layout = QFormLayout(self.client_widget)
        self.client_name_edit = QLineEdit(current_name)
        self.client_ip = QLineEdit("127.0.0.1")
        self.client_port = QSpinBox()
        self.client_port.setRange(1024, 65535)
        self.client_port.setValue(8765)
        client_layout.addRow("Имя разработчика:", self.client_name_edit)
        client_layout.addRow("IP хоста:", self.client_ip)
        client_layout.addRow("Порт:", self.client_port)
        layout.addWidget(self.client_widget)
        self.client_widget.hide()

        self.btn_mode_host.clicked.connect(self._select_host)
        self.btn_mode_client.clicked.connect(self._select_client)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _select_host(self):
        self.selected_action = "host"
        self.btn_mode_host.setChecked(True)
        self.btn_mode_client.setChecked(False)
        self.host_widget.show()
        self.client_widget.hide()

    def _select_client(self):
        self.selected_action = "client"
        self.btn_mode_host.setChecked(False)
        self.btn_mode_client.setChecked(True)
        self.host_widget.hide()
        self.client_widget.show()


class DirectConnectDialog(QDialog):
    """
    Прямое соединение через интернет без посредника.

    Брокер здесь не используется вообще: стороны обмениваются сетевыми
    адресами вручную — код показывает один, второй вставляет его у себя.
    Передать код можно любым способом (мессенджер, почта).
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Прямое соединение без посредника")
        self.resize(560, 420)
        self.created_code = ""

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        info = QLabel(
            "Соединение идёт <b>напрямую</b> между компьютерами, через интернет "
            "ничего не передаётся.<br>"
            "Работает, если ваши сети пропускают входящие UDP-пакеты. "
            "При строгом NAT (часто мобильный интернет) соединиться не удастся — "
            "тогда используйте облачную комнату."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        layout.addWidget(QLabel("<b>Шаг 1. Кто-то один создаёт код и передаёт его напарнику.</b>"))
        self.btn_create = QPushButton("🔑 Создать код-приглашение")
        self.btn_create.clicked.connect(self._create_code)
        layout.addWidget(self.btn_create)

        self.code_edit = QTextEdit()
        self.code_edit.setReadOnly(True)
        self.code_edit.setMaximumHeight(70)
        self.code_edit.setPlaceholderText("Здесь появится код, который нужно передать напарнику")
        layout.addWidget(self.code_edit)

        self.btn_copy = QPushButton("📋 Скопировать код")
        self.btn_copy.clicked.connect(self._copy_code)
        self.btn_copy.setEnabled(False)
        layout.addWidget(self.btn_copy)

        layout.addWidget(QLabel("<b>Шаг 2. Второй участник вставляет код у себя.</b>"))
        self.paste_edit = QTextEdit()
        self.paste_edit.setMaximumHeight(70)
        self.paste_edit.setPlaceholderText("Вставьте сюда код от напарника и нажмите «Подключиться»")
        layout.addWidget(self.paste_edit)

        self.btn_connect = QPushButton("🚀 Подключиться по коду")
        self.btn_connect.clicked.connect(self._connect_by_code)
        layout.addWidget(self.btn_connect)

        self.status_lbl = QLabel("")
        self.status_lbl.setWordWrap(True)
        layout.addWidget(self.status_lbl)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

    def _create_code(self):
        ok, code = self.parent().network.create_direct_invite()
        if not ok:
            self.status_lbl.setText(f"⚠️ {code}")
            return
        self.created_code = code
        self.code_edit.setPlainText(code)
        self.btn_copy.setEnabled(True)
        self.status_lbl.setText(
            "Код создан. Передайте его напарнику и ждите: соединение установится, "
            "как только он вставит код у себя.")

    def _copy_code(self):
        if not self.created_code:
            return
        QApplication.clipboard().setText(self.created_code)
        self.status_lbl.setText("Код скопирован. Отправьте его напарнику.")

    def _connect_by_code(self):
        code = self.paste_edit.toPlainText().strip()
        if not code:
            self.status_lbl.setText("⚠️ Вставьте код от напарника")
            return
        ok, answer = self.parent().network.connect_to_invite(code)
        if not ok:
            self.status_lbl.setText(f"⚠️ {answer}")
            return
        # Свой адрес тоже нужно передать напарнику, иначе он не узнает, куда слать
        self.created_code = answer
        self.code_edit.setPlainText(answer)
        self.btn_copy.setEnabled(True)
        self.status_lbl.setText(
            "Подключаюсь... <b>Отправьте напарнику свой ответный код</b> из поля выше — "
            "он нужен, чтобы он тоже знал ваш адрес.")


class UserProfileDialog(QDialog):
    """Диалог настройки профиля пользователя (Имя и Цвет)."""

    def __init__(self, current_name: str, current_color: str, is_first_run: bool = False, parent=None):
        super().__init__(parent)
        title = "Добро пожаловать в DuoPy!" if is_first_run else "Профиль пользователя"
        self.setWindowTitle(title)
        self.resize(390, 260)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        if is_first_run:
            lbl_welcome = QLabel(
                "👋 <b>Добро пожаловать в DuoPy!</b><br>"
                "<span style='color: #a0a0a0; font-size: 11px;'>"
                "Введите ваше имя для совместной работы над кодом с напарником.</span>"
            )
            lbl_welcome.setWordWrap(True)
            layout.addWidget(lbl_welcome)

        form = QFormLayout()
        self.name_edit = QLineEdit(current_name)
        self.name_edit.setPlaceholderText("Например: Максим, Alex, CodeMaster...")
        form.addRow("Ваше имя:", self.name_edit)
        layout.addLayout(form)

        lbl_color = QLabel("Выберите цвет вашего курсора и выделений:")
        lbl_color.setStyleSheet("font-size: 11px; color: #858585; font-weight: bold;")
        layout.addWidget(lbl_color)

        self.selected_color = current_color
        colors_layout = QHBoxLayout()
        self.color_buttons = []
        for hex_col, title_col in PRESET_COLORS:
            btn = QPushButton()
            btn.setFixedSize(30, 30)
            btn.setToolTip(title_col)
            btn.setCheckable(True)
            is_checked = (hex_col.lower() == current_color.lower())
            btn.setChecked(is_checked)
            border_style = "2px solid #ffffff" if is_checked else "2px solid #333333"
            btn.setStyleSheet(f"background-color: {hex_col}; border-radius: 15px; border: {border_style};")
            btn.clicked.connect(lambda _, c=hex_col: self._select_color(c))
            self.color_buttons.append((btn, hex_col))
            colors_layout.addWidget(btn)
        colors_layout.addStretch()
        layout.addLayout(colors_layout)

        layout.addStretch()

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.button(QDialogButtonBox.StandardButton.Ok).setText("Сохранить")
        btns.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        btns.accepted.connect(self._validate_and_save)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _select_color(self, hex_col: str):
        self.selected_color = hex_col
        for btn, c in self.color_buttons:
            is_sel = (c.lower() == hex_col.lower())
            border_style = "2px solid #ffffff" if is_sel else "2px solid #333333"
            btn.setChecked(is_sel)
            btn.setStyleSheet(f"background-color: {c}; border-radius: 15px; border: {border_style};")

    def _validate_and_save(self):
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "Ошибка", "Пожалуйста, введите ваше имя!")
            return
        set_user_profile(name, self.selected_color)
        self.accept()

    def get_profile(self) -> tuple[str, str]:
        return self.name_edit.text().strip(), self.selected_color


class ClosableTabBar(QTabBar):
    """Вкладки файлов с поддержкой закрытия по клику средней кнопкой мыши (колесиком)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTabsClosable(True)
        self.setMovable(True)
        self.setExpanding(False)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton:
            idx = self.tabAt(event.pos())
            if idx >= 0:
                self.tabCloseRequested.emit(idx)
                return
        super().mousePressEvent(event)


class PythonSettingsDialog(QDialog):
    """Диалог выбора и проверки пути к интерпретатору Python."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Настройка интерпретатора Python")
        self.resize(520, 190)

        layout = QVBoxLayout(self)

        cur_py = get_python_interpreter()
        valid = is_valid_python(cur_py)

        form = QFormLayout()
        self.path_edit = QLineEdit(cur_py)
        btn_browse = QPushButton("Обзор...")
        btn_browse.clicked.connect(self._browse)

        h_path = QHBoxLayout()
        h_path.addWidget(self.path_edit)
        h_path.addWidget(btn_browse)
        form.addRow("Путь к python.exe:", h_path)

        self.status_lbl = QLabel(
            "🟢 Интерпретатор работает штатно" if valid
            else "🔴 Интерпретатор не найден или поврежден"
        )
        self.status_lbl.setStyleSheet("font-weight: bold; color: #4ec9b0;" if valid else "color: #f48771;")
        form.addRow("Статус:", self.status_lbl)

        layout.addLayout(form)

        lbl_desc = QLabel(
            "💡 DuoPy автоматически находит установленные версии Python в системе.\n"
            "Если у вас используется специальное виртуальное окружение (.venv) или Anaconda, "
            "выберите путь к его файлу python.exe здесь."
        )
        lbl_desc.setWordWrap(True)
        lbl_desc.setStyleSheet("color: #858585; font-size: 11px;")
        layout.addWidget(lbl_desc)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Выберите python.exe", "", "Python Executable (python.exe);;All Files (*.*)"
        )
        if path:
            self.path_edit.setText(path)
            if is_valid_python(path):
                self.status_lbl.setText("🟢 Выбранный интерпретатор проверен и работает!")
                self.status_lbl.setStyleSheet("color: #4ec9b0; font-weight: bold;")
            else:
                self.status_lbl.setText("🔴 Выбранный файл не может быть запущен как Python!")
                self.status_lbl.setStyleSheet("color: #f48771; font-weight: bold;")

    def _save(self):
        new_path = self.path_edit.text().strip()
        if new_path:
            if set_custom_python_path(new_path):
                self.accept()
            else:
                QMessageBox.warning(self, "Ошибка", f"Файл '{new_path}' не является рабочим интерпретатором Python.")
        else:
            self.accept()


class MainWindow(QMainWindow):
    """Главное окно приложения DuoPy с премиальным продуманным интерфейсом."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("DuoPy — Совместный Python Редактор")
        self.resize(1240, 820)
        self.setStyleSheet(DARK_THEME_QSS)

        # Загрузка профиля из кэша (сохранение между перезапусками)
        saved_name, saved_color = get_user_profile()
        self.user_name = saved_name or "Разработчик"
        self.user_color = saved_color or "#4ec9b0"

        if not saved_name and self._can_show_startup_dialog():
            QTimer.singleShot(150, self._maybe_show_first_run_dialog)

        self.peer_name = "Ожидание..."
        self.peer_active_file = ""
        self.current_filepath = None
        self.project_path = os.path.abspath(os.getcwd())
        self.is_remote_project = False
        self.remote_project_name = None

        # Список открытых вкладок: [{"title": str, "path": str | None, "content": str}]
        self.tabs: list[dict] = []
        self.current_tab_index = 0

        self.network = NetworkManager(self.user_name, self.user_color, self)
        self.runner = CodeRunner(self)

        # Таймер сглаживания сетевой отправки текста при непрерывном наборе
        self.code_sync_timer = QTimer(self)
        self.code_sync_timer.setSingleShot(True)
        self.code_sync_timer.timeout.connect(self._flush_code_update)

        # Таймер троттлинга отправки курсора (до 20 fps для предотвращения флуда)
        self.cursor_sync_timer = QTimer(self)
        self.cursor_sync_timer.setSingleShot(True)
        self.cursor_sync_timer.timeout.connect(self._flush_cursor_update)
        self.pending_cursor_data = None

        # Измерение качества связи: раз в секунду пересчитываем скорости,
        # раз в две секунды отправляем ping для замера задержки
        self._metrics_tick = 0
        self.metrics_timer = QTimer(self)
        self.metrics_timer.setInterval(1000)
        self.metrics_timer.timeout.connect(self.on_metrics_tick)

        # Подавление обратной отправки RUN_CODE при подтверждении запроса напарника
        self._suppress_remote_run_echo = False
        # Подавление отправки кода, пока применяется обновление от напарника
        self._suppress_code_echo = False
        # Идёт выравнивание состояния (после потери правок или наложения):
        # фрагменты в это время не применяем, ждём полный текст
        self._sync_pending = False
        # Вкладка и сессия, к которым относятся отложенные правки
        self._pending_sync_tab: int | None = None
        self._pending_sync_session: int | None = None
        # Сколько локальных процессов выполняется прямо сейчас
        self._active_runs = 0
        # Защита от повторного входа в closeEvent
        self._closing = False
        # Приветственный диалог показываем только один раз за сессию
        self._first_run_shown = False

        self._init_ui()
        self._init_tabs()
        self._refresh_project_tree()
        self._connect_signals()

        # Тихая проверка обновлений через пару секунд после старта, чтобы не
        # задерживать показ окна и не мешать загрузке проекта
        if not getattr(sys, "_running_tests", False):
            QTimer.singleShot(2500, self._auto_check_updates)

    def _can_show_startup_dialog(self) -> bool:
        """
        Можно ли показывать модальные диалоги на старте.

        В headless/offscreen-режиме (тесты, автоматизация) диалог не показываем:
        его вложенный цикл событий выполнял отложенные вызовы других окон,
        и диалоги открывались каскадом, навсегда блокируя процесс.
        """
        if getattr(sys, "_running_tests", False):
            return False
        app = QApplication.instance()
        try:
            if app is not None and app.platformName() in ("offscreen", "minimal"):
                return False
        except Exception:
            pass
        return True

    def _maybe_show_first_run_dialog(self):
        """Показ приветственного диалога не более одного раза за сессию."""
        if getattr(self, "_first_run_shown", False):
            return
        if not self._can_show_startup_dialog():
            return
        # Флаг ставим ДО exec(): вложенный цикл событий иначе мог вызвать
        # этот слот повторно и открыть второй диалог.
        self._first_run_shown = True
        saved_name, _ = get_user_profile()
        if saved_name:
            return
        dlg = UserProfileDialog("Разработчик", self.user_color, is_first_run=True, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.user_name, self.user_color = dlg.get_profile()
            self.network.username = self.user_name
            self.network.user_color = self.user_color
            self._update_peers_list()

    def _init_tabs(self):
        """Инициализация первой вкладки по умолчанию."""
        self._add_tab("main.py", DEFAULT_CODE_TEMPLATE, None)

    def _init_ui(self):
        # =====================================================================
        # ВЕРХНЯЯ ПАНЕЛЬ ИНСТРУМЕНТОВ (TOOLBAR)
        # =====================================================================
        toolbar = QToolBar("Главная панель")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        # 1. Группа онлайн-подключения
        self.btn_create_room = QPushButton("🌐 Создать комнату")
        self.btn_create_room.setObjectName("btn_create_room")
        self.btn_create_room.setToolTip("Создать онлайн-комнату через интернет (для напарника из любой точки мира)")
        self.btn_create_room.clicked.connect(self.on_create_cloud_room)
        toolbar.addWidget(self.btn_create_room)

        self.btn_join_room = QPushButton("🔗 Войти по коду")
        self.btn_join_room.setToolTip("Подключиться к существующей комнате по коду DUO-XXXX")
        self.btn_join_room.clicked.connect(self.on_join_cloud_room)
        toolbar.addWidget(self.btn_join_room)

        self.btn_lan = QPushButton("🏠 LAN")
        self.btn_lan.setToolTip("Прямое подключение в локальной сети (по IP-адресу)")
        self.btn_lan.clicked.connect(self.on_lan_dialog)
        toolbar.addWidget(self.btn_lan)

        self.btn_direct = QPushButton("⚡ Напрямую")
        self.btn_direct.setToolTip(
            "Прямое соединение через интернет без посредника: обмен кодом вручную")
        self.btn_direct.clicked.connect(self.on_direct_dialog)
        toolbar.addWidget(self.btn_direct)

        self.btn_disconnect = QPushButton("❌ Отключиться")
        self.btn_disconnect.setEnabled(False)
        self.btn_disconnect.clicked.connect(self.on_disconnect)
        toolbar.addWidget(self.btn_disconnect)

        toolbar.addSeparator()

        # 2. Группа запуска кода
        self.btn_run = QPushButton("▶ Запустить (F5)")
        self.btn_run.setObjectName("btn_run")
        self.btn_run.setToolTip("Запустить Python-скрипт (F5)")
        self.btn_run.clicked.connect(self.on_run_code)
        toolbar.addWidget(self.btn_run)

        self.btn_stop = QPushButton("⏹ Стоп")
        self.btn_stop.setObjectName("btn_stop")
        self.btn_stop.setToolTip("Принудительно остановить выполнение (Shift+F5)")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self.on_stop_code)
        toolbar.addWidget(self.btn_stop)

        toolbar.addSeparator()

        # 3. Группа проекта и файлов (кнопка 'Найти' убрана, вызывается по Ctrl+F / Ctrl+H)
        btn_open = QPushButton("📂 Открыть")
        btn_open.setToolTip("Открыть файл с диска")
        btn_open.clicked.connect(self.on_open_file)
        toolbar.addWidget(btn_open)

        btn_save = QPushButton("💾 Сохранить")
        btn_save.setToolTip("Сохранить текущую вкладку (Ctrl+S)")
        btn_save.clicked.connect(self.on_save_file)
        toolbar.addWidget(btn_save)

        btn_open_folder = QPushButton("📁 Папка проекта")
        btn_open_folder.setToolTip("Выбрать рабочую папку проекта")
        btn_open_folder.clicked.connect(self.on_open_project_folder)
        toolbar.addWidget(btn_open_folder)

        btn_python = QPushButton("⚙️ Python")
        btn_python.setToolTip("Настройка пути к интерпретатору Python")
        btn_python.clicked.connect(self.on_configure_python)
        toolbar.addWidget(btn_python)

        toolbar.addSeparator()

        # 4. Тумблер защиты строк от одновременных правок (Line Lock)
        self.btn_lock_toggle = QPushButton("🔒 Lock: Вкл")
        self.btn_lock_toggle.setCheckable(True)
        self.btn_lock_toggle.setChecked(True)
        self.btn_lock_toggle.setStyleSheet("color: #4ec9b0; font-weight: bold;")
        self.btn_lock_toggle.setToolTip("Защита строк (Line Lock): блокирует одновременное редактирование одной строки")
        self.btn_lock_toggle.clicked.connect(self.on_toggle_line_lock)
        toolbar.addWidget(self.btn_lock_toggle)

        btn_updates = QPushButton(f"🔄 v{APP_VERSION}")
        btn_updates.setToolTip("Проверить обновления DuoPy на GitHub")
        btn_updates.clicked.connect(self.on_show_updates)
        toolbar.addWidget(btn_updates)

        # Распорка для выравнивания правого статуса
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        toolbar.addWidget(spacer)

        # Индикатор сессии в тулбаре
        self.lbl_toolbar_badge = QLabel("⚪ Одиночный режим")
        self.lbl_toolbar_badge.setStyleSheet("""
            QLabel {
                background-color: #2d2d30;
                color: #858585;
                font-size: 11px;
                font-weight: bold;
                padding: 4px 12px;
                border-radius: 12px;
                border: 1px solid #3c3c3c;
            }
        """)
        toolbar.addWidget(self.lbl_toolbar_badge)

        # =====================================================================
        # ГЛАВНАЯ РАЗМЕТКА СО СПЛИТТЕРАМИ
        # =====================================================================
        main_h_splitter = QSplitter(Qt.Orientation.Horizontal)

        # 1. Секция слева: Проводник файлов проекта
        project_explorer_widget = self._create_project_explorer()
        main_h_splitter.addWidget(project_explorer_widget)

        # 2. Секция по центру: Вкладки + Редактор + Консоль
        code_v_splitter = QSplitter(Qt.Orientation.Vertical)

        # Контейнер редактора
        editor_container = QWidget()
        editor_layout = QVBoxLayout(editor_container)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        editor_layout.setSpacing(0)

        # Полоса вкладок файлов
        tab_header = QWidget()
        tab_header.setFixedHeight(34)
        tab_header.setStyleSheet("background-color: #181818; border-bottom: 1px solid #252526;")
        tab_header_layout = QHBoxLayout(tab_header)
        tab_header_layout.setContentsMargins(0, 0, 8, 0)
        tab_header_layout.setSpacing(2)

        self.tab_bar = ClosableTabBar()
        self.tab_bar.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tab_bar.customContextMenuRequested.connect(self._on_tab_context_menu)
        self.tab_bar.currentChanged.connect(self.on_tab_changed)
        self.tab_bar.tabCloseRequested.connect(self.on_tab_close_requested)
        tab_header_layout.addWidget(self.tab_bar)

        btn_add_tab = QPushButton("➕")
        btn_add_tab.setToolTip("Создать новый файл во вкладке (Ctrl+N)")
        btn_add_tab.setFixedSize(24, 24)
        btn_add_tab.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #858585;
                border: none;
                font-size: 13px;
                font-weight: bold;
                border-radius: 3px;
            }
            QPushButton:hover {
                background-color: #252526;
                color: #ffffff;
            }
        """)
        btn_add_tab.clicked.connect(self.on_new_tab_clicked)
        tab_header_layout.addWidget(btn_add_tab)

        tab_header_layout.addStretch()

        # Индикатор позиции курсора в углу табов
        self.lbl_cursor_pos = QLabel("Ln 1, Col 1")
        self.lbl_cursor_pos.setStyleSheet("color: #707070; font-family: Consolas, monospace; font-size: 11px;")
        tab_header_layout.addWidget(self.lbl_cursor_pos)

        editor_layout.addWidget(tab_header)

        # Панель поиска и замены (вызывается по Ctrl+F и Ctrl+H)
        self.find_replace_bar = FindReplaceBar(self)
        self.find_replace_bar.find_next_requested.connect(self._on_find_next)
        self.find_replace_bar.find_prev_requested.connect(self._on_find_prev)
        self.find_replace_bar.replace_requested.connect(self._on_replace)
        self.find_replace_bar.replace_all_requested.connect(self._on_replace_all)
        self.find_replace_bar.closed.connect(self._on_find_closed)
        editor_layout.addWidget(self.find_replace_bar)

        # Редактор кода
        self.editor = CodeEditor()
        editor_layout.addWidget(self.editor, 1)

        code_v_splitter.addWidget(editor_container)

        # Нижняя консоль с поддержкой input()
        console_container = QWidget()
        console_layout = QVBoxLayout(console_container)
        console_layout.setContentsMargins(6, 4, 6, 6)
        console_layout.setSpacing(4)

        console_header = QHBoxLayout()
        lbl_console = QLabel("💻 ТЕРМИНАЛ")
        lbl_console.setStyleSheet("font-weight: bold; color: #9cdcfe; font-size: 11px; letter-spacing: 1px;")

        self.lbl_exec_status = QLabel("● Готов к запуску")
        self.lbl_exec_status.setStyleSheet("color: #858585; font-size: 11px;")

        btn_clear_terminal = QPushButton("🧹")
        btn_clear_terminal.setToolTip("Очистить вывод терминала")
        btn_clear_terminal.setFixedSize(22, 22)
        btn_clear_terminal.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                border: none;
                color: #858585;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #2d2d30;
                color: #ffffff;
                border-radius: 3px;
            }
        """)
        btn_clear_terminal.clicked.connect(self.on_clear_console)

        console_header.addWidget(lbl_console)
        console_header.addWidget(self.lbl_exec_status)
        console_header.addStretch()
        console_header.addWidget(btn_clear_terminal)
        console_layout.addLayout(console_header)

        self.console_output = QTextEdit()
        self.console_output.setObjectName("console_output")
        self.console_output.setReadOnly(True)
        console_layout.addWidget(self.console_output, 1)

        # Строка интерактивного ввода input()
        stdin_row = QHBoxLayout()
        stdin_row.setContentsMargins(0, 2, 0, 0)
        stdin_row.setSpacing(6)

        lbl_stdin_prompt = QLabel("❯")
        lbl_stdin_prompt.setStyleSheet("color: #4ec9b0; font-weight: bold; font-size: 14px;")

        self.stdin_input = QLineEdit()
        self.stdin_input.setPlaceholderText("Ввод для программы (input)... Введите значение и нажмите Enter")
        self.stdin_input.setStyleSheet("""
            QLineEdit {
                background-color: #1a1a1a;
                color: #e0e0e0;
                border: 1px solid #333333;
                border-radius: 4px;
                padding: 5px 8px;
                font-family: Consolas, monospace;
                font-size: 12px;
            }
            QLineEdit:focus {
                border: 1px solid #4ec9b0;
                background-color: #141414;
            }
        """)
        self.stdin_input.returnPressed.connect(self.on_send_stdin)

        self.btn_send_stdin = QPushButton("Отправить ↵")
        self.btn_send_stdin.setStyleSheet("padding: 5px 12px; font-size: 11px;")
        self.btn_send_stdin.clicked.connect(self.on_send_stdin)

        stdin_row.addWidget(lbl_stdin_prompt)
        stdin_row.addWidget(self.stdin_input, 1)
        stdin_row.addWidget(self.btn_send_stdin)
        console_layout.addLayout(stdin_row)

        code_v_splitter.addWidget(console_container)
        code_v_splitter.setSizes([540, 220])
        main_h_splitter.addWidget(code_v_splitter)

        # 3. Секция справа: Участники и Чат
        sidebar = QWidget()
        sidebar.setMinimumWidth(230)
        sidebar.setMaximumWidth(320)
        sidebar.setStyleSheet("background-color: #222223;")
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(6, 6, 6, 6)
        sidebar_layout.setSpacing(8)

        # Индикатор комнаты
        self.lbl_room_badge = QLabel("Одиночный режим")
        self.lbl_room_badge.setStyleSheet("""
            background-color: #2a2a2d;
            padding: 8px;
            border-radius: 6px;
            font-weight: bold;
            color: #4ec9b0;
            border: 1px solid #333337;
        """)
        self.lbl_room_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sidebar_layout.addWidget(self.lbl_room_badge)

        # Список участников с кнопкой редактирования профиля
        peers_header = QHBoxLayout()
        lbl_peers = QLabel("👥 УЧАСТНИКИ")
        lbl_peers.setStyleSheet("font-weight: bold; color: #9cdcfe; font-size: 11px; letter-spacing: 1px;")
        peers_header.addWidget(lbl_peers)
        peers_header.addStretch()

        btn_edit_profile = QPushButton("✎ Профиль")
        btn_edit_profile.setToolTip("Изменить имя и цвет")
        btn_edit_profile.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #858585;
                border: none;
                font-size: 11px;
                padding: 1px 4px;
            }
            QPushButton:hover {
                color: #4ec9b0;
                background-color: #2a2a2d;
                border-radius: 3px;
            }
        """)
        btn_edit_profile.clicked.connect(self.on_edit_profile)
        peers_header.addWidget(btn_edit_profile)
        sidebar_layout.addLayout(peers_header)

        self.peers_list = QListWidget()
        self.peers_list.setFixedHeight(85)
        sidebar_layout.addWidget(self.peers_list)
        self._update_peers_list()

        # Встроенный совместный чат
        lbl_chat = QLabel("💬 СОВМЕСТНЫЙ ЧАТ")
        lbl_chat.setStyleSheet("font-weight: bold; color: #ce9178; font-size: 11px; letter-spacing: 1px;")
        sidebar_layout.addWidget(lbl_chat)

        self.chat_history = QTextEdit()
        self.chat_history.setObjectName("chat_history")
        self.chat_history.setReadOnly(True)
        sidebar_layout.addWidget(self.chat_history, 1)

        chat_input_layout = QHBoxLayout()
        chat_input_layout.setSpacing(4)
        self.chat_input = QLineEdit()
        self.chat_input.setPlaceholderText("Напишите сообщение...")
        self.chat_input.returnPressed.connect(self.on_send_chat)

        btn_send_chat = QPushButton("➤")
        btn_send_chat.setFixedWidth(36)
        btn_send_chat.setToolTip("Отправить сообщение")
        btn_send_chat.clicked.connect(self.on_send_chat)

        chat_input_layout.addWidget(self.chat_input)
        chat_input_layout.addWidget(btn_send_chat)
        sidebar_layout.addLayout(chat_input_layout)

        main_h_splitter.addWidget(sidebar)
        main_h_splitter.setSizes([220, 720, 260])

        self.setCentralWidget(main_h_splitter)

        # =====================================================================
        # НИЖНИЙ СТАТУС-БАР (STATUS BAR В СТИЛЕ VS CODE)
        # =====================================================================
        status_bar = self.statusBar()
        status_bar.showMessage("⚪ Одиночный режим")

        # Индикатор встроенного Linter'а
        self.btn_linter_status = QPushButton("✓ Ошибок нет")
        self.btn_linter_status.setToolTip("Синтаксический анализ Python (Linter)")
        self.btn_linter_status.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                border: none;
                color: #4ec9b0;
                font-weight: bold;
                font-size: 11px;
                margin-right: 14px;
                padding: 2px 6px;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 0.15);
                border-radius: 3px;
            }
        """)
        self.btn_linter_status.clicked.connect(self.on_linter_badge_clicked)
        status_bar.addPermanentWidget(self.btn_linter_status)

        self.lbl_sb_py = QLabel("🐍 Python")
        self.lbl_sb_py.setStyleSheet("margin-right: 12px; color: #ffffff;")
        status_bar.addPermanentWidget(self.lbl_sb_py)

        self.lbl_sb_encoding = QLabel("UTF-8")
        self.lbl_sb_encoding.setStyleSheet("margin-right: 12px; color: #ffffff;")
        status_bar.addPermanentWidget(self.lbl_sb_encoding)

        self.lbl_sb_indent = QLabel("Spaces: 4")
        self.lbl_sb_indent.setStyleSheet("margin-right: 8px; color: #ffffff;")
        status_bar.addPermanentWidget(self.lbl_sb_indent)

        # Индикатор скорости соединения: задержка и скорость передачи текста.
        # По клику открывается подробное окно замеров.
        self.lbl_sb_speed = QLabel("📶 нет связи")
        self.lbl_sb_speed.setCursor(Qt.CursorShape.PointingHandCursor)
        self.lbl_sb_speed.setStyleSheet("margin-right: 10px; color: #d0d0d0;")
        self.lbl_sb_speed.setToolTip(
            "Скорость соединения с напарником: задержка (пинг) и объём текста в секунду.\n"
            "Нажмите, чтобы открыть подробные замеры."
        )
        self.lbl_sb_speed.mousePressEvent = lambda _e: self.on_show_connection_stats()
        status_bar.addPermanentWidget(self.lbl_sb_speed)

        # Горячие клавиши
        act_run = QAction("Запуск", self)
        act_run.setShortcut(QKeySequence("F5"))
        act_run.triggered.connect(self.on_run_code)
        self.addAction(act_run)

        act_stop = QAction("Стоп", self)
        act_stop.setShortcut(QKeySequence("Shift+F5"))
        act_stop.triggered.connect(self.on_stop_code)
        self.addAction(act_stop)

        act_save = QAction("Сохранить", self)
        act_save.setShortcut(QKeySequence("Ctrl+S"))
        act_save.triggered.connect(self.on_save_file)
        self.addAction(act_save)

        act_new_tab = QAction("Новая вкладка", self)
        act_new_tab.setShortcut(QKeySequence("Ctrl+N"))
        act_new_tab.triggered.connect(self.on_new_tab_clicked)
        self.addAction(act_new_tab)

        act_close_tab = QAction("Закрыть вкладку", self)
        act_close_tab.setShortcut(QKeySequence("Ctrl+W"))
        act_close_tab.triggered.connect(self.on_close_current_tab)
        self.addAction(act_close_tab)

        # Горячие клавиши поиска и замены
        act_find = QAction("Найти", self)
        act_find.setShortcut(QKeySequence("Ctrl+F"))
        act_find.triggered.connect(self.on_show_find)
        self.addAction(act_find)

        act_replace = QAction("Заменить", self)
        act_replace.setShortcut(QKeySequence("Ctrl+H"))
        act_replace.triggered.connect(self.on_show_replace)
        self.addAction(act_replace)

        act_comment = QAction("Комментировать (Ctrl+/)", self)
        act_comment.setShortcut(QKeySequence("Ctrl+/"))
        act_comment.triggered.connect(self.editor.toggle_comment)
        self.addAction(act_comment)

    def _connect_signals(self):
        self.editor.code_changed_by_user.connect(self.on_local_code_changed)
        self.editor.cursor_position_changed.connect(self.on_local_cursor_moved)
        self.editor.linter_issues_changed.connect(self.on_linter_issues_changed)
        self.editor.line_locked_warning.connect(self.on_line_locked_warning)
        self.editor.divergence_detected.connect(self.on_divergence_detected)

        self.network.connected_signal.connect(self.on_peer_connected)
        self.network.disconnected_signal.connect(self.on_peer_disconnected)
        self.network.peer_file_changed.connect(self.on_peer_file_changed)
        self.network.file_code_received.connect(self.on_remote_code_received)
        self.network.code_edit_received.connect(self.on_remote_code_edit)
        self.network.edit_loss_detected.connect(self.on_edit_loss_detected)
        self.network.cursor_received.connect(self.on_remote_cursor_received)
        self.network.run_code_requested.connect(self.on_remote_run_requested)
        self.network.stop_code_requested.connect(self.on_remote_stop_requested)
        self.network.output_received.connect(self.on_remote_output_received)
        self.network.execution_finished_received.connect(self.on_remote_execution_finished)
        self.network.error_highlight_received.connect(self.on_remote_error_highlight)
        self.network.chat_received.connect(self.on_remote_chat_received)
        self.network.status_signal.connect(self.on_network_status)
        self.network.profile_updated.connect(self.on_peer_profile_updated)
        self.network.sync_requested_signal.connect(self.on_sync_requested)
        self.network.stdin_received.connect(self.on_remote_stdin_received)
        self.network.project_tree_received.connect(self.on_remote_project_tree_received)
        self.network.metrics_updated.connect(self.on_metrics_updated)
        self.network.transport_changed.connect(self.on_transport_changed)
        self.network.file_content_requested.connect(self.on_remote_file_content_requested)
        self.network.file_content_received.connect(self.on_remote_file_content_received)
        self.network.file_create_requested.connect(self.on_remote_file_create_requested)
        self.network.file_delete_requested.connect(self.on_remote_file_delete_requested)
        self.network.file_save_requested.connect(self.on_remote_file_save_requested)

        self.runner.output_received.connect(self.on_local_output_received)
        self.runner.execution_started.connect(self.on_local_execution_started)
        self.runner.execution_finished.connect(self.on_local_execution_finished)
        self.runner.error_detected.connect(self.on_local_error_detected)

    # ------------------ Встроенный Linter синтаксиса ------------------
    def on_linter_issues_changed(self, issues: list):
        """Обновление интерактивного индикатора Linter'а в статус-баре."""
        if not issues:
            self.btn_linter_status.setText("✓ Ошибок нет")
            self.btn_linter_status.setStyleSheet(
                "color: #4ec9b0; font-weight: bold; font-size: 11px; background-color: transparent; border: none; margin-right: 14px;"
            )
            self.btn_linter_status.setToolTip("Синтаксис Python корректен (AST Linter)")
        else:
            err = next((i for i in issues if i.severity == "error"), None)
            if err:
                self.btn_linter_status.setText(f"✕ Строка {err.line}: {err.message}")
                self.btn_linter_status.setStyleSheet(
                    "color: #f48771; font-weight: bold; font-size: 11px; background-color: transparent; border: none; margin-right: 14px;"
                )
                self.btn_linter_status.setToolTip(f"Синтаксическая ошибка на строке {err.line}. Нажмите для перехода.")
            else:
                warn = issues[0]
                self.btn_linter_status.setText(f"⚠ Строка {warn.line}: {warn.message}")
                self.btn_linter_status.setStyleSheet(
                    "color: #e5c07b; font-weight: bold; font-size: 11px; background-color: transparent; border: none; margin-right: 14px;"
                )
                self.btn_linter_status.setToolTip(f"Предупреждение на строке {warn.line}. Нажмите для перехода.")

    def on_linter_badge_clicked(self):
        """Клик по индикатору синтаксиса: мгновенный переход к строке с ошибкой."""
        error = next((i for i in self.editor.linter_issues if i.severity == "error"), None)
        # Раньше брался просто первый элемент списка: в бейдже показывалась
        # ошибка, а переход шёл на предупреждение.
        target = error or (self.editor.linter_issues[0] if self.editor.linter_issues else None)
        if target:
            self.editor.jump_to_line(target.line)

    # ------------------ Защита строк от одновременных правок (Line Lock) ------------------
    def on_line_locked_warning(self, line: int, peer_name: str):
        """Предупреждение при попытке редактирования занятой напарником строки."""
        self.statusBar().showMessage(f"🔒 Строка {line} занята: её редактирует {peer_name}", 2500)

    def on_toggle_line_lock(self):
        """Включение / выключение защиты строк от одновременного редактирования."""
        enabled = self.btn_lock_toggle.isChecked()
        self.editor.line_lock_enabled = enabled
        if enabled:
            self.btn_lock_toggle.setText("🔒 Lock: Вкл")
            self.btn_lock_toggle.setStyleSheet("color: #4ec9b0; font-weight: bold;")
            self.statusBar().showMessage("Защита строк (Line Lock) включена", 3000)
        else:
            self.btn_lock_toggle.setText("🔓 Lock: Выкл")
            self.btn_lock_toggle.setStyleSheet("color: #858585;")
            self.editor.line_locks.clear()
            self.editor.highlight_current_line()
            self.editor.line_number_area.update()
            self.statusBar().showMessage("Защита строк (Line Lock) отключена", 3000)

    # ------------------ Вкладки файлов (Tabs) ------------------
    def _next_tab_title(self) -> str:
        """Уникальное имя для новой вкладки (сквозная нумерация, без повторов)."""
        existing = {t.get("title") for t in self.tabs}
        num = len(self.tabs) + 1
        while f"файл_{num}.py" in existing:
            num += 1
        return f"файл_{num}.py"

    def on_new_tab_clicked(self):
        """Создание новой пустой вкладки."""
        self._add_tab(self._next_tab_title(), "# Новый файл\n", None)

    def _save_current_tab_content(self):
        """Сохранение текста редактора в данные активной вкладки."""
        if 0 <= self.current_tab_index < len(self.tabs):
            self.tabs[self.current_tab_index]["content"] = self.editor.toPlainText()

    def _add_tab(self, title: str, content: str, file_path: str | None = None, is_remote: bool = False):
        """Добавление вкладки с переключением на нее."""
        self._save_current_tab_content()

        tab = {
            "title": title,
            "path": file_path,
            "content": content
        }
        if is_remote:
            tab["is_remote"] = True
        self.tabs.append(tab)

        idx = self.tab_bar.addTab(title)
        # Состояние выставляем явно, не полагаясь на сигнал currentChanged:
        # на этапе инициализации сигналы ещё не подключены, и активная вкладка
        # оставалась рассинхронизированной с редактором.
        self.current_tab_index = idx
        self.current_filepath = file_path
        self.tab_bar.setCurrentIndex(idx)
        self.editor.load_text_programmatically(content)
        self.editor.setFocus()

    def _on_tab_context_menu(self, pos):
        """Контекстное меню по правому клику на вкладку."""
        idx = self.tab_bar.tabAt(pos)
        if idx < 0:
            return
        menu = QMenu(self)
        act_close = menu.addAction("✕ Закрыть вкладку")
        act_close_others = menu.addAction("Закрыть другие вкладки")
        act_close_all = menu.addAction("Закрыть все вкладки")

        action = menu.exec(self.tab_bar.mapToGlobal(pos))
        if action == act_close:
            self.on_tab_close_requested(idx)
        elif action == act_close_others:
            # Закрываем все вкладки, кроме выбранной, от последней к первой:
            # индексы остающихся вкладок при этом не сдвигаются.
            for i in range(len(self.tabs) - 1, -1, -1):
                if i != idx:
                    self.on_tab_close_requested(i)
        elif action == act_close_all:
            for i in range(len(self.tabs) - 1, -1, -1):
                self.on_tab_close_requested(i)

    def on_tab_changed(self, index: int):
        """Смена активной вкладки."""
        if index < 0 or index >= len(self.tabs):
            return

        # ВАЖНО: содержимое редактора сохраняем только когда сигнал означает
        # реальное переключение на эту вкладку. removeTab() эмитит currentChanged
        # синхронно во время удаления, когда self.tabs уже укорочен, — без этой
        # проверки текст активной вкладки записывался в соседнюю (потеря данных).
        if index == self.current_tab_index:
            if 0 <= self.current_tab_index < len(self.tabs):
                self.tabs[self.current_tab_index]["content"] = self.editor.toPlainText()
            return

        # Накопленные правки уходящей вкладки отправляем до смены контекста
        self.flush_pending_sync()
        self._save_current_tab_content()

        self.current_tab_index = index
        tab_data = self.tabs[index]
        self.current_filepath = tab_data["path"]

        self.editor.load_text_programmatically(tab_data["content"])
        self.statusBar().showMessage(f"Активный файл: {tab_data['title']}", 2500)

        # Оповещаем напарника о переходе в другой файл
        if self._session_alive():
            self.network.send_active_file(tab_data["title"])

    def on_tab_close_requested(self, index: int):
        """Закрытие вкладки по крестику или колесику мыши."""
        if index < 0 or index >= len(self.tabs):
            return

        if len(self.tabs) <= 1:
            self.tabs[0] = {"title": "main.py", "path": None, "content": ""}
            self.tab_bar.setTabText(0, "main.py")
            self.current_filepath = None
            self.editor.load_text_programmatically("")
            self.statusBar().showMessage("Вкладка очищена", 2000)
            if self._session_alive():
                self.network.send_active_file("main.py")
            return

        self._save_current_tab_content()

        self.tabs.pop(index)

        # Удаляем вкладку с заблокированными сигналами: иначе removeTab эмитит
        # currentChanged в момент, когда self.tabs и индексы уже разъехались.
        self.tab_bar.blockSignals(True)
        try:
            self.tab_bar.removeTab(index)
        finally:
            self.tab_bar.blockSignals(False)

        # Выбираем ближайшую вкладку так, чтобы текущая не менялась без причины
        if index < self.current_tab_index:
            new_index = self.current_tab_index - 1
        elif index == self.current_tab_index:
            new_index = min(index, len(self.tabs) - 1)
        else:
            new_index = self.current_tab_index
        new_index = max(0, min(new_index, len(self.tabs) - 1))

        self.tab_bar.blockSignals(True)
        try:
            self.tab_bar.setCurrentIndex(new_index)
        finally:
            self.tab_bar.blockSignals(False)

        self.current_tab_index = new_index
        tab_data = self.tabs[new_index]
        self.current_filepath = tab_data.get("path")
        self.editor.load_text_programmatically(tab_data.get("content", ""))
        self.statusBar().showMessage(f"Активный файл: {tab_data['title']}", 2000)

        if self._session_alive():
            self.network.send_active_file(tab_data["title"])

    def on_close_current_tab(self):
        """Закрытие текущей вкладки по Ctrl+W."""
        self.on_tab_close_requested(self.current_tab_index)

    def _open_file_in_tab(self, file_path: str):
        """Открытие файла во вкладке (или переход, если уже открыт)."""
        norm_path = os.path.normcase(os.path.abspath(file_path))
        for i, t in enumerate(self.tabs):
            if t["path"] and os.path.normcase(os.path.abspath(t["path"])) == norm_path:
                self.tab_bar.setCurrentIndex(i)
                return

        # Если единственная вкладка пуста и без привязки к файлу
        if len(self.tabs) == 1 and not self.tabs[0]["path"] and not self.editor.toPlainText().strip():
            try:
                with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
                fname = os.path.basename(file_path)
                self.tabs[0] = {"title": fname, "path": file_path, "content": content}
                self.tab_bar.setTabText(0, fname)
                self.current_filepath = file_path
                self.editor.load_text_programmatically(content)
                self.statusBar().showMessage(f"Открыт файл: {fname}", 4000)
                if self._session_alive():
                    self.network.send_code_update(content, fname)
                return
            except Exception as e:
                QMessageBox.critical(self, "Ошибка чтения файла", str(e))
                return

        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            fname = os.path.basename(file_path)
            self._add_tab(fname, content, file_path)
            self.statusBar().showMessage(f"Открыт файл: {fname}", 4000)
            if self._session_alive():
                self.network.send_code_update(content, fname)
        except Exception as e:
            QMessageBox.critical(self, "Ошибка чтения файла", str(e))

    # ------------------ Поиск и замена (Ctrl+F / Ctrl+H) ------------------
    def on_show_find(self):
        tc = self.editor.textCursor()
        sel = tc.selectedText()
        self.find_replace_bar.show_find(sel)

    def on_show_replace(self):
        tc = self.editor.textCursor()
        sel = tc.selectedText()
        self.find_replace_bar.show_replace(sel)

    def _on_find_next(self, pattern: str, case_sensitive: bool):
        cur, total = self.editor.find_next_match(pattern, case_sensitive)
        self.find_replace_bar.update_match_status(cur, total)

    def _on_find_prev(self, pattern: str, case_sensitive: bool):
        cur, total = self.editor.find_prev_match(pattern, case_sensitive)
        self.find_replace_bar.update_match_status(cur, total)

    def _on_replace(self, pattern: str, replacement: str, case_sensitive: bool):
        cur, total = self.editor.replace_current_match(pattern, replacement, case_sensitive)
        self.find_replace_bar.update_match_status(cur, total)

    def _on_replace_all(self, pattern: str, replacement: str, case_sensitive: bool):
        count = self.editor.replace_all_matches(pattern, replacement, case_sensitive)
        self.statusBar().showMessage(f"Заменено {count} совпадений", 3000)

    def _on_find_closed(self):
        self.editor.clear_search_highlight()
        self.editor.setFocus()

    # ------------------ Интерактивный ввод input() ------------------
    def on_send_stdin(self):
        """Отправка строки в стандартный ввод input() программы."""
        text = self.stdin_input.text()
        if not text and not self.runner.is_running:
            return
        self.stdin_input.clear()

        # Отображаем локальный ввод в консоли красивым бирюзовым цветом
        self.append_console_output(f"❯ {text}\n", is_err=False, is_input=True)

        if self.runner.is_running:
            self.runner.send_stdin(text)

        if self._session_alive():
            self.network.send_stdin_input(text)

    def on_remote_stdin_received(self, text: str):
        """Получение ввода input() от напарника по сети."""
        self.append_console_output(f"❯ [{self.peer_name}]: {text}\n", is_err=False, is_input=True)
        if self.runner.is_running:
            self.runner.send_stdin(text)

    # ------------------ Онлайн подключение через Интернет ------------------
    def on_create_cloud_room(self):
        """Создание онлайн комнаты (работает через интернет)."""
        dialog = CreateRoomDialog(self.user_name, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            data = dialog.get_data()
            self.user_name = data["name"]
            set_user_profile(self.user_name, self.user_color)
            add_recent_room(data["room_code"])
            self.network.username = self.user_name
            self.network.user_color = self.user_color
            self.network.start_cloud_room(data["room_code"], is_creator=True)

            self._set_session_active(True)
            badge_text = f"🟢 Комната: {data['room_code']}"
            self.lbl_room_badge.setText(badge_text)
            self.lbl_toolbar_badge.setText(badge_text)
            self.lbl_toolbar_badge.setStyleSheet("""
                QLabel {
                    background-color: #0d4429;
                    color: #7ee787;
                    font-size: 11px;
                    font-weight: bold;
                    padding: 4px 12px;
                    border-radius: 12px;
                    border: 1px solid #1a7f37;
                }
            """)
            self.statusBar().setStyleSheet("background-color: #0d4429; color: #ffffff;")
            self._update_peers_list()
            # Замеры качества связи начинаем сразу после запуска сессии
            self._start_metrics()

    def on_join_cloud_room(self):
        """Вход в комнату по коду."""
        dialog = JoinRoomDialog(self.user_name, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            data = dialog.get_data()
            if not data["room_code"]:
                QMessageBox.warning(self, "Ошибка", "Пожалуйста, введите код комнаты!")
                return
            self.user_name = data["name"]
            set_user_profile(self.user_name, self.user_color)
            add_recent_room(data["room_code"])
            self.network.username = self.user_name
            self.network.user_color = self.user_color
            self.network.start_cloud_room(data["room_code"], is_creator=False)

            self._set_session_active(True)
            badge_text = f"🟠 Комната: {data['room_code']}"
            self.lbl_room_badge.setText(badge_text)
            self.lbl_toolbar_badge.setText(badge_text)
            self.lbl_toolbar_badge.setStyleSheet("""
                QLabel {
                    background-color: #4a2800;
                    color: #ffb74d;
                    font-size: 11px;
                    font-weight: bold;
                    padding: 4px 12px;
                    border-radius: 12px;
                    border: 1px solid #ff9800;
                }
            """)
            self.statusBar().setStyleSheet("background-color: #4a2800; color: #ffffff;")
            self._update_peers_list()
            # Замеры качества связи начинаем сразу после запуска сессии
            self._start_metrics()

    def on_lan_dialog(self):
        """Локальное подключение по IP."""
        dialog = LanDialog(self.user_name, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            if dialog.selected_action == "host":
                self.user_name = dialog.host_name_edit.text().strip() or "Хост"
                self.network.username = self.user_name
                self.network.start_host(dialog.host_port.value())
                self._set_session_active(True)
                badge_text = f"LAN Хост :{dialog.host_port.value()}"
                self.lbl_room_badge.setText(badge_text)
                self.lbl_toolbar_badge.setText(f"🟢 {badge_text}")
            elif dialog.selected_action == "client":
                self.user_name = dialog.client_name_edit.text().strip() or "Гость"
                self.network.username = self.user_name
                self.network.connect_to_host(dialog.client_ip.text().strip(), dialog.client_port.value())
                self._set_session_active(True)
                badge_text = f"LAN Клиент: {dialog.client_ip.text().strip()}"
                self.lbl_room_badge.setText(badge_text)
                self.lbl_toolbar_badge.setText(f"🟠 {badge_text}")
            self._update_peers_list()
            # Замеры качества связи начинаем сразу после запуска сессии
            self._start_metrics()

    def on_direct_dialog(self):
        """Прямое соединение через интернет без посредника (обмен кодом вручную)."""
        dialog = DirectConnectDialog(self)
        self._set_session_active(True)
        self.lbl_room_badge.setText("Прямое соединение")
        self.lbl_toolbar_badge.setText("🟡 Прямое соединение: ждём напарника")
        self._start_metrics()
        dialog.exec()

    def _set_session_active(self, active: bool):
        self.btn_create_room.setEnabled(not active)
        self.btn_join_room.setEnabled(not active)
        self.btn_lan.setEnabled(not active)
        self.btn_direct.setEnabled(not active)
        self.btn_disconnect.setEnabled(active)

    def on_disconnect(self):
        self.network.stop()
        self._set_session_active(False)
        self.peer_name = "Ожидание..."
        self.lbl_room_badge.setText("Одиночный режим")
        self.lbl_toolbar_badge.setText("⚪ Одиночный режим")
        self.lbl_toolbar_badge.setStyleSheet("""
            QLabel {
                background-color: #2d2d30;
                color: #858585;
                font-size: 11px;
                font-weight: bold;
                padding: 4px 12px;
                border-radius: 12px;
                border: 1px solid #3c3c3c;
            }
        """)
        self.statusBar().setStyleSheet("background-color: #007acc; color: #ffffff;")
        self._clear_peer_editor_state()
        self._update_peers_list()

        # Восстановление локального проекта
        self.is_remote_project = False
        self.remote_project_name = None
        folder_name = os.path.basename(self.project_path) or self.project_path
        self.lbl_project_header.setText("📁 ПРОЕКТ:")
        self.lbl_project_title.setText(folder_name)
        self.lbl_project_title.setToolTip(self.project_path)
        self._refresh_project_tree()

        self.statusBar().showMessage("Сессия завершена. Режим: Одиночный")
        self._stop_metrics()

    def on_transport_changed(self, endpoint: str, kind: str):
        """
        Канал связи с напарником переключился.

        В облачной комнате трафик сначала идёт через посредника, а после
        пробивания NAT — напрямую. Прямой канал в разы быстрее, поэтому
        показываем его в статус-баре.
        """
        if kind == "p2p":
            self.statusBar().showMessage(
                f"⚡ Прямое соединение с напарником: {endpoint}", 8000)
            self.append_chat_system(f"Прямое соединение установлено ({endpoint})")
        else:
            self.append_chat_system("Прямое соединение недоступно, работаем через сервер комнат")
        self._update_speed_label()

    def on_peer_connected(self, peer_name: str, source: str):
        is_reconnect = (self.peer_name == peer_name)
        self.peer_name = peer_name
        self._update_peers_list()
        self.statusBar().showMessage(f"🟢 На связи: {peer_name} ({source})")
        if not is_reconnect:
            self.append_chat_system(f"{peer_name} подключился к сессии!")
            if self.network.is_host:
                self._send_full_sync()

    def _send_full_sync(self):
        """Отправка напарнику текущего кода и структуры проекта."""
        self._save_current_tab_content()
        text = self.editor.toPlainText()
        self.network.send_code_update(text, self._current_file_title())
        # После полной синхронизации фиксируем базу: дальше будут уходить
        # только изменённые фрагменты, а не файл целиком
        self.editor.mark_synced(text)
        self._broadcast_project_manifest()

    def _clear_peer_editor_state(self):
        """
        Полная очистка состояния напарника в редакторе.

        Раньше словарь курсоров чистился напрямую, а блокировки строк оставались:
        до 2.5 с пользователь видел «🔒 Строка занята» от уже ушедшего напарника.
        """
        for uid in list(self.editor.remote_cursors.keys()):
            self.editor.remove_remote_cursor(uid)
        self.editor.remote_cursors.clear()
        self.editor.line_locks.clear()
        self.editor.highlight_current_line()
        self.editor.line_number_area.update()
        self.peer_active_file = ""

    def on_peer_disconnected(self, reason: str):
        self.append_chat_system(f"Напарник вышел: {reason}")
        self.peer_name = "Ожидание..."
        self._clear_peer_editor_state()
        self._stop_metrics()
        self._update_peers_list()
        self.statusBar().showMessage(f"🔴 {reason}")

    def on_sync_requested(self):
        """Новый участник запросил актуальный код и дерево проекта."""
        if self.network.is_host:
            self._send_full_sync()

    def on_network_status(self, msg: str):
        self.statusBar().showMessage(msg)

    def on_peer_profile_updated(self, name: str, color: str):
        """Напарник сменил имя или цвет: это не новое подключение."""
        self.peer_name = name
        self.statusBar().showMessage(f"👤 Напарник обновил профиль: {name}", 3000)
        self._update_peers_list()

    def on_edit_profile(self):
        """Открытие диалога редактирования профиля (имя, цвет)."""
        dlg = UserProfileDialog(self.user_name, self.user_color, is_first_run=False, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.user_name, self.user_color = dlg.get_profile()
            self.network.send_profile_update(self.user_name, self.user_color)
            self._update_peers_list()
            self.statusBar().showMessage(f"Профиль сохранен: {self.user_name}", 3000)

    def on_show_updates(self):
        """Проверка обновлений и информация о версии."""
        self._updates_dialog = AboutAndUpdatesDialog(self, network=self.network)
        self._updates_dialog.exec()
        self._updates_dialog = None

    def _auto_check_updates(self):
        """
        Тихая проверка обновлений при запуске.

        Диалог не показываем: если версия свежая, пользователь ничего не видит,
        а при наличии обновления диалог сам предложит установку.
        """
        if getattr(sys, "_running_tests", False):
            return
        self._updates_dialog = AboutAndUpdatesDialog(self, silent=True, network=self.network)
        self._updates_dialog.check_updates()

    def on_peer_file_changed(self, peer_name: str, file_name: str):
        """Оповещение о том, какой файл сейчас открыл напарник."""
        self.peer_active_file = file_name
        self.statusBar().showMessage(f"👁 {peer_name} открыл файл: {file_name}", 3500)
        self._update_peers_list(peer_active_file=file_name)

    def _update_peers_list(self, peer_active_file: str | None = None):
        if peer_active_file is None:
            # Раньше метод вызывался без аргумента почти везде, и отметка
            # «[файл]» у напарника пропадала после любого события.
            peer_active_file = self.peer_active_file
        self.peers_list.clear()
        self.peers_list.addItem(QListWidgetItem(f"🟢 {self.user_name} (Вы)"))
        if self.network.is_connected and self.peer_name != "Ожидание...":
            suffix = f" [{peer_active_file}]" if peer_active_file else ""
            # Задержку показываем рядом с именем напарника в списке участников
            latency = self.network.latency_ms
            ping = f" · {latency:.0f} мс" if latency > 0 else ""
            self.peers_list.addItem(QListWidgetItem(f"🟢 {self.peer_name}{ping}{suffix}"))
        elif self.network.running:
            item_wait = QListWidgetItem("⏳ Ожидание напарника...")
            item_wait.setForeground(QColor("#858585"))
            self.peers_list.addItem(item_wait)

    # ------------------ Синхронизация кода и курсора ------------------
    def _current_file_title(self) -> str:
        if 0 <= self.current_tab_index < len(self.tabs):
            return self.tabs[self.current_tab_index].get("title", "")
        return ""

    def _session_alive(self) -> bool:
        return bool(self.network.is_connected or self.network.running)

    # ------------------ Скорость соединения (пинг и трафик) ------------------
    def on_metrics_tick(self):
        """Периодический опрос: пересчёт скоростей и отправка ping."""
        if not self.network.is_connected:
            # Соединение ещё устанавливается или уже потеряно — показываем статус
            self._update_speed_label()
            return
        self.network.update_rates()

        if self.network.mode == "direct":
            # В прямом режиме посредника нет: замер кругового времени через
            # брокера невозможен, а канал и так прямой. Показываем честную
            # оценку «меньше миллисекунды» вместо вечного «измеряется».
            self._update_speed_label()
            return

        # Замер задержки раз в две секунды: чаще нет смысла, реже — теряется
        # динамика при ухудшении связи
        self._metrics_tick += 1
        if self._metrics_tick % 2 == 0:
            self.network.send_ping()
        self._update_speed_label()

    def _start_metrics(self):
        """Запуск измерений при подключении."""
        self.network.reset_metrics()
        self._metrics_tick = 0
        if not self.metrics_timer.isActive():
            self.metrics_timer.start()
        self._update_speed_label()

    def _stop_metrics(self):
        """Остановка измерений и сброс индикатора."""
        self.metrics_timer.stop()
        self.lbl_sb_speed.setText("📶 нет связи")
        self.lbl_sb_speed.setStyleSheet("margin-right: 10px; color: #d0d0d0;")
        self.lbl_sb_speed.setToolTip("Соединение не активно")

    def on_metrics_updated(self, latency_ms: float, rate_in: float, rate_out: float):
        """Обновление индикатора скорости в статус-баре."""
        self._update_speed_label()

    def _update_speed_label(self):
        latency = self.network.latency_ms
        rate = (self.network.rate_in_bps + self.network.rate_out_bps) / 1024.0
        # «0 мс» — это не отсутствие замера, а честный результат на быстром
        # канале (localhost): показываем его как «<1 мс»
        measured = bool(self.network.latency_samples)

        if not self.network.is_connected:
            text, color = "📶 нет связи", "#d0d0d0"
        elif not measured and self.network.mode == "direct":
            # Прямой режим без посредника: замерять круговое время не через кого,
            # но канал заведомо быстрый — показываем это честно
            text, color = f"⚡ 🟢 <1 мс · {rate:.1f} КБ/с", "#7ee787"
        elif not measured:
            text, color = "📶 …", "#d0d0d0"
        else:
            if latency < 60:
                icon, color = "🟢", "#7ee787"
            elif latency < 150:
                icon, color = "🟢", "#4ec9b0"
            elif latency < 400:
                icon, color = "🟡", "#e5c07b"
            else:
                icon, color = "🔴", "#f48771"
            shown = f"{latency:.0f} мс" if latency >= 1 else "<1 мс"
            # Прямое соединение помечаем молнией: сразу видно, идёт обмен
            # напрямую или ещё через посредника
            mark = "⚡ " if getattr(self.network, "transport", "") == "p2p" else ""
            text = f"{mark}{icon} {shown} · {rate:.1f} КБ/с"

        self.lbl_sb_speed.setText(text)
        self.lbl_sb_speed.setStyleSheet(f"margin-right: 10px; color: {color};")
        avg = self.network.latency_avg_ms
        avg_text = f"{avg:.0f}" if avg >= 1 else "<1"
        self.lbl_sb_speed.setToolTip(
            f"Задержка: {latency:.0f} мс (средняя {avg_text} мс)\n"
            f"Приём текста: {self.network.rate_in_bps / 1024:.1f} КБ/с\n"
            f"Отправка текста: {self.network.rate_out_bps / 1024:.1f} КБ/с\n"
            f"Пиковая скорость: {self.network.throughput_peak_bps / 1024:.1f} КБ/с\n"
            f"За сессию: ↑ {self.network.bytes_out / 1024:.1f} КБ · "
            f"↓ {self.network.bytes_in / 1024:.1f} КБ\n"
            "Нажмите для подробных замеров."
        )

    def on_show_connection_stats(self):
        """Окно с подробной статистикой соединения."""
        dialog = ConnectionStatsDialog(self.network, self)
        dialog.exec()

    def on_divergence_detected(self):
        """
        Свои правки и правки напарника наложились друг на друга.

        Слить их корректно без общей истории нельзя, поэтому стороны
        синхронизируются полностью: хост отправляет свой текст, гость просит
        прислать актуальную версию. Так расхождение не остаётся навсегда.
        """
        self.statusBar().showMessage("Правки наложились, синхронизирую версии...", 3000)
        self.append_chat_system("Обнаружено наложение правок, выполнена полная синхронизация")

        if self.network.is_host:
            self._send_full_sync()
        else:
            # Свой текст не теряем: сохраняем копию рядом с проектом
            self._backup_conflicting_text()
            self.network.send_sync_request()

    def _backup_conflicting_text(self):
        """
        Сохранение своей версии файла при наложении правок.

        Гость получит версию хоста, поэтому его вариант (с наложением) стоит
        сохранить рядом — иначе локальные правки исчезли бы без следа.
        """
        try:
            title = self._current_file_title() or "main.py"
            safe = os.path.basename(title)
            backup_dir = os.path.join(self.project_path, ".duopy_conflicts")
            os.makedirs(backup_dir, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = os.path.join(backup_dir, f"{stamp}_{safe}")
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.editor.toPlainText())
            self.statusBar().showMessage(f"Ваша версия сохранена: {path}", 5000)
        except Exception as e:
            print(f"[DuoPy] Не удалось сохранить резервную копию: {e}")

    def on_local_code_changed(self, new_code: str):
        if 0 <= self.current_tab_index < len(self.tabs):
            tab = self.tabs[self.current_tab_index]
            tab["content"] = new_code
            # Запоминаем, к какому файлу и к какой сессии относятся правки:
            # дебаунс не должен отправить их как текст другой вкладки.
            tab["dirty"] = True
        if self._session_alive() and not self._suppress_code_echo:
            self._pending_sync_tab = self.current_tab_index
            self._pending_sync_session = id(self.network)
            # Фрагмент весит десятки байт, поэтому отправлять можно чаще, чем
            # раньше (120 мс задавалось для пересылки всего файла целиком)
            self.code_sync_timer.start(60)

    # До этого размера отправляем файл целиком: конфликты одновременного
    # набора сходятся сами (последний победил), а разница в скорости незаметна.
    # Крупные файлы передавать целиком слишком дорого — там шлём фрагмент.
    DELTA_MODE_FROM_CHARS = 8000

    def _flush_code_update(self):
        """
        Отправка накопленных правок.

        Для небольших файлов уходит весь текст (это устойчиво к одновременному
        набору и стоит доли миллисекунды), для больших — только изменённый
        фрагмент: пересылка 100 КБ на публичный брокер занимала около двух секунд.
        """
        if not self._session_alive():
            return
        # Сессия пересоздана или вкладку успели сменить — старые правки уже
        # не относятся к текущему файлу, отправлять их нельзя.
        if getattr(self, "_pending_sync_session", None) != id(self.network):
            return
        if getattr(self, "_pending_sync_tab", self.current_tab_index) != self.current_tab_index:
            return

        text = self.editor.toPlainText()
        if 0 <= self.current_tab_index < len(self.tabs):
            self.tabs[self.current_tab_index]["content"] = text

        if len(text) < self.DELTA_MODE_FROM_CHARS:
            self.editor.mark_synced(text)
            self.network.send_code_update(text, self._current_file_title())
            return

        try:
            pos, removed, insert, digest = self.editor.capture_send_delta()
        except Exception as e:
            # Если минимальную правку посчитать не удалось, отправляем файл целиком:
            # лучше дороже, чем потерять изменение
            print(f"[DuoPy] Не удалось вычислить правку, отправляю файл целиком: {e}")
            self.editor.mark_synced(text)
            self.network.send_code_update(text, self._current_file_title())
            return

        if pos == 0 and removed == 0 and not insert:
            return          # отправлять нечего: текст уже совпадает с базой
        self.network.send_code_delta(pos, removed, insert,
                                     self._current_file_title(), digest)

    def on_edit_loss_detected(self):
        """
        Часть правок напарника потерялась в сети.

        Правки идут без подтверждения доставки (QoS 0) — иначе каждое нажатие
        платило бы полным кругом задержки. Потерю замечаем по пропуску номера
        и сразу выравниваем состояние: гость просит актуальный текст, хост его
        отправляет.
        """
        if self._sync_pending:
            return
        self._sync_pending = True
        self.append_chat_system("Потеряна часть правок, выполняю синхронизацию")
        if self.network.is_host:
            self._send_full_sync()
        else:
            self.network.send_sync_request()

    def on_remote_code_edit(self, pos: int, removed: int, insert: str,
                            file_name: str = "", base_digest: str = ""):
        """Применение правки напарника, пришедшей фрагментом."""
        # Пока идёт выравнивание, правки основаны на устаревшем тексте —
        # применяем только метку номера, содержимое придёт полным снапшотом
        if self._sync_pending:
            return
        if not file_name or not self.tabs:
            if not self.editor.apply_remote_delta(pos, removed, insert, base_digest):
                self.network.send_sync_request()
            if 0 <= self.current_tab_index < len(self.tabs):
                self.tabs[self.current_tab_index]["content"] = self.editor.toPlainText()
            return

        matched_idx = self._find_remote_tab(file_name)
        if matched_idx >= 0 and matched_idx != self.current_tab_index:
            # Вкладка не активна: правку применим, когда её откроют, а пока
            # храним накопленный текст в кэше вкладки
            cached = self.tabs[matched_idx].get("content", "")
            pos = max(0, min(pos, len(cached)))
            removed = max(0, min(removed, len(cached) - pos))
            self.tabs[matched_idx]["content"] = cached[:pos] + insert + cached[pos + removed:]
            return

        if matched_idx < 0:
            # Файл ещё не открыт у нас: просим полную версию
            self.network.send_sync_request()
            return

        # Активная вкладка: пауза набора не мешает, фрагмент маленький
        if not self.editor.apply_remote_delta(pos, removed, insert, base_digest):
            self.statusBar().showMessage("Расхождение версий, запрашиваю полную синхронизацию...", 3000)
            self.network.send_sync_request()
            return

        self._suppress_code_echo = True
        try:
            self.editor.flush_deferred_remote_update()
        finally:
            self._suppress_code_echo = False
        self.tabs[matched_idx]["content"] = self.editor.toPlainText()

    def flush_pending_sync(self):
        """Немедленная отправка накопленных правок (перед сменой вкладки)."""
        if self.code_sync_timer.isActive():
            self.code_sync_timer.stop()
            self._flush_code_update()

    def on_local_cursor_moved(self, line: int, col: int, pos: int, sel_start: int, sel_end: int):
        self.lbl_cursor_pos.setText(f"Ln {line}, Col {col + 1}")
        if self._session_alive():
            # Имя файла запоминаем вместе с координатами: иначе при переключении
            # вкладки внутри 50 мс каретка уходила напарнику как координаты другого файла.
            self.pending_cursor_data = (
                line, col, pos, sel_start, sel_end, self._current_file_title()
            )
            if not self.cursor_sync_timer.isActive():
                self.cursor_sync_timer.start(50)

    def _flush_cursor_update(self):
        """Периодическая отправка координат курсора без перегрузки сети."""
        if self.pending_cursor_data and self._session_alive():
            line, col, pos, sel_start, sel_end, cur_file = self.pending_cursor_data
            self.network.send_cursor_position(line, col, pos, sel_start, sel_end, cur_file)
            self.pending_cursor_data = None

    def _find_remote_tab(self, file_name: str, rel_path: str | None = None) -> int:
        """
        Поиск вкладки, которой соответствует файл напарника.

        Матчинг идёт по относительному пути, а по имени — только среди вкладок,
        которые уже помечены как удалённые. Раньше совпадение по basename могло
        подсунуть чужой код в ЛОКАЛЬНУЮ вкладку с тем же именем файла: путь
        оставался своим, и Ctrl+S перезаписывал свой файл кодом напарника.
        """
        if rel_path:
            norm = os.path.normcase(rel_path.replace("\\", "/"))
            for i, t in enumerate(self.tabs):
                if t.get("is_remote") and t.get("path") and \
                        os.path.normcase(str(t["path"]).replace("\\", "/")) == norm:
                    return i
        if not file_name:
            return -1
        for i, t in enumerate(self.tabs):
            if t.get("is_remote") and t.get("title") == file_name:
                return i
        return -1

    def on_remote_code_received(self, code: str, file_name: str = ""):
        """Применение кода от напарника с привязкой к вкладке/файлу."""
        # Полный снапшот завершает выравнивание: дальше снова можно
        # пользоваться быстрыми фрагментами
        self._sync_pending = False
        if not file_name or not self.tabs:
            self.editor.apply_remote_code(code, defer_on_conflict=True)
            self.editor.flush_deferred_remote_update()
            if 0 <= self.current_tab_index < len(self.tabs):
                self.tabs[self.current_tab_index]["content"] = self.editor.toPlainText()
            return

        matched_idx = self._find_remote_tab(file_name)
        if matched_idx >= 0:
            if matched_idx == self.current_tab_index:
                # Пока приходит текст от напарника, не отправляем обратно свой
                # (возможно, устаревший) вариант — иначе правки напарника гибнут.
                self._suppress_code_echo = True
                try:
                    self.editor.apply_remote_code(code, defer_on_conflict=True)
                    # Правку, отложенную из-за набора, применяем сразу после
                    # паузы: иначе стороны расходятся и текст не появляется.
                    self.editor.flush_deferred_remote_update()
                finally:
                    self._suppress_code_echo = False
            # Кэш берём из редактора: там итоговый текст с учётом отложенной
            # правки, а не устаревший снимок из сети.
            self.tabs[matched_idx]["content"] = (
                self.editor.toPlainText() if matched_idx == self.current_tab_index else code
            )
            return

        # Если открыта единственная пустая вкладка без файла — переиспользуем её
        if len(self.tabs) == 1 and not self.tabs[0].get("path"):
            self.tabs[0] = {
                "title": file_name,
                "path": None,
                "is_remote": True,
                "content": code
            }
            self.tab_bar.setTabText(0, file_name)
            self.editor.apply_remote_code(code)
            return

        # Иначе открываем новую вкладку для файла напарника
        self._save_current_tab_content()
        self.tabs.append({
            "title": file_name,
            "path": None,
            "is_remote": True,
            "content": code
        })
        self.tab_bar.addTab(file_name)

    def on_remote_cursor_received(self, uid: str, name: str, color: str, line: int, col: int,
                                  pos: int = 0, sel_start: int = 0, sel_end: int = 0, file_name: str = ""):
        if not self.network.is_connected and name:
            self.peer_name = name
            self._update_peers_list(self.peer_active_file)

        cur_file = ""
        if 0 <= self.current_tab_index < len(self.tabs):
            cur_file = self.tabs[self.current_tab_index]["title"]

        if not file_name or file_name == cur_file:
            self.editor.update_remote_cursor(uid, name, color, line, col, pos, sel_start, sel_end)
        else:
            self.editor.remove_remote_cursor(uid)

    # ------------------ Запуск кода ------------------
    def on_run_code(self):
        code = self.editor.toPlainText()
        if not code.strip():
            return

        self.editor.clear_error_line()
        self.console_output.clear()

        if self._session_alive() and not self._suppress_remote_run_echo:
            self.network.send_run_command()

        self.runner.run_code(code)

    def on_configure_python(self):
        """Открытие окна настройки интерпретатора Python."""
        dialog = PythonSettingsDialog(self)
        dialog.exec()

    def on_remote_run_requested(self):
        """Напарник нажал 'Запустить': спрашиваем согласие, прежде чем исполнять код локально."""
        if not self.editor.toPlainText().strip():
            self.statusBar().showMessage("Напарник запросил запуск, но редактор пуст", 3000)
            return

        # Отвечаем отказом на время показа диалога, чтобы запрос от напарника
        # не ушёл обратно в сеть и не превратился в бесконечный цикл.
        self._suppress_remote_run_echo = True
        try:
            reply = QMessageBox.question(
                self,
                "Запрос на запуск кода",
                f"{self.peer_name} предлагает выполнить текущий код на этом компьютере.\n\n"
                "Код будет запущен локально в отдельном процессе. Продолжить?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
        finally:
            self._suppress_remote_run_echo = False

        if reply == QMessageBox.StandardButton.Yes:
            self.on_run_code()
        else:
            self.lbl_exec_status.setText(f"● Запуск отклонён (запрос {self.peer_name})")
            self.lbl_exec_status.setStyleSheet("color: #f48771; font-size: 11px;")

    def on_stop_code(self):
        self.runner.stop()
        if self._session_alive():
            self.network.send_stop_command()
        self.lbl_exec_status.setText("● Остановлено")
        self.lbl_exec_status.setStyleSheet("color: #f48771; font-size: 11px;")
        self._active_runs = 0
        self.btn_run.setEnabled(True)
        self.btn_stop.setEnabled(False)

    def on_remote_stop_requested(self):
        self.runner.stop()
        self.lbl_exec_status.setText(f"● Остановлено напарником {self.peer_name}")
        self.lbl_exec_status.setStyleSheet("color: #f48771; font-size: 11px;")
        self._active_runs = 0
        self.btn_run.setEnabled(True)
        self.btn_stop.setEnabled(False)

    # ------------------ Вывод в консоль ------------------
    def on_local_output_received(self, text: str, is_err: bool):
        self.append_console_output(text, is_err)
        if self._session_alive():
            self.network.send_output(text, is_err)

    def on_remote_output_received(self, text: str, is_err: bool):
        self.append_console_output(text, is_err)

    def on_local_execution_started(self):
        self._active_runs += 1
        self.lbl_exec_status.setText("● Выполняется...")
        self.lbl_exec_status.setStyleSheet("color: #4ec9b0; font-weight: bold; font-size: 11px;")
        self.btn_run.setEnabled(False)
        self.btn_stop.setEnabled(True)

    def on_local_execution_finished(self, return_code: int, duration: float):
        self._active_runs = max(0, self._active_runs - 1)
        status_msg = f"✓ Завершено (код {return_code}) за {duration:.2f}с" if return_code == 0 \
            else f"✕ Ошибка (код {return_code}) за {duration:.2f}с"
        self.lbl_exec_status.setText(status_msg)
        color = "#4ec9b0" if return_code == 0 else "#f48771"
        self.lbl_exec_status.setStyleSheet(f"color: {color}; font-size: 11px; font-weight: bold;")
        # Кнопки возвращаем в исходное состояние только когда локальных
        # запусков действительно не осталось.
        self.btn_run.setEnabled(True)
        self.btn_stop.setEnabled(self._active_runs > 0)

        if self._session_alive():
            self.network.send_execution_finished(return_code, duration)

    def on_remote_execution_finished(self, return_code: int, duration: float):
        status_msg = f"✓ Завершено у напарника (код {return_code}) за {duration:.2f}с" if return_code == 0 \
            else f"✕ Ошибка у напарника (код {return_code}) за {duration:.2f}с"
        self.lbl_exec_status.setText(status_msg)
        color = "#4ec9b0" if return_code == 0 else "#f48771"
        self.lbl_exec_status.setStyleSheet(f"color: {color}; font-size: 11px; font-weight: bold;")
        # Чужое завершение не должно разблокировать кнопки, если у нас
        # всё ещё выполняется свой процесс.
        if self._active_runs == 0:
            self.btn_run.setEnabled(True)
            self.btn_stop.setEnabled(False)

    def on_local_error_detected(self, line_num: int, error_text: str):
        self.editor.set_error_line(line_num, error_text)
        if self._session_alive():
            self.network.send_error_line(line_num, error_text)

    def on_remote_error_highlight(self, line_num: int, message: str):
        # Подсветку показываем, но фокус и каретку пользователя не отбираем:
        # ошибка напарника не должна сдвигать наш курсор в начало строки.
        self.editor.set_error_line(line_num, message, take_focus=False)

    def append_console_output(self, text: str, is_err: bool, is_input: bool = False):
        cursor = self.console_output.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        fmt = QTextCharFormat()
        if is_input:
            fmt.setForeground(QColor("#4ec9b0"))
            fmt.setFontWeight(QFont.Weight.Bold)
        elif is_err:
            fmt.setForeground(QColor("#f48771"))
        else:
            fmt.setForeground(QColor("#cccccc"))
        cursor.insertText(text, fmt)
        self.console_output.setTextCursor(cursor)
        self.console_output.ensureCursorVisible()

    def on_clear_console(self):
        self.console_output.clear()
        self.lbl_exec_status.setText("● Очищено")
        self.lbl_exec_status.setStyleSheet("color: #858585; font-size: 11px;")
        self.editor.clear_error_line()

    # ------------------ Чат (Стильные бабблы сообщений) ------------------
    def on_send_chat(self):
        text = self.chat_input.text().strip()
        if not text:
            return
        self.chat_input.clear()

        if self._session_alive():
            time_str = self.network.send_chat_message(text)
            self._append_chat_message(self.user_name, text, time_str, is_me=True)
        else:
            self._append_chat_message(self.user_name, text, "сейчас", is_me=True)

    def on_remote_chat_received(self, sender: str, message: str, time_str: str):
        self._append_chat_message(sender, message, time_str, is_me=False)

    def _append_chat_message(self, sender: str, message: str, time_str: str, is_me: bool):
        color = "#4ec9b0" if is_me else "#ff9800"
        bg_bubble = "#1a364a" if is_me else "#2b2b2f"
        border_bubble = "#007acc" if is_me else "#3c3c3c"
        safe_sender = html.escape(sender)
        safe_msg = html.escape(message).replace("\n", "<br>")
        safe_time = html.escape(time_str)

        h = f"""
        <div style='margin-bottom: 8px;'>
            <div style='background-color: {bg_bubble}; border: 1px solid {border_bubble}; border-radius: 6px; padding: 6px 10px;'>
                <span style='color: {color}; font-weight: bold; font-size: 11px;'>{safe_sender}</span>
                <span style='color: #777777; font-size: 10px; margin-left: 6px;'>[{safe_time}]</span><br>
                <span style='color: #e0e0e0; font-size: 12px; line-height: 1.4;'>{safe_msg}</span>
            </div>
        </div>
        """
        self.chat_history.append(h)

    def append_chat_system(self, message: str):
        safe_msg = html.escape(message)
        h = f"<div style='margin-bottom: 6px; color: #569cd6; font-size: 11px; font-style: italic;'>ℹ {safe_msg}</div>"
        self.chat_history.append(h)

    # ------------------ Файлы и сохранение ------------------
    def on_save_file(self):
        if not (0 <= self.current_tab_index < len(self.tabs)):
            return

        tab_data = self.tabs[self.current_tab_index]
        content = self.editor.toPlainText()
        tab_data["content"] = content

        # Если файл удаленный (из проекта хоста)
        if tab_data.get("is_remote"):
            rel_path = tab_data.get("path")
            if not rel_path:
                QMessageBox.warning(self, "Ошибка", "У удаленной вкладки потерян путь к файлу.")
                return
            if not self._session_alive():
                # Раньше здесь молча показывалось «сохранено», хотя сообщение
                # никуда не уходило: правки жили только в памяти и терялись.
                answer = QMessageBox.question(
                    self,
                    "Нет соединения с напарником",
                    "Связь с хостом потеряна, сохранить содержимое этого файла на диск?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.Yes,
                )
                if answer == QMessageBox.StandardButton.Yes:
                    path, _ = QFileDialog.getSaveFileName(
                        self, "Сохранить файл", tab_data["title"],
                        "Python Files (*.py);;All Files (*)"
                    )
                    if path:
                        try:
                            with open(path, "w", encoding="utf-8") as f:
                                f.write(content)
                            tab_data["is_remote"] = False
                            tab_data["path"] = path
                            tab_data["title"] = os.path.basename(path)
                            self.tab_bar.setTabText(self.current_tab_index, tab_data["title"])
                            self.current_filepath = path
                            self.statusBar().showMessage(f"Файл сохранён локально: {path}", 4000)
                        except Exception as e:
                            QMessageBox.critical(self, "Ошибка сохранения", str(e))
                return
            self.network.send_save_file(rel_path, content)
            self.statusBar().showMessage(f"💾 Изменения отправлены хосту: {tab_data['title']}", 4000)
            return

        if not tab_data["path"]:
            path, _ = QFileDialog.getSaveFileName(self, "Сохранить файл", "", "Python Files (*.py);;All Files (*)")
            if not path:
                return
            tab_data["path"] = path
            tab_data["title"] = os.path.basename(path)
            self.tab_bar.setTabText(self.current_tab_index, tab_data["title"])
            self.current_filepath = path

        try:
            with open(tab_data["path"], "w", encoding="utf-8") as f:
                f.write(content)
            self.statusBar().showMessage(f"Файл сохранен: {tab_data['path']}", 4000)
            self._refresh_project_tree()
            if self.network.is_host and self._session_alive():
                self._broadcast_project_manifest()
        except Exception as e:
            QMessageBox.critical(self, "Ошибка сохранения", str(e))

    def on_open_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Открыть файл", "", "Python Files (*.py);;All Files (*)")
        if path:
            self._open_file_in_tab(path)

    # ------------------ Проводник файлов проекта ------------------
    def _create_project_explorer(self) -> QWidget:
        container = QWidget()
        container.setStyleSheet("background-color: #222223;")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        # Шапка проводника
        header = QHBoxLayout()
        header.setSpacing(4)
        folder_name = os.path.basename(self.project_path) or self.project_path

        self.lbl_project_header = QLabel("📁 ПРОЕКТ:")
        self.lbl_project_header.setStyleSheet("font-weight: bold; color: #9cdcfe; font-size: 11px; letter-spacing: 1px;")

        self.lbl_project_title = QLabel(folder_name)
        self.lbl_project_title.setStyleSheet("color: #4ec9b0; font-weight: bold; font-size: 11px;")
        self.lbl_project_title.setToolTip(self.project_path)

        btn_new_file = QPushButton("📄+")
        btn_new_file.setToolTip("Создать новый файл в проекте")
        btn_new_file.setFixedSize(30, 24)
        btn_new_file.setStyleSheet("padding: 2px 4px; font-size: 11px;")
        btn_new_file.clicked.connect(self.on_new_file_clicked)

        btn_refresh = QPushButton("🔄")
        btn_refresh.setToolTip("Обновить список файлов")
        btn_refresh.setFixedSize(26, 24)
        btn_refresh.setStyleSheet("padding: 2px 4px; font-size: 11px;")
        btn_refresh.clicked.connect(self._refresh_project_tree)

        header.addWidget(self.lbl_project_header)
        header.addWidget(self.lbl_project_title)
        header.addStretch()
        header.addWidget(btn_new_file)
        header.addWidget(btn_refresh)
        layout.addLayout(header)

        # Дерево файлов с поддержкой локальных и удаленных проектов хоста
        self.file_tree = QTreeWidget()
        self.file_tree.setHeaderHidden(True)
        self.file_tree.setAnimated(True)
        self.file_tree.setIndentation(14)
        self.file_tree.itemClicked.connect(self.on_file_item_clicked)
        self.file_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.file_tree.customContextMenuRequested.connect(self.on_file_tree_context_menu)

        layout.addWidget(self.file_tree)
        return container

    def _get_local_project_manifest(self) -> list[dict]:
        """Сканирование локальной структуры проекта для отображения и передачи напарнику."""
        manifest = []
        ignore_names = {
            ".git", ".venv", "venv", "__pycache__", ".idea", ".vscode",
            "build", "dist", ".pytest_cache", ".mypy_cache", "env", "node_modules"
        }
        ignore_exts = {".pyc", ".pyo", ".pyd", ".exe", ".dll", ".so", ".dylib", ".bin"}

        if not os.path.exists(self.project_path):
            return manifest

        for root, dirs, files in os.walk(self.project_path):
            dirs[:] = [d for d in sorted(dirs) if d not in ignore_names and not d.startswith(".")]

            rel_dir = os.path.relpath(root, self.project_path)
            if rel_dir != ".":
                norm_rel = rel_dir.replace("\\", "/")
                manifest.append({"path": norm_rel, "is_dir": True})

            for f in sorted(files):
                if f.startswith("."):
                    continue
                ext = os.path.splitext(f)[1].lower()
                if ext in ignore_exts:
                    continue
                full_path = os.path.join(root, f)
                try:
                    if os.path.getsize(full_path) > 2 * 1024 * 1024:
                        continue
                except OSError:
                    continue
                norm_rel = os.path.relpath(full_path, self.project_path).replace("\\", "/")
                manifest.append({"path": norm_rel, "is_dir": False})

        return manifest

    def _broadcast_project_manifest(self):
        """Хост отправляет актуальную структуру своего проекта гостю."""
        manifest = self._get_local_project_manifest()
        project_name = os.path.basename(self.project_path) or self.project_path
        self.network.send_project_tree(project_name, manifest)

    def _populate_tree_from_manifest(self, manifest: list[dict], is_remote: bool):
        """Заполнение дерева файлов из списка путей."""
        self.file_tree.clear()
        dir_nodes: dict[str, QTreeWidgetItem] = {}

        sorted_entries = sorted(
            manifest,
            key=lambda x: (x.get("path", "").count("/"), 0 if x.get("is_dir") else 1, x.get("path", "").lower())
        )

        for entry in sorted_entries:
            rel_path = entry.get("path", "").replace("\\", "/").strip("/")
            if not rel_path:
                continue
            is_dir = entry.get("is_dir", False)
            parts = rel_path.split("/")
            name = parts[-1]
            parent_path = "/".join(parts[:-1]) if len(parts) > 1 else ""

            # Обеспечиваем существование промежуточных папок
            parent_item = None
            if parent_path:
                accum = ""
                for p in parts[:-1]:
                    accum = f"{accum}/{p}" if accum else p
                    if accum not in dir_nodes:
                        anc_parent = dir_nodes.get("/".join(accum.split("/")[:-1])) if "/" in accum else None
                        if anc_parent:
                            anc_item = QTreeWidgetItem(anc_parent, [f"📁 {p}"])
                        else:
                            anc_item = QTreeWidgetItem(self.file_tree, [f"📁 {p}"])
                        anc_item.setData(0, Qt.ItemDataRole.UserRole, accum)
                        anc_item.setData(0, Qt.ItemDataRole.UserRole + 1, True)
                        anc_item.setData(0, Qt.ItemDataRole.UserRole + 2, is_remote)
                        anc_item.setExpanded(True)
                        dir_nodes[accum] = anc_item
                parent_item = dir_nodes.get(parent_path)

            if is_dir:
                label = f"📁 {name}"
            elif name.endswith(".py"):
                label = f"🐍 {name}"
            elif name.endswith((".json", ".toml", ".yaml", ".yml", ".ini", ".cfg")):
                label = f"⚙️ {name}"
            elif name.endswith((".md", ".txt", ".rst")):
                label = f"📝 {name}"
            elif name.endswith((".html", ".htm", ".css", ".js")):
                label = f"🌐 {name}"
            else:
                label = f"📄 {name}"

            if parent_item:
                item = QTreeWidgetItem(parent_item, [label])
            else:
                item = QTreeWidgetItem(self.file_tree, [label])

            item.setData(0, Qt.ItemDataRole.UserRole, rel_path)
            item.setData(0, Qt.ItemDataRole.UserRole + 1, is_dir)
            item.setData(0, Qt.ItemDataRole.UserRole + 2, is_remote)
            item.setToolTip(0, rel_path)

            if is_dir:
                item.setExpanded(True)
                dir_nodes[rel_path] = item

    def on_open_project_folder(self):
        """Выбор рабочей папки проекта."""
        folder = QFileDialog.getExistingDirectory(self, "Выберите папку проекта", self.project_path)
        if folder:
            self.project_path = os.path.abspath(folder)
            self.is_remote_project = False
            self.remote_project_name = None
            folder_name = os.path.basename(self.project_path) or self.project_path
            self.lbl_project_header.setText("📁 ПРОЕКТ:")
            self.lbl_project_title.setText(folder_name)
            self.lbl_project_title.setToolTip(self.project_path)
            self._refresh_project_tree()
            self.statusBar().showMessage(f"Открыта папка проекта: {self.project_path}", 4000)
            if self.network.is_host and self._session_alive():
                self._broadcast_project_manifest()

    def _refresh_project_tree(self):
        """Обновление дерева файлов (локального или запрос хосту)."""
        if self.is_remote_project:
            if self._session_alive():
                self.network.send_sync_request()
                self.statusBar().showMessage("Запрос актуального проекта у хоста...", 2500)
        else:
            manifest = self._get_local_project_manifest()
            self._populate_tree_from_manifest(manifest, is_remote=False)

    def on_file_item_clicked(self, item: QTreeWidgetItem, column: int):
        """Открытие файла при клике в дереве проекта."""
        rel_path = item.data(0, Qt.ItemDataRole.UserRole)
        is_dir = item.data(0, Qt.ItemDataRole.UserRole + 1)
        is_remote = item.data(0, Qt.ItemDataRole.UserRole + 2)

        if not rel_path:
            return

        if is_dir:
            item.setExpanded(not item.isExpanded())
            return

        if is_remote:
            # Проверяем, открыт ли уже этот удаленный файл во вкладке
            idx = self._find_remote_tab(os.path.basename(rel_path), rel_path)
            if idx >= 0:
                self.tab_bar.setCurrentIndex(idx)
                return
            self.statusBar().showMessage(f"Загрузка '{rel_path}' с хоста...", 3000)
            self.network.send_request_file(rel_path)
        else:
            full_path = os.path.join(self.project_path, rel_path)
            if os.path.isfile(full_path):
                self._open_file_in_tab(full_path)

    def on_new_file_clicked(self):
        """Создание нового файла в проекте."""
        prompt = "Имя файла в проекте хоста:" if self.is_remote_project else "Имя файла в проекте:"
        name, ok = QInputDialog.getText(self, "Новый файл", prompt)
        if ok and name.strip():
            name = name.strip().replace("\\", "/")
            if not os.path.splitext(name)[1]:
                name += ".py"

            if self.is_remote_project:
                self.network.send_create_file(name)
                self.statusBar().showMessage(f"Запрос на создание '{name}' отправлен хосту...", 3000)
                return

            full_path = resolve_inside(name, self.project_path)
            if not full_path:
                QMessageBox.warning(self, "Некорректное имя",
                                    "Имя файла должно быть относительным путём внутри папки проекта.")
                return
            if os.path.exists(full_path):
                QMessageBox.warning(self, "Файл существует", f"Файл '{name}' уже существует в папке проекта!")
                return
            try:
                os.makedirs(os.path.dirname(full_path), exist_ok=True)
                with open(full_path, "w", encoding="utf-8") as f:
                    f.write("# Новый файл проекта\n")
                self._refresh_project_tree()
                self._open_file_in_tab(full_path)
                if self.network.is_host and self._session_alive():
                    self._broadcast_project_manifest()
            except Exception as e:
                QMessageBox.critical(self, "Ошибка создания файла", str(e))

    def on_file_tree_context_menu(self, pos):
        """Контекстное меню в проводнике файлов (Создать, Удалить, Переименовать)."""
        item = self.file_tree.itemAt(pos)
        menu = QMenu(self)

        act_create = menu.addAction("📄 Создать файл")
        act_rename = menu.addAction("✏️ Переименовать")
        act_delete = menu.addAction("🗑️ Удалить")

        if not item:
            act_rename.setEnabled(False)
            act_delete.setEnabled(False)
        elif self.is_remote_project:
            act_rename.setEnabled(False)

        action = menu.exec(self.file_tree.viewport().mapToGlobal(pos))
        if action == act_create:
            self.on_new_file_clicked()
        elif action == act_rename and item and not self.is_remote_project:
            rel_path = item.data(0, Qt.ItemDataRole.UserRole)
            old_name = os.path.basename(rel_path)
            new_name, ok = QInputDialog.getText(self, "Переименовать", "Новое имя:", text=old_name)
            # Берём только имя файла: '..\..\x.py' или 'C:\Windows\x.py' иначе
            # уводили переименование за пределы проекта.
            new_name = os.path.basename((new_name or "").strip().replace("\\", "/"))
            if ok and new_name and new_name != old_name:
                file_path = os.path.join(self.project_path, rel_path)
                new_path = os.path.join(os.path.dirname(file_path), new_name)
                try:
                    os.rename(file_path, new_path)
                    self._refresh_project_tree()
                    old_norm = os.path.normcase(os.path.abspath(file_path))
                    new_norm = os.path.normcase(os.path.abspath(new_path))
                    # Обновляем и файлы, лежащие внутри переименованной папки:
                    # иначе их пути оставались старыми и сохранение шло в никуда.
                    for i, t in enumerate(self.tabs):
                        t_path = t.get("path")
                        if not t_path:
                            continue
                        t_norm = os.path.normcase(os.path.abspath(t_path))
                        if t_norm == old_norm:
                            t["path"] = new_path
                            t["title"] = new_name
                            self.tab_bar.setTabText(i, new_name)
                        elif t_norm.startswith(old_norm + os.sep):
                            t["path"] = new_path + t_path[len(file_path):]
                    if self.current_filepath:
                        cur_norm = os.path.normcase(os.path.abspath(self.current_filepath))
                        if cur_norm == old_norm:
                            self.current_filepath = new_path
                        elif cur_norm.startswith(old_norm + os.sep):
                            self.current_filepath = new_path + self.current_filepath[len(file_path):]
                    if self.network.is_host and self._session_alive():
                        self._broadcast_project_manifest()
                except Exception as e:
                    QMessageBox.critical(self, "Ошибка переименования", str(e))
        elif action == act_delete and item:
            rel_path = item.data(0, Qt.ItemDataRole.UserRole)
            fname = os.path.basename(rel_path)
            reply = QMessageBox.question(
                self, "Подтверждение",
                f"Вы уверены, что хотите удалить '{fname}'?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.Yes:
                if self.is_remote_project:
                    self.network.send_delete_file(rel_path)
                    self.statusBar().showMessage(f"Запрос на удаление '{fname}' отправлен хосту...", 3000)
                else:
                    file_path = resolve_inside(rel_path, self.project_path)
                    if not file_path:
                        QMessageBox.warning(self, "Ошибка", "Путь вне папки проекта.")
                        return
                    try:
                        if os.path.isdir(file_path):
                            import shutil
                            shutil.rmtree(file_path)
                        else:
                            os.remove(file_path)
                        self._close_tabs_for_path(file_path)
                        self._refresh_project_tree()
                        if self.network.is_host and self._session_alive():
                            self._broadcast_project_manifest()
                    except Exception as e:
                        QMessageBox.critical(self, "Ошибка удаления", str(e))

    def _close_tabs_for_path(self, full_path: str):
        """
        Закрытие вкладок удалённого с диска файла (или файлов внутри папки).

        Раньше вкладка оставалась живой, и Ctrl+S молча воссоздавал удалённый файл.
        """
        target = os.path.normcase(os.path.abspath(full_path))
        for i in range(len(self.tabs) - 1, -1, -1):
            t_path = self.tabs[i].get("path")
            if not t_path:
                continue
            t_norm = os.path.normcase(os.path.abspath(t_path))
            if t_norm == target or t_norm.startswith(target + os.sep):
                if len(self.tabs) <= 1:
                    self.tabs[0] = {"title": "main.py", "path": None, "content": ""}
                    self.tab_bar.setTabText(0, "main.py")
                    self.current_filepath = None
                    self.editor.load_text_programmatically("")
                else:
                    self.on_tab_close_requested(i)

    # ------------------ Сетевые обработчики удаленного проекта ------------------
    def on_remote_project_tree_received(self, project_name: str, files: list):
        """Гость получает актуальную структуру проекта хоста."""
        self.is_remote_project = True
        self.remote_project_name = project_name
        self.lbl_project_header.setText("🌐 ПРОЕКТ ХОСТА:")
        self.lbl_project_title.setText(project_name)
        self.lbl_project_title.setToolTip(f"Удаленный проект хоста: {project_name}")
        self._populate_tree_from_manifest(files, is_remote=True)
        self.statusBar().showMessage(f"📁 Проект хоста '{project_name}' синхронизирован ({len(files)} элементов)", 4000)

    def on_remote_file_content_requested(self, rel_path: str):
        """Хост получает запрос на выдачу содержимого файла гостю."""
        full_path = resolve_inside(rel_path, self.project_path)
        if not full_path:
            print(f"[DuoPy] Отклонён запрос файла вне проекта: {rel_path!r}")
            return
        if os.path.isfile(full_path):
            try:
                with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
                self.network.send_file_data(rel_path, content)
            except Exception as e:
                print(f"Ошибка чтения файла для отправки гостю: {e}")

    def on_remote_file_content_received(self, rel_path: str, content: str):
        """Гость получает запрошенное содержимое файла от хоста и открывает во вкладке."""
        fname = os.path.basename(rel_path)
        matched = self._find_remote_tab(fname, rel_path)
        if matched >= 0:
            self.tabs[matched]["content"] = content
            self.tab_bar.setCurrentIndex(matched)
            self.editor.load_text_programmatically(content)
            self.statusBar().showMessage(f"🌐 Открыт удаленный файл: {fname}", 3000)
            return

        # Если открыта единственная пустая вкладка — переиспользуем её
        if len(self.tabs) == 1 and not self.tabs[0]["path"] and not self.editor.toPlainText().strip():
            self.tabs[0] = {
                "title": fname,
                "path": rel_path,
                "is_remote": True,
                "content": content
            }
            self.tab_bar.setTabText(0, fname)
            self.current_filepath = rel_path
            self.editor.load_text_programmatically(content)
            self.statusBar().showMessage(f"🌐 Открыт удаленный файл: {fname}", 4000)
            return

        self._save_current_tab_content()
        self.tabs.append({
            "title": fname,
            "path": rel_path,
            "is_remote": True,
            "content": content
        })
        self.tab_bar.addTab(fname)
        self.statusBar().showMessage(f"🌐 Открыт удаленный файл: {fname}", 4000)

    def on_remote_file_create_requested(self, rel_path: str):
        """Хост создает файл на диске по запросу гостя."""
        full_path = resolve_inside(rel_path, self.project_path)
        if not full_path:
            print(f"[DuoPy] Отклонено создание файла вне проекта: {rel_path!r}")
            return
        try:
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            if not os.path.exists(full_path):
                with open(full_path, "w", encoding="utf-8") as f:
                    f.write("# Создано напарником\n")
            self._refresh_project_tree()
            self._broadcast_project_manifest()
            self.statusBar().showMessage(f"📄 Напарник создал файл в проекте: {rel_path}", 4000)
        except Exception as e:
            print(f"Ошибка создания файла: {e}")

    def on_remote_file_delete_requested(self, rel_path: str):
        """Хост удаляет файл с диска по запросу гостя."""
        full_path = resolve_inside(rel_path, self.project_path)
        if not full_path:
            print(f"[DuoPy] Отклонено удаление файла вне проекта: {rel_path!r}")
            return
        try:
            if os.path.isdir(full_path):
                import shutil
                shutil.rmtree(full_path)
            elif os.path.isfile(full_path):
                os.remove(full_path)
            self._refresh_project_tree()
            self._broadcast_project_manifest()
            self.statusBar().showMessage(f"🗑️ Удален файл по запросу напарника: {rel_path}", 4000)
        except Exception as e:
            print(f"Ошибка удаления файла: {e}")

    def on_remote_file_save_requested(self, rel_path: str, content: str):
        """Хост записывает изменения файла от гостя на локальный диск."""
        full_path = resolve_inside(rel_path, self.project_path)
        if not full_path:
            print(f"[DuoPy] Отклонена запись файла вне проекта: {rel_path!r}")
            return
        try:
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(content)
            self.statusBar().showMessage(f"💾 Напарник сохранил файл на диске: {rel_path}", 4000)
            norm_fp = os.path.normcase(full_path)
            for idx, t in enumerate(self.tabs):
                if t.get("path") and os.path.normcase(os.path.abspath(t["path"])) == norm_fp:
                    if idx == self.current_tab_index:
                        # Не затираем открытый буфер молча: если в редакторе есть
                        # несохранённые правки, сначала спрашиваем пользователя.
                        local_text = self.editor.toPlainText()
                        if t.get("dirty") and local_text != content:
                            answer = QMessageBox.question(
                                self,
                                "Напарник сохранил файл",
                                f"{self.peer_name} сохранил {os.path.basename(full_path)} на диск.\n"
                                "В вашем редакторе есть несохранённые изменения. Заменить их версией напарника?",
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                QMessageBox.StandardButton.No,
                            )
                            if answer != QMessageBox.StandardButton.Yes:
                                break
                        self.editor.load_text_programmatically(content)
                    t["content"] = content
                    t.pop("dirty", None)
                    break
        except Exception as e:
            print(f"Ошибка записи сохранения файла: {e}")

    # ------------------ Завершение работы приложения ------------------
    def closeEvent(self, event):
        """
        Корректное завершение: остановка процесса, закрытие сети, предупреждение
        о несохранённых вкладках.

        Раньше при закрытии окна дочерний Python продолжал работать, временный
        файл скрипта не удалялся, а сеть не закрывалась.
        """
        if getattr(self, "_closing", False):
            event.accept()
            return

        dirty_tabs = []
        for t in self.tabs:
            if t.get("path") and t.get("dirty"):
                dirty_tabs.append(t.get("title", "?"))

        if dirty_tabs:
            answer = QMessageBox.question(
                self,
                "Несохранённые изменения",
                "Есть несохранённые вкладки:\n" + "\n".join(f"• {n}" for n in dirty_tabs) +
                "\n\nВсё равно закрыть DuoPy?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return

        self._closing = True
        try:
            if self.runner.is_running:
                self.runner.stop()
            self.network.stop()
        except Exception:
            pass
        event.accept()
