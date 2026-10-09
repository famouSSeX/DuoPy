"""
Автоматические тесты ключевых модулей DuoPy:
1. Запуск кода и парсинг вывода/ошибок (CodeRunner)
2. Интерактивный ввод input() через stdin (CodeRunner)
3. Сетевое взаимодействие между двумя узлами по TCP (LAN)
4. Онлайн-комнаты через глобальный облачный релей (Internet)
5. Поиск и замена в коде (Find and Replace)
6. Автодополнение кода (IntelliSense)
7. Безопасность путей, целостность вкладок и защита от затирания правок
   (регрессионные тесты на найденные дефекты)
"""

import sys
import os
import time
import unittest

sys._running_tests = True
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QTextCursor
from duopy.runner import CodeRunner
from duopy.network import NetworkManager
from duopy.editor import CodeEditor
from duopy.linter import lint_code

app = QApplication.instance()
if not app:
    app = QApplication([])


class TestRunner(unittest.TestCase):
    """Тестирование выполнения кода и обнаружения ошибок."""

    def test_successful_execution(self):
        runner = CodeRunner()
        output = []
        finished = []

        runner.output_received.connect(lambda text, is_err: output.append(text))
        runner.execution_finished.connect(lambda code, dur: finished.append((code, dur)))

        runner.run_code("print('Hello from DuoPy test')")

        for _ in range(50):
            app.processEvents()
            if finished:
                break
            time.sleep(0.05)

        self.assertTrue(len(finished) > 0, "Процесс должен завершиться")
        self.assertEqual(finished[0][0], 0, "Код возврата должен быть 0")
        full_text = "".join(output)
        self.assertIn("Hello from DuoPy test", full_text)

    def test_error_detection(self):
        runner = CodeRunner()
        errors = []
        finished = []

        runner.error_detected.connect(lambda line, msg: errors.append((line, msg)))
        runner.execution_finished.connect(lambda code, dur: finished.append((code, dur)))

        err_code = "x = 10\ny = 0\nz = x / y\n"
        runner.run_code(err_code)

        for _ in range(50):
            app.processEvents()
            if finished:
                break
            time.sleep(0.05)

        self.assertTrue(len(finished) > 0, "Процесс должен завершиться")
        self.assertNotEqual(finished[0][0], 0, "Код возврата ошибки должен быть не 0")
        self.assertTrue(len(errors) > 0, "Должна быть обнаружена ошибка")
        self.assertEqual(errors[0][0], 3, "Ошибка должна указывать на строку 3")

    def test_stdin_interactive(self):
        """Тест интерактивного ввода input() в выполняющийся процесс."""
        runner = CodeRunner()
        output = []
        finished = []

        runner.output_received.connect(lambda text, is_err: output.append(text))
        runner.execution_finished.connect(lambda code, dur: finished.append((code, dur)))

        code = "name = input()\nprint(f'Привет, {name}!')\n"
        runner.run_code(code)

        # Ждем старта процесса
        for _ in range(30):
            app.processEvents()
            if runner.is_running:
                break
            time.sleep(0.05)

        self.assertTrue(runner.is_running, "Процесс должен быть запущен")

        # Отправляем строку в stdin
        runner.send_stdin("Алексей")

        for _ in range(50):
            app.processEvents()
            if finished:
                break
            time.sleep(0.05)

        self.assertTrue(len(finished) > 0, "Процесс должен успешно завершиться после получения input()")
        full_text = "".join(output)
        self.assertIn("Привет, Алексей!", full_text)

    def test_non_ascii_output_is_not_mojibake(self):
        """
        Вывод процесса с не-ASCII символами не должен превращаться в абракадабру.

        Проверка защищает порядок декодирования: если после UTF-8 попробовать
        cp1251 раньше остальных, русский текст декодируется «успешно», но
        получается мусор.
        """
        runner = CodeRunner()
        output = []
        finished = []

        runner.output_received.connect(lambda text, is_err: output.append(text))
        runner.execution_finished.connect(lambda code, dur: finished.append(code))

        runner.run_code("print('Привет, мир'); print('Ünïcodé — тест')")

        for _ in range(120):
            app.processEvents()
            if finished:
                break
            time.sleep(0.05)

        full_text = "".join(output)
        self.assertIn("Привет, мир", full_text, full_text)
        self.assertIn("Ünïcodé — тест", full_text, full_text)
        self.assertEqual(finished[0], 0)

    def test_process_reference_is_released(self):
        """После завершения ссылка на процесс освобождается (нет утечки пайпов)."""
        runner = CodeRunner()
        finished = []
        runner.execution_finished.connect(lambda code, dur: finished.append(code))
        runner.run_code("print('done')")
        for _ in range(60):
            app.processEvents()
            if finished:
                break
            time.sleep(0.05)
        self.assertIsNone(runner.process)
        self.assertFalse(runner.is_running)


class TestEditorFeatures(unittest.TestCase):
    """Тестирование новых возможностей редактора: поиск/замена и автодополнение."""

    def test_find_and_replace(self):
        editor = CodeEditor()
        sample_text = "apple banana apple cherry apple"
        editor.setPlainText(sample_text)

        # 1. Поиск совпадений
        cur, total = editor.highlight_search_matches("apple")
        self.assertEqual(total, 3, "Должно найтись 3 слова 'apple'")
        self.assertEqual(cur, 1)

        # 2. Навигация далее
        cur, total = editor.find_next_match("apple")
        self.assertEqual(cur, 2)

        # 3. Замена всех совпадений
        count = editor.replace_all_matches("apple", "orange")
        self.assertEqual(count, 3, "Должно быть заменено 3 слова")
        self.assertEqual(editor.toPlainText(), "orange banana orange cherry orange")

    def test_autocomplete_completer(self):
        editor = CodeEditor()
        editor.setPlainText("my_custom_var = 100\nmy_another_var = 200\n")

        editor.update_completer_words()
        model = editor.completer.model()
        words = model.stringList()

        # Проверяем, что в модели есть builtins и локальные переменные
        self.assertIn("print", words)
        self.assertIn("my_custom_var", words)
        self.assertIn("my_another_var", words)

    def test_diff_apply_remote_code(self):
        """Тест дифференциального обновления кода с сохранением позиции курсора."""
        editor = CodeEditor()
        editor.setPlainText("def add(a, b):\n    return a + b\n")

        # Ставим курсор в конец
        cursor = editor.textCursor()
        cursor.setPosition(25)
        editor.setTextCursor(cursor)

        # Применяем обновление в начале документа
        prefix = "# Comment\n"
        updated = prefix + "def add(a, b):\n    return a + b\n"
        editor.apply_remote_code(updated)

        self.assertEqual(editor.toPlainText(), updated)
        self.assertEqual(editor.textCursor().position(), 25 + len(prefix))

    def test_ast_linter(self):
        """Тест обнаружения синтаксических ошибок Linter'ом."""
        from duopy.linter import lint_code

        # 1. Ошибка: пропущено двоеточие
        issues = lint_code("def foo()\n    pass\n")
        self.assertTrue(len(issues) > 0)
        self.assertEqual(issues[0].line, 1)
        self.assertEqual(issues[0].severity, "error")

        # 2. Ошибка: незакрытая скобка
        issues2 = lint_code("x = (10 + 20\n")
        self.assertTrue(len(issues2) > 0)
        self.assertEqual(issues2[0].severity, "error")

        # 3. Предупреждение: неиспользуемый импорт
        issues3 = lint_code("import sys\nprint('hello')\n")
        self.assertTrue(len(issues3) > 0)
        self.assertEqual(issues3[0].severity, "warning")

        # 4. Корректный код
        issues4 = lint_code("x = 10\nprint(x)\n")
        self.assertEqual(len(issues4), 0)


