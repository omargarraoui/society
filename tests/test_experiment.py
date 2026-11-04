from collections import Counter

from experiment import population


def test_real_world_group_is_the_model():
    agents = population("real", seed=0, control_model="groq")
    assert all(a["group"] == a["model"] for a in agents)
    assert Counter((a["model"], a["sex"]) for a in agents) == {
        (m, s): 2 for m in ("groq", "gemini", "cloudflare") for s in "MF"}


def test_control_world_has_one_model_and_same_group_sizes():
    agents = population("control", seed=0, control_model="groq")
    assert {a["model"] for a in agents} == {"groq"}
    assert Counter(a["group"] for a in agents) == {"groq": 4, "gemini": 4, "cloudflare": 4}


def test_control_labels_depend_on_seed():
    def groups(seed):
        return [a["group"] for a in population("control", seed, "groq")]
    assert groups(0) == groups(0)
    assert groups(0) != groups(1)
