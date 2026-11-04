import datetime
import json
import os
import sqlite3

import config

SCHEMA = """
create table if not exists events(id integer primary key, tick integer, kind text, agent text, data text);
create table if not exists usage(
  day text, provider text, n integer default 0, tokens integer default 0, primary key(day, provider));
create table if not exists kv(key text primary key, value text);
"""


def utc_day():
    return datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d")


def world_db_path(namespace):
    return os.path.join(config.DB_DIR, f"{namespace}.db")


class DB:
    def __init__(self, path):
        # check_same_thread=False: kopf runs sync handlers in a thread pool.
        self.c = sqlite3.connect(path, check_same_thread=False)
        self.c.row_factory = sqlite3.Row
        self.c.executescript(SCHEMA)

    def q(self, sql, args=()):
        return self.c.execute(sql, args).fetchall()

    def one(self, sql, args=()):
        return self.c.execute(sql, args).fetchone()

    def x(self, sql, args=()):
        cur = self.c.execute(sql, args)
        self.c.commit()
        return cur.lastrowid

    # --- key/value ---
    def get(self, key, default=None):
        r = self.one("select value from kv where key=?", (key,))
        return json.loads(r["value"]) if r else default

    def set(self, key, value):
        self.x("insert into kv(key,value) values(?,?) on conflict(key) do update set value=excluded.value",
               (key, json.dumps(value)))

    # --- eventi ---
    def log(self, kind, agent=None, **data):
        self.x("insert into events(tick,kind,agent,data) values(?,?,?,?)",
               (self.get("tick", 0), kind, agent, json.dumps(data, ensure_ascii=False)))

    def violations(self, promiser, refrains, since_tick):
        return self.one("select count(*) as n from events where agent=? and kind=? and tick>=?",
                        (promiser, refrains, since_tick))["n"]

    # --- quote LLM (per giorno UTC) ---
    def usage(self, provider):
        """(requests, tokens) used today."""
        r = self.one("select n, tokens from usage where day=? and provider=?", (utc_day(), provider))
        return (r["n"], r["tokens"]) if r else (0, 0)

    def add_usage(self, provider, tokens):
        self.x("insert into usage(day,provider,n,tokens) values(?,?,1,?) "
               "on conflict(day,provider) do update set n=n+1, tokens=tokens+excluded.tokens",
               (utc_day(), provider, tokens))
