# Prompts Directory

This directory contains the external prompt files used by the TidalCycles AI Music Generator agents.

## Files

### composer_prompt.md
The system prompt for the Composer Agent, which generates TidalCycles code patterns.

**Key Responsibilities:**
- Generate TidalCycles code for musical patterns
- Handle ADD, MODIFY, REMOVE, and TRANSFORM operations
- Follow strict Python code wrapping requirements
- Validate all generated code before execution

### creative_director_prompt.md
The system prompt for the Creative Director Agent, which suggests musical directions and next steps.

**Key Responsibilities:**
- Analyze current playing patterns
- Suggest musical progressions
- Balance sonic diversity (percussion, melodic, bass, atmospheric)
- Provide single, specific task suggestions

## Usage

These prompts are loaded automatically by the `tidal_backend.py` module using the `load_prompt()` function:

```python
composer_prompt = load_prompt("composer_prompt.md")
creative_director_prompt = load_prompt("creative_director_prompt.md")
```

## Editing Prompts

You can edit these markdown files directly to modify the behavior of the AI agents. Changes will be reflected the next time the backend module is loaded or restarted.

### Tips for Editing:
- Keep the markdown structure for readability
- Test changes with the system to ensure agents behave as expected
- Maintain the critical instructions about code formatting and output structure
- Be specific about syntax requirements and examples

## Engine-mode prompts (default)

- `director_piece.md`: plans the next piece (title, style, key, BPM, progression, section arc) as JSON.
- `director_section.md`: picks presets per layer role for the next section as JSON.
- `free_layer.md`: optional LLM counter-melody, written as pentatonic scale degrees.

The engine validates every answer (`music/plan.py`) and falls back to its
deterministic planner whenever an answer is missing or invalid. The
`composer_prompt.md` and `creative_director_prompt.md` prompts are only used
by the legacy loop (`orchestrator.py --legacy`).
