"""
Модуль настроек и кэширования профиля пользователя DuoPy.
Сохраняет имя, цвет, путь к интерпретатору Python и историю комнат.
"""

import os
import json
import random

CONFIG_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "DuoPy")
CONFIG_FILE = os.path.join(CONFIG_DIR, "settings.json")

PRESET_COLORS = [
    ("#4ec9b0", "Бирюзовый"),
    ("#569cd6", "Синий"),
    ("#ce9178", "Коралловый"),
    ("#dcdcaa", "Песочный"),
    ("#c586c0", "Пурпурный"),
    ("#ff9800", "Оранжевый"),
    ("#4caf50", "Зеленый"),
    ("#e06c75", "Рубиновый"),
]


def _ensure_dir():
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
    except OSError:
        pass


def load_settings() -> dict:
    """Загрузка сохраненных настроек."""
    _ensure_dir()
    if os.path.isfile(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_settings(settings: dict):
    """Сохранение настроек в JSON файл."""
    _ensure_dir()
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Ошибка сохранения настроек DuoPy: {e}")


def get_user_profile() -> tuple[str | None, str]:
    """
    Возвращает (username, user_color).
    Если имя еще ни разу не задавалось, username будет None.
    """
    settings = load_settings()
    name = settings.get("username")
    color = settings.get("user_color", PRESET_COLORS[0][0])
    return name, color


def set_user_profile(username: str, user_color: str):
    """Сохранение профиля пользователя."""
    settings = load_settings()
    settings["username"] = username.strip()
    settings["user_color"] = user_color.strip()
    save_settings(settings)


def add_recent_room(room_code: str):
    """Добавление комнаты в историю."""
    settings = load_settings()
    recent = settings.get("recent_rooms", [])
    if room_code in recent:
        recent.remove(room_code)
    recent.insert(0, room_code)
    settings["recent_rooms"] = recent[:10]
    save_settings(settings)


def get_recent_rooms() -> list[str]:
    """Получение истории комнат."""
    settings = load_settings()
    return settings.get("recent_rooms", [])
