"""
Подсветка синтаксиса Python для редактора кода DuoPy.
"""

from PyQt6.QtGui import QSyntaxHighlighter, QTextCharFormat, QColor, QFont
from PyQt6.QtCore import QRegularExpression


class PythonHighlighter(QSyntaxHighlighter):
    """Кастомный синтаксический анализатор и подсветчик для кода Python (в стиле VS Code Dark+)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.highlighting_rules: list[tuple[QRegularExpression, QTextCharFormat, int]] = []

        # Цветовая палитра VS Code Dark+
        color_keyword = QColor("#569cd6")       # Синий (def, class, import, if, for...)
        color_builtin = QColor("#4ec9b0")       # Бирюзовый (print, len, range, str...)
        color_class_name = QColor("#4ec9b0")    # Бирюзовый (имя класса)
        color_func_name = QColor("#dcdcaa")     # Желтый (имя функции в def)
        color_func_call = QColor("#dcdcaa")     # Желтый (вызов функции)
        color_decorator = QColor("#dcdcaa")     # Желтый (@decorator)
        color_string = QColor("#ce9178")        # Оранжево-коричневый ("текст")
        color_comment = QColor("#6a9955")       # Зеленый (# комментарий)
        color_number = QColor("#b5cea8")        # Салатовый (числа)
        color_self = QColor("#9cdcfe")          # Голубой (self, cls)

        # 1. Форматы
        fmt_keyword = QTextCharFormat()
        fmt_keyword.setForeground(color_keyword)
        fmt_keyword.setFontWeight(QFont.Weight.Bold)

        fmt_builtin = QTextCharFormat()
        fmt_builtin.setForeground(color_builtin)

        fmt_class_name = QTextCharFormat()
        fmt_class_name.setForeground(color_class_name)
        fmt_class_name.setFontWeight(QFont.Weight.Bold)

        fmt_func_name = QTextCharFormat()
        fmt_func_name.setForeground(color_func_name)
        fmt_func_name.setFontWeight(QFont.Weight.Bold)

        fmt_func_call = QTextCharFormat()
        fmt_func_call.setForeground(color_func_call)

        fmt_decorator = QTextCharFormat()
        fmt_decorator.setForeground(color_decorator)

        fmt_self = QTextCharFormat()
        fmt_self.setForeground(color_self)
        fmt_self.setFontItalic(True)

        fmt_number = QTextCharFormat()
        fmt_number.setForeground(color_number)

        fmt_string = QTextCharFormat()
        fmt_string.setForeground(color_string)

        fmt_comment = QTextCharFormat()
        fmt_comment.setForeground(color_comment)
        fmt_comment.setFontItalic(True)

        # 2. Числа
        pattern_num = QRegularExpression(r"\b(0[xX][0-9a-fA-F]+|0[bB][01]+|\d+(\.\d*)?([eE][+-]?\d+)?)\b")
        self.highlighting_rules.append((pattern_num, fmt_number, 0))

        # 3. Ключевые слова Python
        keywords = [
            "and", "as", "assert", "async", "await", "break", "class", "continue",
            "def", "del", "elif", "else", "except", "finally", "for", "from",
            "global", "if", "import", "in", "is", "lambda", "nonlocal", "not",
            "or", "pass", "raise", "return", "try", "while", "with", "yield",
            "True", "False", "None"
        ]
        for word in keywords:
            pattern = QRegularExpression(rf"\b{word}\b")
            self.highlighting_rules.append((pattern, fmt_keyword, 0))

        # 4. Built-in функции и типы
        builtins = [
            "abs", "all", "any", "bin", "bool", "bytearray", "bytes", "callable",
            "chr", "classmethod", "compile", "complex", "delattr", "dict", "dir",
            "divmod", "enumerate", "eval", "exec", "filter", "float", "format",
            "frozenset", "getattr", "hasattr", "hash", "help", "hex", "id",
            "input", "int", "isinstance", "issubclass", "iter", "len", "list",
            "locals", "map", "max", "memoryview", "min", "next", "object", "oct",
            "open", "ord", "pow", "print", "property", "range", "repr", "reversed",
            "round", "set", "setattr", "slice", "sorted", "staticmethod", "str",
            "sum", "super", "tuple", "type", "vars", "zip", "Exception", "TypeError",
            "ValueError", "KeyError", "IndexError"
        ]
        for word in builtins:
            pattern = QRegularExpression(rf"\b{word}\b")
            self.highlighting_rules.append((pattern, fmt_builtin, 0))

        # 5. self / cls
        for word in ["self", "cls"]:
            pattern = QRegularExpression(rf"\b{word}\b")
            self.highlighting_rules.append((pattern, fmt_self, 0))

        # 6. Вызовы функций: foo(...)
        pattern_call = QRegularExpression(r"\b([a-zA-Z_]\w*)\s*(?=\()")
        self.highlighting_rules.append((pattern_call, fmt_func_call, 1))

        # 7. Объявления функций: def foo(...) (только имя функции yellow)
        pattern_def = QRegularExpression(r"\bdef\s+([a-zA-Z_]\w*)")
        self.highlighting_rules.append((pattern_def, fmt_func_name, 1))

        # 8. Объявления классов: class Foo(...) (только имя класса teal)
        pattern_class = QRegularExpression(r"\bclass\s+([a-zA-Z_]\w*)")
        self.highlighting_rules.append((pattern_class, fmt_class_name, 1))

        # 9. Декораторы (@decorator)
        pattern_dec = QRegularExpression(r"@[a-zA-Z_]\w*")
        self.highlighting_rules.append((pattern_dec, fmt_decorator, 0))

        # 10. Однострочные строки (с поддержкой префиксов r, f, b)
        pattern_str1 = QRegularExpression(r'[frbFRB]?"[^"\\]*(\\.[^"\\]*)*"')
        pattern_str2 = QRegularExpression(r"[frbFRB]?'[^'\\]*(\\.[^'\\]*)*'")
        self.highlighting_rules.append((pattern_str1, fmt_string, 0))
        self.highlighting_rules.append((pattern_str2, fmt_string, 0))

        # 11. Комментарии (# ...)
        pattern_comment = QRegularExpression(r"#[^\n]*")
        self.highlighting_rules.append((pattern_comment, fmt_comment, 0))

        # Многострочные строки (docstrings: \"\"\" и ''')
        self.tri_single_start = QRegularExpression(r"'''")
        self.tri_single_end = QRegularExpression(r"'''")
        self.tri_double_start = QRegularExpression(r'"""')
        self.tri_double_end = QRegularExpression(r'"""')
        self.fmt_multiline_string = fmt_string

    def highlightBlock(self, text: str):
        """Подсветка одного блока (строки) текста."""
        # 1. Применяем стандартные правила с учетом групп захвата
        for pattern, fmt, group_idx in self.highlighting_rules:
            match_iterator = pattern.globalMatch(text)
            while match_iterator.hasNext():
                match = match_iterator.next()
                start = match.capturedStart(group_idx)
                length = match.capturedLength(group_idx)
                if start >= 0 and length > 0:
                    self.setFormat(start, length, fmt)

        # 2. Обработка многострочных тройных кавычек
        self.setCurrentBlockState(0)
        self._match_multiline(text, self.tri_double_start, self.tri_double_end, 1)
        self._match_multiline(text, self.tri_single_start, self.tri_single_end, 2)

    def _match_multiline(self, text: str, start_pattern: QRegularExpression, end_pattern: QRegularExpression, state_val: int):
        start_index = 0
        if self.previousBlockState() == state_val:
            start_index = 0
        else:
            match = start_pattern.match(text)
            start_index = match.capturedStart() if match.hasMatch() else -1

        while start_index >= 0:
            match = end_pattern.match(text, start_index + 3)
            end_index = match.capturedStart() if match.hasMatch() else -1

            if end_index == -1:
                self.setCurrentBlockState(state_val)
                comment_len = len(text) - start_index
            else:
                comment_len = end_index - start_index + match.capturedLength()

            self.setFormat(start_index, comment_len, self.fmt_multiline_string)

            if end_index != -1:
                next_match = start_pattern.match(text, start_index + comment_len)
                start_index = next_match.capturedStart() if next_match.hasMatch() else -1
            else:
                break
