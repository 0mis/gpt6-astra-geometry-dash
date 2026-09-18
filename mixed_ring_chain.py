"""Original short-horizon cube planner for measured yellow, pink and blue rings.

The predictor is local math only. All game inputs pass through cube_assist's
recording gate, and live state is checked again before each individual tap.
"""
import json
import math
import time

import bridge_client as b
import cube_assist as c
import recording as r
import ring_chain
import collision_shapes as shapes

DX = 1.29825
RINGS = {11: (36, 11.18), 12: (141, 8.05), 13: (84, 4.472)}
_plan_cache = None


def physical_ground(player):
    # Recorded ball states retain m_isOnGround after leaving a platform even
    # while vertical velocity changes under gravity. That flag alone must
    # not authorize a free-space flip or invalidate a matching trajectory.
    return player['on_ground'] and not (player.get('ball', False) and abs(player['vy']) > 1e-7)


def body_profile(player):
    """Measured cube dimensions; other sizes are deliberately unsupported."""
    size = player.get('size', 1.0)
    if abs(size-1.0) < .001:
        return 1.0, 15.0, 1.0
    if abs(size-.6) < .001:
        # Ground jump 8.944 and pink pad 8.32 were measured in the original
        # game. Other mini impulses are estimates until individually checked.
        return .6, 9.0, .8
    raise ValueError('Unmeasured cube size')


def key(rect):
    return tuple(round(v, 3) for v in rect)


def ring_key(obj):
    # Different ring colors can share the same rectangle. They remain distinct
    # original objects, even after a tap consumed the first overlapping ring.
    return (obj['type'], obj['id'], *key(obj['rect']))


def ring_used(obj, excluded):
    return ring_key(obj) in excluded or key(obj['rect']) in excluded


def touching(x, y, rect, extent=14):
    rx, ry, rw, rh = rect
    return x+extent > rx and x-extent < rx+rw and y+extent > ry and y-extent < ry+rh


class World:
    def __init__(self, objects):
        self.objects = objects
        self.columns = {}
        for obj in objects:
            if obj['disabled']:
                continue
            kind = obj['type']
            group = 0 if kind == 0 else 1 if kind in (2, 47) else \
                2 if kind in (8, 9, 10) else 3 if kind in RINGS else \
                4 if kind in (3, 4) else None
            if group is None:
                continue
            obstacle = shapes.shape(obj) if group == 1 else None
            rect = obstacle[:4] if obstacle is not None else obj['rect']
            rx, ry, rw, rh = rect
            entry = (obstacle if group == 1 else obj, rect, [rx, -(ry+rh), rw, rh])
            for cell in range(math.floor((rx-17)/64), math.floor((rx+rw+17)/64)+1):
                self.columns.setdefault(cell, ([], [], [], [], []))[group].append(entry)

    def column(self, x):
        return self.columns.get(math.floor(x/64), ((), (), (), (), ()))


