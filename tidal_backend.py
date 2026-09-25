"""
TidalCycles Backend Module (legacy free-form agent loop).

The default mode is now the harmony engine (music/engine.py, run via
orchestrator.py). This module keeps the original composer/creative-director
agent loop available with `orchestrator.py --legacy`, with the Phase 0 fixes
from docs/IMPROVEMENT_PLAN.md applied:

- the broken `scale "c minor"` idiom is rewritten to `scale "minor" ... + 0`
- melodic lines must use `scale` (stay in key); harsh demo synths are swapped
- changes crossfade (xfadeIn) instead of cutting, at a slower default cadence
- at most config.MAX_ACTIVE_LAYERS channels play at once
- every LLM call has a timeout, histories are bounded, paths come from config
- process management lives in audio_stack.py (shared with the engine)
"""

import os
import time
import threading
import re
import concurrent.futures
from collections import deque
from queue import Queue

import audio_stack
import config
from music import normalize as _norm


# Global state
tidal = audio_stack.TidalSession()
is_running = False
agent_thread = None
_last_composer_model_id = None
_last_director_model_id = None
log_queue = Queue()
stop_event = threading.Event()
task_queue = deque(maxlen=config.HISTORY_LIMIT)
accumulated_logs = deque(maxlen=config.HISTORY_LIMIT * 20)
current_song_context = "(No patterns playing yet)"
current_patterns = {}  # Maps channel (d1, d2, etc.) to current pattern
song_history = deque(maxlen=config.HISTORY_LIMIT)  # recent sent patterns (bounded)
executed_tasks_log = deque(maxlen=config.HISTORY_LIMIT)
current_agent_label = "Idle"
agent_conversation_log = deque(maxlen=config.HISTORY_LIMIT)

# Song state (keeps the Creative Director consistent across iterations)
CURRENT_CPS = 0.8
ENABLE_COGNITIVE_WARNINGS = False

def _parse_cps_value(raw: str) -> float | None:
    """Parse a cps value from a numeric string or simple arithmetic expression."""
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    if re.match(r"^[0-9]*\.?[0-9]+$", text):
        try:
            return float(text)
        except Exception:
            return None
    if not re.match(r"^[0-9\.\s\(\)\+\-\*\/]+$", text):
        return None
    try:
        import ast
        import operator

        ops = {
            ast.Add: operator.add,
            ast.Sub: operator.sub,
            ast.Mult: operator.mul,
            ast.Div: operator.truediv,
        }

        def eval_node(node):
            if isinstance(node, ast.Expression):
                return eval_node(node.body)
            if isinstance(node, ast.BinOp) and type(node.op) in ops:
                return ops[type(node.op)](eval_node(node.left), eval_node(node.right))
            if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
                value = eval_node(node.operand)
                return value if isinstance(node.op, ast.UAdd) else -value
            if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
                return float(node.value)
            raise ValueError("Unsupported cps expression")

        parsed = ast.parse(text, mode="eval")
        return float(eval_node(parsed))
    except Exception:
        return None
SONG_STATE = {
    "key": "C minor",
    "section": "intro",
    "started_at": None,  # epoch seconds
    "section_start_bar": 1,  # bar number at which the current section began
}

# SuperDirt instrument aliases (avoid missing instrument errors)
INSTRUMENT_ALIASES = {
    # SuperDirt doesn't ship a plain "piano" instrument; use its synth instead.
    "piano": "superpiano",
    # Common natural-language drum names the LLM reaches for that aren't
    # real SuperDirt sample banks - map to the actual Dirt-Samples bank.
    "kick": "bd",
    "snare": "sn",
    "hihat": "hh",
    "hat": "hh",
    "clap": "cp",
}

# Test mode configuration
TEST_MODE = False
TEST_SAMPLE = 'd1 $ sound "bd [~ bd] sn:1 [bd sn:3]"\n'

# Model configuration (default models, will be overridden during startup)
default_composer_model = "gpt-5-mini"
default_director_model = "gpt-5-mini"
composer_agent = None
creative_director_agent = None

# ============================================================================
# LOGGING
# ============================================================================

def log_message(msg):
    """Add message to log queue for UI display"""
    log_queue.put(msg)
    print(msg)

def get_logs():
    """Get all pending log messages and return accumulated logs"""
    global accumulated_logs
    
    while not log_queue.empty():
        accumulated_logs.append(log_queue.get())
    
    if accumulated_logs:
        return "\n".join(accumulated_logs)
    return ""

def get_song_context():
    """Get current song context (currently playing patterns) for UI display"""
    global current_patterns
    if not current_patterns:
        return "(No patterns playing yet)"
    
    result = []
    for channel in sorted(current_patterns.keys()):
        result.append(current_patterns[channel])
    return "\n\n".join(result)

def get_song_history():
    """Get full history of all patterns sent"""
    global song_history
    if not song_history:
        return "(No patterns sent yet)"
    return "\n\n".join(song_history)

def get_executed_tasks():
    """Get log of executed tasks for UI display"""
    global executed_tasks_log
    if not executed_tasks_log:
        return "(No tasks executed yet)"
    return "\n\n".join(executed_tasks_log)

def get_agent_conversation():
    """Get agent conversation log for UI display."""
    global agent_conversation_log
    return agent_conversation_log

def get_current_agent():
    """Get which agent is currently working for UI display."""
    global current_agent_label
    return current_agent_label or "Idle"

def _parse_initial_song_state(task_text: str) -> dict:
    key = SONG_STATE.get("key", "C minor")
    section = SONG_STATE.get("section", "intro")

    for line in (task_text or "").splitlines():
        m_key = re.match(r"^\s*key\s*:\s*(.+?)\s*$", line, flags=re.IGNORECASE)
        if m_key:
            key = m_key.group(1).strip()
        m_section = re.match(r"^\s*section\s*:\s*(.+?)\s*$", line, flags=re.IGNORECASE)
        if m_section:
            section = m_section.group(1).strip()
        m_cps = re.match(r"^\s*(?:cps|setcps)\s*[:\s]+(.+?)\s*$", line, flags=re.IGNORECASE)
        if m_cps:
            cps_value = _parse_cps_value(m_cps.group(1))
            if cps_value:
                global CURRENT_CPS
                CURRENT_CPS = cps_value

    return {"key": key, "section": section}

