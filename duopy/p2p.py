"""
Прямое P2P-соединение между участниками (UDP hole punching).

Зачем: в режиме облачной комнаты трафик идёт через публичный MQTT-брокер —
это посредник в Европе или Азии, и каждый пакет проходит путь
«вы → брокер → напарник → брокер → вы». Измеренная задержка такого маршрута
составляет 470–610 мс, и почти всё это время — дорога до посредника.

Схема прямого соединения:
1. Каждая сторона поднимает UDP-сокет и узнаёт свой внешний адрес через STUN.
2. Адреса обмениваются через уже установленный канал (MQTT-комнату) — это
   единственное, для чего нужен посредник.
3. Обе стороны одновременно шлют друг другу короткие пакеты: так в NAT
   открываются соответствия для входящих пакетов.
4. Дальше правки идут напрямую, минуя посредника.

Если NAT симметричный или пробить его не удалось, соединение не рвётся:
вызывающая сторона продолжает работать через брокер (автоматический откат).
"""

import errno
import os
import socket
import struct
import threading
import time

from PyQt6.QtCore import QObject, pyqtSignal

# Публичные STUN-серверы для определения внешнего адреса
STUN_SERVERS = [
    ("stun.l.google.com", 19302),
    ("stun.cloudflare.com", 3478),
    ("stun1.l.google.com", 19302),
]

STUN_MAGIC_COOKIE = 0x2112A442
STUN_BINDING_REQUEST = 0x0001
STUN_BINDING_RESPONSE = 0x0101

# Пакет-«простукивание»: открывает соответствие в NAT и подтверждает канал
PROBE_INTERVAL = 0.6
PEER_TIMEOUT = 6.0


