import http.client
import http.server
import json
import sqlite3
import threading

import pytest

import config
from db import DB
from live import events_after, handler_for, read_only


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A world database with two events and a live server on a free port."""
    monkeypatch.setattr(config, "LIVE_MAX_CLIENTS", 1)
    path = str(tmp_path / "w.db")
    db = DB(path)
    db.set("tick", 7)
    db.log("work", "adam")
    db.log("speak", "eve", target="adam", message="Hi")
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler_for(path))
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield db, server.server_address[1]
    server.shutdown()


def read_messages(response, count):
    messages, current = [], {}
    while len(messages) < count:
        line = response.fp.readline().decode().rstrip("\n")
        if line.startswith(":"):
            continue
        if not line:
            if current:
                messages.append(current)
            current = {}
            continue
        key, _, value = line.partition(": ")
        current[key] = json.loads(value) if key == "data" else value
    return messages


def test_the_stream_sends_the_backlog_then_the_tick_then_new_events(world):
    db, port = world
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("GET", "/events")
    response = conn.getresponse()
    assert response.getheader("Content-Type") == "text/event-stream"
    assert response.getheader("Access-Control-Allow-Origin") == "https://omargarraoui.com"
    first = read_messages(response, 3)
    assert [(m["event"], m.get("id")) for m in first] == [("world", "1"), ("world", "2"), ("tick", None)]
    assert first[1]["data"]["data"] == {"target": "adam", "message": "Hi"}
    assert first[2]["data"] == {"tick": 7}
    db.log("pray", "adam", message="Guide us.")
    assert read_messages(response, 1)[0]["data"]["kind"] == "pray"
    conn.close()


def test_one_viewer_too_many_is_turned_away(world):
    _, port = world
    first = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    first.request("GET", "/events")
    read_messages(first.getresponse(), 1)
    second = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    second.request("GET", "/events")
    assert second.getresponse().status == 503
    first.close()


def test_health_and_unknown_paths(world):
    _, port = world
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("GET", "/health")
    assert conn.getresponse().read() == b"ok"
    conn.request("GET", "/admin")
    assert conn.getresponse().status == 404


def test_reading_resumes_after_the_last_id_and_never_writes(world, tmp_path):
    conn = read_only(str(tmp_path / "w.db"))
    assert [e["id"] for e in events_after(conn, 1)] == [2]
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("delete from events")