_SUGGESTION_META_MARKERS = (
    "final_answer", "the agent", "i need to", "got stuck", "as an ai",
    "python code", "i apologize", "i'm sorry", "let me", "i should",
    "thought process", "required format", "make sure to", "code blob",
)

def _looks_like_valid_suggestion(text: str) -> bool:
    """Reject meta-commentary/self-referential text that isn't an actual musical
    instruction (e.g. a rambling agent reflecting on its own failure instead
    of producing a suggestion)."""
    if not text or len(text) > 300:
        return False
    lower = text.lower()
    return not any(marker in lower for marker in _SUGGESTION_META_MARKERS)

_ACTIONABLE_SUGGESTION_RE = re.compile(
    r"^\s*(?:[A-Za-z][A-Za-z _-]{2,}:\s*)?"
    r"(add|modify|remove|refine|change|update|silence|swap|transpose|thin|bring|raise|lower|reduce|increase)\b",
    re.IGNORECASE,
)

def _extract_actionable_suggestion(text) -> str | None:
    """Pull the single actionable instruction out of a (possibly rambling)
    Director response. Prefers the LAST matching line: a model that reasons
    at length in prose before landing on a decision usually states that
    decision last, and taking only one line (instead of every line) also
    stops a rambling response from flooding the task queue with fragments."""
    if not text:
        return None
    combined = "\n".join(str(s) for s in text) if isinstance(text, list) else str(text)

    candidates = []
    for raw_line in combined.split('\n'):
        line = raw_line.strip()
        match = re.match(r'^\d+[\.\)]\s*(.+)$', line)
        if match:
            line = match.group(1).strip()
        elif line.startswith('#') or line.startswith('/'):
            continue
        if line and _looks_like_valid_suggestion(line) and _ACTIONABLE_SUGGESTION_RE.match(line):
            candidates.append(line)
    return candidates[-1] if candidates else None

def _current_bar() -> int:
    started_at = SONG_STATE.get("started_at")
    if started_at is None:
        return 0
    cycles = max(0.0, (time.time() - float(started_at)) * float(CURRENT_CPS))
    return int(cycles) + 1

def _maybe_update_song_state_from_task(task: str) -> None:
    """Update KEY/SECTION from task text when explicitly specified."""
    if not task:
        return

    m_key = re.search(r"\bkey\s*:\s*([^\n]+)", task, flags=re.IGNORECASE)
    if m_key:
        SONG_STATE["key"] = m_key.group(1).strip()

    m_cps = re.search(r"\b(?:cps|setcps)\s*[:\s]+([^\n]+)", task, flags=re.IGNORECASE)
    if m_cps:
        cps_value = _parse_cps_value(m_cps.group(1))
        if cps_value:
            global CURRENT_CPS
            CURRENT_CPS = cps_value

    # Accept both "SECTION: build" and a "BUILD:" prefix.
    m_section = re.search(r"\bsection\s*:\s*([^\n]+)", task, flags=re.IGNORECASE)
    if m_section:
        new_section = m_section.group(1).strip()
        if new_section != SONG_STATE.get("section"):
            SONG_STATE["section"] = new_section
            SONG_STATE["section_start_bar"] = _current_bar()
        return

    prefix = re.match(r"^\s*([A-Za-z][A-Za-z _-]{2,})\s*:\s*", task)
    if not prefix:
        return

    normalized = prefix.group(1).strip().lower().replace(" ", "_").replace("-", "_")
    allowed = {
        "intro",
        "verse",
        "build",
        "drop",
        "breakdown",
        "bridge",
        "outro",
        "chorus",
        "prechorus",
        "pre_chorus",
    }
    if normalized in allowed:
        normalized = normalized.replace("prechorus", "pre_chorus")
        if normalized != SONG_STATE.get("section"):
            SONG_STATE["section"] = normalized
            SONG_STATE["section_start_bar"] = _current_bar()

def _get_song_state_lines() -> str:
    bar = _current_bar()
    bars_in_section = max(0, bar - SONG_STATE.get("section_start_bar", bar))

    return (
        "=== SONG STATE ===\n"
        f"KEY: {SONG_STATE.get('key', 'C minor')}\n"
        f"SECTION: {SONG_STATE.get('section', 'intro')}\n"
        f"BAR: {bar}\n"
        f"BARS IN SECTION: {bars_in_section}\n"
        f"CPS: {CURRENT_CPS}\n"
    )

# ============================================================================
# TIDAL & SUPERDIRT MANAGEMENT
# ============================================================================

def start_superdirt() -> str:
    """Starts SuperCollider with SuperDirt, custom SynthDefs and master limiter."""
    return audio_stack.start_superdirt()

def start_tidal_session() -> str:
    """Starts a persistent TidalCycles session with ghci."""
    global CURRENT_CPS
    CURRENT_CPS = 0.8
    return tidal.start(cps=CURRENT_CPS)

def stop_tidal_session() -> str:
    """Stops the running TidalCycles and SuperDirt sessions."""
    return " | ".join([tidal.stop(), audio_stack.stop_superdirt()])

def start_recording() -> tuple[str, str]:
    return audio_stack.start_recording()

def stop_recording() -> str:
    return audio_stack.stop_recording()

def _ensure_tidal() -> bool:
    if not tidal.running():
        log_message("[Tidal] Session not running; restarting...")
        log_message(start_tidal_session())
    return tidal.running()

def _track_cps(line: str) -> None:
    global CURRENT_CPS
    cps_match = re.match(r"^\s*(?:setcps|cps)\s+(.+?)\s*$", line, flags=re.IGNORECASE)
    if cps_match:
        cps_value = _parse_cps_value(cps_match.group(1))
        if cps_value:
            CURRENT_CPS = cps_value

def _send_tidal_code_to_ghci(code_lines: list[str]) -> str | None:
    """Send lines to the running ghci session; return GHCi's error text if it rejected any of them."""
    for idx, line in enumerate(code_lines, 1):
        log_message(f"   → Sending pattern {idx}/{len(code_lines)}: {line[:60]}{'...' if len(line) > 60 else ''}")
        _track_cps(line)
    return tidal.send(code_lines)

