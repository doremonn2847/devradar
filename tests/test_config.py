from devradar.config import load_config


def test_config_defaults(tmp_path):
    p = tmp_path / "empty.yaml"
    p.write_text("", encoding="utf-8")
    cfg = load_config(p)
    assert cfg["collector"]["interval_min"] == 20
    assert cfg["collector"]["finished_after_min"] == 30
    assert cfg["cost"]["soft_cap_usd"] == 6
    assert cfg["rules"]["cooldown_days"] == 3
    assert cfg["timezone"] == "Asia/Bangkok"


def test_config_override_merges(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("collector:\n  interval_min: 5\n", encoding="utf-8")
    cfg = load_config(p)
    assert cfg["collector"]["interval_min"] == 5
    assert cfg["collector"]["finished_after_min"] == 30
