"""
Модуль запуска и мониторинга выполнения Python кода.
Поддерживает стриминг вывода (stdout/stderr) в реальном времени,
принудительную остановку и парсинг строк traceback ошибок.
"""

import sys
import os
import re
import tempfile
import time
import subprocess
import threading
import shutil
from PyQt6.QtCore import QObject, pyqtSignal


# Пользовательский переопределенный путь к Python
CUSTOM_PYTHON_PATH: str | None = None


def is_valid_python(path: str | None) -> bool:
    """Проверка, является ли путь действительно работающим интерпретатором Python."""
    if not path or not isinstance(path, str):
        return False
    try:
        # Проверяем наличие файла на диске
        if not os.path.isfile(path):
            return False
        # Пробуем запустить с флагом --version
        creation_flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        res = subprocess.run(
            [path, "-c", "import sys; print(sys.version_info[0])"],
            capture_output=True,
            text=True,
            timeout=1.5,
            creationflags=creation_flags
        )
        return res.returncode == 0 and res.stdout.strip() in ("3", "2")
    except Exception:
        return False


def get_python_interpreter() -> str:
    """Определение пути к интерпретатору Python с валидацией запуска."""
    global CUSTOM_PYTHON_PATH

    # 1. Если пользователь сам указал путь и он валиден
    if CUSTOM_PYTHON_PATH and is_valid_python(CUSTOM_PYTHON_PATH):
        return CUSTOM_PYTHON_PATH

    # 2. Если запущено из исходного кода .py
    if not getattr(sys, 'frozen', False):
        if is_valid_python(sys.executable):
            return sys.executable

    # 3. Список потенциальных кандидатов
    candidates = []

    # 3.1 Поиск в PATH
    for name in ("python", "py", "python3"):
        found = shutil.which(name)
        if found:
            candidates.append(found)

    # 3.2 Проверка Windows Local AppData
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    if local_app_data:
        py_dir = os.path.join(local_app_data, "Programs", "Python")
        if os.path.exists(py_dir):
            for entry in sorted(os.listdir(py_dir), reverse=True):
                cand = os.path.join(py_dir, entry, "python.exe")
                candidates.append(cand)

    # 3.3 Проверка Program Files и корней дисков
    prog_files = [os.environ.get("ProgramFiles", "C:\\Program Files"),
                  os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)")]
    for pf in prog_files:
        if pf and os.path.exists(pf):
            for entry in os.listdir(pf):
                if entry.lower().startswith("python"):
                    cand = os.path.join(pf, entry, "python.exe")
                    candidates.append(cand)

    for drive in ("C:", "D:", "E:"):
        for ver in ("Python313", "Python312", "Python311", "Python310", "Python39"):
            cand = os.path.join(drive, "\\", ver, "python.exe")
            candidates.append(cand)

    # 3.4 Проверка Anaconda / Miniconda
    user_home = os.path.expanduser("~")
    for conda in ("anaconda3", "miniconda3"):
        cand = os.path.join(user_home, conda, "python.exe")
        candidates.append(cand)

    # Перебираем кандидатов и возвращаем ПЕРВЫЙ РЕАЛЬНО РАБОТАЮЩИЙ
    for cand in candidates:
        if is_valid_python(cand):
            return cand

    # Резервный вариант
    return "python"


def set_custom_python_path(path: str) -> bool:
    """Установка пользовательского пути к интерпретатору."""
    global CUSTOM_PYTHON_PATH
    if is_valid_python(path):
        CUSTOM_PYTHON_PATH = path
        return True
    return False


