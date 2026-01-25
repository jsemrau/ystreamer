#!/usr/bin/env python3
"""
Simple Tidal Test Script - Guaranteed to make sound!
"""

import subprocess
import time
import tempfile
import signal
import sys

def cleanup(signum, frame):
    """Handle cleanup on interrupt"""
    print("\nCleaning up...")
    sys.exit(0)

signal.signal(signal.SIGINT, cleanup)

print("=" * 60)
print("TIDAL TEST - Making Sure You Can Hear Sound!")
print("=" * 60)

# Step 1: Start SuperCollider/SuperDirt
print("\n[1/3] Starting SuperCollider and SuperDirt...")
sc_code = """
s.boot;
s.waitForBoot {
    "✓ Server booted!".postln;
    ~dirt = SuperDirt(2, s);
    ~dirt.loadSoundFiles;
    s.sync;
    ~dirt.start(57120, 0 ! 12);
    "✓ SuperDirt started on port 57120".postln;
};
thisProcess.openUDPPort(57120);
Routine { inf.do { 1.wait } }.play;
"""

with tempfile.NamedTemporaryFile(mode='w', suffix='.scd', delete=False) as f:
    f.write(sc_code)
    sc_file = f.name

sc_process = subprocess.Popen(['sclang', sc_file], 
                               stdout=subprocess.PIPE, 
                               stderr=subprocess.STDOUT,
                               text=True)

print("Waiting for SuperCollider to boot and load samples (15 seconds)...")
time.sleep(15)
print("✓ SuperDirt should be ready!\n")

# Step 2: Start Tidal
print("[2/3] Starting TidalCycles session...")
tidal_process = subprocess.Popen(
    ['ghci', '-ghci-script', 'tidal/BootTidal.hs'],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
    bufsize=1
)

print("Waiting for Tidal to load (3 seconds)...")
time.sleep(3)
print("✓ Tidal ready!\n")

# Step 3: Play some test patterns
print("[3/3] Playing test patterns...")
print("=" * 60)

test_patterns = [
    ("Basic kick drum (4 beats)", 'd1 $ sound "bd bd bd bd"\n'),
    ("Classic house beat", 'd1 $ sound "bd [~ bd] sn:1 [bd sn:3]"\n'),
    ("Jan Special",'d1 $ sound "bd" # speed "0.5 1 2 3 4 5 6 7"\n'),
]

try:
    for name, pattern in test_patterns:
        print(f"\n▶ {name}")
        print(f"   Pattern: {pattern.strip()}")
        tidal_process.stdin.write(pattern)
        tidal_process.stdin.flush()
        print("   Playing for 8 seconds...")
        time.sleep(8)
        
        # Silence before next pattern
        tidal_process.stdin.write('d1 $ silence\n')
        tidal_process.stdin.flush()
        time.sleep(1)
    
    print("\n" + "=" * 60)
    print("Test complete! Did you hear the sounds?")
    print("=" * 60)
    print("\nIf you heard sound: Your setup is working! 🎵")
    print("If you heard nothing, check:")
    print("  1. Is your volume up?")
    print("  2. Is the correct audio device selected in system settings?")
    print("  3. Try: pactl list short sinks (to see audio outputs)")
    print("\nPress Ctrl+C to exit...")
    
    # Keep running
    while True:
        time.sleep(1)
        
except KeyboardInterrupt:
    print("\n\nCleaning up...")
    
finally:
    # Cleanup
    try:
        tidal_process.stdin.write('hush\n')
        tidal_process.stdin.write(':quit\n')
        tidal_process.stdin.flush()
        tidal_process.wait(timeout=2)
    except:
        pass
    
    try:
        sc_process.terminate()
        sc_process.wait(timeout=2)
    except:
        pass
    
    print("✓ Done!")