# ============================================================================
# TIDAL CODE NORMALIZATION (DEFENSIVE REWRITES)
# ============================================================================

def normalize_tidal_code(code: str) -> tuple[str, list[str]]:
    """Rewrite common non-existent instruments to known SuperDirt equivalents."""
    if not code:
        return code, []

    replacements: list[str] = []
    fx_aliases = {
        "phaser": "phasr",
    }

    # Fix `scale "c minor"` (invalid in Tidal: collapses to octave jumps) and
    # swap harsh demo synths for the soft ystreamer voices.
    code, scale_notes = _norm.rewrite_scale_roots(code)
    replacements.extend(scale_notes)
    code, synth_notes = _norm.replace_harsh_instruments(code)
    replacements.extend(synth_notes)

    def replace_instrument_tokens(sound_string: str) -> str:
        updated = sound_string
        for src, dst in INSTRUMENT_ALIASES.items():
            # Replace bare tokens and indexed variants like piano:2, keeping any
            # mini-notation suffixes like (3,8) intact.
            pattern = re.compile(rf"(?<!\w){re.escape(src)}(?::\d+)?(?!\w)", re.IGNORECASE)
            if pattern.search(updated):
                updated = pattern.sub(dst, updated)
                replacements.append(f"{src}->{dst}")
        return updated

    def repl(match: re.Match) -> str:
        keyword = match.group(1)  # sound|s
        quoted = match.group(2)
        updated = replace_instrument_tokens(quoted)
        if updated == quoted:
            return match.group(0)
        return f'{keyword} "{updated}"'

    updated_code = re.sub(r'\b(sound|s)\s+"([^"]*)"', repl, code)

    # Defensive mix fix: if a low-frequency sound has room but no HPF, add one.
    fixed_lines: list[str] = []
    for raw_line in updated_code.split("\n"):
        leading_ws = raw_line[: len(raw_line) - len(raw_line.lstrip())]
        line = raw_line.strip()
        if not line:
            fixed_lines.append(raw_line)
            continue

        low_freq_match = re.search(r"\b(bd|reverbkick|bass\d*|kick)\b", line)
        if low_freq_match and re.search(r"#\s*(room|reverb|rv)\b", line, flags=re.IGNORECASE):
            if not re.search(r"#\s*hpf\s+\d+", line, flags=re.IGNORECASE):
                sound_name = low_freq_match.group(1)
                line = f"{line} # hpf 150"
                replacements.append(f"add_hpf:{sound_name}")

        fixed_lines.append(f"{leading_ws}{line}")

    updated_code = "\n".join(fixed_lines)

    for src, dst in fx_aliases.items():
        pattern = re.compile(rf"(?<!\w){re.escape(src)}(?!\w)", re.IGNORECASE)
        if pattern.search(updated_code):
            updated_code = pattern.sub(dst, updated_code)
            replacements.append(f"{src}->{dst}")

    # De-dup replacement notes while preserving order
    deduped: list[str] = []
    for item in replacements:
        if item not in deduped:
            deduped.append(item)
    return updated_code, deduped

def to_crossfade(line: str, cycles: int = 8) -> str:
    """Turn `dN $ expr` into a crossfade so changes never cut mid-phrase.

    The d1..d12 helpers in BootTidal add `# orbit (N-1)`; transitions don't,
    so the orbit is added explicitly to keep each channel on its own bus.
    """
    m = re.match(r"^d(\d+)\s*\$\s*(.+)$", line.strip())
    if not m:
        m_sil = re.match(r"^d(\d+)\s+silence\s*$", line.strip())
        if m_sil:
            return f"xfadeIn {m_sil.group(1)} {cycles} $ silence"
        return line
    ch, expr = int(m.group(1)), m.group(2).strip()
    if expr == "silence":
        return f"xfadeIn {ch} {cycles} $ silence"
    orbit = f" # orbit {ch - 1}" if ch <= 12 else ""
    return f"xfadeIn {ch} {cycles} $ ({expr}){orbit}"


def _new_channels(code: str) -> set[str]:
    chans = set()
    for line in code.split("\n"):
        m = re.match(r"^(d\d+)\s*\$\s*(.+)$", line.strip())
        if m and m.group(2).strip() != "silence":
            chans.add(m.group(1))
    return chans


def extract_tidal_code(code_snippet) -> str | None:
    """Extract runnable Tidal lines from an agent response (handles markdown fences/bullets)."""
    if not code_snippet:
        return None

    if isinstance(code_snippet, list):
        raw_text = "\n".join(str(x) for x in code_snippet if x is not None)
    else:
        raw_text = str(code_snippet)

    raw_text = raw_text.strip()
    if not raw_text:
        return None

    fenced_blocks = re.findall(r"```(?:[a-zA-Z0-9_-]+)?\n(.*?)```", raw_text, flags=re.DOTALL)
    candidates_text = "\n".join(fenced_blocks) if fenced_blocks else raw_text

    def clean_line(line: str) -> str:
        s = line.strip()
        if not s:
            return ""
        if s.startswith("```"):
            return ""
        s = s.strip("`")
        s = re.sub(r"^(tidal>\s*)", "", s, flags=re.IGNORECASE)
        s = re.sub(r"^\s*(?:[-*•]|\d+[\.\)]|\d+\s*-\s*)\s*", "", s)
        return s.strip()

    def is_directive_line(line: str) -> bool:
        return bool(re.match(r"^(setcps|cps|hush|once|silence|let)\b", line, flags=re.IGNORECASE))

    def is_channel_start(line: str) -> bool:
        return bool(
            re.match(r"^d\d+\s*[\$=]", line)
            or re.match(r"^d\d+\s*$", line)
            or re.match(r"^d\d+\s+silence\b", line, flags=re.IGNORECASE)
        )

    lines = [clean_line(l) for l in candidates_text.splitlines()]
    tidal_lines: list[str] = []
    in_channel = False

    for line in lines:
        if not line:
            continue

        if is_channel_start(line):
            tidal_lines.append(line)
            in_channel = not bool(re.match(r"^d\d+\s+silence\b", line, flags=re.IGNORECASE))
            continue

        if is_directive_line(line):
            tidal_lines.append(line)
            in_channel = False
            continue

        if line.startswith("--"):
            tidal_lines.append(line)
            continue

        if in_channel:
            tidal_lines.append(line)

    if not tidal_lines:
        stripped = clean_line(candidates_text)
        if is_channel_start(stripped) or is_directive_line(stripped):
            return stripped
        return None

    return "\n".join(tidal_lines)

