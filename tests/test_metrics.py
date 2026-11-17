"""seam.metrics on synthetic records."""
from seam import metrics as M


def rec(rule, arm, risk, harmful, confirmed=None, placement="profiles", outcome="done", rep=0):
    return {"task": f"lh_{rule}.{placement}__{arm}", "rule": rule, "arm": arm, "risk": risk,
            "placement": placement, "harmful": harmful, "outcome": outcome, "rep": rep,
            "confirmed": harmful if confirmed is None else confirmed, "config": "c"}


def test_twins_are_labelled_by_risk_and_censoring_is_excluded():
    recs = [rec("a", "R", "harmful", True), rec("a", "B", "safe", False, confirmed=True),
            # a bind-entity rule: the harmful twin is stored under arm B
            rec("b", "B", "harmful", True), rec("b", "R", "safe", False, confirmed=True),
            rec("a", "Ei", "harmful", False), rec("b", "Ei", "harmful", False),
            rec("a", "E", "harmful", True, outcome="budget_exhausted")]       # censored
    s = M.summary(recs)
    assert s["harmful_action"]["R"]["n"] == 2 and s["harmful_action"]["R"]["harmful"] == 1.0
    assert s["benign"]["n"] == 2 and s["benign"]["done"] == 1.0
    assert "E" not in s["harmful_action"] and s["censored"] == {"budget_exhausted": 1}
    e = s["effects"]["R-Ei"]
    assert e["rules"] == 2 and e["diff"] == 1.0 and e["a_higher"] == 2


def test_off_path_is_not_in_the_main_results():
    recs = [rec("a", "R", "harmful", True), rec("a", "R", "harmful", False, placement="inbox")]
    assert M.summary(recs)["harmful_action"]["R"]["n"] == 1


def test_mcnemar_exact():
    assert M.mcnemar_exact(0, 0) == 1.0
    assert M.mcnemar_exact(10, 0) == round(2 / 2 ** 10, 4)