class TestNetwork(unittest.TestCase):
    """Тестирование сетевого соединения и синхронизации."""

    def test_tcp_handshake_and_sync(self):
        host = NetworkManager("Алиса", "#4ec9b0")
        client = NetworkManager("Боб", "#ff9800")

        port = 9877
        host.start_host(port)
        time.sleep(0.1)

        client_received_code = []
        client.text_received.connect(lambda c: client_received_code.append(c))

        client.connect_to_host("127.0.0.1", port)

        for _ in range(30):
            app.processEvents()
            if host.is_connected and client.is_connected:
                break
            time.sleep(0.05)

        self.assertTrue(host.is_connected)
        self.assertTrue(client.is_connected)

        test_code = "def add(a, b): return a + b"
        host.send_code_update(test_code)

        for _ in range(20):
            app.processEvents()
            if client_received_code:
                break
            time.sleep(0.05)

        self.assertIn(test_code, client_received_code)

        # Тест передачи выделения текста
        host_received_selection = []
        host.cursor_received.connect(
            lambda uid, name, color, line, col, pos, s_start, s_end, *args:
                host_received_selection.append((s_start, s_end))
        )
        client.send_cursor_position(2, 5, 25, 10, 25)

        for _ in range(20):
            app.processEvents()
            if host_received_selection:
                break
            time.sleep(0.05)

        self.assertIn((10, 25), host_received_selection)

        # Тест сетевой передачи stdin input()
        received_stdin = []
        host.stdin_received.connect(lambda t: received_stdin.append(t))
        client.send_stdin_input("test input data")

        for _ in range(20):
            app.processEvents()
            if received_stdin:
                break
            time.sleep(0.05)

        self.assertIn("test input data", received_stdin)

        host.stop()
        client.stop()

    def test_project_tree_and_file_sharing(self):
        """Тест сетевой передачи структуры проекта и удаленных файлов."""
        host = NetworkManager("Хост", "#4ec9b0")
        client = NetworkManager("Клиент", "#ff9800")

        port = 9878
        host.start_host(port)
        time.sleep(0.1)

        tree_received = []
        file_content_received = []
        file_save_received = []

        client.project_tree_received.connect(lambda p, f: tree_received.append((p, f)))
        client.file_content_received.connect(lambda p, c: file_content_received.append((p, c)))
        host.file_save_requested.connect(lambda p, c: file_save_received.append((p, c)))

        client.connect_to_host("127.0.0.1", port)

        for _ in range(30):
            app.processEvents()
            if host.is_connected and client.is_connected:
                break
            time.sleep(0.05)

        self.assertTrue(host.is_connected)
        self.assertTrue(client.is_connected)

        # 1. Хост передает дерево проекта
        sample_files = [
            {"path": "duopy", "is_dir": True},
            {"path": "duopy/main.py", "is_dir": False}
        ]
        host.send_project_tree("TestProj", sample_files)

        for _ in range(20):
            app.processEvents()
            if tree_received:
                break
            time.sleep(0.05)

        self.assertEqual(len(tree_received), 1)
        self.assertEqual(tree_received[0][0], "TestProj")
        self.assertEqual(tree_received[0][1], sample_files)

        # 2. Клиент запрашивает файл у хоста
        host_file_req = []
        host.file_content_requested.connect(lambda p: host_file_req.append(p))
        client.send_request_file("duopy/main.py")

        for _ in range(20):
            app.processEvents()
            if host_file_req:
                break
            time.sleep(0.05)

        self.assertIn("duopy/main.py", host_file_req)

        # 3. Хост отвечает содержимым файла
        host.send_file_data("duopy/main.py", "print('hello from host disk')")

        for _ in range(20):
            app.processEvents()
            if file_content_received:
                break
            time.sleep(0.05)

        self.assertIn(("duopy/main.py", "print('hello from host disk')"), file_content_received)

        # 4. Клиент сохраняет файл обратно хосту
        client.send_save_file("duopy/main.py", "print('modified by client')")

        for _ in range(20):
            app.processEvents()
            if file_save_received:
                break
            time.sleep(0.05)

        self.assertIn(("duopy/main.py", "print('modified by client')"), file_save_received)

        host.stop()
        client.stop()

    def test_main_window_manifest_and_tree(self):
        """Тест генерации манифеста и заполнения QTreeWidget."""
        from duopy.main_window import MainWindow
        win = MainWindow()
        manifest = win._get_local_project_manifest()
        self.assertIsInstance(manifest, list)
        self.assertTrue(len(manifest) > 0)

        # Проверяем, что в манифесте есть duopy
        has_duopy = any("duopy" in item["path"] for item in manifest)
        self.assertTrue(has_duopy)

        # Проверяем удаленное дерево
        win.on_remote_project_tree_received("RemoteProject", [
            {"path": "package", "is_dir": True},
            {"path": "package/app.py", "is_dir": False}
        ])
        self.assertTrue(win.is_remote_project)
        self.assertEqual(win.remote_project_name, "RemoteProject")
        self.assertEqual(win.file_tree.topLevelItemCount(), 1)
        top_item = win.file_tree.topLevelItem(0)
        self.assertIn("package", top_item.text(0))
        self.assertEqual(top_item.childCount(), 1)
        self.assertIn("app.py", top_item.child(0).text(0))
        win.close()


    def test_cloud_room_sync(self):
        """Тест синхронизации через облачный сервер комнат."""
        room_code = f"TEST-{int(time.time())}"
        user1 = NetworkManager("Юзер 1", "#4ec9b0")
        user2 = NetworkManager("Юзер 2", "#ff9800")

        user1.start_cloud_room(room_code, is_creator=True)
        user2.start_cloud_room(room_code, is_creator=False)

        user2_received = []
        user2.text_received.connect(lambda c: user2_received.append(c))

        for _ in range(100):
            app.processEvents()
            if user1.is_connected and user2.is_connected:
                break
            time.sleep(0.1)

        self.assertTrue(user1.is_connected, "User1 должен подключиться к облачной комнате")
        self.assertTrue(user2.is_connected, "User2 должен подключиться к облачной комнате")

        # Даем брокеру подтвердить подписку обоих клиентов
        time.sleep(0.5)

        cloud_code = "print('Synced across countries via cloud room!')"
        user1.send_code_update(cloud_code)

        for _ in range(100):
            app.processEvents()
            if user2_received:
                break
            time.sleep(0.1)

        self.assertIn(cloud_code, user2_received, "Код должен быть доставлен через облачный релей")

        user1.stop()
        user2.stop()

    def test_smart_line_lock(self):
        """Тест системы умной блокировки строк от одновременных правок."""
        from duopy.editor import CodeEditor
        editor = CodeEditor()
        editor.setPlainText("def hello():\n    print('test')\n    return True\n")

        # Регистрация лока на строке 2 напарником
        editor.update_line_lock("usr_999", "Алексей", "#ff9800", line=2)
        lock_info = editor.get_line_lock_info(2)
        self.assertIsNotNone(lock_info)
        self.assertEqual(lock_info["name"], "Алексей")

        # Строка 1 не заблокирована
        self.assertIsNone(editor.get_line_lock_info(1))

        # Перемещение курсора напарника на строку 3 освобождает строку 2
        editor.update_remote_cursor("usr_999", "Алексей", "#ff9800", line=3, col=4)
        self.assertIsNone(editor.get_line_lock_info(2))
        self.assertIsNotNone(editor.get_line_lock_info(3))

        # Удаление напарника снимает все блокировки
        editor.remove_remote_cursor("usr_999")
        self.assertIsNone(editor.get_line_lock_info(3))
        editor.close()

    def test_second_peer_lock_does_not_overwrite_first(self):
        """Замок второго напарника на той же строке не должен затирать первого."""
        editor = CodeEditor()
        editor.setPlainText("a = 1\nb = 2\nc = 3\n")
        editor.update_remote_cursor("u1", "Алиса", "#ff9800", line=2, col=0)
        editor.update_remote_cursor("u2", "Боб", "#4ec9b0", line=2, col=0)
        owners = editor._active_lock_owners(2)
        self.assertEqual(len(owners), 2, "на строке должно быть два владельца")
        self.assertEqual({o["name"] for o in owners}, {"Алиса", "Боб"})
        # get_line_lock_info вызывается из paintEvent и не должен менять состояние
        editor.get_line_lock_info(99)
        self.assertNotIn(99, editor.line_locks)
        editor.close()

    def test_line_locks_cleared_on_peer_leave(self):
        """После ухода напарника его замки строк снимаются (иначе ложный 'занято')."""
        editor = CodeEditor()
        editor.setPlainText("a = 1\nb = 2\n")
        editor.update_remote_cursor("u1", "Алиса", "#ff9800", line=2, col=0)
        self.assertIsNotNone(editor.get_line_lock_info(2))
        editor.remove_remote_cursor("u1")
        self.assertIsNone(editor.get_line_lock_info(2))
        self.assertEqual(editor._active_lock_owners(2), [])
        editor.close()


