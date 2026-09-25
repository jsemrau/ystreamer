# Fresh TidalCycles Installation Guide

## Prerequisites
This guide assumes Ubuntu/Debian-based Linux system.

## Step 1: Install GHC and Cabal (Haskell toolchain)

```bash
# Update package lists
sudo apt-get update

# Install GHC and Cabal
sudo apt-get install -y ghc cabal-install

# Verify installation
ghc --version  # Should show GHC 8.x or 9.x
cabal --version
```

## Step 2: Install SuperCollider

```bash
# Install SuperCollider and plugins
sudo apt-get install -y supercollider sc3-plugins

# Verify installation
sclang -v  # Should show SuperCollider version
```

## Step 3: Update Cabal and Install TidalCycles

```bash
# Update Cabal package list
cabal update

# Install Tidal library (for GHC environment)
cabal install --lib tidal

# Alternative: Install globally
# cabal install tidal --global
```

## Step 4: Install SuperDirt (in SuperCollider)

```bash
# Start SuperCollider and install SuperDirt quarks
echo 'Quarks.install("SuperDirt"); thisProcess.shutdown;' | sclang -D

# This will download and install SuperDirt
# Wait for it to complete (may take a few minutes)
```

## Step 5: Install Dirt-Samples (audio samples)

```bash
# Clone the Dirt-Samples repository
cd ~/
git clone https://github.com/tidalcycles/Dirt-Samples.git

# SuperDirt will automatically find samples in this location
```

## Step 6: Test SuperCollider Audio

```bash
# Start SuperCollider interactive mode
sclang

# In sclang prompt, type:
s.boot;
{ SinOsc.ar(440, 0, 0.3) }.play;  // You should hear a tone
s.quit;
```

**Important:** If you don't hear audio:
- Check audio output device: `pactl list sinks` (PulseAudio)
- Check ALSA: `aplay -l`
- Try setting default audio device in SuperCollider

## Step 7: Test SuperDirt

Create a test file `test_superdirt.scd`:

```supercollider
// Boot server and start SuperDirt
s.boot;

s.waitForBoot {
    "Server booted".postln;
    ~dirt = SuperDirt(2, s);
    ~dirt.loadSoundFiles;
    s.sync;
    ~dirt.start(57120, 0 ! 12);
    "SuperDirt started on port 57120".postln;
};

// Keep alive
Routine { inf.do { 1.wait } }.play;
```

Run it:
```bash
sclang test_superdirt.scd
```

Watch for:
- "Server booted"
- "SuperDirt started on port 57120"
- No error messages about audio devices

## Step 8: Test TidalCycles

```bash
# Start ghci with the BootTidal script
ghci -ghci-script tidal/BootTidal.hs   # from the repo root

# In ghci prompt, type:
d1 $ sound "bd sn"
# You should hear kick and snare alternating

hush  # Stop all patterns
```

## Troubleshooting

### Audio Issues

**1. PulseAudio not working:**
```bash
# Check PulseAudio is running
pulseaudio --check
pulseaudio --start

# Set environment variables
export PULSE_SERVER=unix:/run/user/$(id -u)/pulse/native
```

**2. JACK instead of PulseAudio:**
```bash
sudo apt-get install jackd2
qjackctl  # GUI for JACK
```

**3. Realtime priority warnings:**
```bash
# Add user to audio group
sudo usermod -a -G audio $USER

# Edit limits
sudo nano /etc/security/limits.d/audio.conf
# Add:
# @audio   -  rtprio     95
# @audio   -  memlock    unlimited

# Log out and back in
```

### GHCi can't find Tidal

```bash
# Reinstall with --lib flag
cabal install --lib tidal

# Or add to cabal environment file
mkdir -p ~/.cabal
echo "package-db: $(find ~/.cabal/store -name '*.conf.d' | head -1)" >> ~/.cabal/config
```

### SuperDirt won't start

```bash
# Check if Quarks are installed
sclang -e 'Quarks.installed.do(_.postln); thisProcess.shutdown;'

# Reinstall SuperDirt
sclang -e 'Quarks.install("SuperDirt", true); thisProcess.shutdown;'
```

### Samples not found

```bash
# Check sample location
cd ~/Dirt-Samples
ls  # Should show folders like 808, bass, bd, etc.

# In SuperCollider, set custom sample path:
~dirt.loadSoundFiles(Platform.userHomeDir +/+ "Dirt-Samples/*");
```

## Verify Complete Installation

Run this complete test script:

```bash
#!/bin/bash
echo "=== TidalCycles Installation Check ==="

echo "1. Checking GHC..."
ghc --version || echo "FAILED: GHC not installed"

echo "2. Checking Cabal..."
cabal --version || echo "FAILED: Cabal not installed"

echo "3. Checking SuperCollider..."
sclang -v || echo "FAILED: SuperCollider not installed"

echo "4. Checking Tidal library..."
ghci -e 'import Sound.Tidal.Context' 2>&1 | grep -q "Ok" && echo "OK" || echo "FAILED: Tidal not in GHC environment"

echo "5. Checking Dirt-Samples..."
test -d ~/Dirt-Samples && echo "OK" || echo "WARNING: Dirt-Samples not in home directory"

echo ""
echo "=== Next: Run your Python app ==="
echo "python app.py"
```

## After Installation

Once everything is installed, your Python app should work. The app will:
1. Start SuperCollider with SuperDirt (takes ~15 seconds)
2. Start GHCi with TidalCycles (takes ~3 seconds)
3. Generate and execute patterns
4. Log all output so you can see what's happening

Monitor the logs for `[SuperCollider]` and `[GHCi/Tidal]` messages to verify everything is working.
