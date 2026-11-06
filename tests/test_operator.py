from promise_operator import verdicts


def judged(assessor, kept, tick, reason=None):
    return {"assessor": assessor, "promiser": "adam", "promise": "adam-x", "kept": kept, "tick": tick,
            **({"reason": reason} if reason else {})}


def test_agents_are_counted_and_god_s_latest_verdict_wins():
    assessments = [judged("eve", True, 10), judged("god", False, 30, "Worked on the day of rest."),
                   judged("god", True, 5, "Kept it."), judged("seth", False, 40)]
    assert verdicts(assessments) == {"judgedKept": 1, "judgedBroken": 1,
                                     "godVerdict": {"kept": False, "reason": "Worked on the day of rest.", "tick": 30}}


def test_without_god_there_is_no_verdict():
    assert verdicts([judged("eve", True, 10)]) == {"judgedKept": 1, "judgedBroken": 0}
