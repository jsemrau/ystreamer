#!/usr/bin/env python3
"""
Distil the Director: have Claude Opus answer many varied Director prompts,
keep only the answers that pass every harmony check unchanged, and write
them as a chat-format fine-tuning set for the local model.

    python -m eval.distill_director prepare --count 400        # build the prompts, estimate the cost
    python -m eval.distill_director submit                     # send them as one batch (half price)
    python -m eval.distill_director collect                    # wait, check, write train.jsonl
    python -m eval.distill_director sync --count 3             # a few right away, to try it out

The prompts are the stream's own (Director.piece_prompt), filled with
varied situations: every style, many themes, a first track or one that
follows another, recent keys, hooks and progressions to avoid, a random
suggested key and a spark. The answers are checked with the stream's own
checks (Director.review_piece): only a plan that needed no correction at
all, with its own chords for the main part, the breakdown and the peak, a
hook, plain cues for at least four parts of the track, and a sketch dense enough for a Switch Angel style build
(3-5 more parts, 2+ moving knobs, an odd-length cycle, a lead phrase that
changes bar to bar), goes into the training set. Near-duplicates (same key,
chords and hook) are dropped.

Files go to dataset/director/<name>/: requests.jsonl (prompt + the
situation, to check the answers against), batch.json, raw.jsonl (every
answer), train.jsonl ({"messages": [system, user, assistant]}),
rejected.jsonl (with why) and stats.json.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from collections import Counter, deque

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from music import llm  # noqa: E402
from music.llm import CACHE_BREAK  # noqa: E402
from music.blocks import BLOCKS  # noqa: E402
from music.director import Director  # noqa: E402
from music.plan import STYLES, Piece, plan_piece  # noqa: E402
from music.theory import PROGRESSIONS  # noqa: E402

MODEL = "claude-opus-5"
# Batch prices are half the list price ($5 / $25 per million tokens for Opus 5).
PRICE_IN, PRICE_OUT = 2.5 / 1e6, 12.5 / 1e6
# What an answer costs on the output side, thinking included (a guess; the
# real figure is in stats.json after `collect`).
EST_OUTPUT_TOKENS = 1500

# Themes are put together from these, so no two prompts read the same.
PLACES = ["Tokyo", "Berlin warehouse", "Lisbon rooftop", "Icelandic coast", "desert highway", "orbital station",
          "Kyoto temple garden", "Detroit factory", "Ibiza sunrise", "Seoul subway", "Marrakech night market",
          "Norwegian fjord", "Hong Kong harbour", "Amsterdam canal", "Mexico City", "Bali rice terraces",
          "Glasgow rain", "Cairo rooftop", "abandoned mall", "neon arcade", "mountain observatory",
          "late-night ferry", "Chicago train yard", "Rio beach", "snowed-in cabin", "London night bus"]
MOODS = ["rainy", "golden-hour", "midnight", "euphoric", "melancholic", "hazy", "stormy", "weightless",
         "nostalgic", "hopeful", "dark", "sun-drenched", "frozen", "feverish", "tender", "hypnotic",
         "triumphant", "lonely", "dreamy", "restless"]
GENRE_WORDS = {
    "trance": ["trance", "uplifting trance", "acid trance", "psytrance", "Switch Angel style trance"],
    "techno": ["techno", "acid techno", "hard techno"], "hardcore": ["hardcore", "gabber"],
    "darkwave": ["darkwave", "coldwave", "EBM"], "deephouse": ["deep house", "house"],
    "lofi": ["lofi beats", "lofi"], "ambient": ["ambient", "drone"], "downtempo": ["downtempo", "trip hop"],
    "piano": ["solo piano", "piano"],
}
# How often each style is asked for (the stream plays trance most).
STYLE_WEIGHTS = {"trance": 5, "techno": 2, "hardcore": 1, "darkwave": 1, "deephouse": 2, "lofi": 2,
                 "ambient": 1, "downtempo": 1, "piano": 1}

OUT_ROOT = os.path.join(config.REPO_DIR, "dataset", "director")


def _theme(rng: random.Random, style: str) -> str:
    parts = [rng.choice(MOODS), rng.choice(PLACES)]
    rng.shuffle(parts)
    return f"{parts[0].capitalize()} {parts[1]} {rng.choice(GENRE_WORDS[style])}"


def _block_hooks(style: str) -> list[str]:
    """Hooks from the style's own blocks, for 'recently played' lists."""
    out = []
    for block in BLOCKS.get(style, []):
        tune = block.hook_slot()
        if tune:
            out += [f"{block.role}: {v}" for v in block.patterns[tune[0]]]
    return out