class TestSecurityAndDataIntegrity(unittest.TestCase):
    """Регрессии на найденные дефекты: обход путей, потеря текста, замена кода."""

    def setUp(self):
        self.tmp_root = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".test_tmp"
        )
        self.project = os.path.join(self.tmp_root, "project")
        self.sibling = self.project + "_evil"
        os.makedirs(self.project, exist_ok=True)
        os.makedirs(self.sibling, exist_ok=True)
        self.evil_file = os.path.join(self.sibling, "pwned.py")
        with open(self.evil_file, "w", encoding="utf-8") as f:
            f.write("evil")
        self.created = []

    def tearDown(self):
        import shutil
        for win in self.created:
            win.close()
        shutil.rmtree(self.tmp_root, ignore_errors=True)

    def _window(self):
        from duopy.main_window import MainWindow
        win = MainWindow()
        win.project_path = self.project
        self.created.append(win)
        return win

    def test_resolve_inside_rejects_escapes(self):
        """Путь напарника не может выйти за пределы папки проекта."""
        from duopy.main_window import resolve_inside
        self.assertIsNone(resolve_inside("../outside.py", self.project))
        self.assertIsNone(resolve_inside("../../x.py", self.project))
        self.assertIsNone(resolve_inside(self.evil_file, self.project))
        self.assertIsNone(resolve_inside("..\\project_evil\\pwned.py", self.project))
        self.assertIsNone(resolve_inside("a\x00b.py", self.project))
        self.assertIsNotNone(resolve_inside("sub/a.py", self.project))

    def test_remote_file_operations_cannot_escape_project(self):
        """SAVE/CREATE/DELETE от напарника не трогают файлы вне проекта."""
        win = self._window()
        win.on_remote_file_save_requested("../project_evil/pwned.py", "HACKED")
        with open(self.evil_file, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), "evil", "файл рядом с проектом не должен меняться")
        win.on_remote_file_create_requested("../project_evil/new.py")
        self.assertFalse(os.path.exists(os.path.join(self.sibling, "new.py")))
        win.on_remote_file_delete_requested("../project_evil")
        self.assertTrue(os.path.exists(self.evil_file), "удаление вне проекта запрещено")

    def test_tab_close_does_not_move_content_to_neighbour(self):
        """Закрытие вкладки не переносит текст активной вкладки в соседнюю."""
        win = self._window()
        win._add_tab("B.py", "CONTENT_B", None)
        win._add_tab("C.py", "CONTENT_C", None)
        win.tab_bar.setCurrentIndex(1)          # активна B
        win.current_tab_index = 1
        win.editor.load_text_programmatically("CONTENT_B_EDITED")
        win.on_tab_close_requested(0)           # закрываем первую вкладку

        self.assertEqual([t["title"] for t in win.tabs], ["B.py", "C.py"])
        self.assertEqual(win.tabs[0]["content"], "CONTENT_B_EDITED")
        self.assertEqual(win.tabs[1]["content"], "CONTENT_C",
                         "текст вкладки B не должен попасть в C (иначе потеря данных)")
        self.assertEqual(win.current_tab_index, 0)
        self.assertEqual(win.editor.toPlainText(), "CONTENT_B_EDITED")

    def test_close_others_and_all_tabs(self):
        """«Закрыть другие/все» оставляют корректный набор вкладок."""
        win = self._window()
        win._add_tab("B.py", "B", None)
        win._add_tab("C.py", "C", None)
        win._add_tab("D.py", "D", None)
        # закрываем все, кроме индекса 2 (C.py)
        for i in range(len(win.tabs) - 1, -1, -1):
            if i != 2:
                win.on_tab_close_requested(i)
        self.assertEqual([t["title"] for t in win.tabs], ["C.py"])
        win.on_tab_close_requested(0)
        self.assertEqual(len(win.tabs), 1)
        self.assertEqual(win.tabs[0]["title"], "main.py")

    def test_remote_code_does_not_overwrite_local_file(self):
        """Код напарника не подменяет содержимое локального файла с тем же именем."""
        win = self._window()
        # Закрываем стартовую вкладку main.py, чтобы имя совпадало с файлом на диске
        win.on_tab_close_requested(0)
        self.assertEqual(win.tabs[0]["title"], "main.py")
        self.assertIsNone(win.tabs[0]["path"])

        local_dir = os.path.join(self.tmp_root, "local")
        os.makedirs(local_dir, exist_ok=True)
        local_file = os.path.join(local_dir, "main.py")
        with open(local_file, "w", encoding="utf-8") as f:
            f.write("LOCAL CONTENT")
        win._open_file_in_tab(local_file)
        self.assertEqual(len(win.tabs), 1, "пустая вкладка должна переиспользоваться")

        win.on_remote_code_received("REMOTE CONTENT", "main.py")
        self.assertEqual(win.editor.toPlainText(), "LOCAL CONTENT",
                         "локальный файл не должен получать чужой код")
        self.assertEqual(len(win.tabs), 2, "для файла напарника нужна отдельная вкладка")
        self.assertTrue(win.tabs[1].get("is_remote"))

        # Повторное обновление того же файла обновляет ту же вкладку
        win.on_remote_code_received("REMOTE V2", "main.py")
        self.assertEqual(win.tabs[1]["content"], "REMOTE V2")
        self.assertEqual(win.tabs[0]["content"], "LOCAL CONTENT")

    def test_incoming_update_defers_only_while_typing(self):
        """
        Входящая правка откладывается, только пока пользователь печатает,
        и применяется сама после паузы.

        Раньше она ждала ухода напарника со строки и не применялась никогда —
        из-за этого каждый видел только свой текст и стороны расходились.
        """
        editor = CodeEditor()
        editor.setPlainText("x = 1\n")
        cursor = editor.textCursor()
        cursor.setPosition(4)                     # каретка сразу после 'x = '
        editor.setTextCursor(cursor)

        # Пользователь только что набрал символ, то есть сейчас печатает
        editor._typing_timer.start(900)
        applied = editor.apply_remote_code("x = 9999\n", defer_on_conflict=True)
        self.assertFalse(applied, "во время набора правка должна откладываться")
        self.assertEqual(editor.toPlainText(), "x = 1\n", "локальный текст не должен меняться")
        self.assertIsNotNone(editor._deferred_remote_update)

        # По паузе отложенная правка применяется сама
        editor.flush_deferred_remote_update()
        self.assertEqual(editor.toPlainText(), "x = 9999\n")
        self.assertIsNone(editor._deferred_remote_update)
        editor.close()

    def test_incoming_update_applies_when_not_typing(self):
        """Если пользователь не печатает, правка напарника применяется сразу."""
        editor = CodeEditor()
        editor.setPlainText("x = 1\n")
        cursor = editor.textCursor()
        cursor.setPosition(4)
        editor.setTextCursor(cursor)
        # setPlainText сам считается активностью (защита недавней вставки),
        # поэтому для сценария «пользователь бездействует» останавливаем таймер.
        editor._typing_timer.stop()
        self.assertFalse(editor._typing_timer.isActive())

        applied = editor.apply_remote_code("x = 9999\n", defer_on_conflict=True)
        self.assertTrue(applied, "без активного набора откладывать нечего")
        self.assertEqual(editor.toPlainText(), "x = 9999\n")
        self.assertIsNone(editor._deferred_remote_update)
        editor.close()

    def test_two_peers_typing_in_same_place_converge(self):
        """
        Главный сценарий: оба печатают в одном и том же месте файла.

        Текст обязан совпасть у обоих, а отложенные правки — не зависнуть.
        Прежняя версия оставляла у сторон разный текст навсегда.
        """
        import time as _time
        from duopy.main_window import MainWindow

        port = 9961
        host = MainWindow()
        guest = MainWindow()
        self.created.append(host)
        self.created.append(guest)

        host.network.start_host(port)
        for _ in range(25):
            app.processEvents()
            _time.sleep(0.02)

        try:
            guest.network.connect_to_host("127.0.0.1", port)
            for _ in range(80):
                app.processEvents()
                if host.network.is_connected and guest.network.is_connected:
                    break
                _time.sleep(0.03)
            self.assertTrue(host.network.is_connected and guest.network.is_connected,
                            "два окна должны соединиться")

            def pump(seconds):
                end = _time.time() + seconds
                while _time.time() < end:
                    app.processEvents()
                    _time.sleep(0.02)

            def type_in(win, text):
                win.editor.load_text_programmatically(text)
                cur = win.editor.textCursor()
                cur.setPosition(max(0, win.editor.document().characterCount() - 1))
                win.editor.setTextCursor(cur)
                win.editor.code_changed_by_user.emit(text)
                app.processEvents()

            type_in(host, "count = ")
            type_in(guest, "count = ")
            pump(1.0)

            # Оба «печатают» в одну строку, каретка каждого в области правки
            for i in range(1, 4):
                type_in(host, f"count = {i}")
                pump(0.3)
                type_in(guest, guest.editor.toPlainText() + "x")
                pump(0.3)

            pump(4.0)   # ждём применения отложенных правок

            host_text = host.editor.toPlainText()
            guest_text = guest.editor.toPlainText()
            self.assertEqual(host_text, guest_text,
                             f"текст должен совпасть у обоих: {host_text!r} != {guest_text!r}")
            self.assertIsNone(host.editor._deferred_remote_update,
                              "у хоста не должно остаться зависшей правки")
            self.assertIsNone(guest.editor._deferred_remote_update,
                              "у гостя не должно остаться зависшей правки")
        finally:
            host.network.stop()
            guest.network.stop()

    def test_replace_all_is_literal_and_undoable(self):
        """«Заменить все» вставляет текст буквально и отменяется через Undo."""
        editor = CodeEditor()
        editor.setPlainText("print('a')\nprint('b')\n")
        cursor = editor.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        cursor.movePosition(QTextCursor.MoveOperation.Right, QTextCursor.MoveMode.KeepAnchor, 3)
        editor.setTextCursor(cursor)

        count = editor.replace_all_matches("print", "log.info")
        self.assertEqual(count, 2)
        self.assertIn("log.info('a')", editor.toPlainText())
        self.assertLessEqual(editor.textCursor().position(), 5,
                             "каретка не должна улетать в конец документа")
        editor.undo()
        self.assertIn("print('a')", editor.toPlainText(), "Undo обязан вернуть исходный текст")

        # Обратные слэши и backreference-подобные последовательности — литерально
        editor2 = CodeEditor()
        editor2.setPlainText("value = 1")
        self.assertEqual(editor2.replace_all_matches("1", r"C:\new\1"), 1)
        self.assertIn(r"C:\new\1", editor2.toPlainText())
        editor.close()
        editor2.close()

    def test_replace_uses_fresh_offsets_after_edit(self):
        """Замена идёт по актуальным позициям, а не по устаревшим смещениям."""
        editor = CodeEditor()
        editor.setPlainText("apple banana apple cherry apple")
        editor.highlight_search_matches("apple")
        cursor = editor.textCursor()
        cursor.setPosition(0)
        editor.setTextCursor(cursor)
        cursor.insertText("XY")

        result = editor.replace_current_match("apple", "ORANGE")
        self.assertEqual(result, (1, 2), "после правки остаётся два совпадения")
        self.assertIn("XYORANGE", editor.toPlainText())
        self.assertEqual(editor.toPlainText().count("apple"), 2)
        editor.close()

    def test_search_pattern_change_and_navigation(self):
        """Смена запроса пересчитывает совпадения, обход идёт по кругу."""
        editor = CodeEditor()
        editor.setPlainText("apple banana apple cherry apple")
        self.assertEqual(editor.highlight_search_matches("apple"), (1, 3))
        self.assertEqual([editor.find_next_match("apple")[0] for _ in range(4)], [2, 3, 1, 2])
        self.assertEqual(editor.find_next_match("banana"), (1, 1),
                         "новый запрос должен искаться, а не сдвигать старый список")
        editor.close()

    def test_navigation_honours_case_sensitivity(self):
        """Переключение регистра в панели поиска меняет набор совпадений."""
        editor = CodeEditor()
        editor.setPlainText("Apple apple APPLE")
        insensitive = editor.find_next_match("apple", case_sensitive=False)
        sensitive = editor.find_next_match("apple", case_sensitive=True)
        self.assertEqual(insensitive[1], 3, "без учёта регистра — три совпадения")
        self.assertEqual(sensitive[1], 1, "с учётом регистра — одно совпадение")
        editor.close()

    def test_completion_replaces_typed_prefix(self):
        """Дополнение заменяет набранный префикс и не дублирует слово."""
        editor = CodeEditor()
        editor.setPlainText("printer = 1\n")
        cursor = editor.textCursor()
        cursor.setPosition(7)                    # каретка в конце слова
        editor.setTextCursor(cursor)
        editor.completer.setCompletionPrefix("printer")
        editor.insert_completion("printer_x")
        self.assertEqual(editor.toPlainText(), "printer_x = 1\n")

        # Принятие ровно введённого слова не должно давать "printprint"
        editor2 = CodeEditor()
        editor2.setPlainText("print (1)\n")
        cursor = editor2.textCursor()
        cursor.setPosition(5)
        editor2.setTextCursor(cursor)
        editor2.completer.setCompletionPrefix("print")
        editor2.insert_completion("print")
        self.assertEqual(editor2.toPlainText(), "print (1)\n")
        editor.close()
        editor2.close()

    def test_toggle_comment_with_empty_first_line(self):
        """Ctrl+/ комментирует код даже если выделение начинается с пустой строки."""
        editor = CodeEditor()
        editor.setPlainText("\nprint(1)\nprint(2)\n")
        cursor = editor.textCursor()
        cursor.setPosition(0)
        cursor.setPosition(19, QTextCursor.MoveMode.KeepAnchor)
        editor.setTextCursor(cursor)
        editor.toggle_comment()
        lines = editor.toPlainText().splitlines()
        self.assertEqual(lines[0], "", "пустая строка не должна становиться комментарием")
        self.assertIn("# print(1)", editor.toPlainText())
        editor.toggle_comment()
        self.assertNotIn("# print", editor.toPlainText(), "повторный Ctrl+/ раскомментирует")
        editor.close()

    def test_enter_keeps_single_indent_level(self):
        """Enter не удваивает отступ и учитывает только текст до каретки."""
        # Редактор держим в отдельном показанном окне: без родителя виджет
        # становится disabled при потере фокуса, и вставка текста не работает.
        from PyQt6.QtWidgets import QWidget, QVBoxLayout
        holder = QWidget()
        layout = QVBoxLayout(holder)
        self.created.append(holder)
        editor = CodeEditor()
        layout.addWidget(editor)
        holder.show()
        QApplication.setActiveWindow(holder)

        def press_enter(text):
            editor.setPlainText(text)
            cursor = editor.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            editor.setTextCursor(cursor)
            editor.keyPressEvent(self._enter_event())
            # split('\n'), а не splitlines(): нужен элемент для строки,
            # которую открывает Enter (splitlines() его отбрасывает).
            return editor.toPlainText().split('\n')

        # После ':' добавляется ровно один уровень отступа
        lines = press_enter("def f():\n    x = 1")
        self.assertEqual(len(lines), 3, f"ожидался перенос строки, получено {lines!r}")
        self.assertEqual(lines[2], "    ")

        # Комментарий с двоеточием блок не открывает
        lines2 = press_enter("# note:")
        self.assertEqual(len(lines2), 2, f"ожидался перенос строки, получено {lines2!r}")
        self.assertEqual(lines2[1], "", "комментарий не открывает блок с отступом")

        # Двоеточие после кода в той же строке учитывается только до каретки
        lines3 = press_enter("if x: y = 1")
        self.assertEqual(len(lines3), 2)
        self.assertEqual(lines3[1], "", "двоеточие в середине строки не даёт отступа")
        editor.close()

    @staticmethod
    def _enter_event():
        from PyQt6.QtGui import QKeyEvent
        from PyQt6.QtCore import QEvent, Qt
        return QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier)

    def test_linter_dotted_import_and_messages(self):
        """Линтер не считает используемый `import os.path` лишним."""
        issues = lint_code("import os.path\nprint(os.path.join('a'))\n")
        self.assertTrue(all("не используется" not in i.message for i in issues), issues)
        self.assertTrue(lint_code("def f()\n    pass\n")[0].message.startswith("Пропущено"))
        self.assertEqual(lint_code("x = 10\nprint(x)\n"), [])

    def test_cursor_message_carries_file_name(self):
        """Координаты курсора отправляются вместе с именем файла."""
        win = self._window()
        win.network.running = True          # имитируем активную сессию
        win._add_tab("other.py", "x = 1", None)
        win.on_local_cursor_moved(3, 4, 30, 30, 30)
        self.assertIsNotNone(win.pending_cursor_data, "координаты должны накопиться")
        self.assertEqual(len(win.pending_cursor_data), 6)
        self.assertEqual(win.pending_cursor_data[5], "other.py",
                         "имя файла должно фиксироваться вместе с координатами")

    def test_host_accepts_reconnect_after_stray_connection(self):
        """Случайное подключение не занимает слот: напарник подключается следом."""
        import socket as _socket
        port = 9943
        host = NetworkManager("Хост", "#4ec9b0")
        host.start_host(port)
        time.sleep(0.2)
        try:
            stray = _socket.create_connection(("127.0.0.1", port), timeout=3)
            stray.close()
            time.sleep(0.3)

            guest = NetworkManager("Гость", "#ff9800")
            received = []
            guest.text_received.connect(lambda c: received.append(c))
            guest.connect_to_host("127.0.0.1", port)

            for _ in range(60):
                app.processEvents()
                if host.is_connected and guest.is_connected:
                    break
                time.sleep(0.05)

            self.assertTrue(host.is_connected, "хост должен принять напарника после шумового коннекта")
            self.assertTrue(guest.is_connected, "гость должен подключиться")

            host.send_code_update("print('lan after noise')")
            for _ in range(40):
                app.processEvents()
                if received:
                    break
                time.sleep(0.05)
            self.assertIn("print('lan after noise')", received)
            guest.stop()
        finally:
            host.stop()