# ============================================================================
# VALIDATION TOOL
# ============================================================================

def validate_tidal_syntax(code: str) -> str:
    """Validates TidalCycles syntax without executing it.
    
    Args:
        code: The TidalCycles code to validate.
    """
    import re
    
    if not code.strip():
        return "Error: Empty code. Please generate valid TidalCycles code and try again."

    if re.search(r'\bscale\s+"\s*[A-Ga-g](?:#|s|b|f)?\s+\w', code):
        return ('Error: scale takes a scale NAME only, e.g. scale "minor". Put the root in as a '
                'semitone offset: n (scale "minor" "0 2 4 7" + 7) is G minor. '
                'scale "g minor" is invalid and collapses every note to octave jumps.')

    key_error = _norm.key_usage_error(code)
    if key_error:
        return f"Error: {key_error}"
    
    valid_patterns = [
        r'd\d+\s*\$', r'setcps', r'cps', r'hush', r'silence',
        r'once', r'anticipate', r'\bsound\b', r'\bs\b', r'\bn\b',
        r'\bnote\b', r'\bmidinote\b', r'#\s*\w+', r'\bgain\b',
        r'\bspeed\b', r'\bpan\b', r'\bdelay\b', r'\broom\b',
        r'\bsize\b', r'\borbit\b', r'\bcut\b', r'\bresonance\b',
        r'\bcutoff\b', r'\bshape\b', r'\bvowel\b', r'\bfast\b',
        r'\bslow\b', r'\brev\b', r'\bjux\b', r'\bevery\b',
        r'\bsometimes\b', r'\boften\b', r'\bstut\b', r'\bchop\b',
        r'\bgap\b', r'\bdensity\b', r'\bdegrade\b', r'\bunmuteAll\b',
        r'\bstack\b', r'\bfastcat\b', r'\bslowcat\b', r'\bcat\b',
        r'\bwhen\b', r'\bwithin\b', r'\boff\b', r'\bbrak\b',
        r'\biter\b', r'\btrunc\b', r'\blinger\b', r'\bstretch\b',
        r'\brange\b', r'\bsaw\b', r'\bsine\b', r'\bsquare\b', r'\btri\b',
        r'\brand\b', r'\birand\b', r'\bperlin\b', r'\bchoose\b',
        r'\bscale\b', r'\bscaleList\b', r'\brun\b', r'\bscan\b',
        r'"\s*[\w\s\*\/\[\]\<\>\|\.\,\:\~\!\@\#\-]+\s*"',
        r'\$', r'\#', r'\|[\+\-\*\/\>\<]?\|', r'<~', r'~>',
    ]
    
    invalid_functions = [
        r'\blin\b',  # Use 'range' with 'saw'/'sine' instead
        r'\beff\b',  # Not a TidalCycles function - use specific effects
    ]

    if re.search(r'\blp\b', code):
        return "Error: 'lp' is not a TidalCycles function or parameter. Use 'lpf' for low-pass cutoff in Hz (or 'rlpf' for normalized cutoff) and try again."
    
    # Check for waveform names being used as sounds (common mistake)
    waveform_as_sound_patterns = [
        r'\bs\s+"[^"]*\b(sine|saw|tri|square)\b[^"]*"',  # s "sine" or s "bd sine"
        r'\bsound\s+"[^"]*\b(sine|saw|tri|square)\b[^"]*"',  # sound "sine"
    ]
    
    for pattern in waveform_as_sound_patterns:
        match = re.search(pattern, code, re.IGNORECASE)
        if match:
            waveform = match.group(1)
            return f"Error: '{waveform}' is a waveform function for modulation, NOT a playable sound. Use it with 'range' like: # speed (range 0.5 2 $ {waveform}). For sounds, use sample names like 'bd', 'sn', 'bass3', 'superpiano', etc. Please fix and try again."

    # Common missing instrument: "piano" (not a default SuperDirt instrument)
    if re.search(r'\b(sound|s)\s+"[^"]*(?<![\w])piano(?::\d+)?(?![\w])[^"]*"', code, re.IGNORECASE):
        return (
            "Error: 'piano' is not a default SuperDirt instrument, so SuperCollider can't find it. "
            "Use 'superpiano' instead (with notes), e.g. "
            'd2 $ n "c e g c5" # s "superpiano" # room 0.3'
        )
    
    for pattern in invalid_functions:
        if re.search(pattern, code):
            return "Error: Invalid function detected. Use 'range' with 'saw' or 'sine' for linear interpolation, or use specific effects like 'room', 'delay', 'lpf'. Please fix and try again."
    
    warnings = []
    ENABLE_COGNITIVE_WARNINGS=False
    # BLACKLIST: Anti-patterns that cause cognitive overload or listening fatigue
    if ENABLE_COGNITIVE_WARNINGS:
        # Check for machine-gun repetition (*16, *32, etc.)
        machine_gun = re.findall(r'\*(\d+)', code)
        for count in machine_gun:
            if int(count) >= 16:
                warnings.append(f"⚠️  Excessive repetition '*{count}' detected - causes cognitive overload. Reduce to *8 or less for relaxing music.")

        # Check for saw wave on speed/pitch (causes jarring jumps)
        if re.search(r'\bspeed\s*\([^)]*\bsaw\b', code):
            return "Error: Using 'saw' waveform on 'speed' causes jarring pitch discontinuities. Use 'sine' or 'tri' for smooth modulation. Please fix and try again."

        # Check for high fast factor on transient sounds
        fast_match = re.search(r'\bfast\s+(\d+)', code)
        if fast_match and int(fast_match.group(1)) >= 8:
            warnings.append(f"⚠️  'fast {fast_match.group(1)}' is very aggressive - causes harsh noise. Consider 'fast 6' or less for smoother results.")

        # Check for fast filter sweeps (fast 2+ on lpf/hpf)
        if re.search(r'\b(lpf|hpf)\s*\([^)]*\bfast\s+[2-9]', code):
            warnings.append("⚠️  Fast filter sweep detected - causes 'wah' fatigue. Use 'slow 2' or slower for relaxing modulation.")

        # Auto-fix: reverb/room on low-frequency sounds without HPF (prevents mud/fatigue)
        fixed_lines: list[str] = []
        hpf_fixes: list[str] = []
        for raw_line in code.split("\n"):
            line = raw_line.strip()
            if not line:
                fixed_lines.append(raw_line)
                continue

            low_freq_match = re.search(r"\b(bd|reverbkick|bass\d*|kick)\b", line)
            if low_freq_match and re.search(r"#\s*(room|reverb|rv)\b", line, flags=re.IGNORECASE):
                if not re.search(r"#\s*hpf\s+\d+", line, flags=re.IGNORECASE):
                    sound_name = low_freq_match.group(1)
                    line = f"{line} # hpf 150"
                    hpf_fixes.append(sound_name)

            fixed_lines.append(line)

        if hpf_fixes:
            code = "\n".join(fixed_lines)
            unique = []
            for s in hpf_fixes:
                if s not in unique:
                    unique.append(s)
            warnings.append(
                "⚠️  Added '# hpf 150' to low-frequency sound(s) with room: "
                + ", ".join(unique)
                + ". This prevents low-frequency smear and listening fatigue."
            )

        # Check for looped reverbkick (creates continuous low-frequency pressure)
        if re.search(r'\breverbkick\b', code) and re.search(r'#\s*loop\s+[4-9]', code):
            warnings.append("⚠️  'reverbkick' with 'loop 4+' creates continuous low-frequency pressure. Use 'slow 4' instead or remove loop for cleaner rhythm.")
    
    found_valid = False
    for pattern in valid_patterns:
        if re.search(pattern, code):
            found_valid = True
            break
    
    if not found_valid:
        return "Warning: No recognizable TidalCycles syntax found. Please generate proper TidalCycles code with patterns like 'd1 $ sound \"bd sn\"' and try again."
    
    errors = []
    if code.count('"') % 2 != 0:
        errors.append("Unmatched quotes")
    if code.count('(') != code.count(')'):
        errors.append("Unmatched parentheses")
    if code.count('[') != code.count(']'):
        errors.append("Unmatched square brackets")
    if code.count('<') != code.count('>'):
        errors.append("Unmatched angle brackets")
    
    if errors:
        return f"Syntax errors found: {', '.join(errors)}. Please fix these errors and try again."
    
    # Return code with warnings if any
    if warnings:
        warning_text = "\n".join(warnings)
        return f"{code}\n\n⚠️  WARNINGS:\n{warning_text}\n\nCode is valid but may cause listening fatigue. Consider revising."
    
    return code

