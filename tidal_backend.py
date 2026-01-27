"""
TidalCycles Backend Module
Handles all TidalCycles session management, audio recording, and music generation logic.
"""

import os
import time
import threading
import re
import subprocess
import tempfile
import signal
from queue import Queue
from dotenv import load_dotenv

# Load environment variables from .env file
env_path = os.path.join(os.path.dirname(__file__), '.env')
load_dotenv(dotenv_path=env_path, override=True)

from smolagents import CodeAgent, LiteLLMModel, tool

# Environment configuration
os.environ['HF_HOME'] = "/home/moebius/Models/hfmodels/.cache/"
os.environ['PYTORCH_CUDA_ALLOC_CONF'] = "expandable_segments:True"
os.environ["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
os.environ["CUDA_VISIBLE_DEVICES"] = "0,1"

# Global state
tidal_process = None
superdirt_process = None
recording_process = None
is_running = False
agent_thread = None
log_queue = Queue()
stop_event = threading.Event()
task_queue = []
accumulated_logs = []
current_song_context = "(No patterns playing yet)"
current_patterns = {}  # Maps channel (d1, d2, etc.) to current pattern
song_history = []  # Full history of all sent patterns
executed_tasks_log = []

# SuperDirt instrument aliases (avoid missing instrument errors)
INSTRUMENT_ALIASES = {
    # SuperDirt doesn't ship a plain "piano" instrument; use its synth instead.
    "piano": "superpiano",
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

# ============================================================================
# TIDAL & SUPERDIRT MANAGEMENT
# ============================================================================

def start_superdirt() -> str:
    """Starts SuperCollider with SuperDirt for audio output."""
    global superdirt_process
    
    try:
        if superdirt_process is not None and superdirt_process.poll() is None:
            return "SuperDirt is already running"
        if superdirt_process is not None and superdirt_process.poll() is not None:
            superdirt_process = None
        
        sc_code = """
                    s.boot;
                    s.waitForBoot {
                        "Server booted successfully".postln;
                        ~dirt = SuperDirt(2, s);
                        ~dirt.loadSoundFiles;
                        s.sync;
                        ~dirt.start(57120, 0 ! 12);
                        "SuperDirt started on port 57120".postln;
                    };
                    thisProcess.openUDPPort(57120);
                    Routine { inf.do { 1.wait } }.play;
                    """
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.scd', delete=False) as f:
            f.write(sc_code)
            temp_file = f.name
        
        superdirt_process = subprocess.Popen(
            ['sclang', temp_file],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )
        
        def log_sc():
            for line in superdirt_process.stdout:
                log_message(f"[SuperCollider] {line.rstrip()}")
        
        threading.Thread(target=log_sc, daemon=True).start()
        
        log_message("Waiting for SuperCollider server to boot and load samples...")
        time.sleep(15)
        
        return "SuperDirt started successfully on port 57120"
        
    except Exception as e:
        return f"Error starting SuperDirt: {str(e)}"

def start_tidal_session() -> str:
    """Starts a persistent TidalCycles session with ghci."""
    global tidal_process
    
    try:
        if tidal_process is not None and tidal_process.poll() is None:
            return "TidalCycles session is already running"
        if tidal_process is not None and tidal_process.poll() is not None:
            tidal_process = None
        
        boot_file = "/home/moebius/Projects/youtube-stream-music-generation/tidal/BootTidal.hs"
        
        tidal_process = subprocess.Popen(
            ['ghci', '-ghci-script', boot_file],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )
        
        def log_ghci():
            for line in tidal_process.stdout:
                log_message(f"[GHCi/Tidal] {line.rstrip()}")
        
        threading.Thread(target=log_ghci, daemon=True).start()
        
        time.sleep(3)
        
        tidal_process.stdin.write("setcps 0.8\n")
        tidal_process.stdin.flush()
        time.sleep(0.5)
        
        return "TidalCycles session started successfully with cps=0.8"
        
    except Exception as e:
        return f"Error starting TidalCycles session: {str(e)}"

def stop_tidal_session() -> str:
    """Stops the running TidalCycles and SuperDirt sessions."""
    global tidal_process, superdirt_process
    
    messages = []
    
    if tidal_process is not None:
        try:
            tidal_process.stdin.write(":quit\n")
            tidal_process.stdin.flush()
            tidal_process.wait(timeout=5)
            tidal_process = None
            messages.append("TidalCycles session stopped")
        except Exception as e:
            messages.append(f"Error stopping TidalCycles: {str(e)}")
    else:
        messages.append("No TidalCycles session running")
    
    if superdirt_process is not None:
        try:
            superdirt_process.terminate()
            superdirt_process.wait(timeout=5)
            superdirt_process = None
            messages.append("SuperDirt stopped")
        except Exception as e:
            messages.append(f"Error stopping SuperDirt: {str(e)}")
    else:
        messages.append("No SuperDirt session running")
    
    return " | ".join(messages)

# ============================================================================
# RECORDING
# ============================================================================

def start_recording() -> tuple[str, str]:
    """Starts recording audio output to MP3 file with timestamp."""
    global recording_process
    
    try:
        if recording_process is not None:
            return "Recording already in progress", ""
        
        recordings_dir = "/home/moebius/Projects/youtube-stream-music-generation/recordings"
        os.makedirs(recordings_dir, exist_ok=True)
        
        from datetime import datetime
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"tidal_session_{timestamp}.mp3"
        filepath = os.path.join(recordings_dir, filename)
        
        def pick_pulse_source() -> tuple[str, str]:
            """Pick a PulseAudio/PipeWire source that captures system output (monitor)."""
            try:
                default_sink = subprocess.run(
                    ["pactl", "get-default-sink"],
                    capture_output=True,
                    text=True,
                    timeout=2,
                ).stdout.strip()
            except Exception:
                default_sink = ""

            try:
                sources_text = subprocess.run(
                    ["pactl", "list", "sources", "short"],
                    capture_output=True,
                    text=True,
                    timeout=2,
                ).stdout
            except Exception:
                sources_text = ""

            sources = []
            for line in sources_text.splitlines():
                parts = line.split()
                if len(parts) >= 2:
                    sources.append(parts[1])

            if default_sink:
                monitor = f"{default_sink}.monitor"
                if monitor in sources:
                    return monitor, f"default sink monitor ({default_sink})"

            monitors = [s for s in sources if s.endswith(".monitor")]
            if monitors:
                # Prefer analog monitor over hdmi to match typical desktop playback
                monitors_sorted = sorted(
                    monitors,
                    key=lambda s: (0 if "analog" in s else 1, 0 if "stereo" in s else 1, s),
                )
                return monitors_sorted[0], "first available monitor source"

            return "default", "fallback to Pulse default (may be mic)"

        pulse_source, pulse_reason = pick_pulse_source()

        # Try to detect the correct audio source
        # Check if PulseAudio/PipeWire sources are available
        try:
            subprocess.run(
                ['pactl', 'list', 'sources', 'short'],
                capture_output=True,
                text=True,
                timeout=2,
            )
            log_message(f"[Recording] Using source: {pulse_source} ({pulse_reason})")
        except Exception:
            log_message("[Recording] Warning: Could not query PulseAudio sources")
        
        recording_process = subprocess.Popen([
            'ffmpeg',
            '-hide_banner',
            '-loglevel', 'warning',
            '-nostats',
            '-f', 'pulse',
            '-i', pulse_source,
            '-vn',
            '-acodec', 'libmp3lame',
            '-b:a', '192k',
            '-y',
            filepath
        ], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, bufsize=1)

        def drain_ffmpeg_stderr(proc: subprocess.Popen):
            try:
                assert proc.stderr is not None
                for line in proc.stderr:
                    if line:
                        log_message(f"[Recording/ffmpeg] {line.rstrip()}")
            except Exception:
                pass

        threading.Thread(target=drain_ffmpeg_stderr, args=(recording_process,), daemon=True).start()
        
        log_message(f"[Recording] Started recording to: {filename}")
        log_message("[Recording] If file is silent, ensure SuperCollider outputs to PipeWire/Pulse (monitor) and not JACK-only")
        
        return f"Recording started: {filename}", filepath
        
    except Exception as e:
        return f"Error starting recording: {str(e)}", ""

def stop_recording() -> str:
    """Stops the audio recording."""
    global recording_process
    
    if recording_process is None:
        return "No recording in progress"
    
    try:
        # Graceful stop so the MP3 header/trailer is finalized properly
        recording_process.send_signal(signal.SIGINT)
        recording_process.wait(timeout=10)
        recording_process = None
        return "Recording stopped and saved"
    except Exception as e:
        try:
            recording_process.terminate()
            recording_process.wait(timeout=5)
        except Exception:
            pass
        recording_process = None
        return f"Error stopping recording: {str(e)}"

# ============================================================================
# TIDAL CODE NORMALIZATION (DEFENSIVE REWRITES)
# ============================================================================

def normalize_tidal_code(code: str) -> tuple[str, list[str]]:
    """Rewrite common non-existent instruments to known SuperDirt equivalents."""
    if not code:
        return code, []

    replacements: list[str] = []

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

    # De-dup replacement notes while preserving order
    deduped: list[str] = []
    for item in replacements:
        if item not in deduped:
            deduped.append(item)
    return updated_code, deduped

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

    def is_tidal_line(line: str) -> bool:
        return bool(
            re.match(r"^d\d+\s*[\$=]", line)
            or re.match(r"^d\d+\s+silence\b", line, flags=re.IGNORECASE)
            or re.match(r"^(setcps|cps|hush|once|silence|let)\b", line)
        )

    lines = [clean_line(l) for l in candidates_text.splitlines()]
    tidal_lines = [l for l in lines if l and is_tidal_line(l)]

    if not tidal_lines:
        stripped = clean_line(candidates_text)
        if is_tidal_line(stripped):
            return stripped
        return None

    return "\n".join(tidal_lines)

# ============================================================================
# VALIDATION TOOL
# ============================================================================

@tool
def validate_tidal_syntax(code: str) -> str:
    """Validates TidalCycles syntax without executing it.
    
    Args:
        code: The TidalCycles code to validate.
    """
    import re
    
    if not code.strip():
        return "Error: Empty code. Please generate valid TidalCycles code and try again."
    
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
    
    # BLACKLIST: Anti-patterns that cause cognitive overload or listening fatigue
    warnings = []
    
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

@tool
def execute_tidal_code(code: str) -> str:
    """Executes TidalCycles code in the running session.
    
    Args:
        code: The TidalCycles code to execute.
    """
    global tidal_process
    
    try:
        if tidal_process is None or tidal_process.poll() is not None:
            start_msg = start_tidal_session()
            log_message(start_msg)
            if tidal_process is None or tidal_process.poll() is not None:
                return "Error: No TidalCycles session running. Call start_tidal_session() first."

        normalized, replacements = normalize_tidal_code(code)
        if replacements:
            log_message(f"[Normalize] Rewrote instruments: {', '.join(replacements)}")
        
        tidal_process.stdin.write(normalized + "\n")
        tidal_process.stdin.flush()
        time.sleep(0.5)
        
        return f"TidalCycles code sent to session:\n{normalized}"
            
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

def create_agents(composer_model_id, director_model_id):
    """Create agents with specified models"""
    global composer_agent, creative_director_agent
    
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
    composer_model = LiteLLMModel(model_id=composer_model_id)
    composer_agent = CodeAgent(
        tools=[validate_tidal_syntax], 
        model=composer_model,
        additional_authorized_imports=["re"],
        add_base_tools=True,
        max_steps=10,    
    )
    
    log_message(f"Creating Creative Director Agent with model: {director_model_id}")
    director_model = LiteLLMModel(model_id=director_model_id)
    creative_director_agent = CodeAgent(
        tools=[],
        model=director_model,
        add_base_tools=False,
        max_steps=1,
        planning_interval=None,  # Disable planning for simple text output
    )
    
    log_message("✓ Agents created successfully")

# ============================================================================
# MUSIC GENERATION ENGINE
# ============================================================================

def run_music_generation(task_text, test_mode, director_interval, use_creative_director=True):
    """Main function to run the music generation agent"""
    global is_running, tidal_process, stop_event, task_queue, current_patterns, song_history, executed_tasks_log
    
    stop_event.clear()
    current_patterns = {}  # Track currently playing patterns per channel
    song_history = []  # Full history
    executed_tasks_log = []
    
    # Store the overall musical goal (not just a task - this is the creative vision)
    musical_goal = task_text.split('\n')[0].strip() if task_text else "create music"
    
    tasks = [t.strip() for t in task_text.split('\n') if t.strip()]
    task_queue = []
    
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
                log_message(f"\n   💡 No more tasks. Running Creative Director to suggest next task...")
                
                try:
                    director_start = time.time()
                    
                    # Build current context string from currently playing patterns
                    if current_patterns:
                        current_context = "\n\n".join([current_patterns[ch] for ch in sorted(current_patterns.keys())])
                    else:
                        current_context = "(no patterns playing yet)"
                    
                    suggestions = creative_director_agent.run(
                        f"{creative_director_prompt}\n\n=== YOUR CREATIVE GOAL ===\n{musical_goal}\n\nThis is the overall musical vision you are working towards.\n\n=== CURRENT PATTERNS ===\n{current_context}\n\nSuggest ONE next development that serves this goal."
                    )
                    director_time = time.time() - director_start
                    
                    log_message(f"[Creative Director] Time: {director_time:.2f}s")
                    log_message(f"[Creative Director] Suggestions:\n{suggestions}")
                    
                    if suggestions:
                        suggestion_lines = []
                        if isinstance(suggestions, list):
                            suggestion_lines = [s.strip() for s in suggestions if s.strip()]
                        elif isinstance(suggestions, str):
                            for line in suggestions.split('\n'):
                                line = line.strip()
                                match = re.match(r'^\d+[\.\.\)]\s*(.+)$', line)
                                if match:
                                    suggestion_lines.append(match.group(1).strip())
                                elif line and not line.startswith('#') and not line.startswith('/'):
                                    suggestion_lines.append(line)
                        
                        if suggestion_lines:
                            log_message(f"\n✨ Adding {len(suggestion_lines)} new creative tasks to the queue")
                            tasks.extend(suggestion_lines)
                            for idx, sugg in enumerate(suggestion_lines, 1):
                                log_message(f"   {idx}. {sugg}")
                            # DO NOT use continue here - we want to process the new task immediately
                        else:
                            log_message("[Creative Director] No new tasks extracted from suggestions")
                            log_message(f"\n   Waiting {director_interval}s before next iteration...")
                            time.sleep(director_interval)
                            continue
                    else:
                        log_message("[Creative Director] No suggestions received")
                        log_message(f"\n   Waiting {director_interval}s before next iteration...")
                        time.sleep(director_interval)
                        continue
                    
                except Exception as e:
                    log_message(f"[Creative Director] Error: {e}")
                    time.sleep(director_interval)
                    continue
            else:
                log_message(f"\n   🎵 All tasks completed. Music playing... (press Stop to end)")
                while not stop_event.is_set():
                    time.sleep(0.5)
                break
        
        i = task_index
        task = tasks[i]
        if stop_event.is_set():
            log_message("\n⏹ Stopped by user")
            break
            
        log_message(f"\n{'='*60}")
        log_message(f"Task {i+1}/{len(tasks)}: {task}")
        log_message(f"{'='*60}")
        
        if test_mode:
            log_message(f"\n[TEST MODE] Using test sample instead of agent")
            code_snippet = TEST_SAMPLE
            composer_time = 0
        else:
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
            code_snippet = composer_agent.run(full_prompt)
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
                if tidal_process is None or tidal_process.poll() is not None:
                    log_message("[Tidal] Session not running; restarting...")
                    log_message(start_tidal_session())

                tidal_code, replacements = normalize_tidal_code(tidal_code)
                if replacements:
                    log_message(f"[Normalize] Rewrote instruments before send: {', '.join(replacements)}")

                log_message(f"\n▶ Executing AI-generated pattern...")
                log_message(f"   Code: {tidal_code}")
                
                # Split multi-line code and send each line separately to ensure proper execution
                code_lines = [line.strip() for line in tidal_code.split('\n') if line.strip()]
                
                for idx, line in enumerate(code_lines, 1):
                    log_message(f"   → Sending pattern {idx}/{len(code_lines)}: {line[:60]}{'...' if len(line) > 60 else ''}")
                    tidal_process.stdin.write(line + "\n")
                    tidal_process.stdin.flush()
                    time.sleep(0.2)  # Increased delay to ensure TidalCycles processes each pattern
                
                log_message(f"   ✓ Sent {len(code_lines)} pattern(s) to TidalCycles!")
                time.sleep(0.5)  # Wait for all patterns to be processed before continuing
                
                task_queue.append(tidal_code)
                song_history.append(tidal_code)
                
                # Parse and update current patterns per channel
                log_message(f"   Parsing {len(tidal_code.split(chr(10)))} lines of code...")
                for line in tidal_code.split('\n'):
                    line = line.strip()
                    if not line:
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
                    else:
                        log_message(f"   ⚠ Line didn't match channel pattern: {line[:50]}...")
                
                log_message(f"   Total active channels: {len(current_patterns)} - {sorted(current_patterns.keys())}")
                
                executed_tasks_log.append(f"Task {i+1}: {task}")
                
                # Move to next task
                log_message(f"   Moving to next task (pattern will continue layering)...")
                time.sleep(director_interval)
                
            except Exception as e:
                log_message(f"   ✗ Error executing code: {e}")
        else:
            log_message(f"\n[Error] No valid TidalCycles code extracted or TidalCycles not running")
        
        log_message(f"\n{'='*60}")
        log_message(f"Task {i+1} completed")
        log_message(f"{'='*60}\n")
        
        task_index += 1
    
    log_message("\n✓ All tasks completed!")

def start_system(task_text, test_mode, director_interval, composer_model_id=None, director_model_id=None):
    """Start the TidalCycles system and agent"""
    global is_running, agent_thread, stop_event, accumulated_logs
    
    if is_running:
        return "System is already running!"
    
    # Use default models if not specified
    if composer_model_id is None:
        composer_model_id = default_composer_model
    if director_model_id is None:
        director_model_id = default_director_model
    
    while not log_queue.empty():
        log_queue.get()
    accumulated_logs = []
    
    log_message("="*60)
    log_message("Initializing TidalCycles Environment")
    log_message("="*60)
    
    # Create agents with selected models
    log_message("\nCreating AI agents...")
    create_agents(composer_model_id, director_model_id)
    
    log_message("\nStarting SuperDirt...")
    start_result = start_superdirt()
    log_message(start_result)
    
    log_message("\nStarting TidalCycles session...")
    tidal_result = start_tidal_session()
    log_message(tidal_result)
    
    log_message("\nStarting audio recording...")
    recording_msg, filepath = start_recording()
    log_message(recording_msg)
    if filepath:
        log_message(f"Recording to: {filepath}")
    
    log_message("\n" + "="*60)
    log_message("Environment Ready")
    log_message(f"🎼 Composer Model: {composer_model_id}")
    log_message(f"🎨 Director Model: {director_model_id}")
    log_message("🎨 Creative Director: ENABLED")
    log_message("🔴 Recording: ACTIVE")
    log_message("="*60)
    
    is_running = True
    stop_event.clear()
    
    agent_thread = threading.Thread(
        target=run_music_generation,
        args=(task_text, test_mode, director_interval, True),
        daemon=True
    )
    agent_thread.start()
    
    return "System started! Check logs below."

def stop_system():
    """Stop the TidalCycles system and agent"""
    global is_running, stop_event, tidal_process
    
    if not is_running:
        return "System is not running!"
    
    stop_event.set()
    log_message("\nStopping system...")
    
    log_message("Stopping recording...")
    recording_result = stop_recording()
    log_message(recording_result)
    
    if tidal_process is not None:
        try:
            log_message("Stopping all patterns (hush)...")
            tidal_process.stdin.write("hush\n")
            tidal_process.stdin.flush()
            time.sleep(0.5)
        except Exception as e:
            log_message(f"Error stopping patterns: {e}")
    
    cleanup_result = stop_tidal_session()
    log_message(cleanup_result)
    
    is_running = False
    log_message("✓ System stopped!")
    
    return "System stopped!"

def get_is_running():
    """Check if the system is currently running"""
    return is_running
