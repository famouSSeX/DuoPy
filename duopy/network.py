"""
Сетевой стек DuoPy с поддержкой глобального онлайн-подключения через интернет:
1. Режим онлайн-комнат по коду (Cloud Relay): работает между любыми странами и сетями без белых IP и без настройки роутеров.
2. Режим прямого TCP подключения (Local IP / LAN): для локальных сетей и оффлайн-работы.
"""

import socket
import struct
import json
import threading
import time
import random
import zlib
import base64
import hashlib
import os
from datetime import datetime
from PyQt6.QtCore import QObject, pyqtSignal

from duopy.p2p import P2PTransport

try:
    import paho.mqtt.client as mqtt
    HAS_MQTT = True
except ImportError:
    HAS_MQTT = False

# Глобальные брокеры: сначала шифрованный TLS, затем обычный порт как запасной
# вариант (в некоторых сетях исходящий 8883 закрыт). Формат: (хост, порт, TLS).
DEFAULT_RELAY_BROKERS = [
    ("broker.emqx.io", 8883, True),
    ("broker.hivemq.com", 8883, True),
    ("broker.emqx.io", 1883, False),
    ("broker.hivemq.com", 1883, False),
]

# Уникальные серверы из списка (без повторов транспорта)
RELAY_HOSTS = ["broker.emqx.io", "broker.hivemq.com"]


def broker_order_for_room(room_code: str) -> list[tuple[str, int, bool]]:
    """
    Порядок подключения к серверам комнат, одинаковый у обоих участников.

    Сервер выбирается по коду комнаты, а не по тому, кто первым ответил.
    Раньше каждый клиент подключался к первому доступному брокеру: в разных
    сетях это оказывались РАЗНЫЕ серверы, и участники не видели друг друга
    вовсе, хотя оба «успешно вошли в комнату». Теперь код комнаты однозначно
    определяет сервер, поэтому стороны всегда встречаются.

    Порядок начинается с выбранного сервера (сначала шифрованный порт, затем
    обычный), а остальные идут запасными — на случай, если основной недоступен.
    """
    code = (room_code or "").strip().upper()
    primary = hashlib.blake2b(code.encode("utf-8"), digest_size=8).digest()[0] % len(RELAY_HOSTS)
    ordered = [RELAY_HOSTS[primary]] + [h for i, h in enumerate(RELAY_HOSTS) if i != primary]
    result = []
    for host in ordered:
        result.append((host, 8883, True))
        result.append((host, 1883, False))
    return result


# ---------------------------------------------------------------------------
# Прямое соединение по коду-приглашению (без посредника вообще)
# ---------------------------------------------------------------------------

# Прямое соединение по коду-приглашению: порт выбирается свободный, а
# фактический адрес попадает в код, поэтому фиксировать порт не нужно (иначе
# две копии на одной машине мешали бы друг другу).
DIRECT_CODE_PREFIX = "DP1-"
# Сколько ждём ответа напарника, прежде чем сообщить о неудаче
DIRECT_TIMEOUT = 25.0


def parse_direct_code(code: str) -> tuple[list[tuple[str, int]], str]:
    """
    Разбор кода-приглашения.

    Формат: DP1-<адрес>-<порт>-<сессия>[-<локальный адрес>]. Частей после
    приставки три или четыре, поэтому разбираем их по количеству: программа
    всегда формирует код сама, и структура известна точно.

    Локальный адрес добавляем как запасной вариант: если напарник оказался
    в той же сети, соединение установится по нему, без выхода в интернет.
    """
    text = (code or "").strip().replace(" ", "").replace("\n", "")
    if not text:
        raise ValueError("Код пустой")

    parts = text.split("-")
    if parts and parts[0].upper() == DIRECT_CODE_PREFIX.rstrip("-"):
        parts = parts[1:]

    if len(parts) not in (3, 4):
        raise ValueError("Код неполный: скопируйте его целиком")

    host = parts[0]
    port_text = parts[1]
    session_id = parts[2].lower()
    local_ip = parts[3] if len(parts) == 4 else None

    if not host:
        raise ValueError("В коде не указан адрес")
    if not port_text.isdigit():
        raise ValueError("В коде не указан порт")
    port = int(port_text)
    if not (0 < port < 65536):
        raise ValueError("Некорректный порт в коде")
    if not session_id:
        raise ValueError("В коде отсутствует идентификатор сессии")
    if not all(ch in "0123456789abcdef" for ch in session_id):
        raise ValueError("Испорчен идентификатор сессии в коде")

    targets = [(host, port)]
    if local_ip and local_ip != host:
        targets.append((local_ip, port))
    return targets, session_id

# Максимальный размер одного кадра TCP-протокола (защита от зависания на
# некорректной или враждебной длине кадра)
MAX_FRAME_BYTES = 32 * 1024 * 1024

# С какого размера сжимать сообщение: мелкие пакеты дешевле отправить как есть
COMPRESS_THRESHOLD_BYTES = 2048


