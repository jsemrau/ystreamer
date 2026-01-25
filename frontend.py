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
is_running = False
agent_thread = None
log_queue = Queue()
stop_event = threading.Event()

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
    
    invalid_functions = [r'\blin\b']
    
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
                    Use proper Tidal syntax with patterns like:
                    - d1 $ sound "bd sn"
                    - d1 $ s "drum*4" # speed "0.5 1 2"
                    - d1 $ fast 2 $ sound "bd sn hh cp"
                    - d1 $ s "bd*8" # speed (range 1 2 $ slow 8 $ saw)  -- for linear ramps
                    - d1 $ s "bd cp" # gain (range 0.5 1 $ sine)  -- for sine wave modulation

                    IMPORTANT SYNTAX RULES:
                    - For linear/ramping values, use: range <start> <end> $ slow <cycles> $ saw
                    - For oscillating values, use: range <min> <max> $ sine
                    - NEVER use 'lin' - it doesn't exist in TidalCycles
                    - Always validate with the validate_tidal_syntax tool

                    IMPORTANT: Return ONLY the TidalCycles code, nothing else. No explanations, no markdown, just the code.
                    Always use the validate_tidal_syntax tool to check your code before returning it.
                """

composer_agent = CodeAgent(
    tools=[validate_tidal_syntax], 
    model=model,
    additional_authorized_imports=["re"],
    add_base_tools=False,
    max_steps=3,    
)

def run_music_generation(task_text, test_mode):
    """Main function to run the music generation agent"""
    global is_running, tidal_process, stop_event
    
    stop_event.clear()
    song_context = """