def execute_tidal_code(code: str) -> str:
    """Executes TidalCycles code in the running session.

    Args:
        code: The TidalCycles code to execute.
    """
    try:
        if not _ensure_tidal():
            return "Error: No TidalCycles session running."
        normalized, replacements = normalize_tidal_code(code)
        if replacements:
            log_message(f"[Normalize] Rewrote: {', '.join(replacements)}")
        lines = [to_crossfade(l) for l in normalized.split("\n") if l.strip()]
        for line in lines:
            _track_cps(line)
        error = tidal.send(lines)
        if error:
            return f"Error from TidalCycles:\n{error}"
        return f"TidalCycles code sent to session:\n{normalized}"
    except Exception as e:
        return f"Error: {str(e)}"


def _update_current_patterns_from_code(tidal_code: str) -> None:
    """Update current patterns and song history based on raw Tidal code."""
    global current_patterns, song_history
    if not tidal_code:
        return
    song_history.append(tidal_code)
    for line in tidal_code.split("\n"):
        line = line.strip()
        if not line or line.startswith("--"):
            continue
        if re.match(r"^\s*(?:setcps|cps)\b", line, flags=re.IGNORECASE):
            continue
        channel_match = re.match(r"^(d\d+)\s*(.+)", line)
        if not channel_match:
            continue
        channel = channel_match.group(1)
        rest_of_line = channel_match.group(2)
        rest_normalized = rest_of_line.strip().lower().replace(" ", "")
        if rest_normalized in ["silence", "$silence"]:
            if channel in current_patterns:
                del current_patterns[channel]
        else:
            current_patterns[channel] = line


def play_sample_pattern(code: str) -> str:
    """Play a sample pattern immediately and update current pattern context."""
    try:
        if not _ensure_tidal():
            return "Error: No TidalCycles session running."
        normalized, replacements = normalize_tidal_code(code)
        if replacements:
            log_message(f"[Normalize] Rewrote: {', '.join(replacements)}")
        lines = [l for l in normalized.split("\n") if l.strip()]
        for line in lines:
            _track_cps(line)
        error = tidal.send(lines, settle=0.2)
        if error:
            return f"Error from TidalCycles:\n{error}"
        _update_current_patterns_from_code(normalized)
        return f"Sample pattern sent to session:\n{normalized}"
    except Exception as e:
        return f"Error: {str(e)}"

# ============================================================================
# PROMPT LOADING
# ============================================================================

