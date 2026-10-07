"""
Модуль версии, проверки и установки обновлений DuoPy через GitHub Releases.

Схема обновления:
1. Фоновый поток запрашивает последний релиз через GitHub API.
2. Если версия новее, пользователю предлагается скачать новый EXE.
3. Файл скачивается в %LOCALAPPDATA%\\DuoPy\\updates (папка приложения может
   быть недоступна для записи).
4. Обновление устанавливается скриптом: он ждёт закрытия DuoPy, подменяет
   исполняемый файл и запускает новую версию. Скрипт запускается отдельным
   процессом, поэтому продолжает работу после выхода приложения.
"""

import json
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime

from PyQt6.QtCore import QThread, QUrl, Qt, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QMessageBox, QProgressBar, QPushButton,
    QVBoxLayout
)

APP_VERSION = "1.2.1"
GITHUB_OWNER = "famouSSeX"
GITHUB_REPO = "DuoPy"
GITHUB_REPO_URL = f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}"
GITHUB_API_RELEASES = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
USER_AGENT = "DuoPy-UpdateCheck"
REQUEST_TIMEOUT = 8
DOWNLOAD_CHUNK = 256 * 1024

# Метка, которую установщик обновления ждёт в аргументах командной строки
RESTART_FLAG = "--duopy-restarted"


def is_frozen() -> bool:
    """Запущено ли приложение как собранный EXE (а не из исходников)."""
    return bool(getattr(sys, "frozen", False))


def parse_version(text: str) -> tuple[int, ...]:
    """Разбор версии вида 'v1.2.10' в кортеж для сравнения."""
    if not text:
        return (0,)
    cleaned = text.strip().lstrip("vV")
    parts = []
    for chunk in cleaned.replace("-", ".").split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts) or (0,)


def is_newer(remote: str, local: str = APP_VERSION) -> bool:
    """Строго ли новее удалённая версия (1.2.10 новее 1.2.9 — покомпонентно)."""
    remote_parts = parse_version(remote)
    local_parts = parse_version(local)
    length = max(len(remote_parts), len(local_parts))
    remote_parts += (0,) * (length - len(remote_parts))
    local_parts += (0,) * (length - len(local_parts))
    return remote_parts > local_parts


def updates_dir() -> str:
    """
    Каталог для скачанных обновлений (в стороне от папки приложения).

    Основной вариант — %LOCALAPPDATA%\\DuoPy\\updates. Если он недоступен
    (ограниченные права), используем системный временный каталог, чтобы
    обновление всё равно можно было скачать.
    """
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    candidates = [
        os.path.join(base, "DuoPy", "updates"),
        os.path.join(tempfile.gettempdir(), "DuoPy", "updates"),
    ]
    last_error = None
    for path in candidates:
        try:
            os.makedirs(path, exist_ok=True)
            # Проверяем именно запись: каталог может существовать, но быть закрытым
            probe = os.path.join(path, ".write_test")
            with open(probe, "w", encoding="utf-8") as f:
                f.write("ok")
            os.remove(probe)
            return path
        except Exception as e:
            last_error = e
    raise OSError(f"Нет доступного каталога для обновления: {last_error}")


class ReleaseInfo:
    """Сведения о найденном релизе."""

    def __init__(self, version: str = "", page_url: str = "", asset_url: str = "",
                 asset_name: str = "", notes: str = "", size: int = 0, error: str = ""):
        self.version = version
        self.page_url = page_url or GITHUB_REPO_URL
        self.asset_url = asset_url
        self.asset_name = asset_name
        self.notes = notes
        self.size = size
        self.error = error

    @property
    def has_asset(self) -> bool:
        return bool(self.asset_url)


