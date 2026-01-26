import gradio as gr
import os
import time
import threading
import re
from queue import Queue

# Environment configuration (same as app.py)
os.environ['HF_HOME'] = "/home/moebius/Models/hfmodels/.cache/"
os.environ['PYTORCH_CUDA_ALLOC_CONF'] = "expandable_segments:True"
os.environ["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
os.environ["CUDA_VISIBLE_DEVICES"] = "0,1"

from smolagents import CodeAgent, LiteLLMModel, tool
import subprocess
import tempfile
import signal

# Global state
tidal_process = None
superdirt_process = None
recording_process = None
is_running = False
agent_thread = None
log_queue = Queue()
stop_event = threading.Event()
task_queue = []  # Track executed music patterns for UI display
accumulated_logs = []  # Store all logs for display
current_song_context = "(No patterns playing yet)"  # Current TidalCycles code context
executed_tasks_log = []  # Log of completed tasks

# Import functions from app.py
TEST_MODE = False
TEST_SAMPLE = 'd1 $ sound "bd [~ bd] sn:1 [bd sn:3]"\n'

# Use Ollama for GGUF model support
model = LiteLLMModel(
    model_id="ollama/qwen2.5-coder:14b",
)

def log_message(msg):
    """Add message to log queue for UI display"""
    log_queue.put(msg)
    print(msg)

def start_superdirt() -> str:
    """Starts SuperCollider with SuperDirt for audio output."""
    global superdirt_process
    
    try:
        if superdirt_process is not None:
            return "SuperDirt is already running"
        
        sc_code = """
                    // Boot the audio server first
                    s.boot;

                    // Wait for server to boot, then start SuperDirt
                    s.waitForBoot {
                        "Server booted successfully".postln;
                        ~dirt = SuperDirt(2, s);
                        ~dirt.loadSoundFiles;
                        s.sync;
                        ~dirt.start(57120, 0 ! 12);
                        "SuperDirt started on port 57120".postln;
                    };

                    thisProcess.openUDPPort(57120);

                    // KEEP PROCESS ALIVE FOREVER
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
        if tidal_process is not None:
            return "TidalCycles session is already running"
        
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

def start_recording() -> tuple[str, str]:
    """Starts recording audio output to MP3 file with timestamp."""
    global recording_process
    
    try:
        if recording_process is not None:
            return "Recording already in progress", ""
        
        # Create recordings directory if it doesn't exist
        recordings_dir = "/home/moebius/Projects/youtube-stream-music-generation/recordings"
        os.makedirs(recordings_dir, exist_ok=True)
        
        # Generate filename with timestamp
        from datetime import datetime
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"tidal_session_{timestamp}.mp3"
        filepath = os.path.join(recordings_dir, filename)
        
        # Start ffmpeg recording from default audio output
        # This captures from PulseAudio default monitor (what's playing, not mic)
        recording_process = subprocess.Popen([
            'ffmpeg',
            '-f', 'pulse',
            '-i', '@DEFAULT_MONITOR@',  # Capture from default output monitor (system audio)
            '-acodec', 'libmp3lame',
            '-b:a', '192k',
            '-y',  # Overwrite output file if it exists
            filepath
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        
        return f"Recording started: {filename}", filepath
        
    except Exception as e:
        return f"Error starting recording: {str(e)}", ""

def stop_recording() -> str:
    """Stops the audio recording."""
    global recording_process
    
    if recording_process is None:
        return "No recording in progress"
    
    try:
        # Send SIGINT to ffmpeg for graceful shutdown
        recording_process.terminate()
        recording_process.wait(timeout=5)
        recording_process = None
        return "Recording stopped and saved"
    except Exception as e:
        return f"Error stopping recording: {str(e)}"

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
        r'\beff\b',  # Not a TidalCycles function - use specific effects like 'room', 'delay', etc.
    ]
    
    for pattern in invalid_functions:
        if re.search(pattern, code):
            return "Error: Invalid function detected. Use 'range' with 'saw' or 'sine' for linear interpolation (e.g., 'range 1 2 $ slow 8 $ saw'). Please fix and try again."
    
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
    
    return code

@tool
def execute_tidal_code(code: str) -> str:
    """Executes TidalCycles code in the running session.
    
    Args:
        code: The TidalCycles code to execute.
    """
    global tidal_process
    
    try:
        if tidal_process is None:
            return "Error: No TidalCycles session running. Call start_tidal_session() first."
        
        tidal_process.stdin.write(code + "\n")
        tidal_process.stdin.flush()
        time.sleep(0.5)
        
        return f"TidalCycles code sent to session:\n{code}"
            
    except Exception as e:
        return f"Error: {str(e)}"

# Agent setup
composer_prompt = """
You are an expert TidalCycles live coder and composer.
Your job is to generate TidalCycles code for musical patterns.

CONTEXT AWARENESS:
You will be given the CURRENT PATTERNS that are playing. When asked to:
- ADD: Create a NEW pattern on an unused channel (d2, d3, d4, etc.)
- MODIFY: Update an existing pattern by changing its parameters or effects
- REMOVE: Use "silence" to stop a channel (e.g., "d2 silence")
- TRANSFORM: Change an existing pattern with new effects or variations

Use proper Tidal syntax with DIVERSE sounds:

PERCUSSION:
- d1 $ sound "bd sn"
- d1 $ s "drum*4" # speed "0.5 1 2"
- d1 $ s "hh*16" # gain "0.8 0.6"
- d1 $ s "cp rim cp rim"

MELODIC & HARMONIC:
- d2 $ n "c a f e" # s "superpiano" # room 0.3
- d2 $ n "<0 7 12>" # s "superfm" # lpf 2000 # lpq 0.2
- d2 $ n "0 4 7 12" # s "supermandolin" # delay 0.5
- d2 $ n (scale "minor" "0 2 4 7") # s "supersaw" # room 0.4

BASS & LOW END:
- d3 $ n "c2 c2 e2 g2" # s "bass3" # lpf 400
- d3 $ n "0 0 7 5" # s "reverbkick" # shape 0.6
- d3 $ n "0 7 12" # s "bass1" # lpf 300 # lpq 0.3
- d3 $ s "casio:0" # speed "0.5" # room 0.2

ATMOSPHERIC & TEXTURE:
- d4 $ s "pad:0*4" # room 1 # size 0.9 # orbit 1
- d4 $ s "space:2" # slow 4 # gain 0.7
- d4 $ s "arpy*8" # speed (range 0.5 2 $ slow 8 $ sine) # room 0.8
- d4 $ s "amencutup*16" # n (irand 32) # speed 0.125

EFFECTS & MODULATION:
- d1 $ s "bd*8" # speed (range 1 2 $ slow 8 $ saw)  -- for linear ramps
- d1 $ s "bd cp" # gain (range 0.5 1 $ sine)  -- for sine wave modulation
- d1 $ s "hh*8" # lpf (range 500 5000 $ slow 4 $ tri)
- d1 $ s "sn" # delay 0.5 # delaytime (1/3) # delayfb 0.6
- d2 silence  -- to stop a channel

IMPORTANT SYNTAX RULES:
- For linear/ramping values, use: range <start> <end> $ slow <cycles> $ saw
- For oscillating values, use: range <min> <max> $ sine
- NEVER use 'lin' - it doesn't exist in TidalCycles

MANDATORY WORKFLOW:
You MUST write Python code that:
1. Stores your TidalCycles code in a variable using triple quotes
2. Calls validate_tidal_syntax() to validate it
3. Returns the result using final_answer()

CRITICAL - YOU WILL FAIL IF YOU DO THIS WRONG:
❌ NEVER return raw TidalCycles code directly
❌ NEVER execute: d1 $ sound "bd sn"
✅ ALWAYS wrap in Python: tidal_code = '''d1 $ sound "bd sn"'''

CRITICAL OUTPUT FORMAT:
Write Python code in this EXACT format:

<code>
tidal_code = '''d1 $ sound "bd sn"'''
result = validate_tidal_syntax(tidal_code)
final_answer(result)
</code>

The <code> tags are REQUIRED. The triple quotes are REQUIRED.

REMEMBER: Always wrap TidalCycles code in Python with triple quotes!

EXAMPLE 1 - Percussion:
<code>
tidal_code = '''d1 $ s "bd*4 sn*2" # gain "0.8"'''
result = validate_tidal_syntax(tidal_code)
final_answer(result)
</code>

EXAMPLE 2 - Melodic:
<code>
tidal_code = '''d2 $ n "c e g c5" # s "superpiano" # room 0.3'''
result = validate_tidal_syntax(tidal_code)
final_answer(result)
</code>

EXAMPLE 3 - Bass with effects:
<code>
tidal_code = '''d3 $ n "c2 c2 e2 g2" # s "bass3" # lpf (range 100 800 $ slow 2 $ saw)'''
result = validate_tidal_syntax(tidal_code)
final_answer(result)
</code>

CRITICAL: Use final_answer(result) at the end, NOT print(result).
Use triple quotes (''') for the TidalCycles code string.
Always call validate_tidal_syntax() to check syntax before returning.

"""

composer_agent = CodeAgent(
    tools=[validate_tidal_syntax], 
    model=model,
    additional_authorized_imports=["re"],
    add_base_tools=True,
    max_steps=10,    
)

# Creative Director Agent - suggests next musical tasks based on flow
creative_director_prompt = """
You are a creative musical director and arranger with deep understanding of musical flow, dynamics, and composition.
You analyze TidalCycles code that is currently playing and decide what should happen next.

You will be given the ACTUAL TidalCycles patterns currently playing. Analyze:
- What channels (d1, d2, d3, etc.) are being used
- What sounds and patterns are active
- Rhythmic density and complexity
- Whether patterns are simple or complex
- What's missing or what could be enhanced
- What should be removed or modified

SONIC PALETTE - Consider adding variety from:
- MELODIC: superpiano, supermandolin, superfm, supersaw, superzow
- BASS: bass1, bass2, bass3, reverbkick, casio, sid
- ATMOSPHERIC: pad, space, arpy (with effects)
- PERCUSSION: bd, sn, hh, cp, rim, clap, click
- TEXTURAL: amencutup, glitch, breath, wind
- EFFECTS: room, delay, lpf, hpf, shape, distort

Consider:
- Musical progression and build-up (intro → development → climax → resolution)
- SONIC DIVERSITY - balance percussion with melodic, bass, and atmospheric elements
- Rhythmic complexity and variation
- Harmonic movement and tension/release
- Textural layering (adding/removing elements)
- Energy levels and dynamics
- Contrast and surprise elements
- Overall coherence and structure

Based on the CURRENT CODE you see, suggest ONE specific next action:
1. ADD new patterns on unused channels (prioritize non-percussion if missing)
2. MODIFY existing patterns (e.g., "change d1 to add reverb effect")
3. REMOVE or SIMPLIFY patterns (e.g., "stop d2 to reduce density")
4. TRANSFORM patterns (e.g., "add gradual speed increase to d1")

CRITICAL: Return ONLY ONE suggestion. No list. Just a single task description.
Be specific about channels, sounds, and musical elements.

Example output (ONLY ONE LINE):
add melodic pattern on d2 using superpiano with notes from C minor scale
"""

creative_director_agent = CodeAgent(
    tools=[],
    model=model,
    add_base_tools=True,
    max_steps=5,
)

def run_music_generation(task_text, test_mode, director_interval, use_creative_director=True):
    """Main function to run the music generation agent"""
    global is_running, tidal_process, stop_event, task_queue, current_song_context, executed_tasks_log
    
    stop_event.clear()
    song_context = "Current patterns playing:\n(none yet)\n"
    current_song_context = "(No patterns playing yet)"
    executed_tasks_log = []  # Clear executed tasks log
    
    # Parse initial tasks from text (one per line)
    tasks = [t.strip() for t in task_text.split('\n') if t.strip()]
    task_queue = []  # Initialize empty - will store executed code
    
    if not tasks:
        log_message("No tasks provided. Please enter at least one task.")
        return
    
    log_message(f"\n{'='*60}")
    log_message("Starting Music Generation")
    log_message(f"{'='*60}")
    
    task_index = 0
    while not stop_event.is_set():
        # If we've run out of tasks, check if Creative Director is enabled
        if task_index >= len(tasks):
            if use_creative_director and not test_mode:
                log_message(f"\n   💡 No more tasks. Running Creative Director to suggest next task...")
                
                # Run Creative Director to get new suggestions
                try:
                    director_start = time.time()
                    suggestions = creative_director_agent.run(
                        f"{creative_director_prompt}\n\nCurrent song state:\n{song_context}\n\nSuggest what should come next to enhance the musical flow."
                    )
                    director_time = time.time() - director_start
                    
                    log_message(f"[Creative Director] Time: {director_time:.2f}s")
                    log_message(f"[Creative Director] Suggestions:\n{suggestions}")
                    
                    # Parse suggestions
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
                        else:
                            log_message("[Creative Director] No new tasks extracted from suggestions")
                    else:
                        log_message("[Creative Director] No suggestions received")
                    
                    # Wait before continuing
                    log_message(f"\n   Waiting {director_interval}s before next iteration...")
                    time.sleep(director_interval)
                    continue
                    
                except Exception as e:
                    log_message(f"[Creative Director] Error: {e}")
                    time.sleep(director_interval)
                    continue
            else:
                # No Creative Director - we're done
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
        
        # Generate code
        if test_mode:
            log_message(f"\n[TEST MODE] Using test sample instead of agent")
            code_snippet = TEST_SAMPLE
            composer_time = 0
        else:
            log_message(f"\n[Agent: Composer] Generating code...")
            log_message(f"[Context] Current patterns:\n{song_context}")
            log_message(f"[Task] {task}")
            start_time = time.time()
            
            # Build detailed prompt for composer
            full_prompt = f"""{composer_prompt}

                                CURRENT STATE:
                                {song_context}

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
        
        # Extract TidalCycles code from agent response
        # The agent should have returned validated code from validate_tidal_syntax
        tidal_code = None
        if code_snippet and isinstance(code_snippet, str):
            # The validate_tidal_syntax tool returns the code if valid
            # So the agent's final output should be the validated TidalCycles code
            # First, check if it's already clean TidalCycles code
            stripped = code_snippet.strip()
            if re.match(r'^d\d+\s*[\$]', stripped) or re.match(r'^(setcps|cps|hush|silence)\s', stripped):
                tidal_code = stripped
            else:
                # Try to extract from validation output or other formats
                lines = code_snippet.split('\n')
                tidal_lines = []
                
                for line in lines:
                    line = line.strip()
                    # Match Tidal patterns
                    if (re.match(r'^d\d+\s*[\$\=]', line) or 
                        re.match(r'^(setcps|cps|hush|once|silence|let)\s', line)):
                        tidal_lines.append(line)
                
                if tidal_lines:
                    tidal_code = '\n'.join(tidal_lines)
        
        log_message(f"\n[Extracted Code]:\n{tidal_code if tidal_code else 'None'}")
        
        # If extraction failed, provide helpful debug info
        if not tidal_code and code_snippet:
            log_message(f"[Debug] Could not extract code. Raw output length: {len(code_snippet)} chars")
            log_message(f"[Debug] First 500 chars: {code_snippet[:500]}")
        
        # Execute code
        if tidal_code and tidal_process is not None:
            try:
                log_message(f"\n▶ Executing AI-generated pattern...")
                log_message(f"   Code: {tidal_code}")
                
                tidal_process.stdin.write(tidal_code + "\n")
                tidal_process.stdin.flush()
                log_message("   ✓ Code sent to TidalCycles!")
                
                # Add executed code to task queue for UI display
                task_queue.append(tidal_code)
                
                # Update song context with actual code
                if i == 0:
                    song_context = "Current patterns playing:\n" + tidal_code + "\n"
                    current_song_context = tidal_code
                else:
                    song_context += "\n" + tidal_code + "\n"
                    current_song_context += "\n\n" + tidal_code
                
                # Log executed task
                executed_tasks_log.append(f"Task {i+1}: {task}")
                
                # Start creative director thinking in parallel while music plays
                director_result = {"suggestions": None, "error": None}
                director_thread = None
                
                def run_creative_director():
                    if use_creative_director and not test_mode and not stop_event.is_set():
                        log_message(f"\n🎨 [Creative Director] Analyzing musical flow...")
                        try:
                            director_start = time.time()
                            suggestions = creative_director_agent.run(
                                f"{creative_director_prompt}\n\nCurrent song state:\n{song_context}\n\nSuggest what should come next to enhance the musical flow."
                            )
                            director_time = time.time() - director_start
                            
                            log_message(f"[Creative Director] Time: {director_time:.2f}s")
                            log_message(f"[Creative Director] Suggestions:\n{suggestions}")
                            director_result["suggestions"] = suggestions
                            
                        except Exception as e:
                            log_message(f"[Creative Director] Error: {e}")
                            director_result["error"] = str(e)
                
                # Start creative director in background thread
                if use_creative_director and not test_mode:
                    director_thread = threading.Thread(target=run_creative_director, daemon=True)
                    director_thread.start()
                
                # Pattern plays indefinitely until user stops or we move to next task
                log_message(f"\n   🎵 Pattern is now playing... (press Stop to end)")
                
                # Wait for creative director before deciding what to do next
                if director_thread and director_thread.is_alive():
                    log_message("   Waiting for Creative Director to finish...")
                    director_thread.join(timeout=30)  # Wait max 30 seconds
                
                # Process creative director results if available
                if use_creative_director and not test_mode:
                    # Wait a bit for director thread to finish if still running
                    if director_thread and director_thread.is_alive():
                        director_thread.join(timeout=2)  # Short wait
                    
                    if director_result["suggestions"]:
                        suggestions = director_result["suggestions"]
                        suggestion_lines = []
                        
                        # Handle both string and list responses from agent
                        if isinstance(suggestions, list):
                            # Agent returned a list directly
                            suggestion_lines = [s.strip() for s in suggestions if s.strip()]
                            log_message(f"[Debug] Agent returned list with {len(suggestion_lines)} items")
                        elif isinstance(suggestions, str):
                            # Agent returned a string - parse it
                            for line in suggestions.split('\n'):
                                line = line.strip()
                                # Match numbered list items (e.g., "1. task" or "1) task")
                                match = re.match(r'^\d+[\.\.\)]\s*(.+)$', line)
                                if match:
                                    suggestion_lines.append(match.group(1).strip())
                                elif line and not line.startswith('#') and not line.startswith('/'):
                                    # Include non-numbered lines that might be suggestions
                                    suggestion_lines.append(line)
                        
                        if suggestion_lines:
                            log_message(f"\n✨ Adding {len(suggestion_lines)} new creative tasks to the queue")
                            tasks.extend(suggestion_lines)
                            log_message(f"[Debug] Updated task list. Total tasks now: {len(tasks)}, Remaining: {len(tasks) - task_index - 1}")
                            for idx, sugg in enumerate(suggestion_lines, 1):
                                log_message(f"   {idx}. {sugg}")
                        else:
                            log_message("[Creative Director] No new tasks extracted from suggestions")
                    else:
                        log_message("[Creative Director] No suggestions received")
                
                # Move to next task (or loop back if Creative Director adds more)
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

def start_system(task_text, test_mode, director_interval):
    """Start the TidalCycles system and agent"""
    global is_running, agent_thread, stop_event, accumulated_logs
    
    if is_running:
        return "System is already running!", gr.update(interactive=False), gr.update(interactive=True)
    
    # Clear log queue and accumulated logs
    while not log_queue.empty():
        log_queue.get()
    accumulated_logs = []
    
    log_message("="*60)
    log_message("Initializing TidalCycles Environment")
    log_message("="*60)
    
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
    log_message("🎨 Creative Director: ENABLED")
    log_message("🔴 Recording: ACTIVE")
    log_message("="*60)
    
    is_running = True
    stop_event.clear()
    
    # Start agent in background thread - always use creative director
    agent_thread = threading.Thread(
        target=run_music_generation,
        args=(task_text, test_mode, director_interval, True),  # Always True for creative director
        daemon=True
    )
    agent_thread.start()
    
    return "System started! Check logs below.", gr.update(interactive=False), gr.update(interactive=True)

def stop_system():
    """Stop the TidalCycles system and agent"""
    global is_running, stop_event, tidal_process
    
    if not is_running:
        return "System is not running!", gr.update(interactive=True), gr.update(interactive=False)
    
    stop_event.set()
    log_message("\nStopping system...")
    
    # Stop recording first
    log_message("Stopping recording...")
    recording_result = stop_recording()
    log_message(recording_result)
    
    # Stop all playing patterns first
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
    
    return "System stopped!", gr.update(interactive=True), gr.update(interactive=False)

def get_logs():
    """Get all pending log messages and return accumulated logs"""
    global accumulated_logs
    
    # Get new logs from queue
    while not log_queue.empty():
        accumulated_logs.append(log_queue.get())
    
    # Return all accumulated logs
    if accumulated_logs:
        return "\n".join(accumulated_logs)
    return ""

def get_song_context():
    """Get current song context (all playing code) for UI display"""
    global current_song_context
    return current_song_context

def get_executed_tasks():
    """Get log of executed tasks for UI display"""
    global executed_tasks_log
    if not executed_tasks_log:
        return "(No tasks executed yet)"
    return "\n\n".join(executed_tasks_log)

# Create Gradio interface
with gr.Blocks(title="TidalCycles AI Music Generator") as demo:
    gr.Markdown("# 🎵 TidalCycles AI Music Generator")
    gr.Markdown("Generate live-coded music using AI agents and TidalCycles")
    
    with gr.Row():
        with gr.Column(scale=1):
            gr.Markdown("### Settings")
            
            with gr.Group():
                test_mode_checkbox = gr.Checkbox(
                    label="Test Mode", 
                    value=False,
                    info="Use test sample instead of AI generation"
                )
                
                director_interval_slider = gr.Slider(
                    minimum=3,
                    maximum=30,
                    value=8,
                    step=1,
                    label="Creative Director Interval (seconds)",
                    info="Time between Creative Director suggestions"
                )
                
                task_input = gr.Textbox(
                    label="Initial Task",
                    placeholder="melodic pattern with superpiano using C minor scale",
                    lines=5,
                    value="create a deep bass pattern using bass3 with slow filter modulation"
                )
            
            with gr.Row():
                start_btn = gr.Button("▶ Start", variant="primary", size="lg")
                stop_btn = gr.Button("⏹ Stop", variant="stop", size="lg", interactive=False)
        
        with gr.Column(scale=2):
            gr.Markdown("### Output")
            status_text = gr.Textbox(label="Status", interactive=False)
            
            with gr.Row():
                with gr.Column(scale=1):
                    song_context_output = gr.Textbox(
                        label="Current Song Context (Playing Code)", 
                        lines=25, 
                        max_lines=25,
                        interactive=False,
                        autoscroll=True,
                        placeholder="TidalCycles patterns will appear here..."
                    )
                with gr.Column(scale=1):
                    executed_tasks_output = gr.Textbox(
                        label="Executed Tasks Log",
                        lines=25,
                        max_lines=25,
                        interactive=False,
                        autoscroll=True,
                        placeholder="Completed tasks will appear here..."
                    )
    
    # Event handlers
    start_btn.click(
        fn=start_system,
        inputs=[task_input, test_mode_checkbox, director_interval_slider],
        outputs=[status_text, start_btn, stop_btn]
    )
    
    stop_btn.click(
        fn=stop_system,
        outputs=[status_text, start_btn, stop_btn]
    )
    
    # Auto-refresh song context and executed tasks every 0.5 seconds
    timer = gr.Timer(0.5)
    timer.tick(fn=lambda: get_song_context(), outputs=song_context_output)
    timer.tick(fn=lambda: get_executed_tasks(), outputs=executed_tasks_output)

if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7860, theme=gr.themes.Soft())