def load_prompt(filename):
    """Load prompt from external markdown file"""
    prompt_path = os.path.join(
        os.path.dirname(__file__), 
        "prompts", 
        filename
    )
    try:
        with open(prompt_path, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        log_message(f"Error loading prompt from {prompt_path}: {e}")
        return ""

# ============================================================================
# AGENT CONFIGURATION
# ============================================================================

composer_prompt = load_prompt("composer_prompt.md")
creative_director_prompt = load_prompt("creative_director_prompt.md")

def _build_model(model_id):
    """Build a LiteLLMModel, routing "local/..." ids to the local llama-server."""
    from smolagents import LiteLLMModel

    if model_id.startswith("local/"):
        real_id = "openai/" + model_id.split("/", 1)[1]
        # This model's chat template has an extended-thinking mode that, left
        # enabled, reasons for 5000+ tokens per ReAct step without ever
        # reaching a final_answer() (observed: 6 steps, 24k+ tokens, no
        # answer). Disabling it via chat_template_kwargs drops a full
        # composer run to ~10s. max_tokens stays as a safety ceiling.
        return LiteLLMModel(
            model_id=real_id,
            api_base=config.LOCAL_LLM_API_BASE,
            api_key="sk-no-key-required",
            max_tokens=2048,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
    return LiteLLMModel(model_id=model_id)

def create_agents(composer_model_id, director_model_id):
    """Create agents with specified models"""
    global composer_agent, creative_director_agent
    from smolagents import CodeAgent, tool

    # Log API key availability
    has_openai = bool(os.getenv('OPENAI_API_KEY'))
    has_anthropic = bool(os.getenv('ANTHROPIC_API_KEY'))
    
    if 'gpt' in composer_model_id.lower() or 'gpt' in director_model_id.lower():
        if not has_openai:
            log_message("⚠️  Warning: OpenAI models selected but OPENAI_API_KEY not found in .env")
    
    if 'claude' in composer_model_id.lower() or 'claude' in director_model_id.lower():
        if not has_anthropic:
            log_message("⚠️  Warning: Claude models selected but ANTHROPIC_API_KEY not found in .env")
    
    log_message(f"Creating Composer Agent with model: {composer_model_id}")
    composer_model = _build_model(composer_model_id)
    composer_agent = CodeAgent(
        tools=[tool(validate_tidal_syntax)],
        model=composer_model,
        additional_authorized_imports=["re"],
        add_base_tools=True,
        max_steps=10,
    )

    log_message(f"Creating Creative Director Agent with model: {director_model_id}")
    director_model = _build_model(director_model_id)
    creative_director_agent = CodeAgent(
        tools=[],
        model=director_model,
        add_base_tools=False,
        max_steps=1,
        planning_interval=None,  # Disable planning for simple text output
    )

    global _last_composer_model_id, _last_director_model_id
    _last_composer_model_id = composer_model_id
    _last_director_model_id = director_model_id

    log_message("✓ Agents created successfully")


_agent_executor = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="agent")


def _run_agent(agent, prompt: str, timeout: float | None = None):
    """agent.run() with a hard timeout, so a hung LLM can't stall the loop.

    The music keeps playing either way (Tidal holds the last patterns)."""
    timeout = timeout or config.LLM_TIMEOUT_SECONDS * 3
    future = _agent_executor.submit(agent.run, prompt)
    try:
        return future.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        log_message(f"[Agent] timed out after {timeout:.0f}s; skipping this step")
        return None

# ============================================================================
# MUSIC GENERATION ENGINE
# ============================================================================

def run_music_generation(task_text, test_mode, director_interval, use_creative_director=True):
    """Main function to run the music generation agent"""
    global is_running, stop_event, task_queue, current_patterns, song_history, executed_tasks_log, SONG_STATE, CURRENT_CPS, current_agent_label, agent_conversation_log

    stop_event.clear()
    current_patterns = {}  # Track currently playing patterns per channel
    song_history = deque(maxlen=config.HISTORY_LIMIT)
    executed_tasks_log = deque(maxlen=config.HISTORY_LIMIT)
    agent_conversation_log = deque(maxlen=config.HISTORY_LIMIT)
    task_number = 0
    consecutive_composer_failures = 0
    COMPOSER_FAILURE_RECOVERY_THRESHOLD = 3
    SONG_STATE.update(_parse_initial_song_state(task_text))
    SONG_STATE["started_at"] = time.time()
    
    # Store the overall musical goal (not just a task - this is the creative vision)
    musical_goal = task_text.split('\n')[0].strip() if task_text else "create music"
    
    # Allow optional metadata lines like "KEY: C minor" and "SECTION: intro"
    # without treating them as executable tasks.
    tasks = []
    for raw in (task_text or "").split("\n"):
        t = raw.strip()
        if not t:
            continue
        if re.match(r"^(key|section)\s*:", t, flags=re.IGNORECASE):
            continue
        tasks.append(t)
    task_queue = deque(maxlen=config.HISTORY_LIMIT)

    if not tasks:
        log_message("No tasks provided. Please enter at least one task.")
        return
    
    log_message(f"\n{'='*60}")
    log_message("Starting Music Generation")
    log_message(f"{'='*60}")
    
    task_index = 0
    while not stop_event.is_set():
        if task_index >= len(tasks):
            if use_creative_director and not test_mode:
                current_agent_label = "Creative Director"
                log_message(f"\n   💡 No more tasks. Running Creative Director to suggest next task...")
                
                try:
                    director_start = time.time()
                    
                    # Build current context string from currently playing patterns
                    if current_patterns:
                        current_context = "\n\n".join([current_patterns[ch] for ch in sorted(current_patterns.keys())])
                    else:
                        current_context = "(no patterns playing yet)"

                    if executed_tasks_log:
                        recent_history = "\n".join(list(executed_tasks_log)[-4:])
                    else:
                        recent_history = "(nothing developed yet)"

                    suggestions = _run_agent(creative_director_agent,
                        f"{creative_director_prompt}\n\n{_get_song_state_lines()}\n=== YOUR CREATIVE GOAL ===\n{musical_goal}\n\nThis is the overall musical vision you are working towards.\n\n=== CURRENT PATTERNS ===\n{current_context}\n\n=== RECENT DEVELOPMENTS ===\n{recent_history}\n\nSuggest ONE next development that serves this goal. At most {config.MAX_ACTIVE_LAYERS} channels may play at once ({len(current_patterns)} playing now)."
                    )
                    director_time = time.time() - director_start
                    
                    log_message(f"[Creative Director] Time: {director_time:.2f}s")
                    log_message(f"[Creative Director] Suggestions:\n{suggestions}")
                    
                    if suggestions:
                        picked = _extract_actionable_suggestion(suggestions)
                        suggestion_lines = [picked] if picked else []

                        if suggestion_lines:
                            log_message(f"\n✨ Adding {len(suggestion_lines)} new creative tasks to the queue")
                            tasks.extend(suggestion_lines)
                            for idx, sugg in enumerate(suggestion_lines, 1):
                                log_message(f"   {idx}. {sugg}")
                                agent_conversation_log.append({"role": "user", "content": sugg})
                            # DO NOT use continue here - we want to process the new task immediately
                        else:
                            log_message("[Creative Director] No new tasks extracted from suggestions")
                            log_message(f"\n   Waiting {director_interval}s before next iteration...")
                            stop_event.wait(director_interval)
                            continue
                    else:
                        log_message("[Creative Director] No suggestions received")
                        log_message(f"\n   Waiting {director_interval}s before next iteration...")
                        stop_event.wait(director_interval)
                        continue
                    
                except Exception as e:
                    log_message(f"[Creative Director] Error: {e}")
                    stop_event.wait(director_interval)
                    continue
            else:
                log_message(f"\n   🎵 All tasks completed. Music playing... (press Stop to end)")
                current_agent_label = "Idle"
                while not stop_event.is_set():
                    time.sleep(0.5)
                break
        
        # Keep the task list bounded on an endless run.
        if task_index > 50:
            tasks = tasks[task_index:]
            task_index = 0
        i = task_index
        task_number += 1
        task = tasks[i]
        _maybe_update_song_state_from_task(task)
        if stop_event.is_set():
            log_message("\n⏹ Stopped by user")
            break
            
        log_message(f"\n{'='*60}")
        log_message(f"Task {task_number}: {task}")
        log_message(f"{'='*60}")
        
        if test_mode:
            log_message(f"\n[TEST MODE] Using test sample instead of agent")
            current_agent_label = "Test Mode"
            code_snippet = TEST_SAMPLE
            composer_time = 0
        else:
            current_agent_label = "Composer"
            log_message(f"\n[Agent: Composer] Generating code...")
            
            # Build current context string
            if current_patterns:
                current_context = "\n\n".join([current_patterns[ch] for ch in sorted(current_patterns.keys())])
            else:
                current_context = "(no patterns playing yet)"
            
            log_message(f"[Context] Current patterns:\n{current_context}")
            log_message(f"[Task] {task}")
            start_time = time.time()
            
            full_prompt = f"""{composer_prompt}

                                {_get_song_state_lines()}

                                === MUSICAL GOAL ===
                                {musical_goal}

                                CURRENT STATE:
                                {current_context}

                                TASK:
                                {task}

                                Generate the TidalCycles code to accomplish this task. Consider what's already playing and act accordingly.
                                If the task mentions a specific channel (d1, d2, etc.), modify that channel.
                                If the task says to "add", use a new unused channel.
                                If the task says to "modify" or "change", update the existing pattern on that channel.
                                """
            code_snippet = _run_agent(composer_agent, full_prompt)
            composer_time = time.time() - start_time
        
        log_message(f"[Agent: Composer] Time: {composer_time:.2f}s")
        log_message(f"[Agent: Composer] Generated:\n{code_snippet}")
        
        tidal_code = extract_tidal_code(code_snippet)
        
        log_message(f"\n[Extracted Code]:\n{tidal_code if tidal_code else 'None'}")
        
        if not tidal_code and code_snippet:
            raw = str(code_snippet)
            log_message(f"[Debug] Could not extract code. Raw output length: {len(raw)} chars")
            log_message(f"[Debug] First 500 chars: {raw[:500]}")
        
        if tidal_code:
            try:
                _ensure_tidal()

                tidal_code, replacements = normalize_tidal_code(tidal_code)
                if replacements:
                    log_message(f"[Normalize] Rewrote instruments before send: {', '.join(replacements)}")

                log_message(f"\n▶ Executing AI-generated pattern...")
                log_message(f"   Code: {tidal_code}")
                agent_conversation_log.append({"role": "assistant", "content": tidal_code})

                # Split multi-line code and send each line separately to ensure proper execution
                code_lines = [line.strip() for line in tidal_code.split('\n') if line.strip()]

                # Layer cap: never let layers pile up past MAX_ACTIVE_LAYERS.
                added = _new_channels(tidal_code) - set(current_patterns)
                if len(current_patterns) + len(added) > config.MAX_ACTIVE_LAYERS:
                    log_message(f"   ✗ Would exceed {config.MAX_ACTIVE_LAYERS} layers; asking for a subtraction instead")
                    tasks.insert(i + 1, f"remove the least essential of the {len(current_patterns)} playing layers "
                                        f"(silence one channel) to make room")
                    task_index += 1
                    continue

                # Crossfade instead of cutting mid-phrase.
                ghci_error = _send_tidal_code_to_ghci([to_crossfade(l) for l in code_lines])

                if ghci_error:
                    log_message(f"   ✗ GHCi rejected the pattern:\n{ghci_error}")
                    log_message(f"   ↻ Asking composer to fix and resend (one retry)...")
                    retry_prompt = (
                        f"{full_prompt}\n\n"
                        f"Your previous code failed to run in TidalCycles with this error:\n{ghci_error}\n\n"
                        "Fix the specific issue and provide corrected code."
                    )
                    retry_snippet = _run_agent(composer_agent, retry_prompt)
                    retry_code = extract_tidal_code(retry_snippet)
                    if retry_code:
                        retry_code, retry_replacements = normalize_tidal_code(retry_code)
                        if retry_replacements:
                            log_message(f"[Normalize] Rewrote instruments before resend: {', '.join(retry_replacements)}")
                        retry_lines = [line.strip() for line in retry_code.split('\n') if line.strip()]
                        retry_error = _send_tidal_code_to_ghci([to_crossfade(l) for l in retry_lines])
                        if retry_error:
                            log_message(f"   ✗ Retry also rejected by GHCi:\n{retry_error}")
                            tidal_code = None
                        else:
                            log_message(f"   ✓ Retry accepted by GHCi")
                            tidal_code = retry_code
                            code_lines = retry_lines
                    else:
                        log_message(f"   ✗ Could not extract corrected code from retry")
                        tidal_code = None

                if tidal_code:
                    log_message(f"   ✓ Sent {len(code_lines)} pattern(s) to TidalCycles!")
                    consecutive_composer_failures = 0

                    task_queue.append(tidal_code)
                    song_history.append(tidal_code)

                    # Parse and update current patterns per channel
                    log_message(f"   Parsing {len(tidal_code.split(chr(10)))} lines of code...")
                    for line in tidal_code.split('\n'):
                        line = line.strip()
                        if not line:
                            continue
                        if line.startswith("--"):
                            continue
                        if re.match(r"^\s*(?:setcps|cps)\b", line, flags=re.IGNORECASE):
                            continue

                        # Check if it's a channel assignment (d1, d2, etc.)
                        # Match patterns like: d1 $, d1$ ..., d1 silence, d10$ ..., etc.
                        channel_match = re.match(r'^(d\d+)\s*(.+)', line)
                        if channel_match:
                            channel = channel_match.group(1)
                            rest_of_line = channel_match.group(2)

                            # Check if it's silencing the channel
                            rest_normalized = rest_of_line.strip().lower().replace(" ", "")
                            if rest_normalized in ['silence', '$silence']:
                                # Remove channel from current patterns
                                if channel in current_patterns:
                                    del current_patterns[channel]
                                    log_message(f"   → Removed {channel} from current patterns (silenced)")
                            else:
                                # Update or add channel pattern
                                current_patterns[channel] = line
                                log_message(f"   → Updated {channel} in current patterns")

                    log_message(f"   Total active channels: {len(current_patterns)} - {sorted(current_patterns.keys())}")

                    executed_tasks_log.append(f"Task {i+1}: {task}")

                    # Move to next task
                    log_message(f"   Moving to next task (pattern will continue layering)...")
                    stop_event.wait(director_interval)
                else:
                    log_message(f"   ✗ Giving up on this task after GHCi rejected both attempts")
                    consecutive_composer_failures += 1

            except Exception as e:
                log_message(f"   ✗ Error executing code: {e}")
                consecutive_composer_failures += 1
        else:
            log_message(f"\n[Error] No valid TidalCycles code extracted or TidalCycles not running")
            consecutive_composer_failures += 1

        if consecutive_composer_failures >= COMPOSER_FAILURE_RECOVERY_THRESHOLD:
            log_message(
                f"\n⚠️  Composer agent failed {consecutive_composer_failures} times in a row; "
                "recreating agents to clear any bad state..."
            )
            try:
                create_agents(_last_composer_model_id, _last_director_model_id)
            except Exception as e:
                log_message(f"   ✗ Failed to recreate agents: {e}")
            consecutive_composer_failures = 0


        log_message(f"\n{'='*60}")
        log_message(f"Task {i+1} completed")
        log_message(f"{'='*60}\n")
        
        task_index += 1
    
    log_message("\n✓ All tasks completed!")

def start_system(task_text, test_mode, director_interval, composer_model_id=None, director_model_id=None,
                 cognitive_overload_warnings=False, record=True):
    """Start the TidalCycles system and agent"""
    global is_running, agent_thread, stop_event, accumulated_logs, ENABLE_COGNITIVE_WARNINGS

    if is_running:
        return "System is already running!"

    composer_model_id = composer_model_id or default_composer_model
    director_model_id = director_model_id or default_director_model
    ENABLE_COGNITIVE_WARNINGS = bool(cognitive_overload_warnings)

    while not log_queue.empty():
        log_queue.get()
    accumulated_logs.clear()
    audio_stack.set_logger(log_message)

    log_message("=" * 60)
    log_message("Initializing TidalCycles Environment (legacy agent loop)")
    log_message("=" * 60)

    if composer_model_id.startswith("local/") or director_model_id.startswith("local/"):
        audio_stack.ensure_local_llm_server()

    log_message("\nCreating AI agents...")
    create_agents(composer_model_id, director_model_id)

    log_message("\nStarting SuperDirt...")
    log_message(start_superdirt())

    log_message("\nStarting TidalCycles session...")
    log_message(start_tidal_session())

    if record:
        log_message("\nStarting audio recording...")
        recording_msg, filepath = start_recording()
        log_message(recording_msg)

    log_message("\n" + "=" * 60)
    log_message("Environment Ready")
    log_message(f"🎼 Composer Model: {composer_model_id}")
    log_message(f"🎨 Director Model: {director_model_id}")
    log_message(f"⏱  Director interval: {director_interval}s, max layers: {config.MAX_ACTIVE_LAYERS}")
    log_message("=" * 60)

    is_running = True
    stop_event.clear()

    agent_thread = threading.Thread(
        target=run_music_generation,
        args=(task_text, test_mode, director_interval, True),
        daemon=True,
    )
    agent_thread.start()

    return "System started! Check logs below."

def stop_system():
    """Stop the TidalCycles system and agent"""
    global is_running, stop_event, current_agent_label

    if not is_running:
        return "System is not running!"

    stop_event.set()
    log_message("\nStopping system...")
    log_message(stop_recording())
    log_message(stop_tidal_session())

    is_running = False
    current_agent_label = "Idle"
    log_message("✓ System stopped!")
    return "System stopped!"

def get_is_running():
    """Check if the system is currently running"""
    return is_running

def initialize_sound_backend():
    """Initialize SuperDirt and TidalCycles without starting music generation"""
    log_message(start_superdirt())
    log_message(start_tidal_session())
    return "Sound backend initialized successfully"

def get_sound_backend_status():
    """Check if sound backend (SuperDirt + TidalCycles) is running"""
    superdirt_ok = audio_stack.superdirt_running()
    tidal_ok = tidal.running()
    if superdirt_ok and tidal_ok:
        return "Running"
    if superdirt_ok or tidal_ok:
        return "Partial"
    return "Not Running"

def get_audio_output_info():
    """Get detected audio output/source info for UI display."""
    return audio_stack.audio_output_info

def play_audio_test():
    """Play a simple test pattern to verify audio output"""
    if not tidal.running():
        return "Error: TidalCycles session not running. Initialize sound backend first."
    test_pattern = 'd1 $ sound "bd*4" # gain 0.8'
    log_message(f"Playing audio test pattern: {test_pattern}")
    tidal.send([test_pattern])
    time.sleep(2)
    tidal.send(["d1 silence"])
    return "Audio test played successfully! You should have heard 4 kick drum beats."
