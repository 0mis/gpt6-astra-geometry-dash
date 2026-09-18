# Native recorded bridge

`main.cpp` is the original Geode bridge for this experiment. It observes player state and nearby geometry, accepts bounded ordinary inputs, captures the game render, and waits for encoder acknowledgments before advancing. Original collisions and deaths remain enabled.

Build configuration and mod metadata are in the parent directory. See the repository README for the tested game, SDK and bindings versions. No game assets, dependencies or compiled binaries are included.