class TestUpdater(unittest.TestCase):
    """Проверка логики автообновления: версии, разбор релиза, загрузка."""

    def test_version_comparison(self):
        """Сравнение версий покомпонентное, а не строковое."""
        from duopy import updater as U
        self.assertTrue(U.is_newer("1.2.1", "1.2.0"))
        self.assertTrue(U.is_newer("1.2.10", "1.2.9"), "1.2.10 должно быть новее 1.2.9")
        self.assertFalse(U.is_newer("1.2.0", "1.2.0"))
        self.assertFalse(U.is_newer("1.1.9", "1.2.0"))
        self.assertFalse(U.is_newer("", "1.2.0"))
        self.assertTrue(U.is_newer("v1.3.0", "1.2.0"), "префикс v должен игнорироваться")
        self.assertEqual(U.parse_version("v1.2.10"), (1, 2, 10))

    def test_release_asset_selection(self):
        """Из файлов релиза выбирается именно DuoPy.exe, иначе первый exe/zip."""
        import json
        import urllib.request
        from duopy import updater as U

        real_open = urllib.request.urlopen

        class FakeResponse:
            def __init__(self, payload):
                self.payload = payload

            def read(self):
                return self.payload

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        payload = {
            "tag_name": "v1.3.0",
            "html_url": "https://github.com/example/releases/tag/v1.3.0",
            "body": "",
            "assets": [
                {"name": "source.zip", "browser_download_url": "https://e/s.zip", "size": 10},
                {"name": "DuoPy.exe", "browser_download_url": "https://e/DuoPy.exe", "size": 42},
            ],
        }
        urllib.request.urlopen = lambda req, timeout=None: FakeResponse(json.dumps(payload).encode())
        try:
            info = U.UpdateCheckThread.fetch_latest_release()
            self.assertEqual(info.version, "1.3.0")
            self.assertEqual(info.asset_name, "DuoPy.exe", "должен выбираться собранный EXE")
            self.assertEqual(info.size, 42)

            # Без приложенных файлов релиз всё равно распознаётся
            payload["assets"] = []
            info2 = U.UpdateCheckThread.fetch_latest_release()
            self.assertEqual(info2.version, "1.3.0")
            self.assertFalse(info2.has_asset)

            # Ошибка сети не должна ронять проверку
            def boom(req, timeout=None):
                raise OSError("no network")

            urllib.request.urlopen = boom
            info3 = U.UpdateCheckThread.fetch_latest_release()
            self.assertEqual(info3.version, "")
            self.assertFalse(info3.has_asset)
        finally:
            urllib.request.urlopen = real_open

    def test_download_rejects_non_executable(self):
        """Загрузка отклоняет файл, не являющийся программой (например, HTML)."""
        import urllib.request
        from duopy import updater as U

        real_open = urllib.request.urlopen
        tmp_dir = U.updates_dir()

        class FakeDownload:
            def __init__(self, data):
                self.data = data
                self.headers = {"Content-Length": str(len(data))}

            def read(self, size=-1):
                chunk, self.data = self.data[:size], self.data[size:]
                return chunk

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        # HTML вместо EXE — должно быть отклонено
        urllib.request.urlopen = lambda req, timeout=None: FakeDownload(b"<html>404</html>")
        errors = []
        try:
            thread = U.UpdateDownloadThread("https://e/broken", "_test_broken.exe")
            thread.failed.connect(lambda e: errors.append(e))
            thread.start()
            for _ in range(100):
                app.processEvents()
                if errors:
                    break
                time.sleep(0.05)
            self.assertTrue(errors, "подмена файла на HTML должна распознаваться")
        finally:
            urllib.request.urlopen = real_open
            leftover = os.path.join(tmp_dir, "_test_broken.exe")
            if os.path.exists(leftover):
                os.remove(leftover)

    def test_install_requires_frozen_app(self):
        """Установка обновления доступна только для собранного EXE."""
        from duopy import updater as U
        previous = getattr(sys, "frozen", None)
        if previous:
            del sys.frozen
        try:
            ok, message = U.install_update(os.path.join(os.getcwd(), "run.py"))
            self.assertFalse(ok)
            self.assertIn("собранного", message.lower())
        finally:
            if previous:
                sys.frozen = previous