class UpdateCheckThread(QThread):
    """Фоновая проверка свежей версии на GitHub Releases."""

    # (есть_обновление, версия, URL страницы релиза, URL файла, имя файла, размер)
    check_finished = pyqtSignal(bool, str, str, str, str, int)

    def run(self):
        info = self.fetch_latest_release()
        self.check_finished.emit(
            bool(info.version) and is_newer(info.version),
            info.version or APP_VERSION,
            info.page_url,
            info.asset_url,
            info.asset_name,
            info.size,
        )

    @staticmethod
    def fetch_latest_release() -> ReleaseInfo:
        """Запрос последнего релиза и выбор подходящего файла для скачивания."""
        try:
            req = urllib.request.Request(
                GITHUB_API_RELEASES, headers={"User-Agent": USER_AGENT}
            )
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as response:
                data = json.loads(response.read().decode("utf-8"))
        except Exception as e:
            return ReleaseInfo(error=str(e))

        version = str(data.get("tag_name", "")).lstrip("vV")
        page_url = data.get("html_url") or GITHUB_REPO_URL
        notes = data.get("body") or ""

        asset_url = asset_name = ""
        size = 0
        for asset in data.get("assets") or []:
            name = str(asset.get("name", ""))
            url = asset.get("browser_download_url") or ""
            if not url:
                continue
            lower = name.lower()
            # Предпочитаем собранный EXE, иначе первый .exe/.zip в релизе
            if lower == "duopy.exe":
                asset_url, asset_name, size = url, name, int(asset.get("size") or 0)
                break
            if not asset_url and (lower.endswith(".exe") or lower.endswith(".zip")):
                asset_url, asset_name, size = url, name, int(asset.get("size") or 0)

        return ReleaseInfo(version=version, page_url=page_url, asset_url=asset_url,
                           asset_name=asset_name, notes=notes, size=size)


