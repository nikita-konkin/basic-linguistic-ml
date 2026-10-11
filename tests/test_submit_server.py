"""Проверки приёмника сданных работ (submit-server/server.py).

    python -m pytest tests/test_submit_server.py -q

Сервер поднимается на свободном порту с временной папкой. Настоящее
шифрование здесь не нужно: сервис его не проверяет и не умеет, он смотрит
только на разметку конверта. Расшифровка проверяется в браузере.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "submit-server"))

import server  # noqa: E402

SITE = "https://konkin-nikita.ru"


def envelope(head: int = 200, body: int = 1000) -> bytes:
    """Конверт с правильной разметкой и случайным «шифротекстом»."""
    return (
        server.MAGIC
        + b"\x04"
        + os.urandom(server.POINT - 1)
        + head.to_bytes(4, "big")
        + os.urandom(server.IV)
        + os.urandom(head)
        + os.urandom(server.IV)
        + os.urandom(body)
    )


@pytest.fixture()
def service(tmp_path):
    settings = server.Settings(
        {
            "HOST": "127.0.0.1",
            "PORT": "0",
            "DATA_DIR": str(tmp_path / "data"),
            "ALLOWED_ORIGINS": f"{SITE}, http://localhost:8000",
            "MAX_UPLOAD_MB": "0.05",
            "MAX_TOTAL_MB": "0.12",
            "RATE_LIMIT": "5",
        }
    )
    httpd = server.make_server(settings)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", settings
    httpd.shutdown()
    httpd.server_close()


def call(url: str, method: str = "GET", data: bytes | None = None, headers: dict | None = None):
    request = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read()


def post(base: str, body: bytes, work: str = "lab2", origin: str | None = SITE, ip: str | None = None):
    headers = {"Content-Type": "application/octet-stream"}
    if origin:
        headers["Origin"] = origin
    if ip:
        headers["X-Real-IP"] = ip
    return call(f"{base}/api/submissions?work={work}", "POST", body, headers)


def test_health(service):
    base, _ = service
    status, _, body = call(f"{base}/health")
    assert (status, body) == (200, b"ok\n")


def test_submission_round_trip(service):
    base, settings = service
    sent = envelope()
    status, headers, body = post(base, sent)
    assert status == 201
    assert headers["Access-Control-Allow-Origin"] == SITE
    receipt = json.loads(body)
    assert receipt["work"] == "lab2"
    assert receipt["size"] == len(sent)
    assert receipt["id"].startswith("lab2/")

    status, _, body = call(f"{base}/api/submissions/{receipt['id']}")
    assert status == 200 and body == sent

    status, _, body = call(f"{base}/api/submissions")
    listing = json.loads(body)
    assert [item["id"] for item in listing] == [receipt["id"]]
    # В списке — начало конверта до конца заголовка: журнал читает ФИО
    # и имя файла, не скачивая сами работы.
    head = base64.b64decode(listing[0]["head"])
    assert head == sent[: server.HEAD_START + 200]
    # На диске только конверт, без временных файлов.
    assert [path.name for path in (settings.data / "lab2").iterdir()] == [receipt["id"].split("/")[1] + ".mlgs"]


def test_listing_is_sorted_by_time_across_works(service):
    base, _ = service
    ids = [json.loads(post(base, envelope(), work=work)[2])["id"] for work in ("lab3", "lab1", "lab3")]
    status, _, body = call(f"{base}/api/submissions")
    assert [item["id"] for item in json.loads(body)] == sorted(ids, key=lambda value: value.split("/")[1])


def test_preflight_allows_only_the_course_site(service):
    base, _ = service
    status, headers, _ = call(f"{base}/api/submissions", "OPTIONS", headers={"Origin": SITE})
    assert status == 204
    assert headers["Access-Control-Allow-Origin"] == SITE
    assert "POST" in headers["Access-Control-Allow-Methods"]
    status, headers, _ = call(f"{base}/api/submissions", "OPTIONS", headers={"Origin": "https://evil.example"})
    assert "Access-Control-Allow-Origin" not in headers


def test_foreign_site_cannot_submit(service):
    base, _ = service
    status, _, body = post(base, envelope(), origin="https://evil.example")
    assert status == 403
    assert "страницы курса" in json.loads(body)["error"]


@pytest.mark.parametrize(
    "body",
    [
        b"MLGS1" + b"\x04" * 200,  # длина заголовка не сходится с размером
        b"PK\x03\x04" + os.urandom(400),  # обычный zip, не зашифрован
        envelope(head=8),  # заголовок короче тега GCM
        envelope(head=server.MAX_HEAD + 1),
        b"MLGS1\x02" + envelope()[6:],  # сжатая точка
    ],
    ids=["lengths", "zip", "short-head", "long-head", "compressed-point"],
)
def test_only_envelopes_are_accepted(service, body):
    base, settings = service
    status, _, _ = post(base, body)
    assert status == 400
    assert not list(settings.data.glob("*/*"))


@pytest.mark.parametrize("work", ["", "../etc", "Lab2", "lab2/x", "a" * 41])
def test_work_slug_is_checked(service, work):
    base, _ = service
    status, _, _ = post(base, envelope(), work=work)
    assert status == 400


@pytest.mark.parametrize("submission", ["../server", "lab2/../../x", "lab2/20260101T000000Z-zzzzzzzz", "lab2/nope"])
def test_bad_ids_are_not_found(service, submission):
    base, _ = service
    status, _, _ = call(f"{base}/api/submissions/{submission}")
    assert status == 404


def test_size_limit(service):
    base, _ = service
    status, _, body = post(base, envelope(body=60_000))
    assert status == 413
    assert "большой" in json.loads(body)["error"]


def test_total_limit(service):
    base, _ = service
    # Предел папки 0,12 МБ: два конверта по ~45 КБ входят, третий нет.
    for _ in range(3):
        status, _, body = post(base, envelope(body=45_000), ip="10.0.0.1")
    assert status == 507
    assert "место" in json.loads(body)["error"]


def test_rate_limit_is_per_client(service):
    base, _ = service
    statuses = [post(base, envelope(), ip="10.0.0.2")[0] for _ in range(6)]
    assert statuses == [201] * 5 + [429]
    # Другой адрес в той же аудитории не страдает.
    assert post(base, envelope(), ip="10.0.0.3")[0] == 201


def test_unknown_paths(service):
    base, _ = service
    assert call(f"{base}/")[0] == 404
    assert post(base, envelope(), work="lab2")[0] == 201
    status, _, _ = call(f"{base}/api/other", "POST", envelope(), {"Origin": SITE})
    assert status == 404
