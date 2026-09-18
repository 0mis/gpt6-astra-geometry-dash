"""Original, bounded cube timing planner using observed game collision geometry.

Only proposes ordinary jump inputs to the recording-gated native bridge. The
approximate local trajectory is never written into the game. No supplied macros,
checkpoint edits, collision bypass, or unlock changes are used.
"""
import argparse
import json
import math
import time

import bridge_client as b
import menu_control as m
import recording as r
import collision_shapes as shapes


_geometry_source = None
_geometry_data = None
_empty_column = ((), (), (), ())
_route_stop_x = None


class RouteStop(Exception):
    """A bounded route ended before another gameplay input could be sent."""


class ControllerStop(Exception):
    """A process-matched request stops control between journaled inputs."""


def check_stop():
    path=r.ROOT/'runtime/controller-stop.json'
    if path.is_file() and json.loads(path.read_text())['pid']==r.os.getpid():
        raise ControllerStop()


def geometry(objects):
    """Cache nearby x columns for repeated predictions of one observation."""
    global _geometry_source, _geometry_data
    if _geometry_source is objects:
        return _geometry_data
    solids = [o['rect'] for o in objects if o['type'] == 0 and not o['disabled']]
    hazard_shapes = [shapes.shape(o) for o in objects if o['type'] in (2, 47) and not o['disabled']]
    hazards = [shape[:4] for shape in hazard_shapes]
    pads = [o['rect'] for o in objects if o['type'] == 8 and not o['disabled']
            and abs(o['rotation'] % 360) < .001]
    rings = [o['rect'] for o in objects if o['type'] == 11 and o['id'] == 36 and not o['disabled']]
    columns = {}
    for kind, rects in enumerate((solids, hazards, pads, rings)):
        for index, rect in enumerate(rects):
            rx, _, rw, _ = rect
            entry = rect if kind == 0 else hazard_shapes[index] if kind == 1 else (index, rect)
            for cell in range(math.floor((rx-17)/64), math.floor((rx+rw+17)/64)+1):
                columns.setdefault(cell, ([], [], [], []))[kind].append(entry)
    _geometry_source = objects
    _geometry_data = solids, hazards, pads, rings, columns
    return _geometry_data


def descent_ticks(player, objects):
    """Stop before a predicted landing, including very short platform contact."""
    x, y, vy = player['x'], player['y'], player['vy']
    floor_y = player.get('floor_y', 105.0)
    solids = [o['rect'] for o in objects if o['type'] == 0 and not o['disabled']]
    for tick in range(1, 19):
        old_bottom = y - 15
        vy = max(-15.0, vy - .216)
        y += vy * .225
        x += 1.29825
        landing = y <= floor_y
        for rx, ry, rw, rh in solids:
            if x + 15 > rx and x - 15 < rx + rw and old_bottom >= ry + rh - .1 and y - 15 <= ry + rh:
                landing = True
                break
        if landing:
            return min(16, max(1, tick - 2))
    return 16