class TestConnectionSpeed(unittest.TestCase):
    """Измерение качества связи: задержка (ping/pong) и скорость передачи текста."""

    def test_ping_measures_latency(self):
        """PING/PONG измеряет круговое время и попадает в статистику."""
        import time as _time
        host = NetworkManager("Хост", "#4ec9b0")
        guest = NetworkManager("Гость", "#ff9800")

        port = 9963
        host.start_host(port)
        for _ in range(25):
            app.processEvents()
            _time.sleep(0.02)
        try:
            guest.connect_to_host("127.0.0.1", port)
            for _ in range(80):
                app.processEvents()
                if host.is_connected and guest.is_connected:
                    break
                _time.sleep(0.03)
            self.assertTrue(host.is_connected and guest.is_connected)

            self.assertEqual(host.latency_ms, 0.0, "до замера задержка неизвестна")

            # Даём циклу чтения на стороне хоста запуститься: флаг соединения
            # выставляется чуть раньше, чем сокет начинает читаться
            for _ in range(20):
                app.processEvents()
                _time.sleep(0.03)

            # Ping отправляем с повтором: первый может попасть в момент,
            # когда хост ещё не вошёл в цикл чтения
            for _ in range(5):
                host.send_ping()
                for _ in range(40):
                    app.processEvents()
                    if host.latency_samples:
                        break
                    _time.sleep(0.03)
                if host.latency_samples:
                    break

            # На localhost задержка может быть ровно 0 мс (быстрее разрешения
            # часов), поэтому проверяем сам факт замера, а не его величину
            self.assertGreaterEqual(len(host.latency_samples), 1,
                                    "замер задержки должен состояться")
            self.assertGreater(host.rtt_peak_ms, -1.0, "пик задержки должен быть посчитан")
            self.assertLess(host.latency_ms, 5000.0, "на localhost задержка не может быть огромной")
        finally:
            host.stop()
            guest.stop()

    def test_ping_requires_connection(self):
        """Без соединения ping не отправляется и метрики не портятся."""
        net = NetworkManager("Один", "#4ec9b0")
        self.assertFalse(net.send_ping())
        self.assertEqual(net.latency_ms, 0.0)

    def test_text_rate_smoothing_and_counters(self):
        """Счётчики байтов и сглаживание скорости дают читаемый показатель."""
        net = NetworkManager("Тест", "#4ec9b0")
        net.reset_metrics()
        self.assertEqual(net.rate_in_bps, 0.0)

        # Имитируем приём 4 КБ за одно окно замера
        net.bytes_in = 4096
        time.sleep(0.06)
        net.update_rates()
        self.assertGreater(net.rate_in_bps, 0.0)
        self.assertGreater(net.throughput_peak_bps, 0.0)

        # Ничего не приходит: скорость должна затухать, а не обнуляться мгновенно
        for _ in range(2):
            time.sleep(0.06)
            net.update_rates()
        self.assertGreater(net.rate_in_bps, 0.0, "показатель должен затухать постепенно")
        for _ in range(10):
            time.sleep(0.06)
            net.update_rates()
        self.assertEqual(net.rate_in_bps, 0.0, "после паузы скорость обнуляется")

        # Сброс очищает и пиковые значения
        net.reset_metrics()
        self.assertEqual(net.bytes_in, 0)
        self.assertEqual(net.throughput_peak_bps, 0.0)
        self.assertEqual(net.latency_samples, [])

    def test_speed_indicator_reflects_state(self):
        """Индикатор в статус-баре отражает отсутствие связи и активную задержку."""
        from duopy.main_window import MainWindow
        win = MainWindow()
        try:
            win._stop_metrics()
            self.assertIn("нет связи", win.lbl_sb_speed.text())

            # Имитируем активную сессию с хорошей задержкой
            win.network.is_connected = True
            win.network.latency_ms = 42.0
            win.network.latency_samples = [40.0, 42.0]
            win.network.rate_in_bps = 2048.0
            win.network.rate_out_bps = 0.0
            win._update_speed_label()
            text = win.lbl_sb_speed.text()
            self.assertIn("42 мс", text)
            self.assertIn("2.0 КБ/с", text, "скорость должна показываться в КБ/с")

            # Очень быстрый канал: 0 мс — это замер, а не его отсутствие
            win.network.latency_ms = 0.0
            win.network.rate_in_bps = 0.0
            win._update_speed_label()
            self.assertIn("<1 мс", win.lbl_sb_speed.text(), win.lbl_sb_speed.text())

            # Плохая связь — красный индикатор
            win.network.latency_ms = 900.0
            win._update_speed_label()
            self.assertTrue(win.lbl_sb_speed.text().startswith("🔴"),
                            win.lbl_sb_speed.text())
        finally:
            win.network.is_connected = False
            win.close()

    def test_peers_list_shows_latency(self):
        """В списке участников рядом с напарником видна задержка."""
        from duopy.main_window import MainWindow
        win = MainWindow()
        try:
            win.network.is_connected = True
            win.peer_name = "Напарник"
            win.network.latency_ms = 37.0
            win._update_peers_list()
            items = [win.peers_list.item(i).text() for i in range(win.peers_list.count())]
            self.assertTrue(any("37 мс" in t for t in items), items)
        finally:
            win.network.is_connected = False
            win.close()