def _situation(rng: random.Random, index: int) -> dict:
    """A random moment in a stream: style, theme, what played before."""
    style = rng.choices(list(STYLE_WEIGHTS), weights=list(STYLE_WEIGHTS.values()))[0]
    director = Director(None, random.Random(rng.randrange(1 << 30)), style=style, theme=_theme(rng, style),
                        log=lambda *_: None)
    previous = None
    if rng.random() > 0.25:
        previous = plan_piece(random.Random(rng.randrange(1 << 30)), style=style)
        previous.title = f"{rng.choice(MOODS).capitalize()} {rng.choice(PLACES).split()[-1].capitalize()}"
        hooks = _block_hooks(style)
        if hooks and rng.random() < 0.7:
            role, pattern = rng.choice(hooks).split(": ", 1)
            previous.hook = {"role": role, "pattern": pattern}
        others = [plan_piece(random.Random(rng.randrange(1 << 30)), style=style) for _ in range(rng.randint(0, 3))]
        director.recent_keys = deque([p.key for p in others] + [previous.key], maxlen=4)
        director.recent_progressions = deque([p.progression for p in others] + [previous.progression], maxlen=4)
        director.recent_hooks = deque(rng.sample(hooks, min(len(hooks), rng.randint(0, 3))), maxlen=4)
    avoid = {"progressions": set(), "presets": set()}
    if rng.random() < 0.2:
        avoid["progressions"] = {p.name for p in rng.sample(PROGRESSIONS, 2)}
    fallback = plan_piece(director.rng, previous, style=style,
                          avoid_progressions=set(director.recent_progressions), disliked=avoid["progressions"])
    prompt = director.piece_prompt(previous, fallback, avoid)
    return {
        "custom_id": f"d{index:05d}", "style": style, "theme": director.theme, "prompt": prompt,
        "previous": previous.to_dict() if previous else None, "fallback": fallback.to_dict(),
        "recent_keys": list(director.recent_keys), "recent_progressions": list(director.recent_progressions),
        "recent_hooks": list(director.recent_hooks), "avoid": {k: sorted(v) for k, v in avoid.items()},
    }


def _director_for(req: dict) -> tuple[Director, Piece, dict]:
    director = Director(None, random.Random(0), style=req["style"], theme=req["theme"], log=lambda *_: None)
    director.recent_keys = deque(req["recent_keys"], maxlen=4)
    director.recent_progressions = deque(req["recent_progressions"], maxlen=4)
    director.recent_hooks = deque(req["recent_hooks"], maxlen=4)
    avoid = {k: set(v) for k, v in req["avoid"].items()}
    return director, Piece.from_dict(req["fallback"]), avoid


def check(req: dict, text: str) -> tuple[dict | None, str]:
    """(the answer as JSON, "") when it goes into the training set, else (None, why)."""
    try:
        data = llm.extract_json(text)
    except llm.LLMError as e:
        return None, f"not JSON ({e})"
    director, fallback, avoid = _director_for(req)
    piece, notes = director.review_piece(dict(data), fallback, avoid)
    if notes:
        return None, "; ".join(notes)
    missing = [f for f in ("progression", "hook", "narration", "title") if not data.get(f)]
    if len(piece.cues) < 4:
        missing.append("cues for at least 4 parts of the track")
    sp = data.get("section_progressions") or {}
    missing += [f"section_progressions.{s}" for s in ("breakdown", "chorus") if s not in sp]
    if missing:
        return None, "missing " + ", ".join(missing)
    if not piece.hook:
        return None, "hook dropped"
    thin = sketch_problem(piece)
    if thin:
        return None, thin
    return data, ""


def sketch_problem(piece: Piece) -> str:
    """Why a sketch is too thin for a Switch Angel style build, or ""."""
    from music.blocks import sketch_kinds

    if piece.hook.get("role") in piece.parts:
        return "a part repeats the hook's part"
    if len(piece.parts) < 3:
        return f"only {len(piece.parts)} parts sketched (3-5 wanted)"
    if sum(len(c) for c in piece.motion.values()) < 2:
        return "fewer than 2 moving knobs"
    tunes = [piece.hook] + list(piece.parts.values())
    cycles = [len(t["pattern"].split()) for t in tunes if t.get("kind") == "cycle"]
    if cycles and not any(n not in (4, 8, 16) for n in cycles):
        return "no odd-length cycle against the 4/4"
    if "lead" in sketch_kinds(piece.style):
        lead = piece.hook if piece.hook.get("role") == "lead" else piece.parts.get("lead")
        if not lead or "<" not in lead["pattern"]:
            return "no lead phrase that changes bar to bar"
    return ""


