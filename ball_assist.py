"""Mini ball control with measured gravity, ground flips and yellow rings."""
import mini_cube


def supported(player):
    return player['ball'] and abs(player['size']-.6) < .001 \
        and abs(player['speed']-.9) < .001 \
        and not any(player[k] for k in ('ship', 'ufo', 'wave', 'robot', 'spider', 'swing'))


def run(actions=20, coin_distance=0):
    return mini_cube.run_mode(actions, supported, 'ball', 8)