def get_local_ip() -> str:
    """Получение локального IP-адреса в сети."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
    except Exception:
        try:
            ip = socket.gethostbyname(socket.gethostname())
        except Exception:
            ip = "127.0.0.1"
    finally:
        s.close()
    return ip


def generate_room_code() -> str:
    """Генерация легко запоминающегося кода комнаты."""
    digits = random.randint(1000, 9999)
    return f"DUO-{digits}"


class NetworkManager(QObject):
    """Сетевой менеджер сессии совместного программирования."""

    # Сигналы для UI
    connected_signal = pyqtSignal(str, str)               # (peer_name, room_or_ip)
    disconnected_signal = pyqtSignal(str)                # (reason)
    text_received = pyqtSignal(str)                      # (code_text)
    file_code_received = pyqtSignal(str, str)            # (code_text, file_name)
    cursor_received = pyqtSignal(str, str, str, int, int, int, int, int, str) # (uid, name, color, line, col, pos, sel_start, sel_end, file_name)
    peer_file_changed = pyqtSignal(str, str)             # (peer_name, file_name)
    run_code_requested = pyqtSignal()
    stop_code_requested = pyqtSignal()
    output_received = pyqtSignal(str, bool)              # (text, is_stderr)
    execution_finished_received = pyqtSignal(int, float) # (return_code, duration)
    error_highlight_received = pyqtSignal(int, str)      # (line_number, message)
    chat_received = pyqtSignal(str, str, str)            # (sender, message, time_str)
    status_signal = pyqtSignal(str)                      # (status_message)
    profile_updated = pyqtSignal(str, str)               # (peer_name, peer_color)
    sync_requested_signal = pyqtSignal()                 # Запрос на отправку текущего кода новому участнику
    stdin_received = pyqtSignal(str)                     # Ввод данных для input() от напарника
    project_tree_received = pyqtSignal(str, list)        # (project_name, files_list)
    file_content_requested = pyqtSignal(str)            # (rel_path)
    file_content_received = pyqtSignal(str, str)        # (rel_path, content)
    file_create_requested = pyqtSignal(str)             # (rel_path)
    file_delete_requested = pyqtSignal(str)             # (rel_path)
    file_save_requested = pyqtSignal(str, str)          # (rel_path, content)
    # Качество связи: (задержка в мс, КБ/с входящие, КБ/с исходящие)
    metrics_updated = pyqtSignal(float, float, float)
    # Прямое соединение с напарником установлено: (адрес, «через что»)
    transport_changed = pyqtSignal(str, str)
    # Правка фрагментом: (позиция, сколько символов заменяется, новый фрагмент,
    # имя файла, метка версии текста, на которой основана правка)
    code_edit_received = pyqtSignal(int, int, str, str, str)
    # Пропущен номер правки — часть изменений потерялась в сети
    edit_loss_detected = pyqtSignal()

    def __init__(self, username="Разработчик", user_color="#4ec9b0", parent=None):
        super().__init__(parent)
        self.username = username
        self.user_color = user_color
        self.user_id = f"usr_{int(time.time() * 1000)}_{random.randint(100, 999)}"

        # Измерение качества связи
        self.latency_ms = 0.0            # последняя измеренная задержка
        self.latency_samples: list[float] = []   # скользящее окно для среднего
        self.bytes_in = 0                # всего принято, байт
        self.bytes_out = 0               # всего отправлено, байт
        self.rate_in_bps = 0.0           # текущая скорость приёма, байт/с
        self.rate_out_bps = 0.0          # текущая скорость отправки, байт/с
        self.rtt_peak_ms = 0.0           # худшая задержка за сессию
        self.throughput_peak_bps = 0.0   # пиковая скорость текста, байт/с
        self._stats_prev_in = 0
        self._stats_prev_out = 0
        self._stats_prev_time = 0.0
        self._ping_sent_at = 0.0
        self._ping_pending = False
        self._ping_seq = 0
        # Номера правок: свои для отправки, чужие — для контроля потерь
        self._edit_seq = 0
        self._peer_edit_seq = 0

        # Прямое соединение (UDP hole punching). Объект создаётся в главном
        # потоке при облачном подключении или при прямом соединении по коду:
        # Qt-сигналы из его потока приёма должны доставляться в GUI, а
        # создание QObject из сетевого потока ломает эту доставку.
        self.p2p: P2PTransport | None = None
        self.transport = "relay"          # "relay" или "p2p"
        self._p2p_accepted = False        # ответ на предложение прямого канала уже отправлен
        # Клиент брокера: прямому соединению он не нужен, но stop() должен
        # уметь его закрыть даже до подключения к комнате
        self.mqtt_client: mqtt.Client | None = None

        # Режимы сети: "cloud", "tcp" или "direct"
        self.mode: str | None = None
        self.room_code: str | None = None

        self.is_host = False
        self.is_connected = False
        self.running = False
        self.initial_joined = False

        # TCP ресурсы
        self.server_socket: socket.socket | None = None
        self.client_socket: socket.socket | None = None
        self.peer_socket: socket.socket | None = None
        self.tcp_thread: threading.Thread | None = None
        self.send_lock = threading.Lock()

    def _ensure_p2p(self, session_id: str = "") -> P2PTransport:
        """Создать (или пересоздать) транспорт прямого канала с обработчиками."""
        if self.p2p is not None:
            self.p2p.close()
        self.p2p = P2PTransport(self, session_id=session_id)
        self.p2p.connected_signal.connect(self._on_p2p_connected)
        self.p2p.failed_signal.connect(self._on_p2p_failed)
        self.p2p.message_received.connect(self._on_p2p_message)
        self.p2p.probe_received.connect(self._on_p2p_probe)
        return self.p2p


    # =========================================================================
    # РЕЖИМ 1: ОБЛАЧНЫЕ КОМНАТЫ ЧЕРЕЗ ИНТЕРНЕТ (Между любыми странами и NAT)
    # =========================================================================

    def start_cloud_room(self, room_code: str | None = None, is_creator: bool = True):
        """Создание или подключение к глобальной онлайн-комнате."""
        self.stop()
        if not HAS_MQTT:
            self.status_signal.emit("Ошибка: библиотека paho-mqtt не установлена!")
            return

        self.mode = "cloud"
        self.is_host = is_creator
        self.running = True
        self.room_code = (room_code or generate_room_code()).strip().upper()

        threading.Thread(target=self._cloud_worker, daemon=True).start()

    def _cloud_worker(self):
        """Подключение к глобальному серверу ретрансляции."""
        topic = f"duopy/v1/rooms/{self.room_code}"
        self.status_signal.emit(f"Подключение к серверу комнат... Код: {self.room_code}")

        connected_to_broker = False
        for host, port, use_tls in broker_order_for_room(self.room_code):
            client = None
            try:
                # Уникальный client_id с энтропией: по одному user_id (метка времени
                # в миллисекундах) два клиента могли получить одинаковый идентификатор,
                # и брокер выкидывал одного из них.
                client = mqtt.Client(
                    mqtt.CallbackAPIVersion.VERSION2,
                    client_id=f"duopy_{self.user_id}_{random.randint(1000, 9999)}"
                )
                # Раздельные учётные записи: публичные брокеры требуют
                # аутентификацию, а разные логины не дают клиентам выбивать друг друга.
                client.username_pw_set(
                    "duopy_creator" if self.is_host else "duopy_guest",
                    "duopy2024"
                )
                if use_tls:
                    client.tls_set()
                self.mqtt_client = client
                client.reconnect_delay_set(min_delay=1, max_delay=10)
                client.max_inflight_messages_set(100)

                def on_connect(c, userdata, flags, rc, properties=None):
                    if rc == 0 or (hasattr(rc, "is_failure") and not rc.is_failure):
                        c.subscribe(topic, qos=1)
                        self.is_connected = True

                        is_first = not self.initial_joined
                        self.initial_joined = True

                        if is_first:
                            self._cloud_send({
                                "type": "JOIN",
                                "sender_id": self.user_id,
                                "name": self.username,
                                "color": self.user_color,
                                "is_creator": self.is_host
                            })
                            if self.is_host:
                                self.status_signal.emit(f"Комната {self.room_code} готова! Напарник может войти по этому коду.")
                                # Готовим прямой канал: когда напарник войдёт,
                                # адресами обменяемся через брокер
                                self.start_p2p()
                            else:
                                self.status_signal.emit(f"Вы вошли в комнату {self.room_code}!")
                                self._cloud_send({
                                    "type": "REQUEST_SYNC",
                                    "sender_id": self.user_id
                                })
                                # Пробуем перейти на прямое соединение
                                self.start_p2p()
                        else:
                            # Тихое восстановление связи после секундного перерыва сети
                            self.status_signal.emit(f"🟢 Связь с комнатой {self.room_code} восстановлена")

                def on_message(c, userdata, msg):
                    try:
                        payload = json.loads(msg.payload.decode('utf-8'))
                        sender = payload.get("sender_id")
                        if sender == self.user_id:
                            return
                        self._dispatch_message(payload)
                    except Exception as e:
                        print(f"Ошибка декодирования MQTT: {e}")

                def on_disconnect(c, userdata, flags, rc, properties=None):
                    self.is_connected = False
                    if self.running:
                        self.status_signal.emit(f"🟡 Восстановление связи с комнатой {self.room_code}...")

                client.on_connect = on_connect
                client.on_message = on_message
                client.on_disconnect = on_disconnect

                client.connect(host, port, keepalive=60)
                client.loop_start()
                connected_to_broker = True
                # Сообщаем, шифруется ли канал: на публичном брокере без TLS
                # содержимое комнаты доступно любому подписчику топика.
                if not use_tls:
                    self.status_signal.emit(
                        f"⚠️ Шифрование недоступно, используется незащищённое соединение с {host}"
                    )
                break
            except Exception as e:
                print(f"Не удалось подключиться к брокеру {host}:{port}: {e}")
                # Освобождаем сетевые ресурсы неудачной попытки, иначе потоки
                # и сокеты оставались жить до конца процесса.
                if client is not None:
                    try:
                        client.loop_stop()
                        client.disconnect()
                    except Exception:
                        pass
                continue

        if not connected_to_broker:
            self.status_signal.emit("Не удалось соединиться с облачным сервером комнат. Проверьте интернет.")
            self.running = False
            self.is_connected = False
            self.disconnected_signal.emit("Ошибка связи с сервером")

    def _cloud_send(self, data: dict):
        """Отправка сообщения в топик текущей комнаты."""
        if not self.mqtt_client or not self.room_code:
            return True
        data["sender_id"] = self.user_id
        topic = f"duopy/v1/rooms/{self.room_code}"

        # Для курсора и правок текста используем QoS 0: доставка без
        # подтверждения. С QoS 1 брокер подтверждает каждое сообщение, то есть
        # добавляет полный круг задержки (~300 мс на публичном брокере) к
        # каждому нажатию, а набор становился «вязким». Правка несёт метку
        # версии, поэтому потерянный фрагмент безопасен: следующий фрагмент
        # или полная синхронизация восстановят состояние.
        msg_type = data.get("type")
        qos = 0 if msg_type in ("CURSOR_MOVE", "CODE_UPDATE", "CODE_EDIT") else 1

        try:
            raw = json.dumps(data)
            self.mqtt_client.publish(topic, raw, qos=qos)
            if msg_type not in ("PING", "PONG"):
                self.bytes_out += len(raw.encode("utf-8"))
            return True
        except Exception as e:
            print(f"Ошибка отправки в MQTT: {e}")
            return False

    # =========================================================================
    # РЕЖИМ 2: ПРЯМОЕ TCP ПОДКЛЮЧЕНИЕ (Локальная сеть / LAN / IP)
    # =========================================================================

    def start_host(self, port: int = 8765):
        """Запуск локального TCP сервера."""
        self.stop()
        self.mode = "tcp"
        self.is_host = True
        self.running = True

        try:
            self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.server_socket.bind(('0.0.0.0', port))
            self.server_socket.listen(1)

            local_ip = get_local_ip()
            self.status_signal.emit(f"Локальный сервер запущен: {local_ip}:{port}")

            self.tcp_thread = threading.Thread(target=self._tcp_host_worker, daemon=True)
            self.tcp_thread.start()
        except Exception as e:
            self.status_signal.emit(f"Ошибка запуска сервера: {e}")
            self.stop()

    def _tcp_host_worker(self):
        """
        Обслуживание входящих подключений.

        Сервер продолжает слушать после ухода напарника: раньше цикл завершался
        после первого accept(), поэтому одно случайное подключение (или сканер
        портов) навсегда блокировало вход настоящему напарнику.
        """
        try:
            while self.running:
                try:
                    conn, addr = self.server_socket.accept()
                except OSError:
                    break
                except Exception:
                    break

                # Пока заняты текущим напарником — вежливо отказываем
                if self.is_connected:
                    try:
                        conn.close()
                    except Exception:
                        pass
                    continue

                try:
                    served = self._tcp_serve_peer(conn, addr[0])
                finally:
                    try:
                        conn.close()
                    except Exception:
                        pass
                    if self.peer_socket is conn:
                        self.peer_socket = None
                    self.is_connected = False
                    # Сообщаем об уходе только если напарник действительно был:
                    # иначе случайный коннект (сканер портов) выглядел бы как
                    # «напарник отключился».
                    if served and self.running:
                        self.disconnected_signal.emit("Напарник отключился")
        except Exception as e:
            if self.running:
                self.status_signal.emit(f"Ошибка соединения: {e}")
        finally:
            self.is_connected = False

    def _tcp_serve_peer(self, conn: socket.socket, peer_ip: str) -> bool:
        """
        Приветствие и цикл чтения для одного подключившегося напарника.
        Возвращает True, если напарник реально начал сессию.
        """
        self.peer_socket = conn
        self.is_connected = True

        self._tcp_send({
            "type": "HANDSHAKE",
            "sender_id": self.user_id,
            "name": self.username,
            "color": self.user_color,
            "is_host": True
        })

        # Если после подключения клиент молчит (сканер портов, оборванный
        # коннект), не держим сервер занятым: ждём первый байт ограниченное время.
        try:
            conn.settimeout(5.0)
            if not conn.recv(1, socket.MSG_PEEK):
                self.status_signal.emit(f"Подключение с {peer_ip} закрылось без данных")
                return False
        except (socket.timeout, OSError):
            self.status_signal.emit(f"Подключение с {peer_ip} не прислало данных")
            return False
        finally:
            try:
                conn.settimeout(None)
            except OSError:
                pass

        self.status_signal.emit(f"Напарник подключился с {peer_ip}!")
        self._tcp_listen_loop(conn)
        return True

    def connect_to_host(self, host_ip: str, port: int = 8765):
        """Подключение к локальному TCP серверу."""
        self.stop()
        self.mode = "tcp"
        self.is_host = False
        self.running = True

        self.tcp_thread = threading.Thread(
            target=self._tcp_client_worker, args=(host_ip, port), daemon=True
        )
        self.tcp_thread.start()

    def _tcp_client_worker(self, host_ip: str, port: int):
        was_connected = False
        try:
            self.status_signal.emit(f"Подключение к {host_ip}:{port}...")
            self.client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.client_socket.settimeout(10.0)
            self.client_socket.connect((host_ip, port))
            self.client_socket.settimeout(None)

            self.peer_socket = self.client_socket
            self.is_connected = True
            was_connected = True

            self._tcp_send({
                "type": "HANDSHAKE",
                "sender_id": self.user_id,
                "name": self.username,
                "color": self.user_color,
                "is_host": False
            })
            self._tcp_send({
                "type": "REQUEST_SYNC",
                "sender_id": self.user_id
            })

            self.status_signal.emit(f"Успешно подключено к {host_ip}:{port}!")
            self._tcp_listen_loop(self.client_socket)
        except Exception as e:
            self.status_signal.emit(f"Не удалось подключиться: {e}")
            # Сигнал об обрыве шлём отсюда, только если цикл чтения не запустился;
            # иначе о потере связи сообщали бы оба места и чат получал дубль.
            if not was_connected:
                self.disconnected_signal.emit(str(e))
        finally:
            self.is_connected = False

    def _tcp_listen_loop(self, sock: socket.socket):
        try:
            while self.running:
                header = self._recv_exact(sock, 4)
                if not header:
                    break

                length = struct.unpack('>I', header)[0]
                if length <= 0 or length > MAX_FRAME_BYTES:
                    # Некорректная длина кадра: recv_exact ждал бы данные
                    # бесконечно, а поток оставался висеть навсегда.
                    print(f"[DuoPy] Некорректная длина кадра TCP: {length}")
                    break
                payload_data = self._recv_exact(sock, length)
                if not payload_data:
                    break

                try:
                    payload = json.loads(payload_data.decode('utf-8'))
                    # Служебные ping/pong в статистику скорости не входят
                    if payload.get("type") not in ("PING", "PONG"):
                        self.bytes_in += len(payload_data)
                    self._dispatch_message(payload)
                except Exception as e:
                    print(f"Ошибка парсинга TCP: {e}")
        finally:
            self.is_connected = False

    def _recv_exact(self, sock: socket.socket, n_bytes: int) -> bytes | None:
        data = bytearray()
        while len(data) < n_bytes:
            packet = sock.recv(n_bytes - len(data))
            if not packet:
                return None
            data.extend(packet)
        return bytes(data)

    def _tcp_send(self, data: dict):
        if not self.peer_socket or not self.is_connected:
            return
        data["sender_id"] = self.user_id
        with self.send_lock:
            try:
                raw_json = json.dumps(data).encode('utf-8')
                header = struct.pack('>I', len(raw_json))
                self.peer_socket.sendall(header + raw_json)
                if data.get("type") not in ("PING", "PONG"):
                    self.bytes_out += len(raw_json)
            except Exception as e:
                print(f"Ошибка отправки данных в TCP сокет: {e}")
                self.is_connected = False

    # =========================================================================
    # КАЧЕСТВО СВЯЗИ: ЗАДЕРЖКА (PING) И СКОРОСТЬ ПЕРЕДАЧИ ТЕКСТА
    # =========================================================================

    def send_ping(self):
        """
        Отправка метки времени для измерения задержки связи.

        Метка уходит напарнику и возвращается в PONG, поэтому в расчёт входит
        именно круговое время (RTT), без зависимости от синхронизации часов.
        """
        if not self.is_connected:
            return False
        self._ping_seq += 1
        self._ping_sent_at = time.time()
        self._ping_pending = True
        self._broadcast({"type": "PING", "ts": self._ping_sent_at, "seq": self._ping_seq})
        return True

    def _on_pong(self, msg: dict):
        """Обработка ответа: считаем задержку и публикуем метрики."""
        if not self._ping_pending:
            return
        try:
            sent_at = float(msg.get("ts") or self._ping_sent_at)
        except (TypeError, ValueError):
            sent_at = self._ping_sent_at
        rtt_ms = max(0.0, (time.time() - sent_at) * 1000.0)
        self._ping_pending = False

        self.latency_ms = rtt_ms
        self.latency_samples.append(rtt_ms)
        # Скользящее окно: последние 20 измерений — сглаживает выбросы
        if len(self.latency_samples) > 20:
            self.latency_samples.pop(0)
        self.rtt_peak_ms = max(self.rtt_peak_ms, rtt_ms)

    @property
    def latency_avg_ms(self) -> float:
        """Средняя задержка по последним измерениям."""
        if not self.latency_samples:
            return 0.0
        return sum(self.latency_samples) / len(self.latency_samples)

    def update_rates(self) -> tuple[float, float]:
        """
        Пересчёт текущих скоростей по счётчикам байтов.

        Возвращает (КБ/с принято, КБ/с отправлено) и публикует метрики в UI.
        """
        now = time.time()
        prev = self._stats_prev_time or now
        # Окно замера не может быть короче 50 мс: иначе при двух опросах подряд
        # знаменатель около нуля и скорость раздувается до бессмысленных величин.
        elapsed = max(0.05, now - prev)
        if now - prev < 0.05:
            # Слишком частый опрос: не двигаем окно, иначе дельта «съедается»
            self._stats_prev_time = prev
        else:
            self._stats_prev_time = now

        delta_in = max(0, self.bytes_in - self._stats_prev_in)
        delta_out = max(0, self.bytes_out - self._stats_prev_out)
        self._stats_prev_in = self.bytes_in
        self._stats_prev_out = self.bytes_out

        # Сглаживание: без него скорость падала бы до нуля сразу после набора,
        # и показатель «КБ/с» не успевал бы ничего показать. Затухание за
        # секунду — примерно 40% от прежнего значения.
        DECAY = 0.4
        instant_in = delta_in / elapsed
        instant_out = delta_out / elapsed
        self.rate_in_bps = instant_in if instant_in > self.rate_in_bps else \
            self.rate_in_bps * DECAY + instant_in * (1 - DECAY)
        self.rate_out_bps = instant_out if instant_out > self.rate_out_bps else \
            self.rate_out_bps * DECAY + instant_out * (1 - DECAY)
        if self.rate_in_bps < 8:
            self.rate_in_bps = 0.0
        if self.rate_out_bps < 8:
            self.rate_out_bps = 0.0

        self.throughput_peak_bps = max(self.throughput_peak_bps,
                                       self.rate_in_bps, self.rate_out_bps)
        self.metrics_updated.emit(self.latency_ms, self.rate_in_bps, self.rate_out_bps)
        return self.rate_in_bps / 1024.0, self.rate_out_bps / 1024.0

    def reset_metrics(self):
        """Сброс измерений (при новом подключении)."""
        self.latency_ms = 0.0
        self.latency_samples.clear()
        self.bytes_in = self.bytes_out = 0
        self.rate_in_bps = self.rate_out_bps = 0.0
        self.rtt_peak_ms = 0.0
        self.throughput_peak_bps = 0.0
        self._stats_prev_in = self._stats_prev_out = 0
        self._stats_prev_time = time.time()
        self._ping_pending = False
        self._ping_seq = 0

    # =========================================================================
    # ОБЩАЯ МАРШРУТИЗАЦИЯ И ОТПРАВКА СООБЩЕНИЙ
    # =========================================================================

    def _dispatch_message(self, msg: dict):
        """Маршрутизация сообщений (для обоих режимов: Cloud и TCP)."""
        if msg.get("type") == "PACKED":
            msg = self._unpack(msg)
        msg_type = msg.get("type")

        if msg_type in ("HANDSHAKE", "JOIN"):
            pname = msg.get("name", "Напарник")
            pcolor = msg.get("color", "#ff9800")
            source = f"Комната {self.room_code}" if self.mode == "cloud" else "Прямое подключение"
            self.connected_signal.emit(pname, source)

            # Если к нам присоединились, отправляем приветствие в ответ
            if msg_type == "JOIN" and not msg.get("is_reply"):
                self._broadcast({
                    "type": "JOIN",
                    "name": self.username,
                    "color": self.user_color,
                    "is_reply": True
                })
                # Как только напарник появился, предлагаем прямой канал:
                # дальше правки пойдут напрямую, минуя посредника
                if self.mode == "cloud":
                    self.start_p2p()

        elif msg_type == "REQUEST_SYNC":
            # Напарник попросил актуальный код
            self.sync_requested_signal.emit()

        elif msg_type == "P2P_HELLO":
            # Напарник сообщил адреса для прямого соединения
            self._handle_p2p_hello(msg)

        elif msg_type == "P2P_ACCEPT":
            # Напарник принял предложение и прислал свои адреса
            self._handle_p2p_accept(msg)

        elif msg_type == "P2P_ASK":
            if self.p2p is not None:
                self._cloud_send({
                    "type": "P2P_HELLO",
                    "ip": get_local_ip(),
                    "port": self.p2p.local_port,
                    "ext": list(self.p2p.external_endpoint) if self.p2p.external_endpoint else None,
                })

        elif msg_type == "PING":
            # Отвечаем сразу: метка времени возвращается обратно, и отправитель
            # измеряет круговое время. Ответ служебный, в статистику не входит.
            self._broadcast({
                "type": "PONG",
                "ts": msg.get("ts", 0),
                "seq": msg.get("seq", 0),
            })

        elif msg_type == "PONG":
            self._on_pong(msg)

        elif msg_type == "CODE_UPDATE":
            code = msg.get("code", "")
            fname = msg.get("file", "")
            self.text_received.emit(code)
            self.file_code_received.emit(code, fname)

        elif msg_type == "CODE_EDIT":
            # Правка фрагментом: позиция, сколько удалить, что вставить, метка
            # версии и номер правки (по нему ловится потеря пакета)
            pos = msg.get("pos", 0)
            removed = msg.get("removed", 0)
            insert = msg.get("insert", "")
            fname = msg.get("file", "")
            base = msg.get("base", "")
            seq = int(msg.get("seq", 0) or 0)

            # Правки идут без подтверждения доставки (QoS 0) ради скорости,
            # поэтому пропуск номера означает потерю: сообщаем наверх, чтобы
            # стороны выровнялись полной синхронизацией
            if seq and self._peer_edit_seq and seq != self._peer_edit_seq + 1:
                print(f"[DuoPy] Потеряна правка (было {self._peer_edit_seq}, пришло {seq})")
                self.edit_loss_detected.emit()
                return
            if seq:
                self._peer_edit_seq = seq

            self.code_edit_received.emit(int(pos), int(removed), insert, fname, base)

        elif msg_type == "CURSOR_MOVE":
            uid = msg.get("sender_id", "")
            name = msg.get("name", "")
            color = msg.get("color", "")
            line = msg.get("line", 1)
            col = msg.get("col", 0)
            pos = msg.get("pos", 0)
            sel_start = msg.get("sel_start", 0)
            sel_end = msg.get("sel_end", 0)
            fname = msg.get("file", "")
            self.cursor_received.emit(uid, name, color, line, col, pos, sel_start, sel_end, fname)

        elif msg_type == "ACTIVE_FILE":
            name = msg.get("name", "Напарник")
            fname = msg.get("file", "")
            self.peer_file_changed.emit(name, fname)

        elif msg_type == "PROFILE_UPDATE":
            name = msg.get("name", "Напарник")
            color = msg.get("color", "")
            # Отдельный сигнал: раньше апдейт профиля шёл как connected_signal,
            # и смена имени выглядела как новое подключение с полным ре-синком,
            # который затирал правки напарника.
            self.profile_updated.emit(name, color)

        elif msg_type == "RUN_CODE":
            self.run_code_requested.emit()

        elif msg_type == "STOP_CODE":
            self.stop_code_requested.emit()

        elif msg_type == "OUTPUT":
            text = msg.get("text", "")
            is_err = msg.get("is_err", False)
            self.output_received.emit(text, is_err)

        elif msg_type == "EXEC_FINISHED":
            code = msg.get("code", 0)
            duration = msg.get("duration", 0.0)
            self.execution_finished_received.emit(code, duration)

        elif msg_type == "ERROR_LINE":
            line = msg.get("line", -1)
            text = msg.get("message", "")
            self.error_highlight_received.emit(line, text)

        elif msg_type == "CHAT":
            sender = msg.get("sender", "")
            content = msg.get("message", "")
            t_str = msg.get("time", "")
            self.chat_received.emit(sender, content, t_str)

        elif msg_type == "LEAVE":
            name = msg.get("name", "Напарник")
            reason = msg.get("reason", "покинул комнату")
            self.disconnected_signal.emit(f"{name} {reason}")

        elif msg_type == "STDIN_INPUT":
            text = msg.get("text", "")
            self.stdin_received.emit(text)

        elif msg_type == "PROJECT_TREE":
            pname = msg.get("project_name", "Проект")
            files = msg.get("files", [])
            self.project_tree_received.emit(pname, files)

        elif msg_type == "GET_FILE":
            path = msg.get("path", "")
            self.file_content_requested.emit(path)

        elif msg_type == "FILE_DATA":
            path = msg.get("path", "")
            content = msg.get("content", "")
            self.file_content_received.emit(path, content)

        elif msg_type == "CREATE_FILE":
            path = msg.get("path", "")
            self.file_create_requested.emit(path)

        elif msg_type == "DELETE_FILE":
            path = msg.get("path", "")
            self.file_delete_requested.emit(path)

        elif msg_type == "SAVE_FILE":
            path = msg.get("path", "")
            content = msg.get("content", "")
            self.file_save_requested.emit(path, content)

    def _broadcast(self, data: dict):
        """
        Отправка сообщения в активный канал.

        В режиме прямого соединения (без посредника) трафик идёт только по
        установленному UDP-каналу; в остальных режимах — через брокер или TCP.
        """
        if self.mode == "direct":
            self._send_direct(data)
            return
        if self.p2p is not None and self.p2p.is_connected and self.p2p.send(data):
            return
        if self.mode == "cloud":
            self._cloud_send(data)
        elif self.mode == "tcp":
            self._tcp_send(data)

    # =========================================================================
    # ПРЯМОЕ СОЕДИНЕНИЕ ПО КОДУ-ПРИГЛАШЕНИЮ (совсем без посредника)
    # =========================================================================

    def create_direct_invite(self, bind_ip: str = "0.0.0.0",
                             external_override: tuple[str, int] | None = None) -> tuple[bool, str]:
        """
        Подготовить прямое соединение и выдать код-приглашение.

        Посредник не используется вовсе: код содержит внешний адрес этого
        компьютера и передаётся напарнику любым удобным способом (мессенджер).
        Возвращаем код, который нужно передать напарнику.

        bind_ip и external_override нужны для проверок: два участника на одной
        машине иначе неразличимы.
        """
        self.stop()
        self.mode = "direct"
        self.is_host = True
        self.running = True

        self.p2p = self._ensure_p2p(session_id=os.urandom(4).hex())

        try:
            self.p2p.open(bind_ip=bind_ip)
        except Exception as e:
            return False, f"Не удалось открыть UDP-порт: {e}"
        if external_override and external_override[1]:
            self.p2p.external_endpoint = (external_override[0], external_override[1])
        elif external_override:
            # Адрес задан, порт берём фактический (нужно для проверок)
            self.p2p.external_endpoint = (external_override[0], self.p2p.local_port)
        self.p2p.start_listening()

        external = self.p2p.external_endpoint
        if not external:
            return False, ("Не удалось определить ваш внешний адрес (STUN недоступен). "
                           "Проверьте доступ в интернет и попробуйте снова.")

        host_port = external[1]
        local = get_local_ip()
        parts = [external[0], str(host_port), self.p2p.session_id]
        if local and not local.startswith("127."):
            parts.append(local)
        code = DIRECT_CODE_PREFIX + "-".join(parts)
        return True, code

    def connect_to_invite(self, code: str, bind_ip: str = "0.0.0.0",
                          external_override: tuple[str, int] | None = None) -> tuple[bool, str]:
        """
        Подключиться по коду-приглашению, который прислал напарник.

        Одновременно со своим кодом-ответом сразу начинаем простукивать
        напарника: исходящий пакет открывает проход в нашем NAT, поэтому
        ответный пакет напарника уже доходит.
        """
        self.stop()
        self.mode = "direct"
        self.is_host = False
        self.running = True

        try:
            targets, session_id = parse_direct_code(code)
        except ValueError as e:
            self.running = False
            return False, str(e)

        # Свой порт выбирается свободным: фактический адрес уходит напарнику
        # в ответном коде, поэтому договариваться о порте заранее не нужно
        self.p2p = self._ensure_p2p(session_id=session_id)

        try:
            self.p2p.open(bind_ip=bind_ip)
        except Exception as e:
            return False, f"Не удалось открыть UDP-порт: {e}"
        if external_override:
            self.p2p.external_endpoint = external_override

        self.p2p.start_connecting(targets, timeout=DIRECT_TIMEOUT)
        return True, self.direct_answer_code()

    def direct_answer_code(self) -> str:
        """Код-ответ: свой адрес, чтобы напарник тоже стучался к нам."""
        code = DIRECT_CODE_PREFIX
        parts = []
        if self.p2p and self.p2p.external_endpoint:
            parts.extend([self.p2p.external_endpoint[0], str(self.p2p.external_endpoint[1])])
        parts.append(self.p2p.session_id if self.p2p else "")
        local = get_local_ip()
        if local and not local.startswith("127."):
            parts.append(local)
        return code + "-".join(parts)

    def _send_direct(self, data: dict):
        """Отправка в прямом режиме (только через установленный канал)."""
        if self.p2p and self.p2p.is_connected:
            if self.p2p.send(data):
                if data.get("type") not in ("PING", "PONG"):
                    self.bytes_out += len(json.dumps(data, separators=(",", ":")).encode("utf-8"))
                return True
        return False

    def _on_direct_message(self, msg: dict):
        """Приём сообщения из прямого канала."""
        if msg.get("sender_id") and msg.get("sender_id") == self.user_id:
            return
        raw = json.dumps(msg, separators=(",", ":")).encode("utf-8")
        if msg.get("type") not in ("PING", "PONG"):
            self.bytes_in += len(raw)
        self.is_connected = True
        self._dispatch_message(msg)

    # =========================================================================
    # ПРЯМОЕ СОЕДИНЕНИЕ (P2P)
    # =========================================================================

    def start_p2p(self):
        """
        Попытка перейти на прямой канал.

        Адресами обмениваемся через уже работающий канал (брокер): это
        единственное, для чего нужен посредник. Дальше стороны одновременно
        простукивают друг друга, открывая соответствия в своих NAT.
        """
        if self.p2p is None:
            self.p2p = self._ensure_p2p()
        if self.p2p.is_connected:
            return
        try:
            self.p2p.open()
        except Exception as e:
            print(f"[DuoPy] Не удалось открыть UDP для прямого канала: {e}")
            return

        # Просим напарника сообщить свои адреса
        self._cloud_send({
            "type": "P2P_HELLO",
            "ip": get_local_ip(),
            "port": self.p2p.local_port,
            "ext": list(self.p2p.external_endpoint) if self.p2p.external_endpoint else None,
        })

    def _on_p2p_connected(self, endpoint: str):
        """Прямой канал подтверждён: переключаемся на него."""
        self.transport = "p2p"
        # Канал установлен — соединение активно даже до первого сообщения,
        # иначе интерфейс показывал бы «нет связи» при живом канале
        self.is_connected = True
        self.status_signal.emit(f"⚡ Прямое соединение с напарником: {endpoint}")
        self.transport_changed.emit(endpoint, "p2p")

        # В прямом режиме посредника нет, поэтому знакомиться нужно самим:
        # сразу представляемся, чтобы напарник увидел имя и цвет
        if self.mode == "direct":
            self.is_connected = True
            self._send_direct({
                "type": "JOIN",
                "sender_id": self.user_id,
                "name": self.username,
                "color": self.user_color,
                "is_creator": self.is_host,
            })

    def _on_p2p_failed(self, reason: str):
        """Прямой канал не сложился — остаёмся на брокере, связь не теряется."""
        self.transport = "relay"
        self.status_signal.emit(f"Работаем через сервер комнат ({reason})")
        self.transport_changed.emit("", "relay")

    def _on_p2p_probe(self, addr: str, port: int):
        """От напарника пришёл пакет: запоминаем его адрес и начинаем простукивание."""
        if self.p2p is None or self.p2p.is_connected:
            return
        self.p2p.start_connecting([(addr, port)], timeout=6.0)

    def _on_p2p_message(self, msg: dict):
        """
        Сообщение из прямого канала в облачном режиме.

        Отсекаем собственное эхо: брокер при подтверждённой доставке может
        вернуть отправленное сообщение автору.
        """
        if msg.get("sender_id") and msg.get("sender_id") == self.user_id:
            return
        raw = json.dumps(msg, separators=(",", ":")).encode("utf-8")
        if msg.get("type") not in ("PING", "PONG"):
            self.bytes_in += len(raw)
        self._dispatch_message(msg)

    def _handle_p2p_hello(self, msg: dict):
        """
        Напарник сообщил свои адреса — начинаем простукивание.

        Отвечаем отдельным типом сообщения (P2P_ACCEPT), а не повторным HELLO:
        иначе два клиента отвечали друг другу бесконечно.
        """
        candidates = self._p2p_candidates(msg)
        if candidates:
            self.p2p.start_connecting(candidates, timeout=8.0)

        if not msg.get("is_reply") and not self._p2p_accepted:
            # Отвечаем ровно один раз за сессию
            self._p2p_accepted = True
            self._cloud_send({
                "type": "P2P_ACCEPT",
                "is_reply": True,
                "ip": get_local_ip(),
                "port": self.p2p.local_port,
                "ext": list(self.p2p.external_endpoint) if self.p2p.external_endpoint else None,
            })

    def _handle_p2p_accept(self, msg: dict):
        """Напарник принял предложение прямого канала и прислал свои адреса."""
        candidates = self._p2p_candidates(msg)
        if candidates and not self.p2p.is_connected:
            self.p2p.start_connecting(candidates, timeout=8.0)

    @staticmethod
    def _p2p_candidates(msg: dict) -> list[tuple[str, int]]:
        """Разбор адресов напарника из сообщения обмена."""
        candidates = []
        ext = msg.get("ext")
        if isinstance(ext, (list, tuple)) and len(ext) == 2:
            try:
                candidates.append((str(ext[0]), int(ext[1])))
            except (TypeError, ValueError):
                pass
        peer_ip = msg.get("ip")
        peer_port = msg.get("port")
        if peer_ip and peer_port:
            try:
                candidates.append((str(peer_ip), int(peer_port)))
            except (TypeError, ValueError):
                pass
        return candidates

    # Публичные методы отправки
    def send_code_update(self, code: str, file_name: str = ""):
        self._broadcast({"type": "CODE_UPDATE", "code": code, "file": file_name})

    def send_code_delta(self, pos: int, removed: int, insert: str,
                        file_name: str = "", base_digest: str = ""):
        """
        Отправка только изменённого фрагмента.

        Полный текст передавать на каждое нажатие слишком дорого: через
        публичный брокер файл в 100 КБ доставлялся около двух секунд. Здесь
        уходит лишь фрагмент (и он сжимается, если получился большим).
        base_digest — метка версии текста, на которой основана правка.
        seq — номер правки: по нему получатель замечает потерю.
        """
        self._edit_seq += 1
        payload = {
            "type": "CODE_EDIT",
            "file": file_name,
            "pos": pos,
            "removed": removed,
            "insert": insert,
            "base": base_digest,
            "seq": self._edit_seq,
        }
        compressed = self._maybe_compress(payload)
        self._broadcast(compressed)

    @staticmethod
    def _maybe_compress(payload: dict) -> dict:
        """
        Сжатие крупных сообщений.

        zlib для исходного кода даёт выигрыш в разы (повторяющиеся отступы,
        имена, ключевые слова), а на медленном канале это прямая экономия
        времени доставки. Мелкие сообщения не трогаем: заголовок съест выигрыш.
        """
        raw = json.dumps(payload, separators=(",", ":"))
        if len(raw) < COMPRESS_THRESHOLD_BYTES:
            return payload
        try:
            packed = zlib.compress(raw.encode("utf-8"), 6)
            if len(packed) >= len(raw) * 0.9:
                return payload
            return {"type": "PACKED", "data": base64.b64encode(packed).decode("ascii")}
        except Exception:
            return payload

    @staticmethod
    def _unpack(payload: dict) -> dict:
        """Распаковка сжатого сообщения (обратная сторона _maybe_compress)."""
        if payload.get("type") != "PACKED":
            return payload
        try:
            blob = base64.b64decode(payload.get("data", ""))
            return json.loads(zlib.decompress(blob).decode("utf-8"))
        except Exception as e:
            print(f"[DuoPy] Не удалось распаковать сообщение: {e}")
            return {"type": "_INVALID"}

    def send_project_tree(self, project_name: str, files: list[dict]):
        """Отправка структуры файлов проекта напарнику."""
        self._broadcast({"type": "PROJECT_TREE", "project_name": project_name, "files": files})

    def send_request_file(self, rel_path: str):
        """Запрос содержимого файла у хоста."""
        self._broadcast({"type": "GET_FILE", "path": rel_path})

    def send_file_data(self, rel_path: str, content: str):
        """Отправка содержимого файла гостю."""
        self._broadcast({"type": "FILE_DATA", "path": rel_path, "content": content})

    def send_create_file(self, rel_path: str):
        """Запрос на создание нового файла в проекте."""
        self._broadcast({"type": "CREATE_FILE", "path": rel_path})

    def send_delete_file(self, rel_path: str):
        """Запрос на удаление файла из проекта."""
        self._broadcast({"type": "DELETE_FILE", "path": rel_path})

    def send_save_file(self, rel_path: str, content: str):
        """Отправка сохраненного содержимого файла хосту для записи на диск."""
        self._broadcast({"type": "SAVE_FILE", "path": rel_path, "content": content})

    def send_sync_request(self):
        """Запрос актуального состояния проекта и кода у хоста."""
        self._broadcast({"type": "REQUEST_SYNC"})


    def send_cursor_position(self, line: int, col: int, pos: int = 0, sel_start: int = 0, sel_end: int = 0, file_name: str = ""):
        self._broadcast({
            "type": "CURSOR_MOVE",
            "name": self.username,
            "color": self.user_color,
            "line": line,
            "col": col,
            "pos": pos,
            "sel_start": sel_start,
            "sel_end": sel_end,
            "file": file_name
        })

    def send_active_file(self, file_name: str):
        """Оповещение напарника о переключении на другой файл."""
        self._broadcast({
            "type": "ACTIVE_FILE",
            "name": self.username,
            "file": file_name
        })

    def send_profile_update(self, name: str, color: str):
        """Оповещение напарника об изменении имени или цвета."""
        self.username = name
        self.user_color = color
        self._broadcast({
            "type": "PROFILE_UPDATE",
            "name": name,
            "color": color
        })

    def send_run_command(self):
        self._broadcast({"type": "RUN_CODE"})

    def send_stop_command(self):
        self._broadcast({"type": "STOP_CODE"})

    def send_output(self, text: str, is_err: bool = False):
        self._broadcast({"type": "OUTPUT", "text": text, "is_err": is_err})

    def send_execution_finished(self, return_code: int, duration: float):
        self._broadcast({"type": "EXEC_FINISHED", "code": return_code, "duration": duration})

    def send_error_line(self, line: int, message: str):
        self._broadcast({"type": "ERROR_LINE", "line": line, "message": message})

    def send_chat_message(self, message: str) -> str:
        now_str = datetime.now().strftime("%H:%M:%S")
        self._broadcast({
            "type": "CHAT",
            "sender": self.username,
            "message": message,
            "time": now_str
        })
        return now_str

    def send_stdin_input(self, text: str):
        """Отправка введенных данных input() напарнику."""
        self._broadcast({"type": "STDIN_INPUT", "text": text})

    def stop(self):
        """
        Закрытие всех соединений.

        Метод вызывается и до первого подключения (например, при подготовке
        прямого соединения), поэтому к необязательным ресурсам обращаемся
        через getattr: у свежего объекта их ещё нет.
        """
        self.running = False
        self.is_connected = False
        self.initial_joined = False
        self.transport = "relay"

        # Закрываем прямой канал: сокет и поток приёма
        p2p = getattr(self, "p2p", None)
        if p2p is not None:
            try:
                p2p.close()
            except Exception:
                pass

        client = getattr(self, "mqtt_client", None)
        if client:
            try:
                # Оповещаем об уходе
                if self.room_code:
                    self._cloud_send({"type": "LEAVE", "name": self.username})
                client.loop_stop()
                client.disconnect()
            except Exception:
                pass
            self.mqtt_client = None

        for attr in ("peer_socket", "server_socket", "client_socket"):
            sock = getattr(self, attr, None)
            if sock is None:
                continue
            try:
                if attr == "peer_socket":
                    sock.shutdown(socket.SHUT_RDWR)
                sock.close()
            except Exception:
                pass
            setattr(self, attr, None)