def simulate(player, world, pulses=(), horizon=240, excluded=(), end_at_landing=False,
             require_airborne=False, end_at_portal=None):
    x, y, vy = player['x'], player['y'], player['vy']
    ground, upside = physical_ground(player), player['upside_down']
    size, half, force_scale = body_profile(player)
    ball = player.get('ball', False)
    if ball and size != .6:
        return None
    acceleration = .129 if ball else .216
    # Clubstep's mini-ball scene has measured original ground/ceiling at180/420.
    # Ball execution is restricted to that level until other scenes are measured.
    inner, support, floor = 5*size, half-1, (180 if ball else 90)+half
    ceiling = 420-half if ball else None
    was_airborne = not ground
    tapped, used_rings, used_pads = [], set(excluded), set(excluded)
    # An observation is taken after the original engine has processed contacts.
    # Auto pads already overlapping that initial body must not fire a second
    # time merely because a fresh prediction starts inside their rectangle.
    used_pads.update(key(rect) for _, rect, _ in world.column(x)[2]
                     if touching(x, y, rect, half))
    used_portals = set()
    clearance, last_pulse = 100.0, max(pulses, default=-1)
    for tick in range(horizon):
        sign = -1 if upside else 1
        column = world.column(x)
        if ground and (upside or y > floor+.01) and not \
                (ball and upside and abs(y-ceiling)<.01):
            ground = any(abs(sign*y-half-(rect[1]+rect[3])) < .1
                         and x+support > rect[0] and x-support < rect[0]+rect[2]
                         for _, normal, inverted in column[0]
                         for rect in (inverted if upside else normal,))
        if tick in pulses:
            # The original full-size cube successfully activated yellow17009
            # with less than one pixel of overlap at its outer 15px edge.
            ring_extent = half if size == 1 else support
            ring = next((obj for obj, rect, _ in column[3]
                         if not ring_used(obj, used_rings) and touching(x, y, rect, ring_extent)), None)
            if ring is None:
                if not ground:
                    return None
                if ball:
                    upside = not upside
                    sign = -1 if upside else 1
                    vy = -sign*2.684
                else:
                    vy = sign*11.18*force_scale
                tapped.append({'tick': tick, 'object': None})
            else:
                expected_id, force = RINGS[ring['type']]
                force *= force_scale
                if size == .6 and ring['type'] == 13:
                    force = 3.577  # Three recorded taps, in both gravity directions.
                if ball:
                    # All three mini-ball ring impulses are measured. Execution
                    # still checks the native response after each single tap.
                    force = {11: 6.26, 12: 4.821, 13: 2.505}[ring['type']]
                if ring['id'] != expected_id:
                    return None
                used_rings.add(ring_key(ring))
                if ring['type'] == 13:
                    upside = not upside
                    sign = -1 if upside else 1
                    vy = -sign*force
                else:
                    vy = sign*force
                tapped.append({'tick': tick, 'object': ring})
            ground = False
        old_center = sign*y
        old_bottom = old_center-half
        if not ground:
            was_airborne = True
            vy = sign*max(-15.0, sign*vy-acceleration)
            y += vy*.225
        x += DX
        column = world.column(x)
        if not upside and y < floor:
            y, vy, ground = floor, 0.0, True
        if ball and upside and y > ceiling:
            y, vy, ground = ceiling, 0.0, True
        for _, normal, inverted in column[0]:
            rx, ry, rw, rh = inverted if upside else normal
            if x+half <= rx or x-half >= rx+rw:
                continue
            if sign*y-half < ry+rh and sign*y+half > ry:
                # Recorded corner contacts can land after the outer box has
                # crossed the face. Keep a conservative inner square for a
                # side collision; the native game remains authoritative.
                # Native mini-cube corner contact at Clubstep22680 lands with
                # the prior inner edge .223px beyond the face. Retain only a
                # .25px measured tolerance, never a game collision override.
                corner_tolerance = .25 if size == .6 and not ball else 0.
                if sign*vy <= 0 and (old_bottom >= ry+rh-.05 or old_center-inner >= ry+rh-corner_tolerance):
                    y, vy, ground = sign*(ry+rh+half), 0.0, True
                elif x+inner > rx and x-inner < rx+rw and sign*y-inner < ry+rh and sign*y+inner > ry:
                    return None
        for obstacle, _, _ in column[1]:
            gap = shapes.clearance(x, y, half+1, obstacle)
            clearance = min(clearance, gap)
            if gap <= 0:
                return None
        for pad, rect, _ in column[2]:
            pad_key = key(rect)
            if pad_key in used_pads or not touching(x, y, rect, half):
                continue
            used_pads.add(pad_key)
            # Blue pads at 0 and 180 degrees were measured in both directions.
            # Other unmeasured pad orientations still stop the predictor.
            rotation = pad['rotation'] % 360
            if min(rotation, 360-rotation) > .001 and not \
                    (pad['type'] == 10 and abs(rotation-180) < .001):
                return None
            if pad['type'] in (8, 9):
                if upside:
                    return None
                vy = (16.0 if pad['type'] == 8 else 10.4)*force_scale*(.7 if ball else 1)
                if ball:
                    # Original mini-ball launches measured at ticks9336/9868.
                    vy = 7.68 if pad['type'] == 8 else 5.376
            else:
                upside = not upside
                vy = (6.4 if upside else -6.4)*force_scale*(.7 if ball else 1)
            ground = False
        for portal, rect, _ in column[4]:
            portal_key = key(rect)
            if portal_key in used_portals or not touching(x, y, rect, half):
                continue
            used_portals.add(portal_key)
            target_upside = portal['type'] == 3
            if target_upside != upside:
                # Measured yellow-portal entry: the original engine moves first,
                # then changes gravity and halves velocity without changing sign.
                upside, vy, ground = target_upside, vy*.5, False
        if end_at_portal is not None and touching(x, y, end_at_portal['rect'], half-1):
            return {'x': x, 'y': y, 'vy': vy, 'on_ground': ground,
                    'upside_down': upside, 'size': size, 'ball': ball, 'elapsed': tick+1,
                    'tapped': tapped, 'margin': clearance, 'handoff_portal': end_at_portal}
        if end_at_landing and ground and tick >= last_pulse and (was_airborne or not require_airborne):
            return {'x': x, 'y': y, 'vy': vy, 'on_ground': ground,
                    'upside_down': upside, 'size': size, 'ball': ball, 'elapsed': tick+1,
                    'tapped': tapped, 'margin': clearance}
    if end_at_landing or end_at_portal is not None:
        return None
    return {'x': x, 'y': y, 'vy': vy, 'on_ground': ground,
            'upside_down': upside, 'size': size, 'ball': ball, 'elapsed': horizon,
            'tapped': tapped, 'margin': clearance}


