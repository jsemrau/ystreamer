from stream.watchdog import SilenceTracker, WatchdogConfig, decide


def cfg(**kw):
    c = WatchdogConfig()
    c.silence_seconds, c.heartbeat_limit, c.cooldown, c.startup_grace = 20, 180, 120, 60
    for k, v in kw.items():
        setattr(c, k, v)
    return c


def test_tracker_parses_silencedetect():
    t = SilenceTracker()
    t.feed("[silencedetect @ 0x1] silence_start: 12.5", now=100)
    assert t.silent_for(125) == 25
    t.feed("[silencedetect @ 0x1] silence_end: 40 | silence_duration: 27.5", now=126)
    assert t.silent_for(130) == 0


def test_decide():
    c = cfg()
    assert decide(30, 10, c, since_last_restart=1e9, uptime=1e9) == "stream silent for 30s"
    assert decide(5, 500, c, 1e9, 1e9).startswith("engine heartbeat")
    assert decide(5, 10, c, 1e9, 1e9) is None
    assert decide(30, 10, c, since_last_restart=10, uptime=1e9) is None  # cooldown
    assert decide(30, 10, c, 1e9, uptime=10) is None                    # startup grace
    assert decide(0, None, c, 1e9, 1e9) is None
