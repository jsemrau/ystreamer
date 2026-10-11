"""
The arranger: what a producer does *between* sections, which is most of what
makes club music lift. The Director plans the arc and the Performer plays
the lines; the arranger adds the transitions on the arc's turning points:

- rebuild (the tension before the peak): a riser that sweeps up over 8 bars,
  and a snare roll that speeds up from quarters to 32nds and gets louder;
- the drop into the peak: riser and roll stop, one bar without kick and
  bass (the breath), then everything lands together with an impact;
- build -> groove: an impact on the downbeat;
- groove and peak: a snare fill in the last bar of every 16-bar phrase;
- a lift (pre-chorus): the riser alone, into the peak;
- a break: everything stops; what comes after lands with an impact;
- tag, bridge and interlude: the phrase fills, as in the groove;
- breakdown and outro: none of this.

Club styles only; calm styles keep their own shape. The devices run on
their own channels (d10 riser / impact, d11 roll / fills), outside the
performer's program, so they never count as its lines.
"""

from __future__ import annotations

CLUB_STYLES = {"trance", "techno", "hardcore", "deephouse", "darkwave"}
RISER, FILL = 10, 11
# The breath before the drop silences these (the kick and the bass).
BREATH_ROLES = ("beat", "bass")


def _snare(style: str) -> str:
    return "808sd" if style == "darkwave" else "sn"


def riser() -> str:
    return f'd{RISER} $ slow 8 $ s "ys_riser" # legato 1 # gain 0.9'


def roll(style: str) -> str:
    sn = _snare(style)
    return (f'd{FILL} $ slow 4 $ s "{sn}*4 {sn}*8 {sn}*16 {sn}*32" '
            f'# gain (slow 4 $ range 0.62 0.95 saw) # hpf 350')


def phrase_fill(style: str) -> str:
    sn = _snare(style)
    return (f'd{FILL} $ whenmod 16 15 (const $ s "{sn}*16" # gain (range 0.55 0.9 saw) # hpf 400) '
            f'$ silence')


def impact() -> str:
    return 'once $ s "ys_impact" # gain 1'


def silence(channel: int) -> str:
    return f"d{channel} $ silence"


def plan_for(style: str, before: str | None, after: str) -> dict:
    """What changes when the arc moves from `before` to `after`:
    {"statements": [...], "breath": bool, "impact": bool, "note": str,
     "playing": {channel: statement}} (the devices that play from now on)."""
    if style not in CLUB_STYLES:
        return {"statements": [silence(RISER), silence(FILL)], "breath": False, "impact": False,
                "note": "", "playing": {}}
    if after == "lift":
        playing = {RISER: riser()}
        return {"statements": [riser(), silence(FILL)], "breath": False, "impact": False, "playing": playing,
                "note": "the lift: a riser sweeping up into the peak"}
    if after == "break":
        return {"statements": [silence(RISER), silence(FILL)], "breath": False, "impact": False, "playing": {},
                "note": "the break: the kick and bass drop out"}
    if after in ("tag", "bridge", "interlude"):
        playing = {FILL: phrase_fill(style)}
        return {"statements": [silence(RISER), phrase_fill(style)], "breath": False, "impact": before == "break",
                "playing": playing, "note": {"tag": "the tag: the hook alone over the beat",
                                             "bridge": "the bridge: new chords, the hook rests",
                                             "interlude": "the interlude: another line takes the lead"}[after]}
    if after == "rebuild":
        playing = {RISER: riser(), FILL: roll(style)}
        return {"statements": list(playing.values()), "breath": False, "impact": False, "playing": playing,
                "note": "tension for the peak: a riser sweeping up and a snare roll speeding up"}
    if after in ("groove", "groove2"):
        drop = before in ("rebuild", "lift") or after == "groove2"
        playing = {FILL: phrase_fill(style)}
        note = ("the drop: riser and roll stop, one bar without kick and bass, then it all lands with an impact"
                if drop else "the groove lands with an impact; a snare fill ends every 16-bar phrase")
        return {"statements": [silence(RISER), phrase_fill(style)], "breath": drop, "impact": True,
                "playing": playing, "note": note}
    # build, breakdown, outro: nothing extra.
    note = {"breakdown": "the fills stop for the breakdown", "outro": "the fills stop for the outro"}.get(after, "")
    return {"statements": [silence(RISER), silence(FILL)], "breath": False, "impact": False, "playing": {},
            "note": note}
