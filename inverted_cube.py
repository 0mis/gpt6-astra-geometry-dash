"""Inverted cube planning calibrated against original Dry Out movement.

Only the local prediction coordinate system is mirrored. Live game state is
read-only, and every ordinary input uses the existing recording gate/journal.
Gravity and jump measurements are in inverted-cube-live-calibration.json.
"""
import time

import bridge_client as b
import cube_assist as c


def mirror(player, objects):
    p = dict(player, y=-player['y'], vy=-player['vy'], floor_y=-1_000_000.0)
    mirrored = []
    for obj in objects:
        rx, ry, rw, rh = obj['rect']
        mirrored.append(dict(obj, y=-obj['y'], rect=[rx, -(ry+rh), rw, rh],
                             rotation=(180-obj['rotation']) % 360))
    return p, mirrored


def run(batches, coin_distance=750, stop_x=None):
    previous = c._route_stop_x
    c._route_stop_x = stop_x
    try:
        return _run(batches, coin_distance, stop_x)
    except (c.ControllerStop, c.RouteStop) as exc:
        observed = b.request({'op': 'observe'})
        return {'reason': 'controller_stopped' if isinstance(exc, c.ControllerStop)
                else 'died' if observed['state']['p1']['dead'] else 'planned_route_stop',
                'state': observed['state'], 'nearby_objects': observed['nearby_objects']}
    finally:
        c._route_stop_x = previous


def _run(batches, coin_distance, stop_x):
    for _ in range(batches):
        c.check_stop()
        observed = b.request({'op': 'observe'})
        if not observed.get('ok'):
            raise RuntimeError(str(observed))
        s, objects = observed['state'], observed['nearby_objects']
        actual = s['p1']
        if not s['in_level'] or s['ignore_damage'] or s['test_mode'] or s['practice']:
            raise RuntimeError('An ordinary unmodified normal-mode attempt is required')
        if actual['dead'] or s['completed']:
            return {'reason': 'died' if actual['dead'] else 'completed', 'state': s}
        if s['dual'] or not actual['upside_down'] or any(actual[k] for k in
                ('ship', 'ball', 'ufo', 'wave', 'robot', 'spider', 'swing')) or \
                actual['size'] != 1 or abs(actual['speed']-.9) > .001:
            return {'reason': 'mode_requires_new_plan', 'state': s}
        if stop_x is not None and actual['x'] >= stop_x:
            return {'reason': 'planned_route_stop', 'state': s, 'nearby_objects': objects}
        if not s['recorded_current_frame']:
            time.sleep(.05)
            continue
        # Pads and rings in this mode need their own live measurement before use.
        mechanisms = [o for o in objects if o['type'] in (8, 9, 10, 11, 12, 13)
                      and not o['disabled'] and actual['x']-32 < o['x'] < actual['x']+350]
        if mechanisms:
            return {'reason': 'mechanism_requires_plan', 'state': s,
                    'mechanisms': mechanisms, 'nearby_objects': objects}
        coins = [o for o in objects if o['type'] == 22 and not o['disabled']
                 and actual['x']-20 < o['x'] < actual['x']+coin_distance]
        if coin_distance > 0 and coins:
            return {'reason': 'coin_route_requires_plan', 'state': s,
                    'coins': coins, 'nearby_objects': objects}
        p, objs = mirror(actual, objects)
        # Re-observe closely around a gravity portal rather than coasting through
        # a change of physics with the old prediction. The native game flips it.
        portals = [o for o in objects if o['type'] in (3, 4) and not o['disabled']
                   and actual['x']-40 < o['x'] < actual['x']+160]
        interval_limit = 4 if portals else 100
        if not p['on_ground']:
            c.execute(min(interval_limit, c.descent_ticks(p, objs)), False,
                      'Watching the upside-down landing')
            continue
        if c.simulate(p, objs, None)[0]:
            c.execute(interval_limit, False, 'Following the ceiling path')
            continue
        choices = []
        for delay in range(201):
            c.check_stop()
            safe, margin = c.simulate(p, objs, delay, horizon=480,
                                     end_at_landing=True)
            if safe:
                choices.append((margin, delay))
        if not choices:
            return {'reason': 'no_safe_inverted_cube_plan', 'state': s,
                    'nearby_objects': objects}
        _, delay = max(choices)
        if delay:
            current = c.execute(min(delay, interval_limit), False,
                                'Lining up the upside-down jump')
            if current['p1']['dead']:
                return {'reason': 'died', 'state': current}
            if delay > interval_limit:
                continue
            if not current['p1']['upside_down']:
                return {'reason': 'mode_requires_new_plan', 'state': current}
        c.execute(1, True, 'Jumping away from the ceiling obstacle')
    return {'reason': 'batch_limit', 'state': b.request({'op': 'status'})['state']}
