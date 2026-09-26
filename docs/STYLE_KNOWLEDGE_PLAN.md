# Plan: give the agents a feeling for musical styles

## Problem

The performer (and the Director) have no real sense of what makes each
style what it is. In a darkwave run, Claude opened with "darkwave lives on
cold sustained pads" and brought in the strings first, while the genre is
carried by the sequencer bass and the drum machine, with pads in the back.

Why this happens:

1. **The model can't hear.** It sees code and block names (`cold_strings`,
   `sequencer_bass`, `lpf 1800`), never the sound, and gets no feedback on
   how an edit sounded.
2. **Nothing tells it what a style is.** The prompt only says
   `Style: darkwave`; the model falls back on vague associations.
3. **The guidance is style-agnostic.** The example moves per phase ("let a
   filter breathe", "mute the beat in the breakdown") are the same for lofi
   and hardcore.
4. **Blocks carry no meaning.** `glass_line -> keys` says nothing about it
   being a sparse, icy counter-melody that comes in late and stays quiet.

## Changes (most effective first)

### 1. A style guide per style, in the prompt (biggest lever)

`prompts/styles/<style>.md`, about 150 words each, loaded into the
Performer's and the Director's prompts:

- identity: what defines the genre, what sets it apart from its neighbours
- what carries it: which elements lead, which stay in the back
- arrangement: how a track builds, where tension comes from, how it breaks down
- idiomatic moves, and moves that are wrong for the style
- 3-5 reference artists or tracks

Example (darkwave): *the bass sequencer and drum machine are the song; pads
stay back; restraint and repetition, changes come from filters and one
element at a time; cold, not lush; references: Clan of Xymox, Lebanon
Hanover, Boy Harsher, Sixth June.*

Cost: about 300 tokens per call. In compact mode the style guide stays and
the Tidal glossary goes first.

### 2. Every block describes its role

A one-line `about` field on each `Block`, shown with the addable blocks and
the playing lines, e.g.
`sequencer_bass: pulsing 16th-note bass, the backbone; enters first`.

### 3. Example moves per style and phase

`PHASE_EXAMPLES` becomes `STYLE_PHASE_EXAMPLES[style][phase]`, e.g. a
techno breakdown ("drop the kick, leave rumble and hats, ride the acid
filter") vs. a lofi breakdown ("mute the drums, let the Rhodes ring").

### 4. The judge scores style fit

jevtypesafe gets the style guide in its `state` and an instruction to
reward idiomatic moves, so its score becomes a style-fit measure
("preferred the LLM's idea / probability mass" per style).

### 5. Later: learn from listening

Rate tracks with the feedback tools; feed the best- and worst-rated edits
back as examples ("in this stream, these moves worked"); eventually use
`runtime/llm_calls.jsonl` plus ratings to fine-tune the local model (LoRA).

## Styles to cover

lofi, ambient, downtempo, piano, deephouse, trance, techno, hardcore,
darkwave.

## How to check it worked

- Same theme and seed, before/after: which roles enter first, which moves
  dominate (op entropy per style), `[LLM]` rate.
- Judge in `--judge jevtypesafe` mode with the style guide: probability
  mass on LLM ideas per style.
- Listening: does each style sound like itself?