def get_local_ip() -> str:
    """Локальный IP в текущей сети."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return "127.0.0.1"
    finally:
        s.close()


def discover_external_endpoint(timeout: float = 1.5) -> tuple[str, int] | None:
    """
    Внешний адрес этого компьютера по версии STUN-сервера.

    Нужен, чтобы сообщить напарнику, куда слать пакеты: за NAT локальный
    адрес бесполезен. Если STUN недоступен (сеть закрыта), возвращаем None —
    тогда остаётся путь через посредника.
    """
    for host, port in STUN_SERVERS:
        try:
            info = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_DGRAM)
            if not info:
                continue
            addr = info[0][4]
        except Exception:
            continue

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.settimeout(timeout)
            # Заголовок STUN: тип, длина, магическое число, 12 байт транзакции
            tx_id = os.urandom(12)
            request = struct.pack(">HHI", STUN_BINDING_REQUEST, 0, STUN_MAGIC_COOKIE) + tx_id
            sock.sendto(request, addr)
            data, _ = sock.recvfrom(2048)

            if len(data) < 20:
                continue
            msg_type, length = struct.unpack(">HH", data[:4])
            if msg_type != STUN_BINDING_RESPONSE:
                continue

            # Разбираем атрибуты ответа в поисках XOR-MAPPED-ADDRESS
            offset = 20
            end = min(len(data), 20 + length)
            while offset + 4 <= end:
                attr_type, attr_len = struct.unpack(">HH", data[offset:offset + 4])
                value = data[offset + 4:offset + 4 + attr_len]
                if attr_type in (0x0020, 0x0001) and len(value) >= 8:  # XOR-MAPPED / MAPPED
                    port = struct.unpack(">H", value[2:4])[0]
                    raw_ip = value[4:8]
                    if attr_type == 0x0020:
                        port ^= STUN_MAGIC_COOKIE >> 16
                        raw_ip = bytes(b ^ m for b, m in
                                       zip(raw_ip, struct.pack(">I", STUN_MAGIC_COOKIE)))
                    ip = socket.inet_ntoa(raw_ip)
                    return (ip, port)
                offset += 4 + attr_len + ((4 - attr_len % 4) % 4)
        except Exception:
            continue
        finally:
            sock.close()
    return None


# Ошибки UDP, которые нельзя считать потерей канала: Windows отдаёт их при
# ответе ICMP «порт недоступен» — например, когда простукивание ушло в
# закрытый порт или в широковещательный адрес.
TRANSIENT_UDP_ERRORS = {
    10054,           # WSAECONNRESET: удалённый хост сбросил соединение
    10052,           # WSAENETRESET
    10053,           # WSAECONNABORTED
    errno.ECONNREFUSED,
    errno.EHOSTUNREACH,
    errno.ENETUNREACH,
}


class P2PTransport(QObject):
    """
    Прямой UDP-канал с напарником.

    Состояния: idle -> probing -> connected (либо failed, и тогда работает
    канал через посредника).
    """

    connected_signal = pyqtSignal(str)     # внешний адрес напарника
    failed_signal = pyqtSignal(str)        # причина
    message_received = pyqtSignal(dict)    # разобранное сообщение
    probe_received = pyqtSignal(str, int)  # (адрес отправителя, порт) — для обмена адресами

    def __init__(self, parent=None, own_ips=None):
        super().__init__(parent)
        self.sock: socket.socket | None = None
        # Адреса, которые считаются «своими». По умолчанию определяются
        # автоматически; в тестах можно задать явно.
        self.own_ips = set(own_ips) if own_ips else None
        self.local_port = 0
        self.external_endpoint: tuple[str, int] | None = None
        self.peer_sockaddr: tuple[str, int] | None = None
        self.peer_external: tuple[str, int] | None = None
        self.is_connected = False
        self._running = False
        self._thread: threading.Thread | None = None
        self._probe_thread: threading.Thread | None = None
        self._last_peer_packet = 0.0
        self._connect_deadline = 0.0
        self._probe_targets: list[tuple[str, int]] = []
        self._last_probe_sent = 0.0
        self._lock = threading.Lock()
        # Адрес, к которому привязан сокет (для тестов и проверки своего эха)
        self.bind_ip = "0.0.0.0"

    # ------------------------------------------------------------------ запуск

    def open(self, bind_ip: str = "0.0.0.0") -> int:
        """
        Открыть UDP-сокет и узнать свой внешний адрес.

        bind_ip нужен для тестов: два процесса на одной машине удобно
        разводить по разным loopback-адресам, иначе они выглядят как один узел.
        """
        if self.sock:
            return self.local_port
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((bind_ip, 0))
        self.local_port = self.sock.getsockname()[1]
        self.sock.settimeout(0.2)
        # Свой адрес учитываем при проверке «это наш же пакет»
        if bind_ip and bind_ip != "0.0.0.0":
            self.bind_ip = bind_ip
        self.external_endpoint = discover_external_endpoint()
        return self.local_port

    def start_listening(self):
        """Запустить приём пакетов и цикл простукивания (в отдельных потоках)."""
        if self._running or not self.sock:
            return
        self._running = True
        self._thread = threading.Thread(target=self._receive_loop, daemon=True)
        self._thread.start()
        # Цикл простукивания обязательно нужен: без него пакеты только
        # принимались, а наружу не отправлялись, и канал не устанавливался.
        self._probe_thread = threading.Thread(target=self._probe_loop, daemon=True)
        self._probe_thread.start()

    # ------------------------------------------------------- обмен адресами

    def candidate_endpoints(self) -> list[tuple[str, int]]:
        """
        Адреса, по которым напарник может нас достать.

        Порядок проверки: внешний адрес (через интернет), затем локальный
        адрес в сети (если оба в одной сети, это самый быстрый путь).
        """
        candidates = []
        if self.external_endpoint:
            candidates.append(self.external_endpoint)
        local = get_local_ip()
        if self.local_port and local and not local.startswith("127."):
            candidates.append((local, self.local_port))
        return candidates

    def start_connecting(self, peer_candidates: list, timeout: float = 8.0):
        """
        Начать пробивание NAT: слать пакеты по адресам напарника.

        Первый же ответ подтвердит канал, потому что наш собственный пакет уже
        открыл соответствие в нашем NAT.
        """
        if not self.sock:
            self.failed_signal.emit("UDP-сокет не открыт")
            return
        self.start_listening()
        self._connect_deadline = time.time() + timeout
        self._probe_targets = [tuple(c) for c in peer_candidates if c]
        # Дополнительно шлём широковещательный запрос: если напарник в той же
        # локальной сети, он ответит напрямую, без выхода в интернет
        self._broadcast_probe()
        self.is_connected = False

    def _broadcast_probe(self):
        """
        Поиск напарника широковещательным пакетом в своей сети.

        Так прямое соединение устанавливается, даже когда внешние адреса
        недоступны друг для друга (например, оба за одним роутером).
        """
        if not self.sock or not self.local_port:
            return
        try:
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        except Exception:
            pass
        local = get_local_ip()
        if not local or local.startswith("127."):
            return
        prefix = ".".join(local.split(".")[:3])
        for target in (f"{prefix}.255", "255.255.255.255"):
            try:
                self.sock.sendto(b"DUOPYP2P", (target, self.local_port))
            except Exception:
                continue

    def _probe_loop(self):
        """Периодическая отправка простукиваний, пока канал не подтверждён."""
        while self._running:
            now = time.time()
            if self.is_connected:
                # Канал уже есть: отвечать на простукивания нельзя, иначе две
                # стороны зацикливают обмен пакетами. Поддерживаем NAT редко.
                if now - self._last_probe_sent >= PROBE_INTERVAL * 4:
                    self._send_probe()
                if now - self._last_peer_packet > PEER_TIMEOUT:
                    self.is_connected = False
                    self.failed_signal.emit("напарник перестал отвечать напрямую")
                    return
            elif self._probe_targets:
                if now > self._connect_deadline:
                    self.failed_signal.emit("не удалось установить прямое соединение")
                    return
                self._send_probe()
            elif not self.is_connected and now - self._last_probe_sent >= PROBE_INTERVAL:
                # Канал ещё не подтверждён и адресов напарника нет: ищем его
                # широковещательным запросом в своей сети
                self._broadcast_probe()
            time.sleep(0.1)

    def _send_probe(self):
        payload = b"DUOPYP2P"
        with self._lock:
            targets = list(getattr(self, "_probe_targets", []))
            if self.peer_sockaddr:
                targets.append(self.peer_sockaddr)
        for addr in set(targets):
            try:
                self.sock.sendto(payload, addr)
                self._last_probe_sent = time.time()
            except OSError as e:
                # Ошибка отдельного простукивания не должна ломать попытку
                if getattr(e, "winerror", None) not in TRANSIENT_UDP_ERRORS:
                    print(f"[DuoPy] Простукивание {addr} не удалось: {e}")
                continue
            except Exception:
                continue

    # ------------------------------------------------------------------ приём

    def _receive_loop(self):
        while self._running:
            try:
                data, addr = self.sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError as e:
                # ICMP «порт недоступен» (WinError 10054/10053 и родственные) —
                # обычное дело при простукивании: пакет попал в закрытый порт
                # или в широковещательный адрес. Приём из-за этого прерывать
                # нельзя, иначе канал «умирает» на ровном месте.
                if getattr(e, "winerror", None) in TRANSIENT_UDP_ERRORS or e.errno in TRANSIENT_UDP_ERRORS:
                    continue
                if self._running:
                    print(f"[DuoPy] Приём прямого канала остановлен: {e}")
                    self.is_connected = False
                    self.failed_signal.emit(f"приём прерван: {e}")
                break
            except Exception as e:
                print(f"[DuoPy] Ошибка приёма прямого канала: {e}")
                continue

            if data.startswith(b"DUOPYP2P"):
                self._handle_probe(addr)
                continue

            self._last_peer_packet = time.time()
            if not self.is_connected:
                self._mark_connected(addr)
            try:
                import json
                self.message_received.emit(json.loads(data.decode("utf-8")))
            except Exception:
                continue

    def _own_addresses(self) -> set:
        """Адреса, которые принадлежат нам: их нельзя принимать за напарника."""
        if self.own_ips is not None:
            return self.own_ips
        own = {self.bind_ip, "127.0.0.1", get_local_ip()}
        try:
            hostname = socket.gethostname()
            for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
                own.add(info[4][0])
        except Exception:
            pass
        return {a for a in own if a}

    def _is_own_packet(self, addr) -> bool:
        """
        Наш ли это пакет (эхо широковещательного запроса).

        Без этой проверки клиент «подключался» к самому себе: широковещательный
        поиск возвращался на свой же сокет, и канал считался установленным.
        Порт проверяем всегда: пакет с нашего порта — гарантированно наше эхо.
        """
        if addr[1] == self.local_port:
            return True
        if addr[0] == self.bind_ip and self.bind_ip != "0.0.0.0":
            return True
        return addr[0] in self._own_addresses()

    def _handle_probe(self, addr):
        """
        Простукивание: запоминаем адрес напарника и отвечаем.

        Ответ шлём на СВОЙ порт с адресом отправителя: напарник ждёт пакет
        именно на том порту, который он открыл для приёма. Отправка по
        исходному адресу (с портом отправителя) уходила обратно на свой же
        сокет, и канал оставался без данных.
        """
        if self._is_own_packet(addr):
            return
        self._last_peer_packet = time.time()
        if self.is_connected and self.peer_sockaddr != addr:
            # Пакет от чужого адреса по уже установленному каналу — игнорируем
            return
        was_connected = self.is_connected
        if not was_connected:
            self._mark_connected(addr)
            try:
                self.sock.sendto(b"DUOPYP2P", (addr[0], self.local_port))
            except Exception:
                pass
        self.probe_received.emit(addr[0], addr[1])

    def _mark_connected(self, addr):
        """Канал подтверждён: с этого адреса реально приходят пакеты."""
        with self._lock:
            self.peer_sockaddr = addr
            self.is_connected = True
            self._probe_targets = []
        self.connected_signal.emit(f"{addr[0]}:{addr[1]}")

    # --------------------------------------------------------------- отправка

    def send(self, payload: dict) -> bool:
        """Отправить сообщение напрямую (JSON, как и в остальных каналах)."""
        if not (self.is_connected and self.sock and self.peer_sockaddr):
            return False
        if self._is_own_packet(self.peer_sockaddr):
            # Защита от «соединения с самим собой»: данные ушли бы в свой сокет
            self.is_connected = False
            self.failed_signal.emit("прямой канал указывает на себя")
            return False
        try:
            import json
            raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            with self._lock:
                self.sock.sendto(raw, self.peer_sockaddr)
            return True
        except Exception:
            return False

    def close(self):
        """Закрыть канал."""
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self._thread = None
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None
        self.is_connected = False
        self.peer_sockaddr = None
        self._probe_targets = []