class TestDeltaSync(unittest.TestCase):
    """Передача только изменённого фрагмента: порядок, размер, потери."""

    def _prepared_pair(self, text):
        """Два редактора с одинаковым текстом и согласованными метками."""
        a, b = CodeEditor(), CodeEditor()
        a.load_text_programmatically(text)
        b.load_text_programmatically(text)
        a.mark_synced(text)
        b.mark_synced(text)
        return a, b

    def test_sequential_typing_keeps_order(self):
        """
        Последовательный набор не должен менять порядок символов.

        Регрессия: позиция правки ограничивалась длиной базового текста, а не
        текущего документа, поэтому каждое следующее нажатие вставлялось в
        прежнее место и символы вставали задом наперёд («ZYX» вместо «XYZ»).
        """
        big = "".join(f"def f_{i}(a, b):\n    return a + b + {i}\n" for i in range(500))
        a, b = self._prepared_pair(big)

        for i, ch in enumerate("XYZ"):
            cursor = a.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            a.setTextCursor(cursor)
            a.insertPlainText(ch)
            delta = a.capture_send_delta()
            self.assertTrue(b.apply_remote_delta(*delta), f"правка {ch} должна примениться")

        self.assertTrue(a.toPlainText().endswith("XYZ"), a.toPlainText()[-10:])
        self.assertEqual(a.toPlainText(), b.toPlainText(), "тексты должны совпасть")
        a.close()
        b.close()

    def test_delta_is_small_for_large_file(self):
        """Правка в большом файле передаёт байты, а не весь файл."""
        big = "".join(f"value_{i} = {i}\n" for i in range(10000))
        a, _ = self._prepared_pair(big)

        cursor = a.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        a.setTextCursor(cursor)
        a.insertPlainText("x")
        pos, removed, insert, digest = a.capture_send_delta()

        self.assertEqual(insert, "x")
        self.assertEqual(removed, 0)
        self.assertLess(len(insert.encode()), 8, "уходит только фрагмент")
        self.assertTrue(digest, "метка версии должна передаваться")
        self.assertGreater(len(big), 100_000, "файл для теста должен быть большим")
        a.close()

    def test_no_delta_when_nothing_changed(self):
        """Без правок отправлять нечего."""
        a, _ = self._prepared_pair("print(1)\n")
        pos, removed, insert, _ = a.capture_send_delta()
        self.assertEqual((pos, removed, insert), (0, 0, ""))
        a.close()

    def test_stale_delta_is_rejected(self):
        """Правка, основанная на устаревшей версии, не применяется."""
        a, b = self._prepared_pair("abcdef")
        # Метка не совпадает с последним отправленным текстом
        applied = b.apply_remote_delta(2, 3, "ZZZ", "0000000000000000")
        self.assertFalse(applied)
        self.assertEqual(b.toPlainText(), "abcdef", "текст не должен меняться")
        a.close()
        b.close()

    def test_offset_delta_applied_with_shift(self):
        """Правка применяется, даже если мы уже печатали в другом месте."""
        text = "aaa\nbbb\nccc\n"
        a, b = self._prepared_pair(text)

        # b печатает в начале, a — в конце: области не пересекаются
        cursor = b.textCursor()
        cursor.setPosition(0)
        b.setTextCursor(cursor)
        b.insertPlainText("B")
        b.mark_synced(text)          # напарник ещё не знает о нашей правке

        cursor = a.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        a.setTextCursor(cursor)
        a.insertPlainText("A")
        delta = a.capture_send_delta()

        self.assertTrue(b.apply_remote_delta(*delta), "непересекающуюся правку нужно применить")
        self.assertIn("Baaa", b.toPlainText())
        self.assertTrue(b.toPlainText().endswith("A"))
        a.close()
        b.close()

    def test_loss_detection_by_sequence(self):
        """Пропуск номера правки распознаётся как потеря."""
        from duopy.network import NetworkManager
        net = NetworkManager("Тест")
        losses = []
        net.edit_loss_detected.connect(lambda: losses.append(1))
        received = []
        net.code_edit_received.connect(lambda *args: received.append(args))

        net._dispatch_message({"type": "CODE_EDIT", "pos": 0, "removed": 0,
                               "insert": "a", "file": "f.py", "base": "x", "seq": 1})
        self.assertEqual(len(received), 1, "первая правка применяется")
        self.assertEqual(len(losses), 0)

        # Пропуск номера 2 — потеря
        net._dispatch_message({"type": "CODE_EDIT", "pos": 1, "removed": 0,
                               "insert": "b", "file": "f.py", "base": "x", "seq": 3})
        self.assertEqual(len(losses), 1, "пропуск номера должен обнаруживаться")
        self.assertEqual(len(received), 1, "правка после потери не применяется")

    def test_large_message_is_compressed(self):
        """Крупные сообщения сжимаются, мелкие отправляются как есть."""
        from duopy.network import NetworkManager, COMPRESS_THRESHOLD_BYTES
        net = NetworkManager("Тест")

        small = {"type": "CODE_UPDATE", "code": "x = 1", "file": "a.py"}
        self.assertEqual(net._maybe_compress(small), small, "мелкое не сжимаем")

        big = {"type": "CODE_UPDATE",
               "code": "".join(f"def f_{i}(a, b):\n    return a + b\n" for i in range(2000)),
               "file": "big.py"}
        packed = net._maybe_compress(big)
        self.assertEqual(packed.get("type"), "PACKED", "крупное должно сжаться")
        import json
        raw_size = len(json.dumps(big, separators=(",", ":")))
        packed_size = len(json.dumps(packed))
        self.assertLess(packed_size, raw_size / 2, "сжатие должно давать выигрыш")
        # И распаковывается обратно
        self.assertEqual(net._unpack(packed)["file"], "big.py")
        self.assertGreater(raw_size, COMPRESS_THRESHOLD_BYTES)


