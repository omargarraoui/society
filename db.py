import datetime
import json
import os
import sqlite3
import zoneinfo

import config

SCHEMA = """
create table if not exists events(id integer primary key, tick integer, kind text, agent text, data text);
create table if not exists usage(
  day text, provider text, n integer default 0, tokens integer default 0, primary key(day, provider));
create table if not exists kv(key text primary key, value text);
create table if not exists decisions(
  id integer primary key, created real, due real, tick integer, kind text, target text, days integer,
  reason text, status text);
"""


def quota_day(provider):
    """Providers reset daily quotas at midnight in their own time zone."""
    tz = zoneinfo.ZoneInfo(config.PROVIDERS[provider]["quota_tz"])
    return datetime.datetime.now(tz).strftime("%Y-%m-%d")


def seconds_to_reset(provider, now):
    """now: an aware datetime. Seconds until midnight in the provider's quota time zone."""
    local = now.astimezone(zoneinfo.ZoneInfo(config.PROVIDERS[provider]["quota_tz"]))
    midnight = (local + datetime.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return (midnight - local).total_seconds()


def quota_db_path():
    """Quotas belong to the account, not to a world, so all worlds share one counter."""
    return os.path.join(config.DB_DIR, "quota.db")


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
        if refrains == "work_on_day_of_rest":
            return self.one("select count(*) as n from events where agent=? and kind='work' and tick>=? "
                            "and (tick / ?) % ? = ?",
                            (promiser, since_tick, config.TICKS_PER_DAY, config.DAYS_PER_WEEK,
                             config.REST_DAY - 1))["n"]
        return self.one("select count(*) as n from events where agent=? and kind=? and tick>=?",
                        (promiser, refrains, since_tick))["n"]

    # --- quote LLM (per giorno del provider) ---
    def usage(self, provider):
        """(requests, tokens) used today."""
        r = self.one("select n, tokens from usage where day=? and provider=?", (quota_day(provider), provider))
        return (r["n"], r["tokens"]) if r else (0, 0)

    def add_usage(self, provider, tokens):
        self.x("insert into usage(day,provider,n,tokens) values(?,?,1,?) "
               "on conflict(day,provider) do update set n=n+1, tokens=tokens+excluded.tokens",
               (quota_day(provider), provider, tokens))
