"""
Продвинутый редактор кода для DuoPy:
- Номера строк (LineNumberArea)
- Подсветка синтаксиса Python (PythonHighlighter)
- Встроенный Linter синтаксических ошибок в реальном времени (AST Linter)
- Отображение курсора и выделения напарника в реальном времени
- Подсветка строк с ошибками выполнения (Error Highlighter)
- Авто-отступы и умный Tab (4 пробела)
- Комментирование и раскомментирование кода по Ctrl + /
- Автодополнение кода (IntelliSense / QCompleter) по Ctrl+Space и при наборе
- Подсветка и навигация поиска и замены (Find and Replace)
"""

import re
import time
from PyQt6.QtWidgets import QPlainTextEdit, QWidget, QTextEdit, QCompleter, QToolTip
from PyQt6.QtGui import (
    QColor, QPainter, QTextFormat, QTextCursor, QTextCharFormat, QFont,
    QKeyEvent, QPaintEvent, QResizeEvent
)
from PyQt6.QtCore import Qt, QRect, QSize, pyqtSignal, QStringListModel, QTimer, QEvent
from duopy.highlighter import PythonHighlighter
from duopy.linter import lint_code, LinterIssue


PYTHON_KEYWORDS = [
    "False", "None", "True", "and", "as", "assert", "async", "await",
    "break", "class", "continue", "def", "del", "elif", "else", "except",
    "finally", "for", "from", "global", "if", "import", "in", "is",
    "lambda", "nonlocal", "not", "or", "pass", "raise", "return", "try",
    "while", "with", "yield"
]

PYTHON_BUILTINS = [
    "abs", "aiter", "all", "anext", "any", "ascii", "bin", "bool",
    "breakpoint", "bytearray", "bytes", "callable", "chr", "classmethod",
    "compile", "complex", "delattr", "dict", "dir", "divmod", "enumerate",
    "eval", "exec", "filter", "float", "format", "frozenset", "getattr",
    "globals", "hasattr", "hash", "help", "hex", "id", "input", "int",
    "isinstance", "issubclass", "iter", "len", "list", "locals", "map",
    "max", "memoryview", "min", "next", "object", "oct", "open", "ord",
    "pow", "print", "property", "range", "repr", "reversed", "round",
    "set", "setattr", "slice", "sorted", "staticmethod", "str", "sum",
    "super", "tuple", "type", "vars", "zip", "__init__", "__name__",
    "__main__", "__str__", "__repr__"
]

PYTHON_MODULES = [
    "os", "sys", "math", "random", "time", "datetime", "json", "re",
    "shutil", "subprocess", "threading", "multiprocessing", "collections",
    "itertools", "functools", "pathlib", "typing", "copy", "socket", "urllib"
]


class LineNumberArea(QWidget):
    """Виджет отрисовки номеров строк слева от редактора."""

    def __init__(self, editor: "CodeEditor"):
        super().__init__(editor)
        self.code_editor = editor

    def sizeHint(self) -> QSize:
        return QSize(self.code_editor.line_number_area_width(), 0)

    def paintEvent(self, event: QPaintEvent):
        self.code_editor.line_number_area_paint_event(event)


