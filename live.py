"""Live mirror of one world: every event as soon as it is written, as Server-Sent Events. Read-only.

  python live.py WORLD [--port 8080]

GET /events streams the last events, then each new one and each new tick; GET /health answers "ok".
"""
import argparse
import http.server
import json
import sqlite3
import threading
import time

import config
from db import world_db_path

BACKLOG = 60
KEEPALIVE_SECONDS = 15


def read_only(path):
    # mode=ro: this process can never change the world, whatever a request asks.
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def events_after(conn, last_id):
    """New events after last_id; with no last_id, the latest BACKLOG ones."""
    if last_id is None:
        rows = conn.execute("select * from (select id, tick, kind, agent, data from events order by id desc "
                            "limit ?) order by id", (BACKLOG,)).fetchall()
    else:
        rows = conn.execute("select id, tick, kind, agent, data from events where id>? order by id",
                            (last_id,)).fetchall()
    return [{**dict(r), "data": json.loads(r["data"])} for r in rows]


def current_tick(conn):
    row = conn.execute("select value from kv where key='tick'").fetchone()
    return json.loads(row["value"]) if row else None


def sse(kind, payload, event_id=None):
    head = f"id: {event_id}\n" if event_id is not None else ""
    return f"{head}event: {kind}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()


def handler_for(path):
    clients = threading.BoundedSemaphore(config.LIVE_MAX_CLIENTS)

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass  # one line per request would flood the container log

        def do_GET(self):
            if self.path == "/health":
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"ok")
            elif self.path == "/events":
                if not clients.acquire(blocking=False):
                    self.send_error(503, "too many viewers")
                    return
                try:
                    self.stream()
                finally:
                    clients.release()
            else:
                self.send_error(404)

        def stream(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Access-Control-Allow-Origin", config.LIVE_ORIGIN)
            self.end_headers()
            conn = read_only(path)
            resume = self.headers.get("Last-Event-ID")
            last_id = int(resume) if resume and resume.isdigit() else None
            last_tick, quiet_since = None, time.monotonic()
            try:
                while True:
                    out = b""
                    for e in events_after(conn, last_id):
                        out += sse("world", e, e["id"])
                        last_id = e["id"]
                    tick = current_tick(conn)
                    if tick != last_tick:
                        out += sse("tick", {"tick": tick})
                        last_tick = tick
                    if not out and time.monotonic() - quiet_since > KEEPALIVE_SECONDS:
                        out = b": keepalive\n\n"
                    if out:
                        self.wfile.write(out)
                        self.wfile.flush()
                        quiet_since = time.monotonic()
                    time.sleep(1)
            except (BrokenPipeError, ConnectionResetError):
                return  # the viewer closed the page
            finally:
                conn.close()

    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("world")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    server = http.server.ThreadingHTTPServer(("", args.port), handler_for(world_db_path(args.world)))
    server.daemon_threads = True
    server.serve_forever()


if __name__ == "__main__":
    main()