class CodeRunner(QObject):
    """Асинхронный исполнитель кода Python с отслеживанием вывода и ошибок."""

    output_received = pyqtSignal(str, bool)     # (текст, is_stderr)
    execution_started = pyqtSignal()
    execution_finished = pyqtSignal(int, float) # (return_code, duration_seconds)
    error_detected = pyqtSignal(int, str)       # (line_number, error_message)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.process: subprocess.Popen | None = None
        self.temp_file: str | None = None
        self.is_running = False
        self.start_time = 0.0
        self.stdin_lock = threading.Lock()
        self.pending_stdin: list[str] = []
        self._stop_requested = False

    def run_code(self, code: str):
        """Запуск кода в отдельном процессе."""
        if self.is_running:
            self.stop()

        self.is_running = True
        self._stop_requested = False
        # Ввод, оставшийся от прошлого запуска, к новой программе не относится
        with self.stdin_lock:
            self.pending_stdin.clear()
        self.start_time = time.time()
        self.execution_started.emit()

        # Создаем временный файл скрипта
        try:
            temp_dir = tempfile.gettempdir()
            self.temp_file = os.path.join(temp_dir, f"duopy_run_{int(time.time() * 1000)}.py")
            with open(self.temp_file, "w", encoding="utf-8") as f:
                f.write(code)
        except Exception as e:
            self.output_received.emit(f"Ошибка создания временного файла: {e}\n", True)
            self.is_running = False
            self.execution_finished.emit(-1, 0.0)
            return

        # Запуск в фоновом потоке
        thread = threading.Thread(target=self._run_process_worker, daemon=True)
        thread.start()

    def _run_process_worker(self):
        """Фоновый рабочий поток запуска процесса."""
        return_code = 0
        # Сырые байты stderr: traceback парсим по ним, чтобы декодирование
        # кусками не искажало многострочные сообщения об ошибках
        raw_stderr = bytearray()
        process = None

        try:
            creation_flags = 0
            if sys.platform == "win32":
                creation_flags = subprocess.CREATE_NO_WINDOW

            # Переменные окружения для принудительного UTF-8 в Python процессе
            proc_env = os.environ.copy()
            proc_env["PYTHONIOENCODING"] = "utf-8"
            proc_env["PYTHONUTF8"] = "1"

            py_bin = get_python_interpreter()
            if not is_valid_python(py_bin):
                self.output_received.emit(
                    f"\n[ОШИБКА] Не удалось запустить интерпретатор Python!\n"
                    f"Путь '{py_bin}' не найден или поврежден (ошибка 0x80070003).\n"
                    f"Нажмите кнопку '⚙️ Python' в панели инструментов, чтобы выбрать файл python.exe вручную.\n",
                    True
                )
                return_code = -1
                return

            process = subprocess.Popen(
                [py_bin, "-X", "utf8", "-u", self.temp_file],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=proc_env,
                creationflags=creation_flags
            )
            self.process = process

            # Если пользователь успел нажать «Стоп» до создания процесса,
            # останавливаем его сразу: иначе is_running уже сброшен, а процесс живёт.
            if self._stop_requested:
                try:
                    process.terminate()
                except Exception:
                    pass

            # Проверяем наличие отложенного ввода
            with self.stdin_lock:
                for pending in self.pending_stdin:
                    try:
                        process.stdin.write((pending + "\n").encode("utf-8"))
                        process.stdin.flush()
                    except Exception:
                        pass
                self.pending_stdin.clear()

            def decode_output(raw_bytes: bytes) -> str:
                """
                Декодирование вывода процесса.

                Порядок важен: UTF-8 проверяем строго первым. Раньше после него
                пробовался cp1251, который определён почти для любых байтов и
                молча превращал корректный русский текст UTF-8 в абракадабру.
                """
                try:
                    return raw_bytes.decode("utf-8")
                except UnicodeDecodeError:
                    pass
                for enc in (locale.getpreferredencoding(False), "cp866", "cp1251"):
                    try:
                        return raw_bytes.decode(enc)
                    except (UnicodeDecodeError, LookupError):
                        continue
                return raw_bytes.decode("utf-8", errors="replace")

            # Потоки чтения stdout и stderr
            def read_stdout():
                for line_bytes in iter(process.stdout.readline, b''):
                    if line_bytes:
                        self.output_received.emit(decode_output(line_bytes), False)
                process.stdout.close()

            def read_stderr():
                for line_bytes in iter(process.stderr.readline, b''):
                    if line_bytes:
                        raw_stderr.extend(line_bytes)
                        self.output_received.emit(decode_output(line_bytes), True)
                process.stderr.close()

            t_out = threading.Thread(target=read_stdout, daemon=True)
            t_err = threading.Thread(target=read_stderr, daemon=True)
            t_out.start()
            t_err.start()

            t_out.join()
            t_err.join()

            try:
                if process.stdin and not process.stdin.closed:
                    process.stdin.close()
            except Exception:
                pass

            return_code = process.wait()

        except Exception as e:
            self.output_received.emit(f"\nОшибка запуска процесса: {e}\n", True)
            return_code = -1
        finally:
            # Подчищаем процесс, если его остановили или он остался жив
            if process is not None:
                try:
                    if process.poll() is None:
                        process.terminate()
                except Exception:
                    pass
            # Ссылку на процесс не оставляем: иначе send_stdin писал бы
            # в уже завершённый процесс, а объект с пайпами не освобождался.
            self.process = None
            self.is_running = False
            self._stop_requested = False
            duration = time.time() - self.start_time

            # Анализируем traceback на предмет номера строки ошибки
            if raw_stderr:
                self._parse_traceback(decode_output(bytes(raw_stderr)))

            # Удаляем временный файл
            if self.temp_file and os.path.exists(self.temp_file):
                try:
                    os.remove(self.temp_file)
                except OSError:
                    pass

            self.execution_finished.emit(return_code, duration)

    def _parse_traceback(self, stderr_text: str):
        """Поиск строки ошибки в стандартном Python traceback."""
        matches = []
        if self.temp_file:
            pat = rf'File "{re.escape(self.temp_file)}", line (\d+)'
            matches = list(re.finditer(pat, stderr_text))

        if not matches:
            matches = list(re.finditer(r'File ".*?", line (\d+)', stderr_text))

        if matches:
            last_match = matches[-1]
            try:
                line_num = int(last_match.group(1))
                err_lines = [l.strip() for l in stderr_text.strip().splitlines() if l.strip()]
                msg = err_lines[-1] if err_lines else "Ошибка выполнения"
                self.error_detected.emit(line_num, msg)
            except (ValueError, IndexError):
                pass

    def stop(self):
        """Принудительная остановка запущенного процесса."""
        # Флаг нужен и когда процесс ещё не создан: воркер проверяет его сразу
        # после Popen, иначе «Стоп», нажатый в первые миллисекунды, терялся.
        self._stop_requested = True
        process = self.process
        if process is not None and self.is_running:
            try:
                if process.stdin and not process.stdin.closed:
                    process.stdin.close()
                process.terminate()
                self.output_received.emit("\n[Процесс принудительно остановлен пользователем]\n", True)
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass
        self.is_running = False

    def send_stdin(self, text: str):
        """Отправка строки в стандартный ввод работающего процесса (для input())."""
        with self.stdin_lock:
            if self.process and self.is_running and self.process.stdin and not self.process.stdin.closed:
                try:
                    payload = (text + "\n").encode("utf-8")
                    self.process.stdin.write(payload)
                    self.process.stdin.flush()
                except Exception as e:
                    self.output_received.emit(f"\n[Ошибка отправки в stdin: {e}]\n", True)
            elif self.is_running:
                # Ограничиваем очередь: иначе напарник мог забить память
                # произвольным количеством строк ввода.
                if len(self.pending_stdin) < 100:
                    self.pending_stdin.append(text)