def used(state):
    result = set()
    path = r.ROOT/'runtime/ring-inputs.jsonl'
    for line in path.read_text().splitlines() if path.exists() else ():
        row = json.loads(line); prior = row['state']
        if prior['pid'] == state['pid'] and prior['generation'] == state['generation'] \
                and prior['ticks'] <= state['ticks']:
            plan = row['plan']
            rect = plan.get('ring_rect', [plan.get('ring_x', 0)-18, plan.get('ring_y', 0)-18, 36, 36])
            result.add((11, 36, *key(rect)))
    path = r.ROOT/'runtime/mechanism-inputs.jsonl'
    if path.exists():
        for line in path.read_text().splitlines():
            row = json.loads(line)
            if row['after']['pid'] == state['pid'] and row['after']['generation'] == state['generation'] \
                    and row['after']['ticks'] <= state['ticks']:
                obj = row['object']
                result.add(ring_key(obj) if obj['type'] in RINGS else key(obj['rect']))
    return result


def landing_has_exit(player, world, excluded):
    """Reject a safe contact that immediately traps the cube against a hazard."""
    if simulate(player, world, horizon=120, excluded=excluded) is not None:
        return True
    if any(simulate(player, world, (delay,), 240, excluded,
                    end_at_landing=True) is not None for delay in range(65)):
        return True
    if abs(player.get('size', 1)-.6) > .001:
        return False
    # A short mini platform can exit via a jump into the next ring chain.
    # Requiring another plain-jump landing falsely rejects those platforms.
    # Check the actual tap and a bounded safe continuation, not an unbounded
    # upward drift. The main route search still requires its own landing.
    next_rings = sorted((o for o in world.objects if o['type'] in RINGS
                         and not o['disabled'] and not ring_used(o, excluded)
                         and player['x'] < o['x'] < player['x']+350), key=lambda o:o['x'])
    for ring in next_rings[:3]:
        center = round((ring['x']-player['x'])/DX)
        for delay in range(0, 65, 4):
            c.check_stop()
            for pulse in range(max(delay+2, center-20), center+21, 4):
                predicted = simulate(player, world, (delay, pulse), pulse+21, excluded)
                if predicted and any(t['object'] is ring for t in predicted['tapped']):
                    return True
    return False


