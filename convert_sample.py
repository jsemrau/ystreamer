import pandas as pd
import json

# Load the 10k dataset we generated
df = pd.read_csv("tidal_10k_dataset.csv")

def format_to_llm(row):
    # Standard Alpaca/Instruct format
    return {
        "instruction": f"Translate this musical intent: {row['instruction']} into TidalCycles code.",
        "input": "", # Optional context
        "output": row['haskell_code'],
        "style": row['style'] # Metadata helps for filtered training
    }

# Apply formatting
formatted_data = df.apply(format_to_llm, axis=1).tolist()

# Save as JSONL
with open("tidal_llm_train.jsonl", "w") as f:
    for entry in formatted_data:
        f.write(json.dumps(entry) + "\n")

print(f"Done! Created 10,000 training pairs in 'tidal_llm_train.jsonl'")