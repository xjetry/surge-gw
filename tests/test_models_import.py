from surge_gw.models import RuleResult, SkippedItem


def test_models_construct():
    r = RuleResult()
    r.lines.append("FINAL,DIRECT")
    r.skipped.append(SkippedItem("rule", "DSCP,1,DIRECT", "unsupported"))
    assert r.lines == ["FINAL,DIRECT"]
    assert r.skipped[0].kind == "rule"