Current TidalCycles session state:
- No patterns currently playing
- Available channels: d1, d2, d3, d4, etc.
- Tempo: default (setcps can be used to change)
- Available sounds: bd (bass drum), sn (snare), cp (clap), hh (hihat), etc.
"""
    
    # Parse tasks from text (one per line)
    tasks = [t.strip() for t in task_text.split('\n') if t.strip()]
    
    if not tasks:
        log_message("No tasks provided. Please enter at least one task.")
        return
    
    log_message(f"\n{'='*60}")
    log_message("Starting Music Generation")
    log_message(f"{'='*60}")
    
    for i, task in enumerate(tasks):
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
            start_time = time.time()
            code_snippet = composer_agent.run(f"{composer_prompt}\n\n{song_context}\n\nGenerate TidalCycles code for: {task}")
            composer_time = time.time() - start_time
        
        log_message(f"[Agent: Composer] Time: {composer_time:.2f}s")
        log_message(f"[Agent: Composer] Generated:\n{code_snippet}")
        
        # Extract TidalCycles code
        tidal_code = None
        if code_snippet and isinstance(code_snippet, str):
            code_match = re.search(r'```(?:tidal|haskell)?\n?(.*?)\n?```', code_snippet, re.DOTALL)
            if code_match:
                tidal_code = code_match.group(1).strip()
            else:
                lines = code_snippet.split('\n')
                tidal_lines = []
                in_pattern = False
                
                for line in lines:
                    stripped = line.strip()
                    if re.match(r'^d\d+\s*\$', stripped) or re.match(r'^(setcps|cps|hush|once|silence)', stripped):
                        in_pattern = True
                        tidal_lines.append(line.rstrip())
                    elif in_pattern and (line.startswith('  ') or line.startswith('\t') or 
                                        re.match(r'^\s*[\$\#\|]', line) or
                                        re.match(r'^\s*\w+\s', line)):
                        tidal_lines.append(line.rstrip())
                    elif in_pattern and not stripped:
                        continue
                    elif in_pattern:
                        in_pattern = False
                
                if tidal_lines:
                    tidal_code = '\n'.join(tidal_lines)
        
        log_message(f"\n[Extracted Code]:\n{tidal_code if tidal_code else 'None'}")
        
        # Execute code
        if tidal_code and tidal_process is not None:
            try:
                log_message(f"\n▶ Executing AI-generated pattern...")
                log_message(f"   Code: {tidal_code}")
                
                tidal_process.stdin.write(tidal_code + "\n")
                tidal_process.stdin.flush()
                log_message("   ✓ Code sent to TidalCycles!")
                
                song_context += f"\n- Pattern {i+1} on d{i+1}: {tidal_code[:50]}..."
                
                # Pattern plays indefinitely until user stops or we move to next task
                # For single task: plays until stop button
                # For multiple tasks: could add logic to auto-transition
                log_message(f"\n   🎵 Pattern is now playing... (press Stop to end)")
                
                # If this is the last task, wait for stop signal
                if i == len(tasks) - 1:
                    while not stop_event.is_set():
                        time.sleep(0.5)
                else:
                    # For multi-task, optionally wait a bit before next task
                    # or just continue immediately (patterns layer)
                    log_message(f"   Moving to next task (pattern will continue layering)...")
                    time.sleep(2)
                
            except Exception as e:
                log_message(f"   ✗ Error executing code: {e}")
        else:
            log_message(f"\n[Error] No valid TidalCycles code extracted or TidalCycles not running")
        
        log_message(f"\n{'='*60}")
        log_message(f"Task {i+1} completed")
        log_message(f"{'='*60}\n")
    
    log_message("\n✓ All tasks completed!")

def start_system(task_text, test_mode):
    """Start the TidalCycles system and agent"""
    global is_running, agent_thread, stop_event
    
    if is_running:
        return "System is already running!", gr.update(interactive=False), gr.update(interactive=True)
    
    # Clear log queue
    while not log_queue.empty():
        log_queue.get()
    
    log_message("="*60)
    log_message("Initializing TidalCycles Environment")
    log_message("="*60)
    
    log_message("\nStarting SuperDirt...")
    start_result = start_superdirt()
    log_message(start_result)
    
    log_message("\nStarting TidalCycles session...")
    tidal_result = start_tidal_session()
    log_message(tidal_result)
    
    log_message("\n" + "="*60)
    log_message("Environment Ready")
    log_message("="*60)
    
    is_running = True
    stop_event.clear()
    
    # Start agent in background thread
    agent_thread = threading.Thread(
        target=run_music_generation,
        args=(task_text, test_mode),
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
    """Get all pending log messages"""
    logs = []
    while not log_queue.empty():
        logs.append(log_queue.get())
    if logs:
        return "\n".join(logs)
    return ""

# Create Gradio interface
with gr.Blocks(title="TidalCycles AI Music Generator") as demo:
    gr.Markdown("# 🎵 TidalCycles AI Music Generator")
    gr.Markdown("Generate live-coded music using AI agents and TidalCycles")
    
    with gr.Row():
        with gr.Column(scale=1):
            gr.Markdown("### Settings")
            test_mode_checkbox = gr.Checkbox(
                label="Test Mode", 
                value=False,
                info="Use test sample instead of AI generation"
            )
            
            gr.Markdown("### Tasks")
            task_input = gr.Textbox(
                label="Music Generation Tasks (one per line)",
                placeholder="base drum pattern with increasing speed over 8 cycles\nsnare pattern with reverb effect\netc...",
                lines=10,
                value="base drum pattern with increasing speed over 8 cycles"
            )
            
            with gr.Row():
                start_btn = gr.Button("▶ Start", variant="primary", size="lg")
                stop_btn = gr.Button("⏹ Stop", variant="stop", size="lg", interactive=False)
        
        with gr.Column(scale=2):
            gr.Markdown("### Output Logs")
            status_text = gr.Textbox(label="Status", interactive=False)
            log_output = gr.Textbox(
                label="Logs", 
                lines=25, 
                max_lines=25,
                interactive=False,
                autoscroll=True
            )
    
    # Event handlers
    start_btn.click(
        fn=start_system,
        inputs=[task_input, test_mode_checkbox],
        outputs=[status_text, start_btn, stop_btn]
    )
    
    stop_btn.click(
        fn=stop_system,
        outputs=[status_text, start_btn, stop_btn]
    )
    
    # Auto-refresh logs every 0.5 seconds
    timer = gr.Timer(0.5)
    timer.tick(fn=lambda: get_logs(), outputs=log_output)

if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7860, theme=gr.themes.Soft())