class TestP2PTransport(unittest.TestCase):
    """
    Прямое соединение между участниками (UDP).

    Двух машин в тесте нет, поэтому участники разведены по разным loopback-
    адресам: так у каждого свой адрес, как у двух отдельных узлов.
    """

    def _pair(self, timeout: float = 6.0, bind_ip: str | None = None):
        from duopy.p2p import P2PTransport
        # «Своими» считаем только несуществующий адрес: оба участника теста
        # живут на одной машине, поэтому автоопределение отнесло бы loopback
        # напарника к своим адресам и канал не установился бы.
        a = P2PTransport(own_ips=["203.0.113.1"])
        b = P2PTransport(own_ips=["203.0.113.1"])
        # Простукивание несёт идентификатор сессии: он отсекает посторонние
        # пакеты, поэтому у участников он должен совпадать
        b.session_id = a.session_id
        a.open(bind_ip=bind_ip or "127.0.0.1")
        b.open(bind_ip="127.0.0.2")
        self.addCleanup(a.close)
        self.addCleanup(b.close)
        a.start_listening()
        b.start_connecting([(a.bind_ip, a.local_port)], timeout=timeout)
        for _ in range(int(timeout * 20) + 40):
            app.processEvents()
            if a.is_connected and b.is_connected:
                break
            time.sleep(0.05)
        return a, b

    def test_direct_channel_connects(self):
        """Канал устанавливается в обе стороны, и обе стороны знают адрес напарника."""
        a, b = self._pair()
        self.assertTrue(a.is_connected, "сторона А должна установить канал")
        self.assertTrue(b.is_connected, "сторона Б должна установить канал")
        self.assertEqual(a.peer_sockaddr[1], b.local_port)
        self.assertEqual(b.peer_sockaddr[1], a.local_port)

    def test_direct_data_both_ways(self):
        """Данные ходят напрямую в обе стороны (это и есть смысл P2P)."""
        a, b = self._pair()
        self.assertTrue(a.is_connected and b.is_connected, "нужен установленный канал")

        got_b, got_a = [], []
        b.message_received.connect(lambda m: got_b.append(m))
        a.message_received.connect(lambda m: got_a.append(m))

        self.assertTrue(a.send({"type": "T", "from": "A"}), "отправка А должна пройти")
        self.assertTrue(b.send({"type": "T", "from": "B"}), "отправка Б должна пройти")
        for _ in range(60):
            app.processEvents()
            if got_a and got_b:
                break
            time.sleep(0.02)

        self.assertTrue(got_b, "Б должен получить данные напрямую")
        self.assertTrue(got_a, "А должен получить данные напрямую")
        self.assertEqual(got_b[0].get("from"), "A")
        self.assertEqual(got_a[0].get("from"), "B")

    def test_no_self_connection(self):
        """
        Собственное эхо не должно приниматься за напарника.

        Признаки «это наш пакет»: порт отправителя равен нашему порту либо
        адрес отправителя совпадает с адресом, к которому привязан сокет.
        Регрессия: проверка стояла только в обработчике простукиваний, поэтому
        любой другой свой пакет «подключал» клиента к самому себе.
        """
        from duopy.p2p import P2PTransport
        t = P2PTransport(own_ips=["203.0.113.1"])
        t.open(bind_ip="127.0.0.1")
        self.addCleanup(t.close)
        # Пакет с нашего порта — наше эхо
        self.assertTrue(t._is_own_packet(("192.0.2.10", t.local_port)))
        # Пакет с нашего адреса привязки — тоже наше эхо
        self.assertTrue(t._is_own_packet(("127.0.0.1", t.local_port + 1)))
        # А это напарник с другого адреса и порта
        self.assertFalse(t._is_own_packet(("192.0.2.10", t.local_port + 1)))

    def test_bind_address_own_only_when_bound(self):
        """
        При обычной работе сокет слушает 0.0.0.0, и адрес отправителя сам по
        себе ничего не значит: пакеты напарника принимать можно.
        """
        from duopy.p2p import P2PTransport
        t = P2PTransport(own_ips=["203.0.113.1"])
        t.open()
        self.addCleanup(t.close)
        self.assertEqual(t.bind_ip, "0.0.0.0")
        self.assertFalse(t._is_own_packet(("127.0.0.1", t.local_port + 1)))

    def test_probe_loop_is_running(self):
        """
        Цикл простукивания обязан запускаться вместе с приёмом.

        Регрессия: поток отправки не стартовал, поэтому пакеты только
        принимались, наружу не уходили, и прямой канал не устанавливался.
        """
        from duopy.p2p import P2PTransport
        t = P2PTransport(own_ips=["203.0.113.1"])
        t.open(bind_ip="127.0.0.1")
        self.addCleanup(t.close)
        t.start_listening()
        self.assertTrue(t._probe_thread is not None and t._probe_thread.is_alive(),
                        "поток простукивания должен быть запущен")

    def test_transient_udp_error_does_not_kill_receiver(self):
        """
        Ошибки вроде WinError 10054 (ответ ICMP на закрытый порт) не должны
        прерывать приём: иначе канал «умирает» после первого же простукивания
        по недоступному адресу.
        """
        import socket as _socket
        from duopy.p2p import TRANSIENT_UDP_ERRORS
        err = OSError("connection reset")
        err.winerror = 10054
        self.assertIn(10054, TRANSIENT_UDP_ERRORS)
        self.assertIn(err.winerror, TRANSIENT_UDP_ERRORS)


