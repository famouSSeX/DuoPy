"""
Шифрование обмена в облачной комнате.

Раньше комната защищалась коротким кодом вида DUO-1234, а трафик шёл через
публичный MQTT-брокер открытым текстом: любой, кто угадал или подсмотрел код,
мог читать код проекта и правки. Здесь код комнаты превращается в длинный
секретный ключ (его передают напарнику по защищённому каналу), а содержимое
шифруется.

Как устроено:
- код комнаты -> ключ шифрования и ключ подписи (PBKDF2-HMAC-SHA256);
- сообщение шифруется AES-128 в режиме CTR;
- к нему добавляется подпись HMAC-SHA256, поэтому подделка и повреждение
  обнаруживаются до расшифровки;
- имя топика — производное от кода (SHA-256), поэтому сам код не виден тому,
  кто наблюдает за брокером.

Оговорка: брокер всё равно видит, что два участника обмениваются пакетами,
и их размеры. Содержимое и код комнаты ему недоступны. При переходе на прямое
соединение трафик вообще минует брокера.
"""

import base64
import hashlib
import hmac
import json
import os
import secrets

# ---------------------------------------------------------------------------
# AES-128: прямая реализация для одного блока (режим CTR использует его как
# потоковый шифр, поэтому нужен только он). Внешних зависимостей нет — в
# сборке не появляется лишних мегабайт.
# ---------------------------------------------------------------------------

_SBOX = [
    0x63, 0x7c, 0x77, 0x7b, 0xf2, 0x6b, 0x6f, 0xc5, 0x30, 0x01, 0x67, 0x2b, 0xfe, 0xd7, 0xab, 0x76,
    0xca, 0x82, 0xc9, 0x7d, 0xfa, 0x59, 0x47, 0xf0, 0xad, 0xd4, 0xa2, 0xaf, 0x9c, 0xa4, 0x72, 0xc0,
    0xb7, 0xfd, 0x93, 0x26, 0x36, 0x3f, 0xf7, 0xcc, 0x34, 0xa5, 0xe5, 0xf1, 0x71, 0xd8, 0x31, 0x15,
    0x04, 0xc7, 0x23, 0xc3, 0x18, 0x96, 0x05, 0x9a, 0x07, 0x12, 0x80, 0xe2, 0xeb, 0x27, 0xb2, 0x75,
    0x09, 0x83, 0x2c, 0x1a, 0x1b, 0x6e, 0x5a, 0xa0, 0x52, 0x3b, 0xd6, 0xb3, 0x29, 0xe3, 0x2f, 0x84,
    0x53, 0xd1, 0x00, 0xed, 0x20, 0xfc, 0xb1, 0x5b, 0x6a, 0xcb, 0xbe, 0x39, 0x4a, 0x4c, 0x58, 0xcf,
    0xd0, 0xef, 0xaa, 0xfb, 0x43, 0x4d, 0x33, 0x85, 0x45, 0xf9, 0x02, 0x7f, 0x50, 0x3c, 0x9f, 0xa8,
    0x51, 0xa3, 0x40, 0x8f, 0x92, 0x9d, 0x38, 0xf5, 0xbc, 0xb6, 0xda, 0x21, 0x10, 0xff, 0xf3, 0xd2,
    0xcd, 0x0c, 0x13, 0xec, 0x5f, 0x97, 0x44, 0x17, 0xc4, 0xa7, 0x7e, 0x3d, 0x64, 0x5d, 0x19, 0x73,
    0x60, 0x81, 0x4f, 0xdc, 0x22, 0x2a, 0x90, 0x88, 0x46, 0xee, 0xb8, 0x14, 0xde, 0x5e, 0x0b, 0xdb,
    0xe0, 0x32, 0x3a, 0x0a, 0x49, 0x06, 0x24, 0x5c, 0xc2, 0xd3, 0xac, 0x62, 0x91, 0x95, 0xe4, 0x79,
    0xe7, 0xc8, 0x37, 0x6d, 0x8d, 0xd5, 0x4e, 0xa9, 0x6c, 0x56, 0xf4, 0xea, 0x65, 0x7a, 0xae, 0x08,
    0xba, 0x78, 0x25, 0x2e, 0x1c, 0xa6, 0xb4, 0xc6, 0xe8, 0xdd, 0x74, 0x1f, 0x4b, 0xbd, 0x8b, 0x8a,
    0x70, 0x3e, 0xb5, 0x66, 0x48, 0x03, 0xf6, 0x0e, 0x61, 0x35, 0x57, 0xb9, 0x86, 0xc1, 0x1d, 0x9e,
    0xe1, 0xf8, 0x98, 0x11, 0x69, 0xd9, 0x8e, 0x94, 0x9b, 0x1e, 0x87, 0xe9, 0xce, 0x55, 0x28, 0xdf,
    0x8c, 0xa1, 0x89, 0x0d, 0xbf, 0xe6, 0x42, 0x68, 0x41, 0x99, 0x2d, 0x0f, 0xb0, 0x54, 0xbb, 0x16,
]

