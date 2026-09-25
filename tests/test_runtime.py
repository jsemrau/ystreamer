from music import runtime


def test_overlay_wraps_and_strips_percent(isolated_runtime):
    runtime.write_overlay("Title · C minor", ["x" * 300, "gain 50%"], width=100, max_lines=3)
    code = (isolated_runtime / "code.txt").read_text().splitlines()
    assert len(code) == 3 and code[-1].strip() == "..."
    assert "%" not in (isolated_runtime / "code.txt").read_text()


def test_state_roundtrip_and_heartbeat(isolated_runtime):
    runtime.save_state({"a": 1})
    assert runtime.load_state() == {"a": 1}
    runtime.heartbeat()
    assert runtime.heartbeat_age() < 5
