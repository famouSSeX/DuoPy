"""
Встроенный быстрый Linter для DuoPy на основе AST Python.
Проверяет код в реальном времени на синтаксические ошибки (SyntaxError, IndentationError,
незакрытые скобки/кавычки, пропущенные двоеточия) и неиспользуемые импорты.
"""

import ast
from dataclasses import dataclass


@dataclass
class LinterIssue:
    line: int              # 1-indexed номер строки
    col: int               # 0-indexed номер колонки
    message: str           # Понятное описание ошибки
    severity: str          # 'error' или 'warning'
    code_line: str = ""    # Текст строки


def lint_code(source_code: str) -> list[LinterIssue]:
    """Анализ исходного кода на наличие синтаксических ошибок и предупреждений."""
    issues: list[LinterIssue] = []
    lines = source_code.splitlines()

    if not source_code.strip():
        return issues

    # 1. Синтаксический анализ через встроенный AST
    try:
        tree = ast.parse(source_code)
    except SyntaxError as e:
        line_num = max(1, e.lineno or 1)
        col_num = max(0, (e.offset or 1) - 1)
        raw_msg = e.msg or "Синтаксическая ошибка"
        line_text = lines[line_num - 1] if line_num - 1 < len(lines) else ""

        # Преобразуем технические сообщения компилятора в понятные подсказки.
        # Проверяем именно сообщение компилятора: раньше вторая половина условия
        # искала literal "expected ':'" в строке кода и была мёртвой ветвью.
        msg_lower = raw_msg.lower()
        if "expected ':'" in msg_lower:
            friendly = "Пропущено двоеточие ':' в конце конструкции"
        elif "was never closed" in msg_lower or "unclosed" in msg_lower:
            friendly = f"Незакрытая скобка или строка: {raw_msg}"
        elif "unexpected indent" in msg_lower:
            friendly = "Неожиданный отступ (лишние пробелы)"
        elif "unindent does not match" in msg_lower:
            friendly = "Отступ не соответствует предыдущему блоку кода"
        elif "expected an indented block" in msg_lower:
            friendly = "Ожидался блок кода с отступом (после def, if, for, while, with, try)"
        elif "invalid syntax" in msg_lower:
            friendly = "Неверный синтаксис (опечатка, неверный символ или оператор)"
        elif "cannot assign to" in msg_lower:
            friendly = f"Нельзя присвоить значение: {raw_msg}"
        elif "duplicate argument" in msg_lower:
            friendly = f"Повторяющийся аргумент в функции: {raw_msg}"
        else:
            friendly = f"Синтаксическая ошибка: {raw_msg}"

        issues.append(LinterIssue(
            line=line_num,
            col=col_num,
            message=friendly,
            severity="error",
            code_line=line_text
        ))
        return issues
    except Exception as e:
        issues.append(LinterIssue(
            line=1,
            col=0,
            message=f"Ошибка разбора: {e}",
            severity="error"
        ))
        return issues

    # 2. Проверка неиспользуемых импортов (Warning)
    try:
        imported_modules: dict[str, int] = {}
        used_names: set[str] = set()

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    # Связывается только первый компонент: 'import os.path' создаёт
                    # имя 'os'. Раньше в словарь попадало 'os.path', в used_names
                    # было 'os', и используемый импорт помечался неиспользуемым.
                    name = alias.asname or alias.name.split(".")[0]
                    imported_modules[name] = node.lineno
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    name = alias.asname or alias.name
                    imported_modules[name] = node.lineno
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                used_names.add(node.id)

        for name, lineno in imported_modules.items():
            if name not in used_names and name != "*" and not name.startswith("_"):
                line_text = lines[lineno - 1] if lineno - 1 < len(lines) else ""
                issues.append(LinterIssue(
                    line=lineno,
                    col=0,
                    message=f"Импорт '{name}' не используется в коде",
                    severity="warning",
                    code_line=line_text
                ))
    except Exception:
        pass

    return issues