def choose(player, objects, excluded=(), beam_width=64):
    started, evaluations = time.perf_counter(), 0
    world = World(objects)
    rings = sorted((o for o in objects if o['type'] in RINGS and not o['disabled']
                    and not ring_used(o, excluded)
                    and player['x']-32 < o['x'] < player['x']+650), key=lambda o:o['x'])
    coast = simulate(player, world, horizon=240, excluded=excluded,
                     end_at_landing=True, require_airborne=physical_ground(player))
    if coast is not None and landing_has_exit(coast, world, excluded):
        return {'pulses': [], 'landing': coast, 'first_ring': None,
                'planning_seconds': time.perf_counter()-started, 'evaluations': 1}
    # Walking off a support can activate pads before the first ring, so retain
    # the no-jump prefix even when the initial observation is grounded.
    can_ground_input = physical_ground(player)
    beam = [()] + [(delay,) for delay in range(65)] if can_ground_input else [()]
    # A nearby pad can hand control here even when there are no rings. Check
    # ordinary platform jumps independently of the ring-expansion loop.
    if can_ground_input:
        landings = []
        for prefix in beam:
            if not prefix:
                continue
            c.check_stop()
            final = simulate(player, world, prefix, 240, excluded, end_at_landing=True)
            evaluations += 1
            if final is not None and landing_has_exit(final, world, excluded):
                landings.append((final['margin'], prefix, final))
        if landings:
            _, pulses, final = max(landings, key=lambda v: (v[0], -v[1][0]))
            return {'pulses': list(pulses), 'landing': final,
                    'first_ring': final['tapped'][0]['object'],
                    'planning_seconds': time.perf_counter()-started, 'evaluations': evaluations}
    for ring in rings[:7]:
        c.check_stop()
        center = round((ring['x']-player['x'])/DX)
        candidates = set(beam)
        for prefix in beam:
            for pulse in range(max(0, center-24), center+25, 2):
                if prefix and pulse <= prefix[-1]+1:
                    continue
                candidate = prefix+(pulse,)
                predicted = simulate(player, world, candidate, pulse+1, excluded)
                evaluations += 1
                if predicted and any(v['object'] is ring for v in predicted['tapped']):
                    candidates.add(candidate)
        expanded, solutions, airborne = [], [], []
        checkpoint = max(1, center+26)
        for prefix in candidates:
            predicted = simulate(player, world, prefix, checkpoint, excluded)
            evaluations += 1
            if predicted is None:
                continue
            final = simulate(player, world, prefix, 600, excluded, end_at_landing=True)
            evaluations += 1
            if final is not None and prefix and landing_has_exit(final, world, excluded):
                solutions.append((final['margin'], prefix, final))
            elif prefix and any(v['object'] is ring for v in predicted['tapped']):
                # Some ring routes lead through gravity portals without touching
                # a platform. Keep a bounded, checked airborne endpoint instead
                # of demanding an artificial landing before handing off.
                endpoint = simulate(player, world, prefix,
                                    min(600, max(240, checkpoint+180)), excluded)
                evaluations += 1
                if endpoint is not None and not endpoint['on_ground']:
                    airborne.append((endpoint['margin'], prefix, endpoint))
            expanded.append((predicted['margin'], prefix, predicted))
        if solutions:
            _, pulses, final = max(solutions, key=lambda v:(v[0], -len(v[1])))
            return {'pulses': list(pulses), 'landing': final,
                    'first_ring': final['tapped'][0]['object'],
                    'planning_seconds': time.perf_counter()-started, 'evaluations': evaluations}
        # A mini cube must complete the gravity-ring chain and reach a landing.
        # A locally clear upward endpoint can strand it above the later rings.
        if airborne and abs(player.get('size', 1)-1) < .001:
            _, pulses, final = max(airborne, key=lambda v: (v[0], -len(v[1])))
            return {'pulses': list(pulses), 'landing': final,
                    'first_ring': final['tapped'][0]['object'], 'airborne_handoff': True,
                    'planning_seconds': time.perf_counter()-started, 'evaluations': evaluations}
        if not expanded:
            return None
        expanded.sort(key=lambda v:v[0], reverse=True)
        bins, beam = set(), []
        for _, prefix, predicted in expanded:
            cell = (round(predicted['y']/2), round(predicted['vy'], 1), predicted['upside_down'])
            if cell in bins:
                continue
            bins.add(cell)
            beam.append(prefix)
            if len(beam) >= beam_width:
                break
    return None


def reuse_plan(cache, state, objects, excluded):
    """Reuse only a matching recorded prefix, then check all remaining geometry."""
    if cache is None:
        return None
    before, plan = cache['state'], cache['plan']
    elapsed = state['ticks']-before['ticks']
    if any(state[k] != before[k] for k in ('pid', 'generation', 'level_id')) \
            or not 0 < elapsed < plan['landing']['elapsed']:
        return None
    started = time.perf_counter()
    predicted = simulate(before['p1'], World(cache['objects']), plan['pulses'],
                         elapsed, cache['excluded'])
    p = state['p1']
    if predicted is None or p['dead'] or abs(p['size']-before['p1']['size']) > .001 \
            or p.get('ball', False) != before['p1'].get('ball', False) \
            or p['upside_down'] != predicted['upside_down'] \
            or physical_ground(p) != predicted['on_ground'] \
            or any(abs(p[k]-predicted[k]) > limit for k, limit in
                   (('x', .15), ('y', .15), ('vy', .03))):
        return None
    pulses = [tick-elapsed for tick in plan['pulses'] if tick >= elapsed]
    airborne = plan.get('airborne_handoff', False)
    final = simulate(p, World(objects), pulses,
                     plan['landing']['elapsed']-elapsed+(0 if airborne else 4),
                     excluded, not airborne)
    if final is None:
        return None
    return {'pulses': pulses, 'landing': final,
            'first_ring': final['tapped'][0]['object'] if final['tapped'] else None,
            'airborne_handoff': airborne,
            'planning_seconds': time.perf_counter()-started,
            'evaluations': 2, 'reused_validated_plan': True}


