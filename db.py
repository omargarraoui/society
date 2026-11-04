import json
import sqlite3
import datetime

SCHEMA = """
create table if not exists agents(
  id integer primary key, name text unique, sex text, model text,
  role text default 'none', parent_a integer, parent_b integer,
  alive integer default 1, born_tick integer, food integer default 5,
  summary text default '');
create table if not exists marriages(id integer primary key, a integer, b integer, tick integer);
create table if not exists requests(
  id integer primary key, kind text, agent_id integer, target_id integer,
  payload text, status text default 'pending', tick integer);
create table if not exists events(id integer primary key, tick integer, kind text, agent_id integer, data text);
create table if not exists usage(day text, provider text, n integer default 0, primary key(day, provider));
create table if not exists kv(key text primary key, value text);
"""


class DB:
    def __init__(self, path):
        self.c = sqlite3.connect(path)
        self.c.row_factory = sqlite3.Row
        self.c.executescript(SCHEMA)

    def q(self, sql, args=()):
        return self.c.execute(sql, args).fetchall()

    def one(self, sql, args=()):
        r = self.c.execute(sql, args).fetchone()
        return r

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
    def log(self, kind, agent_id=None, **data):
        self.x("insert into events(tick,kind,agent_id,data) values(?,?,?,?)",
               (self.get("tick", 0), kind, agent_id, json.dumps(data, ensure_ascii=False)))

    # --- quote LLM (per giorno UTC) ---
    def usage(self, provider):
        day = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
        r = self.one("select n from usage where day=? and provider=?", (day, provider))
        return r["n"] if r else 0

    def add_usage(self, provider):
        day = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
        self.x("insert into usage(day,provider,n) values(?,?,1) "
               "on conflict(day,provider) do update set n=n+1", (day, provider))