def simulate(player, objects, jump_at, horizon=220, end_at_landing=False, check_escape=True, state_out=None):
    x, y, vy = player['x'], player['y'], player['vy']
    floor_y = player.get('floor_y', 105.0)
    ground = player['on_ground']
    dx = 1.29825
    solids, hazards, pads, rings, columns = geometry(objects)
    pulse_ticks = (jump_at,) if isinstance(jump_at, int) else tuple(jump_at or ())
    last_pulse = max(pulse_ticks, default=-1)
    triggered_rings = set()
    triggered_pads = set()
    clearance = 100.0
    jumped = False
    forward = [rx+rw for rx,ry,rw,rh in hazards if rx+rw > x+16]
    forward += [rx+rw for rx,ry,rw,rh in solids if rx > x+14 and ry+rh > y-14]
    first_end = min(forward) if forward else x
    for tick in range(horizon):
        old_bottom = y - 15
        column = columns.get(math.floor(x/64), _empty_column)
        if ground and y > floor_y + .01:
            ground = any(abs(old_bottom-(ry+rh)) < .1 and x+14 > rx and x-14 < rx+rw
                         for rx, ry, rw, rh in column[0])
        if tick in pulse_ticks:
            ring_index = next((i for i, (rx, ry, rw, rh) in column[3]
                               if i not in triggered_rings and x+14 > rx and x-14 < rx+rw
                               and y+14 > ry and y-14 < ry+rh), None)
            if not ground and ring_index is None:
                return False, -1.0
            if ring_index is not None:
                triggered_rings.add(ring_index)
            ground, vy = False, 11.18
            jumped = True
        if not ground:
            vy = max(-15.0, vy - .216)
            y += vy * .225
        x += dx
        column = columns.get(math.floor(x/64), _empty_column)
        if y < floor_y:
            y, vy, ground = floor_y, 0.0, True
        for rx, ry, rw, rh in column[0]:
            if x+14.5 <= rx or x-14.5 >= rx+rw:
                continue
            if y-15 < ry+rh and y+15 > ry:
                if vy <= 0 and old_bottom >= ry+rh-.05:
                    y, vy, ground = ry+rh+15, 0.0, True
                else:
                    return False, -1.0
        for obstacle in column[1]:
            gap = shapes.clearance(x, y, 16, obstacle)
            clearance = min(clearance, gap)
            if gap <= 0:
                return False, gap
        # Measured in the original game: a normal yellow pad sets vy=16 after
        # that tick's movement; the next tick resumes the same -.216 gravity.
        for pad_index, (rx, ry, rw, rh) in column[2]:
            if pad_index not in triggered_pads and x+15 > rx and x-15 < rx+rw and y-15 < ry+rh and y+15 > ry:
                triggered_pads.add(pad_index)
                vy, ground = 16.0, False
        if end_at_landing and jumped and ground and tick >= last_pulse:
            # Landing on a raised ledge can safely overlap a low spike in x.
            # The collision checks above already require vertical clearance.
            if x-14 <= first_end and y <= floor_y + .01:
                return False, -1.0
            if check_escape:
                landed = {'x':x, 'y':y, 'vy':0.0, 'on_ground':True, 'floor_y':floor_y}
                survives, _ = simulate(landed, objects, None, horizon=90, check_escape=False)
                if not survives:
                    survives = any(simulate(landed, objects, delay, horizon=400,
                        end_at_landing=True, check_escape=False)[0] for delay in range(81))
                if not survives:
                    next_rings = [o for o in objects if o['type'] == 11 and o['id'] == 36
                                  and not o['disabled'] and x < o['x'] < x + 260]
                    if next_rings:
                        import ring_assist
                        survives = ring_assist.choose(landed, objects,
                            min(next_rings, key=lambda o:o['x']), check_escape=False) is not None
                if not survives:
                    return False, -1.0
            if state_out is not None:
                state_out.update(x=x,y=y,vy=vy,on_ground=ground,
                                 triggered_rings=[rings[i] for i in sorted(triggered_rings)])
            return True, clearance
    if end_at_landing:
        return False, -1.0
    if state_out is not None:
        state_out.update(x=x,y=y,vy=vy,on_ground=ground,
                         triggered_rings=[rings[i] for i in sorted(triggered_rings)])
    return True, clearance


def execute(ticks, hold, purpose, verbose=True):
    check_stop()
    stop_after = False
    if _route_stop_x is not None:
        current = b.request({'op':'status'})['state']
        # A small conservative speed bound also covers float32 rounding in x.
        available = math.floor((_route_stop_x-current['p1']['x'])/1.300)
        if available < ticks:
            if available <= 0:
                raise RouteStop()
            ticks, stop_after = available, True
    caption = m.client({'action': 'caption', 'text': 'GPT-6 ASTRA | ' + purpose})
    if not caption.get('ok'):
        raise RuntimeError(str(caption))
    for attempt in range(5):
        result = b.request({'op': 'step', 'ticks': ticks, 'hold': hold, 'hold2': False})
        if result.get('ok'):
            break
        # This exact native rejection occurs before ticksLeft or inputs change.
        # A new frozen render can briefly race its encoder acknowledgment.
        # Never retry a timeout, uncertain request, or any other failed action.
        if result.get('error') != 'The current native game frame is not acknowledged by the encoder':
            break
        time.sleep(.1)
    if not result.get('ok'):
        raise RuntimeError(str(result))
    state = result['state']
    row = {'wall_time': time.time(), 'purpose': purpose, 'ticks_requested': ticks, 'hold': hold,
           'request_id': result['request_id'], 'reason': result.get('reason'), 'state': state,
           'round_trip_seconds': result['round_trip_seconds']}
    with (r.ROOT/'runtime/cube-inputs.jsonl').open('a', encoding='utf-8') as out:
        out.write(json.dumps(row)+'\n')
    if verbose and (hold or ticks >= 80 or state['p1']['dead']):
        print(json.dumps({'percent': round(state['percent'],3), 'x':round(state['p1']['x'],2),
                          'y':round(state['p1']['y'],2),'tick':state['ticks'], 'dead':state['p1']['dead'],
                          'purpose':purpose,'seconds':round(result['round_trip_seconds'],3)}), flush=True)
    if result.get('reason') not in {'requested_ticks_finished_recorded_frozen', 'died_recorded'}:
        raise RuntimeError('Step stopped early; inspect recording and current state: ' + str(result.get('reason')))
    if stop_after:
        raise RouteStop()
    return state


def run(batches, coin_distance=500, stop_x=None):
    global _route_stop_x
    previous = _route_stop_x
    _route_stop_x = stop_x
    try:
        return _run(batches,coin_distance,stop_x)
    except ControllerStop:
        obs=b.request({'op':'observe'})
        return {'reason':'controller_stopped','state':obs['state'],
                'nearby_objects':obs['nearby_objects']}
    except RouteStop:
        obs=b.request({'op':'observe'})
        return {'reason':'died' if obs['state']['p1']['dead'] else 'planned_route_stop',
                'state':obs['state'],'nearby_objects':obs['nearby_objects']}
    finally:
        _route_stop_x=previous


