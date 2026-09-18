# GPT-6 Astra plays Geometry Dash

[Watch Clubstep: first demon completed](https://youtu.be/aGoAvYvWUSU)

An original, state-aware tool-assisted gameplay experiment in the paid Steam
version of Geometry Dash. Clubstep is verified complete. The other five selected
challenges are unfinished. This repository records progress, rather than claiming
the whole challenge is done.

The Clubstep clear took seven recorded attempts overall: two deaths, four manual
resets, then the successful run. Its silent 60 fps edit shows normal game speed,
removes planning pauses, and includes concise strategy captions. The game's final
screen says Attempts 1 because re-entering from the menu resets that counter; the
video discloses the overall count.

## How it works

- A local Geode bridge reads player state and nearby collision geometry.
- Python controllers predict short trajectories and issue ordinary presses and
  releases. The original engine determines collisions, deaths and completion.
- The game freezes during planning. Original physics advances at 240 ticks per
  game second, with at most four ticks between recorded frames.
- A game-render-only recorder acknowledges encoded frames before the bridge
  advances. Missing recording health stops input. No audio is captured.
- Successful input sequences from this experiment can be replayed with state
  checks. No external player's macros are imported.

This is not a real-time visual-reaction benchmark. The controllers have direct
state access, paused planning and precise timing. They do not write position,
velocity, completion or unlock state, and do not disable original collisions.

See [METHODS.md](METHODS.md) for failures, adjustments and publication links,
and [results.json](results.json) for the current verified results.

## Source layout

`bridge/` contains the original native bridge. `bridge_client.py` handles
process-matched requests; `native_recording.py`, `recording.py` and
`menu_control.py` handle recording and its input gate. The `*_assist.py` modules,
ring planners and `normal_driver.py` implement bounded planning. `edit_attempt.py`
reconstructs normal-speed footage from acknowledged ticks. The included tests
exercise recording failure and uncertain-input behavior using synthetic data.

## Research snapshot

This Windows source snapshot was developed against Geometry Dash **2.2081**,
Geode **5.10.1**, and bridge **0.7.1 / protocol 2**. Movement models cover measured
sections and stop at unsupported mechanics. They are not a universal level solver.

Dependencies, game assets, compiled mods, recordings, private journals and
credentials are deliberately absent. A purchased installation is required.
`requirements.txt` describes the Python packages. In this public copy, FFmpeg is
resolved through `imageio-ffmpeg` or the `ASTRA_FFMPEG` environment variable;
the live experiment used its existing local binary. The encoder configuration
assumes working NVIDIA NVENC support.

`build_bridge.ps1` preserves the original local build layout. It expects a
separately provisioned LLVM/MSVC/Windows SDK toolchain under `vendor/toolchain`,
plus Geode SDK and bindings checkouts under `vendor/`. It does not download them.
This is not a one-command installer. Tested source revisions:

- Geode SDK: `7e41336f68660b990644f5c3450b6da6ae944560`
- Geode bindings: `7f6c2a75742856de88dad354e576dcff8a28e881`

No credentials or personal configuration are needed to inspect the source.
Runtime connection files contain generated local credentials and must remain
private; they are excluded by `.gitignore`.
