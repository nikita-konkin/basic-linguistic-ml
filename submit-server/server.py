"""Приём сданных работ курса: https://submit.konkin-nikita.ru

Сайт курса статический (GitHub Pages), поэтому принимать файлы сам не
может; этим занимается этот сервис за nginx на сервере преподавателя.

Работа приходит уже зашифрованной: страница сдачи шифрует её в браузере
открытым ключом преподавателя (ECDH P-256 + AES-256-GCM, см.
site/crypto.js). Сервис не видит ни ФИО, ни файла — только номер работы,
размер и время. Поэтому список и сами конверты отдаются без пароля:
расшифровать их может только журнал преподавателя.

Конверт (все числа — big-endian):

    "MLGS1"                       5 байт
    эфемерный открытый ключ P-256  65 байт, 0x04 || X || Y
    длина шифротекста заголовка H  4 байта
    IV заголовка                   12 байт
    шифротекст заголовка           H байт: JSON {ФИО, работа, имя файла, ...}
    IV файла                       12 байт
    шифротекст файла               до конца

Заголовок отделён от файла, чтобы список сдач отдавался вместе с
заголовками («head» в ответе /api/submissions): журналу хватает одного
запроса, чтобы показать, кто что сдал, а работы скачиваются по одной.

Сервис умеет ровно три вещи: принять конверт, отдать список, отдать
конверт. Удаления нет — лишнее преподаватель удаляет на сервере руками.
Защита от мусора: проверка формата конверта, предел размера, частоты
отправок с одного адреса и общего объёма.

    python server.py                  # порт и папка — из переменных окружения

Переменные окружения:
    HOST              на каком адресе слушать (0.0.0.0 — для Docker)
    PORT              порт (8095)
    DATA_DIR          куда складывать конверты (/data)
    ALLOWED_ORIGINS   с каких сайтов можно отправлять, через запятую
    MAX_UPLOAD_MB     предел одного конверта (12)
    MAX_TOTAL_MB      предел всей папки (2048)
    RATE_LIMIT        отправок с одного адреса за RATE_WINDOW секунд (60);
                      в аудитории вся группа выходит в сеть с одного адреса
    RATE_WINDOW       окно для RATE_LIMIT, секунд (600)
"""

from __future__ import annotations

import base64
import datetime as dt
import json
import os
import re
import secrets
import threading
import time
from collections import defaultdict, deque
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

MAGIC = b"MLGS1"
POINT = 65  # открытый ключ P-256 без сжатия
TAG = 16  # тег AES-GCM
IV = 12
# Заголовок — пара сотен байт JSON; запас на длинные ФИО и имена файлов.
MAX_HEAD = 8192
HEAD_START = len(MAGIC) + POINT + 4 + IV
MIN_ENVELOPE = HEAD_START + TAG + IV + TAG
WORK = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
SUBMISSION_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}/\d{8}T\d{6}Z-[0-9a-f]{8}$")
MOSCOW = dt.timezone(dt.timedelta(hours=3))


class Settings:
    def __init__(self, env: dict[str, str] | None = None) -> None:
        env = os.environ if env is None else env
        self.host = env.get("HOST", "0.0.0.0")
        self.port = int(env.get("PORT", "8095"))
        self.data = Path(env.get("DATA_DIR", "/data"))
        self.origins = {o.strip() for o in env.get("ALLOWED_ORIGINS", "https://konkin-nikita.ru").split(",") if o.strip()}
        self.max_upload = int(float(env.get("MAX_UPLOAD_MB", "12")) * 1024 * 1024)
        self.max_total = int(float(env.get("MAX_TOTAL_MB", "2048")) * 1024 * 1024)
        self.rate_limit = int(env.get("RATE_LIMIT", "60"))
        self.rate_window = float(env.get("RATE_WINDOW", "600"))