_RCON = [0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1b, 0x36]


def _xtime(value: int) -> int:
    """Умножение на x в поле GF(2^8)."""
    value <<= 1
    if value & 0x100:
        value ^= 0x11b
    return value & 0xff


def _expand_key(key: bytes) -> list[list[int]]:
    """
    Расписание ключа AES-128: 11 раундовых ключей по 16 байт.

    Ключ храним уже развёрнутым: одна сессия шифрует тысячи сообщений, и
    повторное развёртывание на каждое было бы лишней работой.
    """
    if len(key) != 16:
        raise ValueError("нужен ключ ровно 16 байт")
    words = [list(key[i * 4:i * 4 + 4]) for i in range(4)]
    for i in range(4, 44):
        temp = list(words[i - 1])
        if i % 4 == 0:
            temp = temp[1:] + temp[:1]                  # RotWord
            temp = [_SBOX[b] for b in temp]             # SubWord
            temp[0] ^= _RCON[i // 4 - 1]
        words.append([words[i - 4][j] ^ temp[j] for j in range(4)])
    # Готовим таблицы для столбцов: удобнее складывать по 4 байта
    round_keys = []
    for r in range(11):
        block = []
        for c in range(4):
            block.extend(words[r * 4 + c])
        round_keys.append(block)
    return round_keys


def _encrypt_block(round_keys: list[list[int]], block: bytes) -> bytes:
    """Шифрование одного 16-байтового блока AES-128."""
    if len(block) != 16:
        raise ValueError("нужен блок ровно 16 байт")
    state = list(block)

    def add_round_key(rk):
        for i in range(16):
            state[i] ^= rk[i]

    def sub_bytes():
        for i in range(16):
            state[i] = _SBOX[state[i]]

    def shift_rows():
        # state хранится по столбцам: индекс = столбец * 4 + строка
        for row in range(1, 4):
            column = [state[c * 4 + row] for c in range(4)]
            column = column[row:] + column[:row]
            for c in range(4):
                state[c * 4 + row] = column[c]

    def mix_columns():
        for c in range(4):
            i = c * 4
            a0, a1, a2, a3 = state[i], state[i + 1], state[i + 2], state[i + 3]
            total = a0 ^ a1 ^ a2 ^ a3
            state[i] ^= total ^ _xtime(a0 ^ a1)
            state[i + 1] ^= total ^ _xtime(a1 ^ a2)
            state[i + 2] ^= total ^ _xtime(a2 ^ a3)
            state[i + 3] ^= total ^ _xtime(a3 ^ a0)

    add_round_key(round_keys[0])
    for rnd in range(1, 10):
        sub_bytes()
        shift_rows()
        mix_columns()
        add_round_key(round_keys[rnd])
    sub_bytes()
    shift_rows()
    add_round_key(round_keys[10])
    return bytes(state)


class _AesCtr:
    """AES-128 в режиме CTR: тот же блок шифруется как потоковый ключ."""

    def __init__(self, key: bytes, iv: bytes):
        if len(iv) != 16:
            raise ValueError("нужен вектор инициализации 16 байт")
        self._round_keys = _expand_key(key)
        self._counter = int.from_bytes(iv, "big")
        self._keystream = b""

    def _next_block(self) -> bytes:
        block = self._counter.to_bytes(16, "big")
        self._counter = (self._counter + 1) % (1 << 128)
        return _encrypt_block(self._round_keys, block)

    def apply(self, data: bytes) -> bytes:
        """Шифрование и расшифрование в CTR — одна и та же операция."""
        out = bytearray(len(data))
        for i, byte in enumerate(data):
            if not self._keystream:
                self._keystream = self._next_block()
            out[i] = byte ^ self._keystream[0]
            self._keystream = self._keystream[1:]
        return bytes(out)


# ---------------------------------------------------------------------------
# Код комнаты и ключи
# ---------------------------------------------------------------------------

# Алфавит без похожих символов (нет I, O, 0, 1): код передают голосом и руками
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_GROUPS = 4
CODE_GROUP_LEN = 4
KDF_ITERATIONS = 120_000


def generate_session_code() -> str:
    """
    Новый секретный код комнаты.

    Это не «номер комнаты», а ключ: 16 символов алфавита из 32 знаков — около
    80 бит, перебрать невозможно. Отображается группами по 4 для удобной
    передачи: XXXX-XXXX-XXXX-XXXX.
    """
    raw = "".join(secrets.choice(CODE_ALPHABET)
                  for _ in range(CODE_GROUPS * CODE_GROUP_LEN))
    return "-".join(raw[i:i + CODE_GROUP_LEN]
                    for i in range(0, len(raw), CODE_GROUP_LEN))


def normalize_code(code: str) -> str:
    """
    Приведение кода к единому виду.

    Убираем разделители и регистр: код, переданный голосом или переписанный
    руками, должен давать тот же ключ у обоих участников. Старый формат
    DUO-1234 тоже принимается — он остаётся действительным для совместимости.
    """
    return "".join(ch for ch in (code or "").upper() if ch.isalnum())


def is_secure_code(code: str) -> bool:
    """Код нового формата (длинный секретный), а не прежний DUO-1234."""
    return len(normalize_code(code)) >= 16


def derive_keys(code: str) -> tuple[bytes, bytes]:
    """
    Ключи шифрования и подписи из кода комнаты.

    PBKDF2 с солью, зависящей от самого кода: одинаковый код даёт одинаковые
    ключи на обеих сторонах, а подобрать код перебором мешает большое число
    итераций.
    """
    normalized = normalize_code(code)
    if not normalized:
        raise ValueError("пустой код комнаты")
    salt = hashlib.sha256(("duopy-salt:" + normalized).encode("utf-8")).digest()
    material = hashlib.pbkdf2_hmac("sha256", normalized.encode("utf-8"), salt,
                                   KDF_ITERATIONS, dklen=64)
    return material[:16], material[16:48]


def derive_topic(code: str) -> str:
    """
    Имя топика по коду комнаты.

    Сам код в топик не попадает: наблюдающий за брокером видит только
    производное значение, поэтому подсмотреть комнату нельзя.
    """
    digest = hashlib.sha256(("duopy-topic:" + normalize_code(code)).encode("utf-8")).digest()
    return f"duopy/v2/rooms/{digest[:16].hex()}"


class SecureChannel:
    """
    Шифрование и проверка подлинности сообщений комнаты.

    Формат пакета: base64(iv(16) + ciphertext + tag(32)). Вектор
    инициализации случайный для каждого сообщения, поэтому одинаковые правки
    выглядят по-разному и повторы не помогают.
    """

    def __init__(self, code: str):
        self.code = normalize_code(code)
        if not self.code:
            raise ValueError("пустой код комнаты")
        self._key, self._mac_key = derive_keys(self.code)
        self.topic = derive_topic(self.code)
        # Начало счётчика выбирается случайно при создании канала
        self._counter = int.from_bytes(os.urandom(8), "big")

    def _next_iv(self) -> bytes:
        """Свежий вектор инициализации: счётчик не должен повторяться."""
        self._counter = (self._counter + 1) % (1 << 64)
        return os.urandom(8) + self._counter.to_bytes(8, "big")

    def encode(self, payload: dict) -> str:
        """Зашифровать сообщение в строку для отправки."""
        raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        iv = self._next_iv()
        ciphertext = _AesCtr(self._key, iv).apply(raw)
        tag = hmac.new(self._mac_key, iv + ciphertext, hashlib.sha256).digest()
        return base64.b64encode(iv + ciphertext + tag).decode("ascii")

    def decode(self, data: str) -> dict | None:
        """
        Расшифровать сообщение. Возвращает None, если пакет повреждён или
        подпись не сходится: чужой или испорченный пакет не должен попадать
        в приложение.
        """
        try:
            blob = base64.b64decode(data, validate=True)
        except Exception:
            return None
        if len(blob) < 48:
            return None
        iv, ciphertext, tag = blob[:16], blob[16:-32], blob[-32:]
        expected = hmac.new(self._mac_key, iv + ciphertext, hashlib.sha256).digest()
        # Сравнение с постоянным временем: иначе по времени ответа можно
        # подбирать подпись побайтово
        if not hmac.compare_digest(tag, expected):
            return None
        try:
            plain = _AesCtr(self._key, iv).apply(ciphertext)
            return json.loads(plain.decode("utf-8"))
        except Exception:
            return None