class CodeEditor(QPlainTextEdit):
    """Полнофункциональный редактор кода с совместным режимом и Linter'ом."""

    # Сигналы для сети и UI
    code_changed_by_user = pyqtSignal(str)
    cursor_position_changed = pyqtSignal(int, int, int, int, int)  # line, col, pos, sel_start, sel_end
    linter_issues_changed = pyqtSignal(list)                       # list[LinterIssue]
    line_locked_warning = pyqtSignal(int, str)                     # line_number, peer_name

    def __init__(self, parent=None):
        super().__init__(parent)

        # Флаг для подавления отправки сетевых событий при получении апдейта от напарника
        self.is_applying_remote_update = False

        # Настройка шрифта (моноширинный)
        font = QFont("Consolas", 12)
        font.setStyleHint(QFont.StyleHint.Monospace)
        self.setFont(font)

        # Отключаем произвольный перенос строк, чтобы код не ломал структуру
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setCursorWidth(2)

        # Настройка табуляции: 4 пробела
        self.setTabStopDistance(self.fontMetrics().horizontalAdvance(' ') * 4)

        # Подсветчик синтаксиса
        self.highlighter = PythonHighlighter(self.document())

        # Область номеров строк
        self.line_number_area = LineNumberArea(self)

        # Сигналы изменений редактора
        self.blockCountChanged.connect(self.update_line_number_area_width)
        self.updateRequest.connect(self.update_line_number_area)
        self.cursorPositionChanged.connect(self.on_cursor_position_changed)
        self.selectionChanged.connect(self.on_cursor_position_changed)
        self.textChanged.connect(self.on_text_changed)

        self.update_line_number_area_width(0)

        # Удаленные курсоры напарников
        self.remote_cursors = {}

        # Умная блокировка строк (Smart Line Lock от одновременных правок)
        self.line_locks = {}  # {line_num: [{"name": str, "color": str, "time": float, "uid": str}, ...]}
        self.line_lock_enabled = True
        self.lock_timeout = 2.5  # Секунды удержания замка после последнего действия

        self.lock_clean_timer = QTimer(self)
        self.lock_clean_timer.setInterval(600)
        self.lock_clean_timer.timeout.connect(self.clean_expired_locks)
        self.lock_clean_timer.start()

        # Строка ошибки выполнения (для подсветки)
        self.error_line = -1
        self.error_message = ""

        # Состояния поиска
        self.search_matches: list[tuple[int, int]] = []
        self.current_search_idx: int = -1
        self._search_pattern: str = ""
        self._search_case: bool = False

        # Отложенное обновление от напарника (чтобы не затирать набор)
        self._deferred_remote_update: dict | None = None
        # Подавление сетевых сигналов при программной загрузке текста
        self._loading_programmatically = False
        # Совпадения поиска недействительны после правки текста
        self.search_is_stale = False

        # Встроенный Linter
        self.linter_issues: list[LinterIssue] = []
        self.lint_timer = QTimer(self)
        self.lint_timer.setSingleShot(True)
        self.lint_timer.setInterval(350)
        self.lint_timer.timeout.connect(self._run_linter)

        # Включение мышиного отслеживания для всплывающих подсказок ошибок
        self.setMouseTracking(True)

        # Таймер «пользователь печатает»: пока он активен, входящая правка в
        # области набора откладывается, а по паузе применяется. Раньше ожидание
        # было привязано к уходу напарника со строки — при обычном наборе он с
        # неё не уходит, поэтому правка не появлялась вообще.
        self._typing_timer = QTimer(self)
        self._typing_timer.setSingleShot(True)
        self._typing_timer.timeout.connect(self.flush_deferred_remote_update)

        # Настройки редактора
        self.setStyleSheet("""
            QPlainTextEdit {
                background-color: #1e1e1e;
                color: #d4d4d4;
                border: 1px solid #2d2d30;
                selection-background-color: #264f78;
                selection-color: #ffffff;
            }
        """)

        # Инициализация автодополнения (IntelliSense)
        self._completer_source_text: str | None = None
        self._init_completer()

    # =========================================================================
    # СИСТЕМА УМНОЙ БЛОКИРОВКИ СТРОК (SMART LINE LOCK)
    # =========================================================================

    def update_line_lock(self, user_id: str, name: str, color: str, line: int):
        """Регистрация / продление блокировки строки напарником."""
        if not self.line_lock_enabled or line < 1:
            return
        now = time.time()
        # Снимаем предыдущую блокировку этого пользователя с других строк
        for l in list(self.line_locks.keys()):
            remaining = [owner for owner in self.line_locks[l] if owner.get("uid") != user_id]
            if remaining:
                self.line_locks[l] = remaining
            else:
                del self.line_locks[l]

        # На одной строке могут работать несколько напарников: храним список
        # владельцев, иначе замок второго молча затирал имя и цвет первого.
        self.line_locks.setdefault(line, []).append({
            "uid": user_id,
            "name": name,
            "color": color,
            "time": now
        })
        self.line_number_area.update()
        self.highlight_current_line()

    def clean_expired_locks(self):
        """Очистка устаревших блокировок строк по истечении таймаута неактивности."""
        if not self.line_locks:
            return
        now = time.time()
        changed = False
        for l in list(self.line_locks.keys()):
            active = [owner for owner in self.line_locks[l]
                      if now - owner.get("time", 0) <= self.lock_timeout]
            if active:
                self.line_locks[l] = active
            else:
                del self.line_locks[l]
                changed = True
        if changed:
            self.line_number_area.update()
            self.highlight_current_line()

    def get_line_lock_info(self, line: int) -> dict | None:
        """
        Возвращает информацию о блокировке строки, если замок активен.

        Метод только читает состояние: он вызывается из обработчика отрисовки
        номеров строк, а мутация словаря прямо в paintEvent приводила к тому,
        что истёкший замок исчезал из гуттера, но оставался в подсветке редактора.
        """
        owners = self._active_lock_owners(line)
        return owners[-1] if owners else None

    def _active_lock_owners(self, line: int) -> list[dict]:
        """Список активных владельцев строки (без изменения состояния)."""
        if not self.line_lock_enabled or line not in self.line_locks:
            return []
        now = time.time()
        return [owner for owner in self.line_locks[line]
                if now - owner.get("time", 0) <= self.lock_timeout]

    # =========================================================================
    # ВСТРОЕННЫЙ LINTER (СИНТАКСИС В РЕАЛЬНОМ ВРЕМЕНИ)
    # =========================================================================

    def _run_linter(self):
        """Фоновый запуск AST-линтера кода."""
        code = self.toPlainText()
        self.linter_issues = lint_code(code)
        self.linter_issues_changed.emit(self.linter_issues)
        self.highlight_current_line()
        self.line_number_area.update()

    def jump_to_line(self, line_num: int, take_focus: bool = True):
        """Быстрый переход к заданной строке кода."""
        if line_num > 0:
            block = self.document().findBlockByLineNumber(line_num - 1)
            if block.isValid():
                tc = QTextCursor(block)
                self.setTextCursor(tc)
                self.centerCursor()
                if take_focus:
                    self.setFocus()

    def event(self, event: QEvent) -> bool:
        """Показ всплывающей подсказки (Tooltip) при наведении на строку с ошибкой синтаксиса."""
        if event.type() == QEvent.Type.ToolTip:
            cursor = self.cursorForPosition(event.pos())
            line = cursor.blockNumber() + 1
            for issue in self.linter_issues:
                if issue.line == line:
                    icon = "❌" if issue.severity == "error" else "⚠️"
                    # У QHelpEvent нет метода globalPosition(); нужен globalPos(),
                    # иначе ветка падала с AttributeError при наведении мыши.
                    QToolTip.showText(
                        event.globalPos(),
                        f"{icon} Строка {line}: {issue.message}",
                        self
                    )
                    return True
        return super().event(event)

    # =========================================================================
    # АВТОДОПОЛНЕНИЕ КОДА (INTELLISENSE / QCOMPLETER)
    # =========================================================================

    def _init_completer(self):
        """Настройка QCompleter для автодополнения кода."""
        self.completer = QCompleter(self)
        self.completer.setWidget(self)
        self.completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.completer.setWrapAround(False)
        self.completer.activated.connect(self.insert_completion)

        popup = self.completer.popup()
        popup.setStyleSheet("""
            QListView {
                background-color: #252526;
                color: #d4d4d4;
                border: 1px solid #3c3c3c;
                border-radius: 4px;
                selection-background-color: #04395e;
                selection-color: #ffffff;
                font-family: Consolas, monospace;
                font-size: 12px;
                padding: 2px;
            }
            QListView::item {
                padding: 4px 6px;
                border-radius: 2px;
            }
            QListView::item:hover {
                background-color: #2a2d2e;
            }
        """)

    def insert_completion(self, completion: str):
        """Вставка выбранного варианта дополнения вместо набранного префикса."""
        if self.completer.widget() != self:
            return
        tc = self.textCursor()
        if tc.hasSelection():
            tc.insertText(completion)
            self.setTextCursor(tc)
            return

        # Заменяем ровно набранный префикс перед кареткой. Явное выделение
        # диапазона надёжнее серии deletePreviousChar(): та удаляла не тот
        # символ, и дополнение оставляло хвост старого слова ("printer_xter").
        typed = self._typed_prefix()
        if typed:
            end = tc.position()
            start = end - len(typed)
            tc.beginEditBlock()
            tc.setPosition(start)
            tc.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
            tc.insertText(completion)   # заменяет выделение целиком
            tc.endEditBlock()
        else:
            tc.insertText(completion)
        self.setTextCursor(tc)

    def _typed_prefix(self) -> str:
        """
        Набранная часть слова непосредственно перед кареткой.

        WordUnderCursor отдаёт слово целиком (включая часть после каретки), а
        QCompleter сравнивает префикс с началом слова, поэтому опираться на него
        нельзя: при каретке в середине слова и при каретке в начале слова
        вставка дополнения сдвигала и портила соседний текст.
        """
        cursor = self.textCursor()
        before = cursor.block().text()[:cursor.positionInBlock()]
        match = re.search(r'[A-Za-z0-9_]+$', before)
        return match.group(0) if match else ""

    def text_under_cursor(self) -> str:
        """Слово под курсором (для проверок и подсказок)."""
        tc = self.textCursor()
        tc.select(QTextCursor.SelectionType.WordUnderCursor)
        return tc.selectedText()

    def update_completer_words(self, force: bool = False):
        """Сбор динамического словаря: ключевые слова + builtins + слова документа.

        Модель пересобирается только когда содержимое документа действительно
        изменилось: иначе на каждый набранный символ выполнялся полный разбор
        файла и пересоздание QStringListModel.
        """
        doc_text = self.toPlainText()
        if not force and doc_text == self._completer_source_text:
            return
        self._completer_source_text = doc_text

        words = set(PYTHON_KEYWORDS + PYTHON_BUILTINS + PYTHON_MODULES)
        words.update(re.findall(r'\b[A-Za-z_][A-Za-z0-9_]{2,}\b', doc_text))
        self.completer.setModel(QStringListModel(sorted(words), self.completer))

    def trigger_autocomplete(self, force: bool = False, prefix: str | None = None):
        """Запуск всплывающего окна подсказок автодополнения."""
        if prefix is None:
            prefix = self._typed_prefix()
        popup = self.completer.popup()

        if not force and len(prefix) < 2:
            if popup.isVisible():
                popup.hide()
            return

        self.update_completer_words()
        self.completer.setCompletionPrefix(prefix)

        if self.completer.completionCount() == 0:
            popup.hide()
            return

        popup.setCurrentIndex(self.completer.completionModel().index(0, 0))

        cr = self.cursorRect()
        cr.setWidth(popup.sizeHintForColumn(0)
                    + popup.verticalScrollBar().sizeHint().width() + 25)
        self.completer.complete(cr)

    # =========================================================================
    # ПОИСК И ЗАМЕНА В КОДЕ (FIND AND REPLACE)
    # =========================================================================

    def highlight_search_matches(self, pattern: str, case_sensitive: bool = False) -> tuple[int, int]:
        """Поиск всех совпадений паттерна и подсветка их в коде."""
        self.search_matches.clear()
        self.current_search_idx = -1
        self._search_pattern = pattern
        self._search_case = case_sensitive
        # Смещения только что посчитаны — подсветку снова можно рисовать
        self.search_is_stale = False

        if not pattern:
            self.highlight_current_line()
            return (0, 0)

        text = self.toPlainText()
        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            for m in re.finditer(re.escape(pattern), text, flags):
                self.search_matches.append((m.start(), m.end()))
        except Exception:
            return (0, 0)

        total = len(self.search_matches)
        if total > 0:
            cur_pos = self.textCursor().position()
            best_idx = 0
            for i, (st, en) in enumerate(self.search_matches):
                if st >= cur_pos:
                    best_idx = i
                    break
            self.current_search_idx = best_idx
            self._scroll_to_match(self.current_search_idx)

        self.highlight_current_line()
        return (self.current_search_idx + 1 if total > 0 else 0, total)

    def _ensure_matches_for(self, pattern: str, case_sensitive: bool) -> bool:
        """
        Пересчёт совпадений, если паттерн, режим регистра или позиция курсора
        изменились. Без этого смена запроса в панели поиска игнорировалась:
        функция просто сдвигала индекс в списке совпадений старого паттерна.
        """
        if (pattern != self._search_pattern
                or case_sensitive != self._search_case
                or not self.search_matches
                or self.search_is_stale):
            self.highlight_search_matches(pattern, case_sensitive)
            return True
        return False

    def find_next_match(self, pattern: str, case_sensitive: bool = False) -> tuple[int, int]:
        """Переход к следующему совпадению поиска."""
        if not pattern:
            return self.highlight_search_matches(pattern, case_sensitive)
        if self._ensure_matches_for(pattern, case_sensitive):
            return (self.current_search_idx + 1 if self.search_matches else 0,
                    len(self.search_matches))
        # Если каретка не стоит на текущем совпадении (её передвинули или это
        # первый вызов после поиска), начинаем с ближайшего совпадения справа,
        # а не со следующего за устаревшим индексом.
        if not self._cursor_on_current_match():
            for i, (st, en) in enumerate(self.search_matches):
                if st >= self.textCursor().position():
                    self.current_search_idx = i - 1
                    break

        self.current_search_idx = (self.current_search_idx + 1) % len(self.search_matches)
        self._scroll_to_match(self.current_search_idx)
        self.highlight_current_line()
        return (self.current_search_idx + 1, len(self.search_matches))

    def find_prev_match(self, pattern: str, case_sensitive: bool = False) -> tuple[int, int]:
        """Переход к предыдущему совпадению поиска."""
        if not pattern:
            return self.highlight_search_matches(pattern, case_sensitive)
        if self._ensure_matches_for(pattern, case_sensitive):
            return (self.current_search_idx + 1 if self.search_matches else 0,
                    len(self.search_matches))

        if not self.search_matches:
            return (0, 0)

        if not self._cursor_on_current_match():
            for i in range(len(self.search_matches) - 1, -1, -1):
                if self.search_matches[i][1] <= self.textCursor().position():
                    self.current_search_idx = i + 1
                    break

        self.current_search_idx = (self.current_search_idx - 1) % len(self.search_matches)
        self._scroll_to_match(self.current_search_idx)
        self.highlight_current_line()
        return (self.current_search_idx + 1, len(self.search_matches))

    def _cursor_on_current_match(self) -> bool:
        """Стоит ли каретка (возможно, с выделением) на текущем совпадении."""
        if self.current_search_idx < 0 or self.current_search_idx >= len(self.search_matches):
            return False
        st, en = self.search_matches[self.current_search_idx]
        cursor = self.textCursor()
        # _scroll_to_match выделяет совпадение, поэтому проверяем и выделение,
        # и простое положение каретки на границе совпадения
        return (cursor.selectionStart(), cursor.selectionEnd()) == (st, en) or \
            cursor.position() == en

    def _scroll_to_match(self, index: int):
        """Установка курсора и прокрутка к выбранному совпадению."""
        if 0 <= index < len(self.search_matches):
            st, en = self.search_matches[index]
            tc = self.textCursor()
            tc.setPosition(st)
            tc.setPosition(en, QTextCursor.MoveMode.KeepAnchor)
            self.setTextCursor(tc)
            self.centerCursor()

    def replace_current_match(self, pattern: str, replacement: str, case_sensitive: bool = False) -> tuple[int, int]:
        """Замена текущего совпадения и переход к следующему."""
        if not pattern:
            return (0, 0)
        # Пересчитываем совпадения, если текст правился после последнего поиска:
        # иначе замена шла по устаревшим смещениям и сносила соседние символы
        # (например, вставленный в начало текст).
        if self.search_is_stale or not self.search_matches or self.current_search_idx < 0:
            self.highlight_search_matches(pattern, case_sensitive)

        if not self.search_matches or self.current_search_idx < 0:
            return (0, 0)

        st, en = self.search_matches[self.current_search_idx]
        tc = self.textCursor()
        tc.setPosition(st)
        tc.setPosition(en, QTextCursor.MoveMode.KeepAnchor)
        tc.insertText(replacement)

        return self.highlight_search_matches(pattern, case_sensitive)

    def replace_all_matches(self, pattern: str, replacement: str, case_sensitive: bool = False) -> int:
        """Замена всех найденных совпадений за одну транзакцию с сохранением Undo."""
        if not pattern:
            return 0

        text = self.toPlainText()
        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            # replacement передаём через lambda: иначе '\1', '\g<0>', '\n' из поля
            # «Заменить на» трактуются re.subn как backreference или escape и портят код.
            new_text, count = re.subn(re.escape(pattern), lambda _m: replacement, text, flags=flags)
        except Exception:
            return 0

        if count > 0 and new_text != text:
            # Позицию каретки сохраняем: курсорная замена переводит её в конец
            # документа, и пользователь терял место после «Заменить все».
            saved_pos = self.textCursor().position()
            # Курсорная замена вместо setPlainText: тот очищает стек Undo,
            # из-за чего «Заменить все» было необратимым.
            self._replace_whole_document(new_text)
            # Новый курсор создаём ПОСЛЕ замены: курсор, взятый до неё, после
            # изменения документа указывает в конец и позицию уже не принимает.
            new_cursor = QTextCursor(self.document())
            new_cursor.setPosition(
                max(0, min(saved_pos, self.document().characterCount() - 1))
            )
            self.setTextCursor(new_cursor)
            self.highlight_search_matches(pattern, case_sensitive)
        return count

    def clear_search_highlight(self):
        """Очистка подсветки результатов поиска."""
        self.search_matches.clear()
        self.current_search_idx = -1
        self._search_pattern = ""
        self.search_is_stale = False
        self.highlight_current_line()

    # =========================================================================
    # РАЗМЕТКА И ОТРИСОВКА НОМЕРОВ СТРОК
    # =========================================================================

    def line_number_area_width(self) -> int:
        """Расчет ширины области номеров строк."""
        digits = 1
        max_num = max(1, self.blockCount())
        while max_num >= 10:
            max_num //= 10
            digits += 1
        space = 24 + self.fontMetrics().horizontalAdvance('9') * digits
        return space

    def update_line_number_area_width(self, _):
        self.setViewportMargins(self.line_number_area_width(), 0, 0, 0)

    def update_line_number_area(self, rect: QRect, dy: int):
        if dy:
            self.line_number_area.scroll(0, dy)
        else:
            self.line_number_area.update(0, rect.y(), self.line_number_area.width(), rect.height())

        if rect.contains(self.viewport().rect()):
            self.update_line_number_area_width(0)

    def resizeEvent(self, event: QResizeEvent):
        super().resizeEvent(event)
        cr = self.contentsRect()
        self.line_number_area.setGeometry(QRect(cr.left(), cr.top(), self.line_number_area_width(), cr.height()))

    def line_number_area_paint_event(self, event: QPaintEvent):
        """Отрисовка номеров строк, маркеров Linter и ошибок выполнения."""
        painter = QPainter(self.line_number_area)
        painter.fillRect(event.rect(), QColor("#1e1e1e"))

        block = self.firstVisibleBlock()
        block_number = block.blockNumber()
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + round(self.blockBoundingRect(block).height())

        current_line = self.textCursor().blockNumber()

        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                line_1_based = block_number + 1
                number_str = str(line_1_based)

                is_current = (block_number == current_line)
                is_runtime_error = (line_1_based == self.error_line)

                # Проверяем наличие синтаксических ошибок Linter на этой строке
                lint_err = next((i for i in self.linter_issues if i.line == line_1_based and i.severity == "error"), None)
                lint_warn = next((i for i in self.linter_issues if i.line == line_1_based and i.severity == "warning"), None)

                # Проверяем блокировку строки напарником (Line Lock)
                lock_info = self.get_line_lock_info(line_1_based)

                # 1. Маркер ошибки / предупреждения / блокировки
                if is_runtime_error or lint_err:
                    painter.setBrush(QColor("#f48771"))
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.drawEllipse(4, top + 5, 8, 8)
                    painter.setPen(QColor("#f48771"))
                elif lint_warn:
                    painter.setBrush(QColor("#e5c07b"))
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.drawEllipse(4, top + 5, 8, 8)
                    painter.setPen(QColor("#e5c07b"))
                elif lock_info:
                    # Индикатор блокировки строки напарником
                    l_color = QColor(lock_info.get("color", "#ff9800"))
                    painter.setBrush(l_color)
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.drawRoundedRect(3, top + 4, 8, 10, 2, 2)
                    painter.setPen(l_color)
                elif is_current:
                    painter.setPen(QColor("#c6c6c6"))
                else:
                    painter.setPen(QColor("#858585"))

                font = painter.font()
                font.setBold(is_current or is_runtime_error or bool(lint_err) or bool(lock_info))
                painter.setFont(font)

                painter.drawText(
                    0, top,
                    self.line_number_area.width() - 8,
                    self.fontMetrics().height(),
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                    number_str
                )

            block = block.next()
            top = bottom
            bottom = top + round(self.blockBoundingRect(block).height())
            block_number += 1

    def load_text_programmatically(self, text: str):
        """
        Загрузка текста без рассылки его напарнику и без сброса Undo.

        Программные setPlainText (переключение вкладки, открытие файла) раньше
        выглядели как правка пользователя: содержимое файла уходило в сеть и
        могло затереть локальные правки напарника. Используется курсорная замена,
        поэтому история Undo сохраняется.
        """
        if text == self.toPlainText():
            # Текст совпал (например, две вкладки с одинаковым содержимым),
            # но каретку всё равно ставим в начало и обновляем подсветку.
            cursor = self.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.Start)
            self.setTextCursor(cursor)
            self.highlight_current_line()
            return
        self._loading_programmatically = True
        try:
            self._replace_whole_document(text)
        finally:
            self._loading_programmatically = False
        # Курсор в начало, подсветка и линтер — как после обычного открытия файла
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        self.setTextCursor(cursor)
        self.search_is_stale = True
        self.highlight_current_line()
        self._run_linter()
        self.viewport().update()

    def _replace_whole_document(self, text: str):
        """Замена всего текста курсором: сохраняет Undo, в отличие от setPlainText."""
        full_cursor = QTextCursor(self.document())
        full_cursor.beginEditBlock()
        full_cursor.select(QTextCursor.SelectionType.Document)
        full_cursor.insertText(text)
        full_cursor.endEditBlock()

    def on_cursor_position_changed(self):
        """Обработка перемещения локального курсора."""
        self.highlight_current_line()

        cursor = self.textCursor()
        line = cursor.blockNumber() + 1
        col = cursor.positionInBlock()
        pos = cursor.position()
        sel_start = cursor.selectionStart()
        sel_end = cursor.selectionEnd()

        if not self.is_applying_remote_update and not self._loading_programmatically:
            self.cursor_position_changed.emit(line, col, pos, sel_start, sel_end)

    def on_text_changed(self):
        """Обработка изменения текста пользователем."""
        self.lint_timer.start(350)
        # Позиции найденных совпадений становятся недействительными при любой
        # правке: иначе подсветка рисовалась по старым смещениям.
        if self.search_matches:
            self.search_is_stale = True
        if not self.is_applying_remote_update and not self._loading_programmatically:
            # Набор пользователя: отложенная правка напарника подождёт паузы
            self._typing_timer.start(900)
            self.code_changed_by_user.emit(self.toPlainText())

    def highlight_current_line(self):
        """Подсветка текущей строки, результатов поиска, Linter'а, ошибок и курсоров напарника."""
        extra_selections = []

        if not self.isReadOnly():
            # 1. Подсветка текущей строки локального пользователя
            selection = QTextEdit.ExtraSelection()
            line_color = QColor("#282828")
            selection.format.setBackground(line_color)
            selection.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
            selection.cursor = self.textCursor()
            selection.cursor.clearSelection()
            extra_selections.append(selection)

        # 2. Подсветка совпадений поиска (только если позиции ещё актуальны)
        if not self.search_is_stale:
            for i, (st, en) in enumerate(self.search_matches):
                if en > self.document().characterCount():
                    break
                match_sel = QTextEdit.ExtraSelection()
                tc = QTextCursor(self.document())
                tc.setPosition(st)
                tc.setPosition(en, QTextCursor.MoveMode.KeepAnchor)
                match_sel.cursor = tc
                if i == self.current_search_idx:
                    match_sel.format.setBackground(QColor(255, 140, 0, 200))
                    match_sel.format.setForeground(QColor("#000000"))
                else:
                    match_sel.format.setBackground(QColor(230, 200, 0, 90))
                extra_selections.append(match_sel)

        # 3. Волнистое подчеркивание ошибок и предупреждений Linter'а
        for issue in self.linter_issues:
            block = self.document().findBlockByLineNumber(issue.line - 1)
            if block.isValid():
                lint_sel = QTextEdit.ExtraSelection()
                tc = QTextCursor(block)
                if issue.col > 0 and issue.col < block.length():
                    tc.movePosition(QTextCursor.MoveOperation.Right, QTextCursor.MoveMode.MoveAnchor, issue.col)
                tc.movePosition(QTextCursor.MoveOperation.EndOfBlock, QTextCursor.MoveMode.KeepAnchor)
                lint_sel.cursor = tc

                fmt = QTextCharFormat()
                fmt.setUnderlineStyle(QTextCharFormat.UnderlineStyle.WaveUnderline)
                if issue.severity == "error":
                    fmt.setUnderlineColor(QColor("#f48771"))
                else:
                    fmt.setUnderlineColor(QColor("#e5c07b"))
                lint_sel.format = fmt
                extra_selections.append(lint_sel)

        # 4. Подсветка строки с ошибкой выполнения скрипта
        if 0 < self.error_line <= self.document().blockCount():
            err_block = self.document().findBlockByLineNumber(self.error_line - 1)
            if err_block.isValid():
                err_selection = QTextEdit.ExtraSelection()
                err_selection.format.setBackground(QColor("#5a1a1a"))
                err_selection.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
                err_selection.cursor = QTextCursor(err_block)
                extra_selections.append(err_selection)

        # 5. Подсветка выделений напарников (мягкий полупрозрачный фон, не закрывающий текст)
        total_chars = self.document().characterCount()
        for user_id, cinfo in self.remote_cursors.items():
            color = QColor(cinfo.get("color", "#ffaa00"))
            sel_start = cinfo.get("sel_start", 0)
            sel_end = cinfo.get("sel_end", 0)

            # Выделение текста напарником
            if sel_start != sel_end and 0 <= sel_start < total_chars and 0 <= sel_end <= total_chars:
                r_sel_cursor = QTextCursor(self.document())
                r_sel_cursor.setPosition(min(sel_start, total_chars - 1))
                r_sel_cursor.setPosition(min(sel_end, total_chars - 1), QTextCursor.MoveMode.KeepAnchor)

                r_selection = QTextEdit.ExtraSelection()
                r_selection.cursor = r_sel_cursor
                sel_color = QColor(color)
                sel_color.setAlpha(55)
                r_selection.format.setBackground(sel_color)
                extra_selections.append(r_selection)

        # 6. Подсветка заблокированных напарником строк (Smart Line Lock)
        if self.line_lock_enabled and self.line_locks:
            for l_num in list(self.line_locks.keys()):
                owners = self._active_lock_owners(l_num)
                if not owners:
                    continue
                block = self.document().findBlockByLineNumber(l_num - 1)
                if block.isValid():
                    lock_sel = QTextEdit.ExtraSelection()
                    # Цвет берём у последнего активного владельца строки
                    lock_color = QColor(owners[-1].get("color", "#ff9800"))
                    lock_color.setAlpha(28)
                    lock_sel.format.setBackground(lock_color)
                    lock_sel.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
                    lock_sel.cursor = QTextCursor(block)
                    extra_selections.append(lock_sel)

        self.setExtraSelections(extra_selections)

    def paintEvent(self, event: QPaintEvent):
        """Отрисовка текста редактора и кареток напарников."""
        super().paintEvent(event)
        self._paint_remote_cursors(event)

    def _paint_remote_cursors(self, event: QPaintEvent):
        """Отрисовка кареток (вертикальных полос 2px) и бейджей имен напарников."""
        if not self.remote_cursors:
            return

        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        for user_id, cinfo in self.remote_cursors.items():
            name = cinfo.get("name", "Напарник")
            color = QColor(cinfo.get("color", "#ff9800"))
            r_line = cinfo.get("line", 1) - 1
            r_col = cinfo.get("col", 0)

            block = self.document().findBlockByLineNumber(r_line)
            if not block.isValid() or not block.isVisible():
                continue

            cursor_pos = block.position() + min(r_col, max(0, block.length() - 1))
            c = QTextCursor(self.document())
            c.setPosition(cursor_pos)
            cursor_rect = self.cursorRect(c)

            if not cursor_rect.intersects(event.rect()):
                continue

            # 1. Четкая вертикальная каретка напарника (2px)
            painter.fillRect(cursor_rect.x(), cursor_rect.y(), 2, cursor_rect.height(), color)

            # 2. Стильный бейдж с именем напарника
            if name:
                badge_font = QFont("Segoe UI", 9)
                badge_font.setBold(True)
                painter.setFont(badge_font)
                fm = painter.fontMetrics()
                badge_w = fm.horizontalAdvance(name) + 10
                badge_h = 16
                badge_x = cursor_rect.x()
                badge_y = cursor_rect.y() - badge_h

                if badge_y < 0:
                    badge_y = cursor_rect.y() + cursor_rect.height()

                painter.setBrush(color)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawRoundedRect(badge_x, badge_y, badge_w, badge_h, 3, 3)

                text_color = QColor("#000000") if color.lightness() > 140 else QColor("#ffffff")
                painter.setPen(text_color)
                painter.drawText(badge_x + 5, badge_y + badge_h - 4, name)

    def _local_edit_reservation(self) -> tuple[int, int]:
        """
        Диапазон, который локальный пользователь считает "своим": выделение,
        либо символ перед каретной и сам символ под ней. Обновление от
        напарника, попадающее в этот диапазон, откладывается, чтобы не съесть
        только что набранный текст (обратная сторона Smart Line Lock).
        """
        cursor = self.textCursor()
        if cursor.hasSelection():
            return cursor.selectionStart(), cursor.selectionEnd()
        pos = cursor.position()
        return max(0, pos - 1), min(self.document().characterCount(), pos + 2)

    def _ranges_overlap(self, a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
        return a_start < b_end and b_start < a_end

    def apply_remote_code(self, new_text: str, defer_on_conflict: bool = False) -> bool:
        """
        Применение кода от напарника дифференциально, без мерцания и с сохранением позиции курсора.

        При defer_on_conflict=True обновление, затрагивающее область, которую
        локальный пользователь прямо сейчас набирает, не применяется молча, а
        откладывается: применять его будет remove_remote_cursor, когда напарник
        уйдёт с этой строки. Иначе набранные символы исчезали бы под курсором.
        """
        current_text = self.toPlainText()
        if current_text == new_text:
            return True

        cursor = self.textCursor()
        cur_pos = cursor.position()
        cur_anchor = cursor.anchor()
        had_selection = cursor.hasSelection()

        # Ищем общий префикс
        prefix_len = 0
        min_len = min(len(current_text), len(new_text))
        while prefix_len < min_len and current_text[prefix_len] == new_text[prefix_len]:
            prefix_len += 1

        # Ищем общий суффикс, не перекрывающий префикс: suffix_len ограничен
        # остатком более короткой строки, иначе суффикс мог "съесть" часть
        # префикса и границы diff уезжали (текст портился).
        suffix_len = 0
        suffix_limit = min_len - prefix_len
        while (suffix_len < suffix_limit and
               current_text[len(current_text) - 1 - suffix_len] == new_text[len(new_text) - 1 - suffix_len]):
            suffix_len += 1

        old_start = prefix_len
        old_end = len(current_text) - suffix_len
        new_replacement = new_text[prefix_len : len(new_text) - suffix_len]

        if defer_on_conflict and old_start < old_end and self._typing_timer.isActive():
            # Откладываем ТОЛЬКО пока пользователь активно печатает: иначе
            # правка напарника не появлялась бы вообще, пока он находится на
            # той же строке, что и вы (это и была причина «не вижу, как он пишет»).
            res_start, res_end = self._local_edit_reservation()
            if self._ranges_overlap(old_start, old_end, res_start, res_end):
                self._deferred_remote_update = {
                    "text": new_text,
                    "old_start": old_start,
                    "old_end": old_end,
                }
                return False

        self._deferred_remote_update = None
        self.is_applying_remote_update = True
        try:
            v_scroll = self.verticalScrollBar().value()
            h_scroll = self.horizontalScrollBar().value()

            # Выполняем точечную замену только измененного фрагмента
            edit_cursor = QTextCursor(self.document())
            edit_cursor.beginEditBlock()
            edit_cursor.setPosition(old_start)
            edit_cursor.setPosition(old_end, QTextCursor.MoveMode.KeepAnchor)
            edit_cursor.insertText(new_replacement)
            edit_cursor.endEditBlock()

            # Корректируем положение локального курсора
            delta = len(new_replacement) - (old_end - old_start)

            def adjust_pos(p):
                if p <= old_start:
                    return p
                elif p >= old_end:
                    return max(0, min(len(new_text), p + delta))
                else:
                    return old_start + len(new_replacement)

            new_cur_pos = adjust_pos(cur_pos)
            new_anchor = adjust_pos(cur_anchor)

            new_cursor = self.textCursor()
            new_cursor.setPosition(new_anchor)
            if had_selection and new_anchor != new_cur_pos:
                new_cursor.setPosition(new_cur_pos, QTextCursor.MoveMode.KeepAnchor)
            else:
                new_cursor.setPosition(new_cur_pos)
            self.setTextCursor(new_cursor)

            self.verticalScrollBar().setValue(v_scroll)
            self.horizontalScrollBar().setValue(h_scroll)
        finally:
            self.is_applying_remote_update = False
            # Наша собственная применённая правка — не «набор пользователя»:
            # иначе следующие 0.9 с правки напарника снова откладывались бы.
            self._typing_timer.stop()
            self.highlight_current_line()
            # Линтер гоняем через тот же debounce-таймер, а не синхронно: напарник
            # шлёт апдейты каждые ~120 мс, и полный ast.parse блокировал GUI.
            self.lint_timer.start(350)
            self.viewport().update()
        return True

    def flush_deferred_remote_update(self):
        """
        Применение отложенного обновления, когда пользователь сделал паузу.

        Вызывается таймером набора, уходом напарника и сменой файла. Никаких
        дополнительных условий: отложенная правка обязана примениться, иначе
        стороны расходятся навсегда (именно это и происходило).
        """
        pending = self._deferred_remote_update
        if not pending:
            return False
        self._deferred_remote_update = None
        return self.apply_remote_code(pending["text"])

    def update_remote_cursor(self, user_id: str, name: str, color_hex: str, line: int, col: int,
                             pos: int = 0, sel_start: int = 0, sel_end: int = 0):
        """Обновление положения курсора и выделения напарника."""
        self.remote_cursors[user_id] = {
            "name": name,
            "color": color_hex,
            "line": line,
            "col": col,
            "pos": pos,
            "sel_start": sel_start,
            "sel_end": sel_end
        }
        if self.line_lock_enabled:
            self.update_line_lock(user_id, name, color_hex, line)
        self.highlight_current_line()
        self.viewport().update()
        # Напарник ушёл с нашей строки — можно применить отложенное обновление
        self.flush_deferred_remote_update()

    def remove_remote_cursor(self, user_id: str):
        """Удаление курсора отключившегося участника."""
        if user_id in self.remote_cursors:
            del self.remote_cursors[user_id]
        for l in list(self.line_locks.keys()):
            remaining = [owner for owner in self.line_locks[l] if owner.get("uid") != user_id]
            if remaining:
                self.line_locks[l] = remaining
            else:
                del self.line_locks[l]
        self.highlight_current_line()
        self.line_number_area.update()
        self.viewport().update()
        # Напарник ушёл: отложенное обновление больше некому удерживать
        self.flush_deferred_remote_update()

    def set_error_line(self, line_num: int, message: str = "", take_focus: bool = True):
        """Установка подсветки строки с ошибкой выполнения."""
        total_blocks = self.document().blockCount()
        if line_num < 1 or line_num > total_blocks:
            self.error_line = -1
            self.error_message = message
            self.line_number_area.update()
            self.highlight_current_line()
            return

        self.error_line = line_num
        self.error_message = message
        self.line_number_area.update()
        self.highlight_current_line()
        self.jump_to_line(line_num, take_focus=take_focus)

    def clear_error_line(self):
        """Сброс подсветки ошибки выполнения."""
        self.error_line = -1
        self.error_message = ""
        self.line_number_area.update()
        self.highlight_current_line()

    def _selection_end_block(self, end_pos: int):
        """
        Последний блок, реально входящий в выделение.

        Если выделение заканчивается ровно на позиции 0 строки (обычная протяжка
        мышью до начала следующей строки), эта строка НЕ должна обрабатываться:
        иначе она получала лишний отступ по Tab, лишнее снятие по Shift+Tab и
        лишний '# ' по Ctrl+/.
        """
        block = self.document().findBlock(end_pos)
        if block.isValid() and block.position() == end_pos and block.position() > 0:
            prev = block.previous()
            if prev.isValid():
                return prev
        return block

    def toggle_comment(self):
        """Комментирование / раскомментирование строк (Ctrl + /)."""
        cursor = self.textCursor()
        start_pos = cursor.selectionStart()
        end_pos = cursor.selectionEnd()

        cursor.beginEditBlock()

        start_block = self.document().findBlock(start_pos)
        end_block = self._selection_end_block(end_pos)

        blocks = []
        curr = start_block
        while curr.isValid():
            blocks.append(curr)
            if curr == end_block:
                break
            curr = curr.next()

        # Пустая строка не считается закомментированной: иначе выделение,
        # начинающееся с пустой строки, уходило в ветку раскомментирования
        # и расставляло '# ' на строках кода вместо комментария.
        all_commented = True
        for block in blocks:
            text = block.text().strip()
            if text and not text.startswith("#"):
                all_commented = False
                break

        if all_commented:
            for block in blocks:
                text = block.text()
                stripped = text.lstrip()
                indent = len(text) - len(stripped)
                if stripped.startswith("# "):
                    c = QTextCursor(block)
                    c.movePosition(QTextCursor.MoveOperation.Right, QTextCursor.MoveMode.MoveAnchor, indent)
                    c.movePosition(QTextCursor.MoveOperation.Right, QTextCursor.MoveMode.KeepAnchor, 2)
                    c.removeSelectedText()
                elif stripped.startswith("#"):
                    c = QTextCursor(block)
                    c.movePosition(QTextCursor.MoveOperation.Right, QTextCursor.MoveMode.MoveAnchor, indent)
                    c.movePosition(QTextCursor.MoveOperation.Right, QTextCursor.MoveMode.KeepAnchor, 1)
                    c.removeSelectedText()
        else:
            for block in blocks:
                text = block.text()
                stripped = text.lstrip()
                # Пустые строки не трогаем, чтобы не плодить строки из одного '# '
                if not stripped:
                    continue
                indent = len(text) - len(stripped)
                # insertText на курсоре с выделением заменяет выделение, поэтому
                # текст, помеченный как выделенный, не остаётся под комментарием.
                c = QTextCursor(block)
                c.setPosition(block.position() + indent)
                c.insertText("# ")

        cursor.endEditBlock()

    def keyPressEvent(self, event: QKeyEvent):
        """Обработка горячих клавиш: Completer, Ctrl+/, Tab, Shift+Tab, Enter."""
        # 0. Защита строки от одновременного редактирования (Smart Line Lock)
        if self.line_lock_enabled and not self.isReadOnly():
            is_navigation_or_copy = (
                event.key() in (
                    Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_Left, Qt.Key.Key_Right,
                    Qt.Key.Key_PageUp, Qt.Key.Key_PageDown, Qt.Key.Key_Home, Qt.Key.Key_End,
                    Qt.Key.Key_Escape, Qt.Key.Key_Shift, Qt.Key.Key_Control, Qt.Key.Key_Alt
                ) or (
                    (event.modifiers() & Qt.KeyboardModifier.ControlModifier) and
                    event.key() in (Qt.Key.Key_C, Qt.Key.Key_A, Qt.Key.Key_F, Qt.Key.Key_H)
                )
            )

            if not is_navigation_or_copy:
                cur_line = self.textCursor().blockNumber() + 1
                lock_info = self.get_line_lock_info(cur_line)
                if lock_info:
                    peer_name = lock_info.get("name", "Напарник")
                    self.line_locked_warning.emit(cur_line, peer_name)
                    c_rect = self.cursorRect()
                    QToolTip.showText(
                        self.viewport().mapToGlobal(c_rect.topRight()),
                        f"🔒 Строка {cur_line} занята: её редактирует {peer_name}",
                        self
                    )
                    event.ignore()
                    return

        # 1. Если completer открыт и нажаты клавиши подтверждения выбора
        if self.completer and self.completer.popup().isVisible():
            if event.key() in (Qt.Key.Key_Enter, Qt.Key.Key_Return, Qt.Key.Key_Tab):
                event.ignore()
                return
            if event.key() == Qt.Key.Key_Escape:
                self.completer.popup().hide()
                event.accept()
                return

        # 2. Вызов автодополнения по Ctrl+Space
        is_ctrl_space = (event.modifiers() & Qt.KeyboardModifier.ControlModifier) and event.key() == Qt.Key.Key_Space
        if is_ctrl_space:
            self.trigger_autocomplete(force=True)
            return

        cursor = self.textCursor()

        # 3. Комбинация Ctrl + / (в любой раскладке клавиатуры)
        if (event.modifiers() & Qt.KeyboardModifier.ControlModifier) and (
            event.key() in (Qt.Key.Key_Slash, Qt.Key.Key_Question, 47, 63)
            or event.text() in ('/', '?')
        ):
            self.toggle_comment()
            return

        # 4. Клавиша Tab -> 4 пробела
        if event.key() == Qt.Key.Key_Tab and not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            if cursor.hasSelection():
                start = cursor.selectionStart()
                end = cursor.selectionEnd()
                start_block = self.document().findBlock(start)
                end_block = self._selection_end_block(end)

                cursor.beginEditBlock()
                curr_block = start_block
                while curr_block.isValid():
                    c = QTextCursor(curr_block)
                    c.insertText("    ")
                    if curr_block == end_block:
                        break
                    curr_block = curr_block.next()
                cursor.endEditBlock()
            else:
                c = QTextCursor(cursor.block())
                c.setPosition(cursor.position())
                c.insertText("    ")
                self.setTextCursor(c)
            return

        # 5. Клавиша Shift + Tab -> убрать 4 пробела
        if event.key() == Qt.Key.Key_Backtab or (event.key() == Qt.Key.Key_Tab and (event.modifiers() & Qt.KeyboardModifier.ShiftModifier)):
            start_block = self.document().findBlock(cursor.selectionStart())
            end_block = self._selection_end_block(cursor.selectionEnd())

            cursor.beginEditBlock()
            curr_block = start_block
            while curr_block.isValid():
                text = curr_block.text()
                spaces_to_remove = 0
                for i in range(min(4, len(text))):
                    if text[i] == ' ':
                        spaces_to_remove += 1
                    elif text[i] == '\t' and spaces_to_remove == 0:
                        # Убираем и табуляцию, если строка отбита ею, а не пробелами
                        spaces_to_remove = 1
                        break
                    else:
                        break
                if spaces_to_remove > 0:
                    c = QTextCursor(curr_block)
                    c.movePosition(QTextCursor.MoveOperation.Right, QTextCursor.MoveMode.KeepAnchor, spaces_to_remove)
                    c.removeSelectedText()
                if curr_block == end_block:
                    break
                curr_block = curr_block.next()
            cursor.endEditBlock()
            return

        # 6. Клавиша Enter -> умный авто-отступ
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            block = cursor.block()
            line_text = block.text()
            # Учитываем только текст ДО курсора: иначе 'if x: y = 1' с кареткой
            # сразу после ':' не получал отступа, а комментарий '# note:' получал лишние 4 пробела.
            text_before_cursor = line_text[:cursor.positionInBlock()]
            leading_spaces = len(text_before_cursor) - len(text_before_cursor.lstrip(' '))
            stripped = text_before_cursor.strip()
            # Линия-комментарий не открывает блок, даже если в тексте есть ':'
            opens_block = stripped.endswith(':') and not stripped.startswith('#')
            extra_indent = 4 if opens_block else 0
            indent = ' ' * (leading_spaces + extra_indent)

            cursor.beginEditBlock()
            # Перенос строки и отступ вставляем сами, не полагаясь на базовый
            # класс: его результат зависит от фокуса окна, а мы уже посчитали
            # ровно тот отступ, который нужен.
            self.insertPlainText('\n' + indent)
            cursor.endEditBlock()
            return

        super().keyPressEvent(event)

        # 7. Автоматический запуск IntelliSense при наборе слова (2+ символа)
        if event.text() and (event.text().isalnum() or event.text() in ('_', '.')):
            self.trigger_autocomplete(force=False, prefix=self._typed_prefix())
        elif self.completer and self.completer.popup().isVisible():
            if not self._typed_prefix():
                self.completer.popup().hide()