class TestRelaySelection(unittest.TestCase):
    """
    Выбор сервера комнат.

    Регрессия: каждый клиент подключался к первому доступному брокеру, и в
    разных сетях это оказывались разные серверы — участники не видели друг
    друга, хотя оба «успешно вошли в комнату».
    """

    def test_both_peers_pick_same_broker(self):
        from duopy.network import broker_order_for_room
        # Код комнаты вводится вручную и может отличаться регистром/пробелами
        for code in ("DUO-1234", "duo-1234", " DUO-1234 ", "DUO-0007"):
            first = broker_order_for_room(code)[0]
            for other in ("DUO-1234", " duo-1234"):
                if other.strip().upper() == code.strip().upper():
                    self.assertEqual(first, broker_order_for_room(other)[0],
                                     f"для кода {code!r} сервер должен совпадать")

    def test_plain_and_tls_variants_available(self):
        """Для выбранного сервера пробуем шифрованный порт, затем обычный."""
        from duopy.network import broker_order_for_room
        order = broker_order_for_room("DUO-1234")
        host0 = order[0][0]
        variants = [(h, p, t) for h, p, t in order if h == host0]
        self.assertIn((host0, 8883, True), variants, "сначала шифрованный порт")
        self.assertIn((host0, 1883, False), variants, "затем обычный порт")
        self.assertEqual(order[0][1], 8883, "первым идёт шифрованный порт")

    def test_backup_broker_present(self):
        """Остальные серверы остаются запасными, чтобы не терять связь вовсе."""
        from duopy.network import broker_order_for_room, RELAY_HOSTS
        order = broker_order_for_room("DUO-1234")
        hosts = [h for h, _, _ in order]
        for host in RELAY_HOSTS:
            self.assertIn(host, hosts, f"{host} должен быть в списке")
        self.assertEqual(hosts[0], order[0][0])
        self.assertEqual(hosts[0], order[1][0], "оба порта выбранного сервера идут подряд")

    def test_room_codes_spread_across_brokers(self):
        """Коды комнат распределяются по серверам, а не липнут к одному."""
        from duopy.network import broker_order_for_room, RELAY_HOSTS
        chosen = {broker_order_for_room(f"DUO-{i:04d}")[0][0] for i in range(200)}
        self.assertEqual(len(chosen), len(RELAY_HOSTS),
                         "оба сервера должны использоваться")


class TestDirectWithoutBroker(unittest.TestCase):
    """
    Прямое соединение по коду-приглашению: посредник не участвует.

    Участники разведены по разным loopback-адресам, потому что на одной машине
    иначе они неразличимы.
    """

    def setUp(self):
        import duopy.p2p as p2p_module
        # Свой пакет отличается портом отправителя, а адрес напарника
        # отбрасывать нельзя: подменяем список «своих» адресов на заглушку
        original = p2p_module.P2PTransport.__init__

        def patched(self, parent=None, own_ips=None, session_id=""):
            original(self, parent, own_ips=["203.0.113.1"], session_id=session_id)

        p2p_module.P2PTransport.__init__ = patched
        self.addCleanup(lambda: setattr(p2p_module.P2PTransport, "__init__", original))
        self._original = original

    def _pair(self, timeout: float = 10.0):
        from duopy.network import NetworkManager
        host = NetworkManager("Хост")
        guest = NetworkManager("Гость")
        self.addCleanup(host.stop)
        self.addCleanup(guest.stop)

        ok, code = host.create_direct_invite(bind_ip="127.0.0.1",
                                             external_override=("127.0.0.1", None))
        self.assertTrue(ok, f"код-приглашение должен создаться: {code}")
        self.assertTrue(code.startswith("DP1-"), code)

        ok2, _answer = guest.connect_to_invite(code, bind_ip="127.0.0.2")
        self.assertTrue(ok2, "подключение по коду должно начаться")

        for _ in range(int(timeout * 20)):
            app.processEvents()
            if host.p2p.is_connected and guest.p2p.is_connected:
                break
            time.sleep(0.05)
        return host, guest, code

    def test_channel_established_without_broker(self):
        host, guest, _code = self._pair()
        self.assertIsNone(host.mqtt_client, "брокер не должен использоваться")
        self.assertIsNone(guest.mqtt_client, "брокер не должен использоваться")
        self.assertEqual(host.mode, "direct")
        self.assertTrue(host.p2p.is_connected, "хост должен установить канал")
        self.assertTrue(guest.p2p.is_connected, "гость должен установить канал")
        # Каждый знает адрес другого, а не свой
        self.assertNotEqual(host.p2p.peer_sockaddr[1], host.p2p.local_port,
                            "хост не должен «подключиться» к себе")
        self.assertEqual(host.p2p.peer_sockaddr[1], guest.p2p.local_port)

    def test_data_flows_both_ways(self):
        host, guest, _code = self._pair()
        self.assertTrue(host.p2p.is_connected and guest.p2p.is_connected,
                        "нужен установленный канал")

        got_guest, got_host = [], []
        guest.text_received.connect(lambda c: got_guest.append(c))
        host.text_received.connect(lambda c: got_host.append(c))

        host.send_code_update("print('от хоста')\n", "main.py")
        guest.send_code_update("print('от гостя')\n", "main.py")
        for _ in range(80):
            app.processEvents()
            if got_guest and got_host:
                break
            time.sleep(0.02)

        self.assertTrue(got_guest, "гость должен получить код")
        self.assertTrue(got_host, "хост должен получить код")
        self.assertIn("от хоста", got_guest[0])
        self.assertIn("от гостя", got_host[0])

    def test_peers_learn_names(self):
        """В прямом режиме знакомиться нужно самим: посредника нет."""
        host, guest, _code = self._pair()
        names = []
        host.connected_signal.connect(lambda n, s: names.append(n))
        guest.connected_signal.connect(lambda n, s: names.append(n))
        for _ in range(60):
            app.processEvents()
            if len(names) >= 2:
                break
            time.sleep(0.02)
        self.assertIn("Гость", names)
        self.assertIn("Хост", names)


class TestDirectCodeParsing(unittest.TestCase):
    """Разбор кода-приглашения: формат, устойчивость к опечаткам."""

    def test_parses_generated_code(self):
        from duopy.network import parse_direct_code
        for code in ("DP1-203.0.113.5-8765-ab12cd34",
                     "DP1-203.0.113.5-8765-ab12cd34-192.168.1.7"):
            targets, session = parse_direct_code(code)
            self.assertEqual(targets[0], ("203.0.113.5", 8765))
            self.assertEqual(session, "ab12cd34")

    def test_local_address_is_backup_target(self):
        from duopy.network import parse_direct_code
        targets, _ = parse_direct_code("DP1-203.0.113.5-8765-ab12cd34-192.168.1.7")
        self.assertEqual(len(targets), 2, "локальный адрес идёт запасным")
        self.assertEqual(targets[1], ("192.168.1.7", 8765))

    def test_case_and_prefix_tolerance(self):
        from duopy.network import parse_direct_code
        upper = parse_direct_code("DP1-203.0.113.5-8765-AB12CD34")
        lower = parse_direct_code("dp1-203.0.113.5-8765-ab12cd34")
        self.assertEqual(upper, lower, "регистр не должен влиять")
        # Приставку можно не копировать
        self.assertEqual(parse_direct_code("203.0.113.5-8765-ab12cd34"),
                         lower)

    def test_broken_codes_rejected(self):
        from duopy.network import parse_direct_code
        for bad in ("", "DP1-203.0.113.5", "DP1-203.0.113.5-99999-ab12cd34",
                    "DP1-203.0.113.5-abc-ab12cd34", "DP1-203.0.113.5-8765",
                    "DP1-203.0.113.5-8765-ab12cd34-192.168.1.7-лишнее"):
            with self.assertRaises(ValueError, msg=f"должен быть отклонён: {bad!r}"):
                parse_direct_code(bad)


if __name__ == "__main__":
    unittest.main()
