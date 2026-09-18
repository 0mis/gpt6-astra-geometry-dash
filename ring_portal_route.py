"""Local ring search that must reach an observed mode portal before handoff.

This module only predicts trajectories; it never writes live game state.
"""
import json
import time
import mixed_ring_chain as mx
import cube_assist as c
import bridge_client as b
import recording as r


def choose(player, objects, portal, excluded=(), beam_width=256):
    started = time.perf_counter()
    world = mx.World(objects)
    horizon = min(900, max(1, round((portal['x']-player['x'])/mx.DX)+65))
    rings = sorted((o for o in objects if o['type'] in mx.RINGS and not o['disabled']
                    and not mx.ring_used(o, excluded)
                    and player['x']-33 < o['x'] < portal['x']), key=lambda o:o['x'])
    # A coast can land on another platform before the first deliberate jump.
    # Simulate that contact instead of limiting every jump to the first ledge.
    beam = [()] + [(delay,) for delay in range(min(240,horizon))] if mx.physical_ground(player) else [()]
    checked = 0
    direct=[]
    for prefix in beam:
        c.check_stop()
        final=mx.simulate(player,world,prefix,horizon,excluded,end_at_portal=portal)
        checked+=1
        if final is not None:
            direct.append((final['margin'],prefix,final))
    if direct:
        _,pulses,final=max(direct,key=lambda v:(v[0],-len(v[1])))
        return {'pulses':list(pulses),'landing':final,
                'first_ring':final['tapped'][0]['object'] if final['tapped'] else None,
                'airborne_handoff':True,'portal_handoff':portal,
                'planning_seconds':time.perf_counter()-started,'evaluations':checked}
    for ring in rings:
        c.check_stop()
        center = round((ring['x']-player['x'])/mx.DX)
        candidates = set(beam)
        for prefix in beam:
            for pulse in range(max(0, center-26), center+27, 2):
                if prefix and pulse <= prefix[-1]+1:
                    continue
                candidate = prefix+(pulse,)
                pred = mx.simulate(player, world, candidate, pulse+1, excluded)
                checked += 1
                if pred and any(t['object'] is ring for t in pred['tapped']):
                    candidates.add(candidate)
        expanded, solutions = [], []
        checkpoint = max(1, center+28)
        for prefix in candidates:
            pred = mx.simulate(player, world, prefix, checkpoint, excluded)
            checked += 1
            if pred is None:
                continue
            final = mx.simulate(player, world, prefix, horizon, excluded, end_at_portal=portal)
            checked += 1
            if final is not None:
                solutions.append((final['margin'], prefix, final))
            expanded.append((pred['margin'], prefix, pred))
        print(json.dumps({'portal_route_ring_x':ring['x'], 'color':ring['type'],
                          'expanded':len(expanded), 'solutions':len(solutions),
                          'evaluations':checked, 'seconds':time.perf_counter()-started}), flush=True)
        if solutions:
            _, pulses, final = max(solutions, key=lambda v:(v[0],-len(v[1])))
            return {'pulses':list(pulses), 'landing':final,
                    'first_ring':final['tapped'][0]['object'] if final['tapped'] else None,
                    'airborne_handoff':True, 'portal_handoff':portal,
                    'planning_seconds':time.perf_counter()-started,'evaluations':checked}
        if not expanded:
            return None
        expanded.sort(key=lambda v:v[0], reverse=True)
        bins, beam = set(), []
        for _, prefix, pred in expanded:
            cell = (round(pred['y']/2),round(pred['vy'],1),pred['upside_down'],
                    tuple(mx.ring_key(t['object']) for t in pred['tapped'] if t['object']))
            if cell in bins:
                continue
            bins.add(cell);beam.append(prefix)
            if len(beam) >= beam_width:
                break
    return None


def execute_verified(saved, max_steps=128):
    """Execute our computed route in short inputs, validating actual movement."""
    source, plan = saved['state'], saved['plan']
    if plan is None:
        raise ValueError('A complete locally predicted portal route is required')
    initial = b.request({'op':'status'})['state']
    if initial['level_id'] != source['level_id'] or initial['ticks'] != source['ticks'] \
            or initial['practice'] or initial['ignore_damage'] or initial['test_mode']:
        raise ValueError('Route requires its verified normal-mode entry')
    world = mx.World(saved['objects'])
    excluded = mx.used(source)
    for _ in range(max_steps):
        c.check_stop()
        obs = b.request({'op':'observe'});state = obs['state'];p = state['p1']
        if any(state[k] != initial[k] for k in ('pid','generation','level_id')):
            return {'reason':'route_identity_changed','state':state}
        if p['dead'] or state['completed']:
            return {'reason':'died' if p['dead'] else 'completed','state':state}
        if p['ship'] or abs(p['size']-source['p1']['size'])>.001:
            return {'reason':'mode_requires_new_plan','state':state}
        elapsed = state['ticks']-initial['ticks']
        expected = mx.simulate(source['p1'],world,plan['pulses'],elapsed,excluded)
        if expected is None or p['upside_down'] != expected['upside_down'] \
                or any(abs(p[k]-expected[k]) > lim for k,lim in (('x',.4),('y',.3),('vy',.03))):
            return {'reason':'portal_route_response_requires_plan','state':state,'expected':expected}
        if elapsed >= plan['landing']['elapsed']:
            return {'reason':'predicted_portal_reached','state':state}
        tap = next((v for v in plan['landing']['tapped'] if v['tick']==elapsed),None)
        if tap is not None:
            obj = tap['object']
            if obj is None:
                if not mx.physical_ground(p):
                    return {'reason':'portal_route_ground_alignment','state':state}
                c.execute(1,True,'Jumping from the platform into the portal route')
            else:
                fresh = next((o for o in obs['nearby_objects'] if mx.ring_key(o)==mx.ring_key(obj)),None)
                extent = mx.body_profile(p)[1]-(0 if abs(p['size']-1)<.001 else 1)
                if fresh is None or not mx.touching(p['x'],p['y'],fresh['rect'],extent):
                    return {'reason':'portal_route_ring_alignment','state':state,'object':obj}
                after = c.execute(1,True,'Tapping the '+{11:'yellow',12:'pink',13:'blue'}[obj['type']]+' ring in the portal route')
                with (r.ROOT/'runtime/mechanism-inputs.jsonl').open('a') as out:
                    out.write(json.dumps({'time':time.time(),'object':fresh,'before':state,'after':after})+'\n')
        else:
            next_tick = min([v for v in plan['pulses'] if v>elapsed]+[plan['landing']['elapsed']])
            c.execute(min(16,next_tick-elapsed),False,'Following the verified ring route',verbose=False)
    return {'reason':'batch_limit','state':b.request({'op':'status'})['state']}
