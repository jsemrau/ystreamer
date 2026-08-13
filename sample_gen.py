import pandas as pd
import random

def generate_massive_dataset(n=10000):
    # Expanded musical components for maximum variety
    config = {
        "Relaxing": {
            "s": ["superpiano", "superpad", "superbell", "superflute", "bowl", "glass"],
            "scales": ["major", "ionian", "lydian", "pentatonic", "hexatonic"],
            "fx": ["# room 0.8 # sustain 8", "# lpf 600 # sz 0.9", "# delay 0.6 # dtime 0.5", "# gain 0.6"]
        },
        "Trance": {
            "s": ["supersaw", "superpluck", "hoover", "superstatic"],
            "notes": ["c4", "g3", "a3", "f3", "e3", "d3"],
            "arps": ["up", "down", "updown", "converge"],
            "fx": ["# lpf 2800 # delay 0.25", "# resonance 0.4 # room 0.3", "# phaser 0.5", "# squiz 1.2"]
        },
        "Techno": {
            "s": ["bd", "industrial", "glitch", "metal", "heavy", "hc", "kick"],
            "euclid": ["(3,8)", "(5,8)", "(4,8)", "(7,16)", "(11,16)"],
            "fx": ["# squiz 2.5 # lpf (range 300 1500 saw)", "# shape 0.9 # crush 3", "# room 0.2", "# distort 0.4"]
        },
        "House": {
            "s": ["superrhodes", "superorgan", "bass", "909", "clap", "rim"],
            "chords": ["'m7", "'9", "'maj7", "'m9", "'13", "'dim7"],
            "fx": ["# nudge 0.03 # room 0.3", "# gain 0.9 # cutoff 1400", "# delay 0.1 # dt 0.2"]
        },
        "Lofi": {
            "s": ["superrhodes", "superguitar", "vinyl", "lofi", "tape"],
            "notes": ["c4", "d4", "e4", "g4", "a4"],
            "fx": ["# lpf 1200 # nudge 0.06", "# crush 8 # coarse 4", "# gain 0.7 # hpf 500", "# vowel 'a'"]
        }
    }

    rows = []
    for i in range(n):
        style = random.choice(list(config.keys()))
        c = config[style]
        
        # Combinatorial Logic to ensure every row is unique
        smp = random.choice(c["s"])
        fx = random.choice(c["fx"])
        
        if style == "Relaxing":
            note_val = random.randint(0, 11)
            scale = random.choice(c["scales"])
            code = f"d1 $ slow 8 $ n (scale \"{scale}\" \"{note_val}\") # s \"{smp}\" {fx}"
            instr = f"Generate a peaceful {style} ambient wash using {smp} in {scale}"
        
        elif style == "Trance":
            note = random.choice(c["notes"])
            arp = random.choice(c["arps"])
            code = f"d1 $ fast 2 $ n (arp \"{arp}\" \"{note}'minor\") # s \"{smp}\" {fx}"
            instr = f"Create an energetic {style} arpeggio using {smp} with {arp} movement"
            
        elif style == "Techno":
            e = random.choice(c["euclid"])
            code = f"d1 $ s \"{smp}{e}\" {fx} # gain 1.1"
            instr = f"A driving {style} {e} rhythm using {smp} with industrial processing"
            
        elif style == "House":
            root = random.choice(["c", "f", "g", "a"])
            chord = random.choice(c["chords"])
            code = f"d1 $ stack [s \"bd*4\", n \"{root}{chord}\" # s \"{smp}\"] {fx}"
            instr = f"A classic 4-on-the-floor {style} groove with {smp} {chord} chords"
            
        elif style == "Lofi":
            note = random.choice(c["notes"])
            code = f"d1 $ n \"{note}\" # s \"{smp}\" # lpq 0.2 {fx}"
            instr = f"Chill {style} beats using {smp} with a dusty low-pass filter"

        rows.append({"id": i, "style": style, "instruction": instr, "haskell_code": code})

    df = pd.DataFrame(rows)
    df.to_csv("tidal_10k_dataset.csv", index=False)
    return df

# Generate it
dataset = generate_massive_dataset(10000)
print(f"Success! {len(dataset)} samples saved to 'tidal_10k_dataset.csv'")