def execute_plan(state, objects, beam_width=64, max_advance=120):
    global _plan_cache
    player = state['p1']
    if state['practice'] or state['ignore_damage'] or state['test_mode'] or state['dual'] or \
            min(abs(player['size']-1), abs(player['size']-.6)) > .001 \
            or (player['ball'] and abs(player['size']-.6) > .001) \
            or (player['ball'] and state['level_id'] != 14) \
            or abs(player['speed']-.9) > .001 or any(player[k] for k in
            ('ship', 'ufo', 'wave', 'robot', 'spider', 'swing')):
        return {'reason': 'unsupported_mixed_ring_state', 'state': state}
    excluded = used(state)
    plan = reuse_plan(_plan_cache, state, objects, excluded)
    if plan is None:
        plan = choose(player, objects, excluded, beam_width)
    if plan is None:
        _plan_cache = None
        return {'reason': 'no_safe_mixed_ring_plan', 'state': state, 'nearby_objects': objects}
    _plan_cache = {'state': state, 'objects': objects, 'plan': plan, 'excluded': excluded}
    print(json.dumps({'mixed_ring_plan': plan, 'percent': state['percent']}), flush=True)
    if not plan['pulses']:
        current = c.execute(min(plan['landing']['elapsed'], max_advance), False,
                            'Following the measured route to the next landing', verbose=False)
        return {'reason': 'coast_advanced', 'state': current}
    delay = plan['pulses'][0]
    if delay > max_advance:
        current = c.execute(max_advance, False, 'Approaching the next measured ring sequence')
        return {'reason': 'coast_advanced', 'state': current}
    if delay:
        current = c.execute(delay, False, 'Timing the next ring in the measured sequence')
        if current['p1']['dead']:
            return {'reason': 'died', 'state': current}
    fresh = b.request({'op': 'observe'})
    current, ring = fresh['state'], plan['first_ring']
    p = current['p1']
    expected = simulate(player, World(objects), (), delay, used(state))
    if expected is None or current['pid'] != state['pid'] or current['generation'] != state['generation'] \
            or p['dead'] or p['upside_down'] != expected['upside_down'] \
            or abs(p['size']-player['size']) > .001 \
            or p.get('ball', False) != player.get('ball', False) \
            or abs(p['x']-expected['x']) > .3 or abs(p['y']-expected['y']) > .5 \
            or (ring is None and not p['on_ground']) \
            or (ring is not None and not touching(p['x'], p['y'], ring['rect'],
                body_profile(p)[1]-(0 if abs(p['size']-1)<.001 else 1))):
        return {'reason': 'mixed_ring_alignment_requires_plan', 'state': current,
                'plan': plan, 'nearby_objects': fresh['nearby_objects']}
    predicted = simulate(p, World(fresh['nearby_objects']), (0,), 1, used(current))
    if predicted is None:
        return {'reason': 'mixed_ring_tap_requires_plan', 'state': current}
    caption = ('Flipping ball gravity' if player['ball'] else 'Jumping to the next platform') if ring is None else \
        'Tapping the '+{11:'yellow', 12:'pink', 13:'blue'}[ring['type']]+' ring'
    after = c.execute(1, True, caption)
    if ring is not None:
        with (r.ROOT/'runtime/mechanism-inputs.jsonl').open('a', encoding='utf-8') as out:
            out.write(json.dumps({'time': time.time(), 'object': ring, 'before': current, 'after': after})+'\n')
    if after['p1']['dead']:
        return {'reason': 'died', 'state': after}
    if after['p1']['upside_down'] != predicted['upside_down'] or abs(after['p1']['vy']-predicted['vy']) > .03:
        return {'reason': 'unexpected_mixed_ring_response', 'state': after, 'plan': plan}
    after = c.execute(1, False, 'Releasing before the next ring', verbose=False)
    return {'reason': 'jump_launched' if ring is None else 'ring_launched', 'state': after}


def run(actions=8):
    for _ in range(actions):
        c.check_stop()
        observed = b.request({'op': 'observe'})
        state = observed['state']
        if state['p1']['dead']:
            return {'reason': 'died', 'state': state}
        if state['p1']['on_ground']:
            return {'reason': 'landed', 'state': state}
        result = execute_plan(state, observed['nearby_objects'])
        r.atomic_json(r.ROOT/'runtime/mixed-ring-last-result.json', result)
        if result['reason'] not in ('ring_launched', 'jump_launched', 'coast_advanced'):
            return result
    return {'reason': 'batch_limit', 'state': b.request({'op': 'status'})['state']}