class UpdateDownloadThread(QThread):
    """Фоновая загрузка файла релиза с отчётом о прогрессе."""

    progress = pyqtSignal(int, int)          # (получено байт, всего байт)
    finished_ok = pyqtSignal(str)            # путь к скачанному файлу
    failed = pyqtSignal(str)                 # текст ошибки

    def __init__(self, url: str, filename: str, parent=None):
        super().__init__(parent)
        self.url = url
        self.filename = filename or "DuoPy_new.exe"

    def run(self):
        try:
            target = os.path.join(updates_dir(), self.filename)
        except Exception as e:
            self.failed.emit(f"Не удалось подготовить каталог для загрузки: {e}")
            return
        tmp_path = target + ".part"
        try:
            req = urllib.request.Request(self.url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as response:
                total = int(response.headers.get("Content-Length") or 0)
                received = 0
                with open(tmp_path, "wb") as f:
                    while True:
                        chunk = response.read(DOWNLOAD_CHUNK)
                        if not chunk:
                            break
                        f.write(chunk)
                        received += len(chunk)
                        self.progress.emit(received, total)

            # Проверяем, что скачался исполняемый файл, а не HTML-страница ошибки
            with open(tmp_path, "rb") as f:
                head = f.read(2)
            if head != b"MZ":
                os.remove(tmp_path)
                self.failed.emit("Скачанный файл не является программой (возможно, ссылка устарела).")
                return

            if os.path.exists(target):
                os.remove(target)
            os.rename(tmp_path, target)
            self.finished_ok.emit(target)
        except Exception as e:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
            self.failed.emit(str(e))


def install_update(new_exe_path: str) -> tuple[bool, str]:
    """
    Подготовка установки обновления.

    Возвращает (успех, сообщение). Приложение при этом НЕ закрывается:
    скрипт установки ждёт выхода DuoPy, подменяет файл и запускает новую версию.
    """
    if not is_frozen():
        return False, ("Обновление доступно только для собранного DuoPy.exe. "
                       "При запуске из исходников обновите файлы из репозитория (git pull).")

    current_exe = os.path.abspath(sys.executable)
    if not os.path.isfile(new_exe_path):
        return False, "Файл обновления не найден, попробуйте скачать заново."

    try:
        work_dir = updates_dir()
    except Exception as e:
        return False, f"Не удалось подготовить каталог для установки: {e}"

    script_path = os.path.join(work_dir, "apply_update.cmd")
    log_path = os.path.join(work_dir, "update.log")
    # Текст скрипта только латиницей: cmd.exe читает .cmd в OEM-кодировке,
    # поэтому кириллица в журнале установки превращалась бы в мусор.
    script = f"""@echo off
rem DuoPy update installer: waits for the app to exit, replaces the EXE, starts it.
rem The process check uses PowerShell: tasklist may answer "Access denied" in
rem restricted environments, which made the wait loop finish prematurely.
setlocal
set "NEW={new_exe_path}"
set "CUR={current_exe}"
set "LOG={log_path}"
set "PID={os.getpid()}"
echo [%DATE% %TIME%] update started >> "%LOG%"
set /a TRIES=0
:wait
powershell -NoProfile -Command "if (Get-Process -Id %PID% -ErrorAction SilentlyContinue) {{ exit 0 }} else {{ exit 1 }}"
if not errorlevel 1 (
    set /a TRIES+=1
    if %TRIES% GEQ 240 (
        echo [%DATE% %TIME%] timeout: DuoPy still running, update cancelled >> "%LOG%"
        goto :end
    )
    timeout /t 1 /nobreak >nul
    goto wait
)
echo [%DATE% %TIME%] app closed, copying new build >> "%LOG%"
copy /y "%NEW%" "%CUR%" >> "%LOG%" 2>&1
if errorlevel 1 (
    echo [%DATE% %TIME%] failed to replace the executable >> "%LOG%"
    goto :end
)
echo [%DATE% %TIME%] starting the new version >> "%LOG%"
start "" "%CUR%"
:end
endlocal
"""
    try:
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(script)
        creation_flags = 0
        if sys.platform == "win32":
            creation_flags = subprocess.CREATE_NO_WINDOW
        subprocess.Popen(["cmd", "/c", script_path], creationflags=creation_flags,
                         close_fds=True)
    except Exception as e:
        return False, f"Не удалось запустить установщик обновления: {e}"

    return True, ("Обновление готово к установке. Закройте DuoPy — новая версия "
                  "запустится автоматически.")


class AboutAndUpdatesDialog(QDialog):
    """Диалог с информацией о версии, ссылкой на GitHub и проверкой обновлений."""

    def __init__(self, parent=None, silent: bool = False):
        super().__init__(parent)
        self.setWindowTitle("О программе и обновлениях DuoPy")
        self.resize(520, 340)

        # silent: проверка при старте приложения — без лишних диалогов,
        # сообщаем только когда действительно есть новая версия
        self.silent = silent
        self.release: ReleaseInfo | None = None
        self.check_thread: UpdateCheckThread | None = None
        self.download_thread: UpdateDownloadThread | None = None
        self.downloaded_path = ""

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        lbl_title = QLabel("🚀 <b>DuoPy — Python Collaborative IDE</b>")
        lbl_title.setStyleSheet("font-size: 15px; color: #4ec9b0;")
        layout.addWidget(lbl_title)

        lbl_desc = QLabel(
            f"Текущая версия: <b>v{APP_VERSION}</b><br>"
            "Среда парного программирования в реальном времени с поддержкой "
            "онлайн-комнат, AST-линтера, блокировки строк и общего запуска кода."
        )
        lbl_desc.setWordWrap(True)
        lbl_desc.setStyleSheet("color: #cccccc; font-size: 12px; line-height: 1.4;")
        layout.addWidget(lbl_desc)

        self.lbl_status = QLabel("● Нажмите «Проверить обновления»")
        self.lbl_status.setStyleSheet("color: #858585; font-size: 11px;")
        self.lbl_status.setWordWrap(True)
        layout.addWidget(self.lbl_status)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.hide()
        layout.addWidget(self.progress)

        layout.addStretch()

        btns_row = QHBoxLayout()
        self.btn_check = QPushButton("🔄 Проверить обновления")
        self.btn_check.clicked.connect(self.check_updates)

        self.btn_install = QPushButton("⬇️ Скачать и установить")
        self.btn_install.clicked.connect(self.download_and_install)
        self.btn_install.setEnabled(False)

        btn_git = QPushButton("🌐 Открыть GitHub")
        btn_git.clicked.connect(self._open_git)

        btn_close = QPushButton("Закрыть")
        btn_close.clicked.connect(self.accept)

        btns_row.addWidget(self.btn_check)
        btns_row.addWidget(self.btn_install)
        btns_row.addWidget(btn_git)
        btns_row.addStretch()
        btns_row.addWidget(btn_close)
        layout.addLayout(btns_row)

    # ------------------------------------------------------------------ UI

    def _open_git(self):
        QDesktopServices.openUrl(QUrl(GITHUB_REPO_URL))

    def _set_status(self, text: str, color: str = "#858585", bold: bool = False):
        self.lbl_status.setText(text)
        weight = "font-weight: bold;" if bold else ""
        self.lbl_status.setStyleSheet(f"color: {color}; font-size: 11px; {weight}")

    def check_updates(self):
        """Запуск проверки обновлений в фоне."""
        self.btn_check.setEnabled(False)
        self.btn_install.setEnabled(False)
        self._set_status("⏳ Проверка обновлений на GitHub...", "#4ec9b0")

        self.check_thread = UpdateCheckThread(self)
        self.check_thread.check_finished.connect(self._on_check_finished)
        self.check_thread.start()

    def _on_check_finished(self, has_update: bool, version: str, release_url: str,
                           asset_url: str, asset_name: str, size: int):
        self.btn_check.setEnabled(True)
        self.release = ReleaseInfo(version=version, page_url=release_url,
                                   asset_url=asset_url, asset_name=asset_name, size=size)

        if not has_update:
            self._set_status(f"✓ У вас самая свежая версия (v{APP_VERSION})", "#4ec9b0")
            return

        size_note = f", {size / 1048576:.1f} МБ" if size else ""
        self._set_status(f"🎉 Доступна версия v{version}{size_note}", "#7ee787", bold=True)

        if asset_url:
            self.btn_install.setEnabled(True)
            self.btn_install.setText(f"⬇️ Обновить до v{version}")
        else:
            self.btn_install.setEnabled(False)
            self._set_status(
                f"🎉 Доступна версия v{version}, но файл для автообновления не найден. "
                "Откройте страницу релиза.",
                "#e5c07b", bold=True,
            )

        if self.silent:
            # Тихая проверка при запуске: спрашиваем только если обновиться можно
            if asset_url:
                self._offer_install(version)
            else:
                self._notify_no_asset(version, release_url)
            return

        reply = QMessageBox.question(
            self,
            "Обновление доступно",
            f"Вышла новая версия DuoPy v{version}!\n\n"
            + ("Скачать и установить сейчас?" if asset_url
               else "Файл обновления не приложен к релизу. Открыть страницу релиза?"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        if asset_url:
            self.download_and_install()
        else:
            QDesktopServices.openUrl(QUrl(release_url))

    def _offer_install(self, version: str):
        reply = QMessageBox.question(
            self,
            "Доступно обновление DuoPy",
            f"Вышла новая версия DuoPy v{version}.\n\n"
            "Скачать и установить сейчас? Приложение закроется, файл обновится "
            "автоматически и DuoPy запустится заново.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.download_and_install()

    def _notify_no_asset(self, version: str, release_url: str):
        reply = QMessageBox.question(
            self,
            "Доступно обновление DuoPy",
            f"Вышла новая версия DuoPy v{version}, но файл для автообновления "
            "не приложен к релизу.\n\nОткрыть страницу релиза?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply == QMessageBox.StandardButton.Yes:
            QDesktopServices.openUrl(QUrl(release_url))

    # -------------------------------------------------------------- загрузка

    def download_and_install(self):
        """Скачивание файла обновления и запуск установки."""
        if not self.release or not self.release.asset_url:
            return

        self.btn_install.setEnabled(False)
        self.btn_check.setEnabled(False)
        self.progress.setValue(0)
        self.progress.show()
        self._set_status("⏳ Загрузка обновления...", "#4ec9b0")

        self.download_thread = UpdateDownloadThread(
            self.release.asset_url,
            self.release.asset_name or "DuoPy_new.exe",
            self,
        )
        self.download_thread.progress.connect(self._on_progress)
        self.download_thread.finished_ok.connect(self._on_downloaded)
        self.download_thread.failed.connect(self._on_download_failed)
        self.download_thread.start()

    def _on_progress(self, received: int, total: int):
        if total > 0:
            self.progress.setRange(0, 100)
            self.progress.setValue(min(100, int(received * 100 / total)))
        else:
            self.progress.setRange(0, 0)

    def _on_downloaded(self, path: str):
        self.progress.setValue(100)
        self.progress.hide()
        self.btn_check.setEnabled(True)
        self.downloaded_path = path

        ok, message = install_update(path)
        if not ok:
            self._set_status(f"✕ {message}", "#f48771", bold=True)
            self.btn_install.setEnabled(True)
            QMessageBox.warning(self, "Обновление не установлено", message)
            return

        self._set_status(f"✓ {message}", "#7ee787", bold=True)
        QMessageBox.information(
            self,
            "Обновление готово",
            message + f"\n\nФайл: {path}\nЖурнал установки: "
            + os.path.join(updates_dir(), "update.log"),
        )

    def _on_download_failed(self, error: str):
        self.progress.hide()
        self.btn_check.setEnabled(True)
        self.btn_install.setEnabled(True)
        self._set_status(f"✕ Не удалось скачать обновление: {error}", "#f48771", bold=True)
