import datetime

from fakes import at

from bot import alert_texts, batches, digest_due, digest_text, handle
from db import DB

PROMISE = {"metadata": {"name": "p"}, "spec": {"body": "I will not steal."}}
AGENTS = {
    "adam": {"name": "adam", "sex": "M", "model": "groq", "spouse": "eve", "promises": [PROMISE], "food": 5},
    "eve": {"name": "eve", "sex": "F", "model": "gemini", "spouse": "adam", "promises": [], "food": 5},
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
    assert handle("ciao", "genesis", world_db(tmp_path, []), None, AGENTS) == (
        "Comandi: /stato, /pausa, /riprendi, /annulla <numero della sentenza>")


def test_status_shows_agents_quotas_and_pause(tmp_path):
    db = world_db(tmp_path, [(at(1, 8), "work", "adam", {})])
    db.set("paused", True)
    quota = DB(str(tmp_path / "q.db"))
    quota.add_usage("groq", 790)
    text = handle("/stato@society_bot", "genesis", db, quota, AGENTS)
    assert text.startswith(f"Mondo genesis: week 1, day 1, 08:00 (tick {at(1, 8)}), IN PAUSA")
    assert "- Adam (groq), sposato con Eve, promesse: 1" in text
    assert "- groq: 1/1000 richieste, 790 token" in text
    assert "- Week 1, day 1, 08:00: Adam worked" in text


def test_alerts_for_marriage_and_theft_and_counted_skips(tmp_path):
    db = world_db(tmp_path, [(at(1, 8), "accept_marriage", "eve", {"target": "adam"}),
                             (at(1, 9), "steal", "adam", {"target": "eve", "amount": 3, "taken": 2}),
                             (at(1, 9), "speak", "eve", {"target": "adam", "message": "Give it back."}),
                             (at(1, 9), "work", "eve", {}),
                             (32, "skip", "eve", {"reason": "gemini: quota exhausted"}),
                             (33, "skip", "eve", {"reason": "gemini: quota exhausted"})])
    texts, skips = alert_texts(rows(db))
    assert texts == ["Matrimonio: Eve e Adam", "Furto: Adam ha rubato 2 cibo a Eve",
                     'Week 1, day 1, 09:00: Eve said to Adam: "Give it back."']
    assert skips == {"gemini: quota exhausted": 2}


def test_digest_lists_new_events_and_returns_the_last_id(tmp_path):
    db = world_db(tmp_path, [(at(1, 7), "work", "adam", {}),
                             (at(1, 8), "promise", "eve",
                              {"message": "I will rest.", "refrains": "work_on_day_of_rest"}),
                             (at(1, 9), "work", "adam", {})])
    text, last_id = digest_text("genesis", db, since_id=0)
    assert text.splitlines() == ["Digest genesis, da week 1, day 1, 07:00 a week 1, day 1, 09:00",
                                 "Azioni: work 2, promise 1",
                                 '- Week 1, day 1, 08:00: Eve promised: "I will rest."']
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


def pending_death(db):
    return db.x("insert into decisions(created, due, tick, kind, target, days, reason, status) "
                "values(0, 86400, 28, 'death', 'adam', NULL, 'He killed.', 'pending')")


def test_annulla_vetoes_a_pending_sentence_and_the_world_hears_the_pardon(tmp_path):
    db = world_db(tmp_path, [])
    decision = pending_death(db)
    assert handle(f"/annulla {decision}", "genesis", db, None, AGENTS) == (
        f"Sentenza #{decision} annullata: Adam non subira' death.")
    assert db.one("select status from decisions")["status"] == "vetoed"
    assert rows(db)[-1]["kind"] == "pardon"
    assert handle(f"/annulla {decision}", "genesis", db, None, AGENTS) == f"Nessuna sentenza #{decision} in attesa."


def test_annulla_needs_a_number(tmp_path):
    assert handle("/annulla", "genesis", world_db(tmp_path, []), None, AGENTS).startswith("Uso: /annulla")


def test_alerts_for_sentences_and_anomalies(tmp_path):
    db = world_db(tmp_path, [(28, "sentence", "god", {"target": "adam", "penalty": "exile", "days": 2,
                                                      "reason": "He stole.", "decision": 4}),
                             (28, "sentence", "god", {"target": "eve", "penalty": "warning", "reason": "She lied."}),
                             (28, "anomaly", "god", {"reason": "too many deaths in a day: world paused",
                                                     "sentence": {"target": "eve", "penalty": "death"}})])
    texts, _ = alert_texts(rows(db))
    assert texts[0] == ("Sentenza #4: Adam, exile per 2 giorni. Motivo: He stole.\n"
                        "Diventa effettiva tra 24 ore. Per annullarla: /annulla 4")
    assert texts[1] == "Dio a Eve: warning. Motivo: She lied."
    assert texts[2].startswith("ALLARME: too many deaths in a day: world paused.")


def test_one_round_goes_out_as_one_message_split_only_at_the_limit():
    assert batches([]) == []
    assert batches(["a", "b"]) == ["a\n\nb"]
    assert batches(["x" * 30, "y" * 30], limit=50) == ["x" * 30, "y" * 30]
