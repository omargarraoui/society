import datetime

from bot import alert_texts, digest_due, digest_text, handle
from db import DB

PROMISE = {"metadata": {"name": "p"}, "spec": {"body": "I will not steal."}}
AGENTS = {
    "adam": {"name": "adam", "sex": "M", "model": "groq", "spouse": "eve", "promises": [PROMISE]},
    "eve": {"name": "eve", "sex": "F", "model": "gemini", "spouse": "adam", "promises": []},
}


def world_db(tmp_path, events):
    db = DB(str(tmp_path / "w.db"))
    for tick, kind, agent, data in events:
        db.set("tick", tick)
        db.log(kind, agent, **data)
    return db


def rows(db):
    return db.q("select id, tick, kind, agent, data from events order by id")


def test_pause_and_resume_set_the_flag_the_world_reads(tmp_path):
    db = world_db(tmp_path, [])
    handle("/pausa", "genesis", db, None, AGENTS)
    assert db.get("paused") is True
    handle("/riprendi", "genesis", db, None, AGENTS)
    assert db.get("paused") is False


def test_unknown_text_gets_the_help(tmp_path):
    assert handle("ciao", "genesis", world_db(tmp_path, []), None, AGENTS) == "Comandi: /stato, /pausa, /riprendi"


def test_status_shows_agents_quotas_and_pause(tmp_path):
    db = world_db(tmp_path, [(30, "work", "adam", {})])
    db.set("paused", True)
    quota = DB(str(tmp_path / "q.db"))
    quota.add_usage("groq", 790)
    text = handle("/stato@society_bot", "genesis", db, quota, AGENTS)
    assert text.startswith("Mondo genesis: week 1, day 1, 07:30 (tick 30), IN PAUSA")
    assert "- Adam (groq), sposato con Eve, promesse: 1" in text
    assert "- groq: 1/1000 richieste, 790 token" in text
    assert "- Week 1, day 1, 07:30: Adam worked" in text


def test_alerts_for_marriage_and_theft_and_counted_skips(tmp_path):
    db = world_db(tmp_path, [(30, "accept_marriage", "eve", {"target": "adam"}),
                             (31, "steal", "adam", {"target": "eve"}),
                             (31, "work", "eve", {}),
                             (32, "skip", "eve", {"reason": "gemini: quota exhausted"}),
                             (33, "skip", "eve", {"reason": "gemini: quota exhausted"})])
    texts, skips = alert_texts(rows(db))
    assert texts == ["Matrimonio: Eve e Adam", "Furto: Adam ha rubato a Eve"]
    assert skips == {"gemini: quota exhausted": 2}


def test_digest_lists_new_events_and_returns_the_last_id(tmp_path):
    db = world_db(tmp_path, [(28, "work", "adam", {}),
                             (29, "promise", "eve", {"message": "I will rest.", "refrains": "work_on_day_of_rest"}),
                             (30, "work", "adam", {})])
    text, last_id = digest_text("genesis", db, since_id=0)
    assert text.splitlines() == ["Digest genesis, da week 1, day 1, 07:00 a week 1, day 1, 07:30",
                                 "Azioni: work 2, promise 1",
                                 '- Week 1, day 1, 07:15: Eve promised: "I will rest."']
    assert last_id == 3
    assert digest_text("genesis", db, since_id=3) == ("Digest genesis: nessun evento.", 3)


def test_digest_once_a_day_after_the_hour(tmp_path):
    db = world_db(tmp_path, [])
    evening = datetime.datetime(2026, 11, 5, 21, 5)
    assert not digest_due(db, datetime.datetime(2026, 11, 5, 20, 59))
    assert digest_due(db, evening)
    db.set("digest_day", "2026-11-05")
    assert not digest_due(db, evening)
    assert digest_due(db, datetime.datetime(2026, 11, 6, 21, 0))