def _run(batches, coin_distance=500, stop_x=None):
    for _ in range(batches):
        check_stop()
        observed = b.request({'op': 'observe'})
        if not observed.get('ok'):
            raise RuntimeError(str(observed))
        s = observed['state']
        if not s['in_level'] or s['ignore_damage'] or s['test_mode']:
            raise RuntimeError('An unmodified ordinary play attempt is required')
        p = s['p1']
        if p['dead'] or s['completed']:
            return {'reason': 'died' if p['dead'] else 'completed', 'state': s}
        if stop_x is not None and p['x'] >= stop_x:
            return {'reason': 'planned_route_stop', 'state': s, 'nearby_objects': observed['nearby_objects']}
        if not s['recorded_current_frame']:
            time.sleep(.05)
            continue
        if s.get('end_animation_started'):
            execute(240, False, 'Letting the original completion screen finish')
            continue
        if any(p[k] for k in ('ship','ball','ufo','wave','robot','spider','swing','upside_down')) or p['size'] != 1 or abs(p['speed']-.9)>.001:
            return {'reason': 'mode_requires_new_plan', 'state': s}
        if not s['started']:
            execute(240, False, 'Allowing the original level intro to finish')
            continue
        objs = observed['nearby_objects']
        new_mechanisms = [o for o in objs if (o['type'] in (9, 10, 12, 13)
                          or o['type'] == 11 and o['id'] != 36
                          or o['type'] == 8 and abs(o['rotation'] % 360) >= .001)
                          and not o['disabled'] and p['x'] - 20 < o['x'] < p['x'] + 160]
        if new_mechanisms:
            return {'reason': 'mechanism_requires_plan', 'state': s,
                    'mechanisms': new_mechanisms, 'nearby_objects': objs}
        coins = [o for o in objs if o['type'] == 22 and not o['disabled'] and p['x']-20 < o['x'] < p['x']+coin_distance]
        if coin_distance > 0 and coins:
            return {'reason':'coin_route_requires_plan', 'state':s, 'coins':coins, 'nearby_objects':objs}
        if not p['on_ground']:
            import ring_chain
            used_rings=ring_chain.used(s)
            nearby_rings=[o for o in objs if o['type']==11 and o['id']==36 and not o['disabled']
                          and ring_chain.key(o['rect']) not in used_rings and p['x']-32<o['x']<p['x']+65]
            touching=[]
            for ring in nearby_rings:
                rx,ry,rw,rh=ring['rect']
                if p['x']+14>rx and p['x']-14<rx+rw and p['y']+14>ry and p['y']-14<ry+rh:
                    touching.append(ring)
            if touching:
                # Some original rings lead into a low ceiling. Preserve the
                # no-input route when it reaches safe support beneath them.
                coast_state={}
                coast_safe,_=simulate(p,objs,None,horizon=90,
                                      check_escape=False,state_out=coast_state)
                if not (coast_safe and coast_state['on_ground']):
                    result=ring_chain.execute_plan(s,objs)
                    if result['reason']!='ring_launched':
                        return result
                    continue
            interval=descent_ticks(p,objs)
            if nearby_rings:
                interval=min(interval,4)
            execute(interval, False, 'Watching the landing before the next jump')
            continue
        rings = [o for o in objs if o['type'] == 11 and o['id'] == 36 and not o['disabled']
                 and p['x'] < o['x'] < p['x'] + 350]
        if rings:
            import ring_assist
            result = ring_assist.execute_plan(s, objs, min(rings, key=lambda o:o['x']))
            if result['reason'] == 'ring_launched':
                continue
            if result['reason'] != 'no_safe_yellow_ring_plan':
                return result
            import ring_chain
            result=ring_chain.execute_plan(s,objs)
            if result['reason'] in ('ring_launched','jump_launched'):
                continue
            if result['reason']!='no_safe_airborne_ring_chain':
                return result
        safe, _ = simulate(p, objs, None)
        if safe:
            execute(100, False, 'Approaching the next obstacle')
            continue
        candidates = []
        for delay in range(201):
            check_stop()
            safe, margin = simulate(p, objs, delay, horizon=480, end_at_landing=True)
            if safe:
                candidates.append((margin, delay))
        if not candidates:
            return {'reason': 'no_safe_cube_plan', 'state': s, 'nearby_objects': objs}
        _, delay = max(candidates)
        if delay:
            state = execute(delay, False, 'Lining up the next jump')
            if state['p1']['dead']:
                return {'reason':'died', 'state':state}
        execute(1, True, 'Jumping over the next obstacle')
    return {'reason':'batch_limit', 'state':b.request({'op':'status'})['state']}


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--batches',type=int,default=20)
    args=parser.parse_args()
    if not 1<=args.batches<=300:
        raise ValueError('Use a bounded 1..300 observation batches')
    result=run(args.batches)
    r.atomic_json(r.ROOT/'runtime/cube-last-result.json',result)
    print(json.dumps({'reason':result['reason'],'state':result['state']},indent=2),flush=True)