class Store:
    """Конверты на диске: DATA_DIR/<работа>/<время>-<случайное>.mlgs."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()

    def total_size(self) -> int:
        return sum(path.stat().st_size for path in self.root.glob("*/*.mlgs"))

    def save(self, work: str, envelope: bytes) -> dict:
        now = dt.datetime.now(dt.timezone.utc)
        name = f"{now:%Y%m%dT%H%M%SZ}-{secrets.token_hex(4)}"
        folder = self.root / work
        folder.mkdir(exist_ok=True)
        temporary = folder / f".{name}.part"
        temporary.write_bytes(envelope)
        temporary.rename(folder / f"{name}.mlgs")
        return self.describe(folder / f"{name}.mlgs")

    def describe(self, path: Path) -> dict:
        stamp = dt.datetime.strptime(path.stem.split("-")[0], "%Y%m%dT%H%M%SZ").replace(tzinfo=dt.timezone.utc)
        with path.open("rb") as handle:
            prefix = handle.read(HEAD_START)
            prefix += handle.read(head_length(prefix))
        return {
            "id": f"{path.parent.name}/{path.stem}",
            "work": path.parent.name,
            "received": stamp.astimezone(MOSCOW).isoformat(timespec="seconds"),
            "size": path.stat().st_size,
            "head": base64.b64encode(prefix).decode("ascii"),
        }

    def listing(self) -> list[dict]:
        return sorted((self.describe(path) for path in self.root.glob("*/*.mlgs")), key=lambda item: item["id"].split("/")[1])

    def path(self, submission_id: str) -> Path | None:
        if not SUBMISSION_ID.match(submission_id):
            return None
        path = self.root / f"{submission_id}.mlgs"
        return path if path.is_file() else None


def head_length(envelope: bytes) -> int:
    start = len(MAGIC) + POINT
    return int.from_bytes(envelope[start:start + 4], "big")


def valid_envelope(envelope: bytes) -> bool:
    """Похоже ли на конверт со страницы сдачи. Расшифровать его сервис не может,
    поэтому проверяется только разметка: магия, точка P-256, длины частей."""
    if len(envelope) < MIN_ENVELOPE or not envelope.startswith(MAGIC) or envelope[len(MAGIC)] != 0x04:
        return False
    head = head_length(envelope)
    return TAG <= head <= MAX_HEAD and len(envelope) >= HEAD_START + head + IV + TAG


class RateLimiter:
    def __init__(self, limit: int, window: float) -> None:
        self.limit, self.window = limit, window
        self.hits: dict[str, deque] = defaultdict(deque)
        self.lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self.lock:
            hits = self.hits[key]
            while hits and now - hits[0] > self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                return False
            hits.append(now)
            return True


def make_handler(settings: Settings, store: Store, limiter: RateLimiter):
    class Handler(BaseHTTPRequestHandler):
        server_version = "mlg-submit"
        sys_version = ""

        # --- ответы

        def cors(self) -> None:
            origin = self.headers.get("Origin", "")
            if origin in settings.origins:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")

        def reply(self, status: int, body: bytes = b"", kind: str = "application/json; charset=utf-8") -> None:
            self.send_response(status)
            self.cors()
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def reply_json(self, status: int, payload) -> None:
            self.reply(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"))

        def error(self, status: int, message: str) -> None:
            self.reply_json(status, {"error": message})

        def client(self) -> str:
            # За nginx настоящий адрес — в X-Real-IP; без nginx — адрес соединения.
            return self.headers.get("X-Real-IP") or self.client_address[0]

        def log_message(self, format: str, *args) -> None:  # noqa: A002 — так зовёт базовый класс
            # Без тела и без заголовков: в журнале только время, адрес и строка запроса.
            print(f"{dt.datetime.now(MOSCOW):%Y-%m-%d %H:%M:%S} {self.client()} {format % args}", flush=True)

        # --- маршруты

        def do_OPTIONS(self) -> None:  # noqa: N802
            self.send_response(HTTPStatus.NO_CONTENT)
            self.cors()
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Max-Age", "86400")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802
            url = urlsplit(self.path)
            if url.path == "/health":
                self.reply(HTTPStatus.OK, b"ok\n", "text/plain; charset=utf-8")
            elif url.path == "/api/submissions":
                self.reply_json(HTTPStatus.OK, store.listing())
            elif url.path.startswith("/api/submissions/"):
                path = store.path(url.path[len("/api/submissions/"):])
                if path is None:
                    self.error(HTTPStatus.NOT_FOUND, "нет такой сдачи")
                else:
                    self.reply(HTTPStatus.OK, path.read_bytes(), "application/octet-stream")
            else:
                self.error(HTTPStatus.NOT_FOUND, "нет такого адреса")

        do_HEAD = do_GET

        def do_POST(self) -> None:  # noqa: N802
            url = urlsplit(self.path)
            if url.path != "/api/submissions":
                self.error(HTTPStatus.NOT_FOUND, "нет такого адреса")
                return
            origin = self.headers.get("Origin")
            if origin is not None and origin not in settings.origins:
                self.error(HTTPStatus.FORBIDDEN, "отправка разрешена только со страницы курса")
                return
            work = parse_qs(url.query).get("work", [""])[0]
            if not WORK.match(work):
                self.error(HTTPStatus.BAD_REQUEST, "не указана работа")
                return
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                self.error(HTTPStatus.LENGTH_REQUIRED, "нужен Content-Length")
                return
            if length > settings.max_upload:
                self.error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "файл слишком большой")
                return
            if length < MIN_ENVELOPE:
                self.error(HTTPStatus.BAD_REQUEST, "это не зашифрованная работа")
                return
            if not limiter.allow(self.client()):
                self.error(HTTPStatus.TOO_MANY_REQUESTS, "слишком много отправок подряд, попробуйте позже")
                return
            envelope = self.rfile.read(length)
            if len(envelope) != length or not valid_envelope(envelope):
                self.error(HTTPStatus.BAD_REQUEST, "это не зашифрованная работа")
                return
            with store.lock:
                if store.total_size() + length > settings.max_total:
                    self.error(HTTPStatus.INSUFFICIENT_STORAGE, "место для работ закончилось, сообщите преподавателю")
                    return
                receipt = store.save(work, envelope)
            self.reply_json(HTTPStatus.CREATED, receipt)

    return Handler


def make_server(settings: Settings) -> ThreadingHTTPServer:
    store = Store(settings.data)
    limiter = RateLimiter(settings.rate_limit, settings.rate_window)
    server = ThreadingHTTPServer((settings.host, settings.port), make_handler(settings, store, limiter))
    server.daemon_threads = True
    return server


def main() -> None:
    settings = Settings()
    server = make_server(settings)
    print(f"mlg-submit: порт {settings.port}, папка {settings.data}, сайты {sorted(settings.origins)}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