def _paths(name: str) -> dict:
    root = os.path.join(OUT_ROOT, name)
    return {k: os.path.join(root, f) for k, f in (("root", ""), ("requests", "requests.jsonl"),
                                                   ("batch", "batch.json"), ("raw", "raw.jsonl"),
                                                   ("train", "train.jsonl"), ("rejected", "rejected.jsonl"),
                                                   ("stats", "stats.json"))}


def _read_jsonl(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _params(req: dict, effort: str) -> dict:
    # The brief before CACHE_BREAK is the same for every request of a style
    # and theme: cached (batches cache too, best effort), 1h as a batch runs long.
    stable, _, rest = req["prompt"].partition(CACHE_BREAK)
    content = [{"type": "text", "text": stable, "cache_control": {"type": "ephemeral", "ttl": "1h"}}]
    if rest:
        content.append({"type": "text", "text": rest})
    return {"model": MODEL, "max_tokens": 16000,
            "system": Director.PIECE_SYSTEM,
            "output_config": {"effort": effort},
            "messages": [{"role": "user", "content": content}]}


def prepare(args) -> None:
    p = _paths(args.name)
    if os.path.exists(p["requests"]) and not args.force:
        sys.exit(f"{p['requests']} exists (use --force or another --name)")
    os.makedirs(p["root"], exist_ok=True)
    rng = random.Random(args.seed)
    reqs = [_situation(rng, i) for i in range(args.count)]
    with open(p["requests"], "w", encoding="utf-8") as f:
        for r in reqs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    styles = Counter(r["style"] for r in reqs)
    print(f"wrote {len(reqs)} prompts to {p['requests']}")
    print("styles: " + ", ".join(f"{s} {n}" for s, n in styles.most_common()))
    _estimate(reqs, args.effort)


def _estimate(reqs: list[dict], effort: str) -> None:
    """Measured input tokens for one prompt (token counting is free), times the count."""
    import anthropic

    try:
        sample = anthropic.Anthropic().messages.count_tokens(
            **{k: v for k, v in _params(reqs[0], effort).items() if k not in ("max_tokens", "output_config")})
        per_in = sample.input_tokens
    except Exception as e:  # no credentials, offline: estimate from characters
        print(f"(token count unavailable: {e}; estimating from length)")
        per_in = len(reqs[0]["prompt"]) // 3
    cost = len(reqs) * (per_in * PRICE_IN + EST_OUTPUT_TOKENS * PRICE_OUT)
    print(f"about {per_in:,} input tokens per prompt; at batch prices for {MODEL} the whole set costs "
          f"roughly ${cost:.2f} (output assumed ~{EST_OUTPUT_TOKENS} tokens each, thinking included)")


def submit(args) -> None:
    import anthropic
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request

    p = _paths(args.name)
    if os.path.exists(p["batch"]) and not args.force:
        sys.exit(f"already submitted ({p['batch']}); `collect` it, or --force to send again")
    reqs = _read_jsonl(p["requests"])
    client = anthropic.Anthropic()
    batch = client.messages.batches.create(requests=[
        Request(custom_id=r["custom_id"], params=MessageCreateParamsNonStreaming(**_params(r, args.effort)))
        for r in reqs])
    with open(p["batch"], "w") as f:
        json.dump({"id": batch.id, "model": MODEL, "effort": args.effort, "count": len(reqs),
                   "submitted": time.strftime("%Y-%m-%d %H:%M:%S")}, f, indent=1)
    print(f"batch {batch.id} submitted ({len(reqs)} requests); run `collect --name {args.name}` "
          "(most batches finish within an hour)")


def collect(args) -> None:
    import anthropic

    p = _paths(args.name)
    with open(p["batch"]) as f:
        batch_id = json.load(f)["id"]
    client = anthropic.Anthropic()
    while True:
        batch = client.messages.batches.retrieve(batch_id)
        if batch.processing_status == "ended":
            break
        c = batch.request_counts
        print(f"{batch.processing_status}: {c.processing} processing, {c.succeeded} done, {c.errored} errored")
        if not args.wait:
            sys.exit("not finished yet; run collect again later (or add --wait)")
        time.sleep(60)
    answers, usage = {}, Counter()
    for result in client.messages.batches.results(batch_id):
        if result.result.type != "succeeded":
            answers[result.custom_id] = {"error": result.result.type}
            continue
        msg = result.result.message
        usage["input"] += msg.usage.input_tokens
        usage["output"] += msg.usage.output_tokens
        if msg.stop_reason == "refusal":
            answers[result.custom_id] = {"error": "refusal"}
            continue
        answers[result.custom_id] = {"text": "".join(b.text for b in msg.content if b.type == "text"),
                                     "stop_reason": msg.stop_reason}
    _finish(p, answers, usage)


def sync(args) -> None:
    """A few prompts through the regular API, to see the answers right away."""
    import anthropic

    p = _paths(args.name)
    os.makedirs(p["root"], exist_ok=True)
    rng = random.Random(args.seed)
    reqs = [_situation(rng, i) for i in range(args.count)]
    with open(p["requests"], "w", encoding="utf-8") as f:
        for r in reqs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    client = anthropic.Anthropic()
    answers, usage = {}, Counter()
    for r in reqs:
        msg = client.beta.messages.create(**_params(r, args.effort), betas=["server-side-fallback-2026-07-01"],
                                          fallbacks="default")
        usage["input"] += msg.usage.input_tokens
        usage["output"] += msg.usage.output_tokens
        answers[r["custom_id"]] = ({"error": "refusal"} if msg.stop_reason == "refusal" else
                                   {"text": "".join(b.text for b in msg.content if b.type == "text"),
                                    "stop_reason": msg.stop_reason})
        print(f"{r['custom_id']} {r['style']} · {r['theme']}: {answers[r['custom_id']].get('text', '')[:300]}\n")
    _finish(p, answers, usage, price_factor=2.0)


def _finish(p: dict, answers: dict, usage: Counter, price_factor: float = 1.0) -> None:
    reqs = {r["custom_id"]: r for r in _read_jsonl(p["requests"])}
    kept, rejected, seen = [], [], set()
    for cid, answer in answers.items():
        req = reqs.get(cid)
        if req is None:
            continue
        if "error" in answer:
            rejected.append({"custom_id": cid, "why": answer["error"]})
            continue
        if answer.get("stop_reason") == "max_tokens":
            rejected.append({"custom_id": cid, "why": "cut off", "text": answer["text"]})
            continue
        data, why = check(req, answer["text"])
        if data is None:
            rejected.append({"custom_id": cid, "why": why, "text": answer["text"]})
            continue
        signature = (data["key"], data["progression"], json.dumps(data["hook"], sort_keys=True))
        if signature in seen:
            rejected.append({"custom_id": cid, "why": "duplicate", "text": answer["text"]})
            continue
        seen.add(signature)
        kept.append((req, data))
    with open(p["raw"], "w", encoding="utf-8") as f:
        for cid, answer in answers.items():
            f.write(json.dumps({"custom_id": cid, **answer}, ensure_ascii=False) + "\n")
    with open(p["train"], "w", encoding="utf-8") as f:
        for req, data in kept:
            f.write(json.dumps({"messages": [
                {"role": "system", "content": Director.PIECE_SYSTEM},
                {"role": "user", "content": req["prompt"].replace(CACHE_BREAK, "\n")},
                {"role": "assistant", "content": json.dumps(data, ensure_ascii=False)}],
                "meta": {"custom_id": req["custom_id"], "style": req["style"], "theme": req["theme"],
                         "teacher": MODEL}}, ensure_ascii=False) + "\n")
    with open(p["rejected"], "w", encoding="utf-8") as f:
        for r in rejected:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    datas = [d for _, d in kept]
    stats = {
        "answers": len(answers), "kept": len(kept), "rejected": len(rejected),
        "rejected_why": Counter(r["why"].split(" (")[0].split(";")[0] for r in rejected).most_common(10),
        "modes": Counter(d["key"].split()[-1] for d in datas).most_common(),
        "distinct_keys": len({d["key"] for d in datas}), "distinct_progressions": len({d["progression"] for d in datas}),
        "distinct_hooks": len({d["hook"]["pattern"] for d in datas}), "distinct_titles": len({d["title"] for d in datas}),
        "hook_parts": Counter(d["hook"]["role"] for d in datas).most_common(),
        "parts_per_sketch": round(sum(len(d.get("parts") or {}) for d in datas) / max(1, len(datas)), 2),
        "moving_knobs_per_sketch": round(sum(sum(len(c) for c in (d.get("motion") or {}).values())
                                             for d in datas) / max(1, len(datas)), 2),
        "tokens": dict(usage),
        "cost_usd": round(price_factor * (usage["input"] * PRICE_IN + usage["output"] * PRICE_OUT), 2),
    }
    with open(p["stats"], "w") as f:
        json.dump(stats, f, indent=1)
    print(json.dumps(stats, indent=1))
    print(f"training set: {p['train']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("command", choices=["prepare", "submit", "collect", "sync"])
    parser.add_argument("--name", default="set1", help="dataset/director/<name>/")
    parser.add_argument("--count", type=int, default=200)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--effort", default="high", choices=["low", "medium", "high", "xhigh", "max"])
    parser.add_argument("--wait", action="store_true", help="collect: poll until the batch has finished")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    {"prepare": prepare, "submit": submit, "collect": collect, "sync": sync}[args.command](args)


if __name__ == "__main__":
    main()
