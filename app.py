import os
import time

# Test mode configuration
TEST_MODE = False  # Set to True to use test sample instead of agent
TEST_SAMPLE = 'd1 $ sound "bd [~ bd] sn:1 [bd sn:3]"\n'

# Environment configuration
os.environ['HF_HOME'] = "/home/moebius/Models/hfmodels/.cache/"
os.environ['PYTORCH_CUDA_ALLOC_CONF'] = "expandable_segments:True"
os.environ["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
os.environ["CUDA_VISIBLE_DEVICES"] = "0,1"

from smolagents import CodeAgent, LiteLLMModel, DuckDuckGoSearchTool, tool
import subprocess
import tempfile
import os
import signal
import time
import threading
import re

# Global process handles
tidal_process = None
superdirt_process = None

# Use Ollama for GGUF model support
model = LiteLLMModel(
    model_id="ollama/qwen2.5-coder:14b",
    temperature=0.01,
    max_new_tokens=1024,
)

def start_superdirt() -> str:
    """
    Starts SuperCollider with SuperDirt for audio output.
    Must be called before executing TidalCycles code.
    """
    global superdirt_process
    
    try:
        if superdirt_process is not None:
            return "SuperDirt is already running"
        
        # Create a SuperCollider script to boot server and start SuperDirt
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
        
        # Start SuperCollider with live logging
        superdirt_process = subprocess.Popen(
            ['sclang', temp_file],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )
        
        # Log SuperCollider output in background thread
        def log_sc():
            for line in superdirt_process.stdout:
                print(f"[SuperCollider] {line}", end="")
        
        threading.Thread(target=log_sc, daemon=True).start()
        
        # Give SuperDirt time to boot the server and load samples
        print("Waiting for SuperCollider server to boot and load samples...")
        time.sleep(15)  # Increased to allow sample loading
        
        return "SuperDirt started successfully on port 57120 (server booted, process kept alive)"
        
    except Exception as e:
        return f"Error starting SuperDirt: {str(e)}"

def start_tidal_session() -> str:
    """
    Starts a persistent TidalCycles session with ghci.
    Must be called before executing TidalCycles code.
    """
    global tidal_process
    
    try:
        if tidal_process is not None:
            return "TidalCycles session is already running"
        
        boot_file = "/home/moebius/Projects/youtube-stream-music-generation/tidal/BootTidal.hs"
        
        # Start ghci with TidalCycles loaded and live logging
        tidal_process = subprocess.Popen(
            ['ghci', '-ghci-script', boot_file],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )
        
        # Log GHCi output in background thread
        def log_ghci():
            for line in tidal_process.stdout:
                print(f"[GHCi/Tidal] {line}", end="")
        
        threading.Thread(target=log_ghci, daemon=True).start()
        
        # Give it time to load and check logs
        time.sleep(3)
        
        # Send initial tempo setting
        tidal_process.stdin.write("setcps 0.8\n")
        tidal_process.stdin.flush()
        time.sleep(0.5)
        
        return "TidalCycles session started successfully with cps=0.8"
        
    except Exception as e:
        return f"Error starting TidalCycles session: {str(e)}"

def stop_tidal_session() -> str:
    """
    Stops the running TidalCycles and SuperDirt sessions.
    """
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
    """
    Validates TidalCycles syntax without executing it.
    
    Args:
        code: TidalCycles code string to validate
    """
    import re
    
    if not code.strip():
        return "Error: Empty code. Please generate valid TidalCycles code and try again."
    
    # Valid TidalCycles patterns and functions
    valid_patterns = [
        # Pattern assignments
        r'd\d+\s*\$',  # d1 $, d2 $, etc.
        r'setcps', r'cps',
        r'hush', r'silence',
        r'once', r'anticipate',
        
        # Sound sources
        r'\bsound\b', r'\bs\b', r'\bn\b', r'\bnote\b', r'\bmidinote\b',
        
        # Effects
        r'#\s*\w+', r'\bgain\b', r'\bspeed\b', r'\bpan\b', r'\bdelay\b',
        r'\broom\b', r'\bsize\b', r'\borbit\b', r'\bcut\b', r'\bresonance\b',
        r'\bcutoff\b', r'\bshape\b', r'\bvowel\b', r'\bacceleratе\b',
        
        # Pattern transformations
        r'\bfast\b', r'\bslow\b', r'\brev\b', r'\bjux\b', r'\bevery\b',
        r'\bsometimes\b', r'\boften\b', r'\bstut\b', r'\bchop\b',
        r'\bgap\b', r'\bdensity\b', r'\bdegrade\b', r'\bunmuteAll\b',
        r'\bstack\b', r'\bfastcat\b', r'\bslowcat\b', r'\bcat\b',
        r'\bwhen\b', r'\bwithin\b', r'\boff\b', r'\bbrak\b',
        r'\biter\b', r'\btrunc\b', r'\blinger\b', r'\bstretch\b',
        
        # Continuous patterns and modulation
        r'\brange\b', r'\bsaw\b', r'\bsine\b', r'\bsquare\b', r'\btri\b',
        r'\brand\b', r'\birand\b', r'\bperlin\b', r'\bchoose\b',
        r'\bscale\b', r'\bscaleList\b', r'\brun\b', r'\bscan\b',
        
        # Mini notation patterns
        r'"\s*[\w\s\*\/\[\]\<\>\|\.\,\:\~\!\@\#\-]+\s*"',
        
        # Operators
        r'\$', r'\#', r'\|[\+\-\*\/\>\<]?\|', r'<~', r'~>',
    ]
    
    # Invalid/non-existent functions that should be caught
    invalid_functions = [
        r'\blin\b',  # Common mistake - use 'range' with 'saw' instead
    ]
    
    # Check for invalid functions
    for pattern in invalid_functions:
        if re.search(pattern, code):
            return "Error: Invalid function detected. Use 'range' with 'saw' or 'sine' for linear interpolation (e.g., 'range 1 2 $ slow 8 $ saw'). Please fix and try again."
    
    # Check if code contains at least one valid TidalCycles pattern
    found_valid = False
    for pattern in valid_patterns:
        if re.search(pattern, code):
            found_valid = True
            break
    
    if not found_valid:
        return "Warning: No recognizable TidalCycles syntax found. Please generate proper TidalCycles code with patterns like 'd1 $ sound \"bd sn\"' and try again."
    
    # Check for common syntax errors
    errors = []
    
    # Check for unmatched quotes
    quote_count = code.count('"')
    if quote_count % 2 != 0:
        errors.append("Unmatched quotes")
    
    # Check for unmatched parentheses
    if code.count('(') != code.count(')'):
        errors.append("Unmatched parentheses")
    
    # Check for unmatched brackets
    if code.count('[') != code.count(']'):
        errors.append("Unmatched square brackets")
    
    # Check for unmatched angle brackets
    if code.count('<') != code.count('>'):
        errors.append("Unmatched angle brackets")
    
    if errors:
        return f"Syntax errors found: {', '.join(errors)}. Please fix these errors and try again."
    
    # If valid, return the code itself
    return code

@tool
def execute_tidal_code(code: str) -> str:
    """
    Executes TidalCycles code in the running session.
    Requires start_tidal_session() and start_superdirt() to be called first.
    
    Args:
        code: Valid TidalCycles code string to execute
    """
    global tidal_process
    
    try:
        if tidal_process is None:
            return "Error: No TidalCycles session running. Call start_tidal_session() first."
        
        # Send code to the running ghci process
        tidal_process.stdin.write(code + "\n")
        tidal_process.stdin.flush()
        
        # Give it a moment to process
        time.sleep(0.5)
        
        return f"TidalCycles code sent to session:\n{code}"
            
    except Exception as e:
        return f"Error: {str(e)}"

# Agent 1: Creates and validates TidalCycles code
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


# Parse command-line arguments
import argparse

parser = argparse.ArgumentParser(description='TidalCycles AI Music Generator')
parser.add_argument('--time', type=int, required=True, help='Duration in seconds to play each pattern')
args = parser.parse_args()

PLAY_DURATION = args.time

# Array of tasks to execute
tasks = [
    "base drum pattern with increasing speed over 8 cycles"
]

# Context of the existing song
song_context = f"""
Current TidalCycles session state:
- No patterns currently playing
- Available channels: d1, d2, d3, d4, etc.
- Tempo: cps=0.8

Initial goal: {tasks[0]}
"""

# Initialize TidalCycles environment at startup
print("="*60)
print("Initializing TidalCycles Environment")
print("="*60)

print("\nStarting SuperDirt...")
start_result = start_superdirt()
print(start_result)

print("\nStarting TidalCycles session...")
tidal_result = start_tidal_session()
print(tidal_result)

print("\n" + "="*60)
print("Environment Ready")
print("="*60)

# Execute all tasks in the array
for i, task in enumerate(tasks):
    print(f"\n{'='*60}")
    print(f"Task {i+1}/{len(tasks)}: {task}")
    print(f"{'='*60}")
    
    # Agent 1: Generate TidalCycles code
    if TEST_MODE:
        print(f"\n[TEST MODE] Using test sample instead of agent")
        code_snippet = TEST_SAMPLE
        composer_time = 0
    else:
        print(f"\n[Agent 1: Composer] Generating code...")
        start_time = time.time()
        code_snippet = composer_agent.run(f"{composer_prompt}\n\n{song_context}\n\nGenerate TidalCycles code for: {task}")
        composer_time = time.time() - start_time
    
    print(f"[Agent 1: Composer] Time: {composer_time:.2f}s")
    print(f"[Agent 1: Composer] Generated:\n{code_snippet}")
    
    # Extract TidalCycles code from response - handle multi-line patterns
    tidal_code = None
    
    if code_snippet and isinstance(code_snippet, str):
        # First try: Extract from code blocks
        code_match = re.search(r'```(?:tidal|haskell)?\n?(.*?)\n?```', code_snippet, re.DOTALL)
        if code_match:
            tidal_code = code_match.group(1).strip()
        else:
            # Second try: Find all lines that look like Tidal code
            lines = code_snippet.split('\n')
            tidal_lines = []
            in_pattern = False
            
            for line in lines:
                stripped = line.strip()
                
                # Start of a pattern
                if re.match(r'^d\d+\s*\$', stripped) or re.match(r'^(setcps|cps|hush|once|silence)', stripped):
                    in_pattern = True
                    tidal_lines.append(line.rstrip())
                # Continuation of pattern (indented or starts with operators/functions)
                elif in_pattern and (line.startswith('  ') or line.startswith('\t') or 
                                    re.match(r'^\s*[\$\#\|]', line) or
                                    re.match(r'^\s*\w+\s', line)):
                    tidal_lines.append(line.rstrip())
                # Empty line might be part of pattern
                elif in_pattern and not stripped:
                    continue
                # Non-Tidal line ends pattern
                elif in_pattern:
                    in_pattern = False
            
            if tidal_lines:
                tidal_code = '\n'.join(tidal_lines)
    
    print(f"\n[Extracted Code]:\n{tidal_code if tidal_code else 'None'}")
    
    # Execute the code directly (simpler than agent-based execution)
    if tidal_code:
        print(f"\n▶ Executing AI-generated pattern...")
        print(f"   Code: {tidal_code}")
        
        # Send directly to tidal_process
        if tidal_process is not None:
            try:
                tidal_process.stdin.write(tidal_code + "\n")
                tidal_process.stdin.flush()
                print("   ✓ Code sent to TidalCycles!")
                
                # Update song context
                song_context += f"\n- Pattern {i+1} on d{i+1}: {tidal_code[:50]}..."
                
                # Let the pattern play
                print(f"\n   🎵 Playing for {PLAY_DURATION} seconds... (Listen!)")
                for remaining in range(PLAY_DURATION, 0, -5):
                    print(f"   ⏱  {remaining} seconds remaining...")
                    time.sleep(5)
                
                # Stop the pattern
                print("\n   ■ Stopping pattern...")
                tidal_process.stdin.write("hush\n")
                tidal_process.stdin.flush()
                time.sleep(0.5)
                print("   ✓ Pattern stopped\n")
                
            except Exception as e:
                print(f"   ✗ Error executing code: {e}")
        else:
            print("   ✗ Error: TidalCycles session not running")
    else:
        print(f"\n[Error] No valid TidalCycles code extracted")
    
    print(f"\n{'='*60}")
    print(f"Task {i+1} completed")
    print(f"{'='*60}\n")

# Cleanup
print("\nCleaning up...")
cleanup_result = stop_tidal_session()
print(cleanup_result)

print("✓ Done!")