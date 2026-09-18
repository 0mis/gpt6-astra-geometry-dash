"""Measured yellow-ring input planning, using only ordinary button presses."""
import json
import time

import bridge_client as b
import cube_assist as c
import recording as r


def choose(player, objects, ring, check_escape=True):
    if not player['on_ground']:
        return None
    center_tick = round((ring['x'] - player['x']) / 1.29825)
    for spread, ages in [(4, range(32, 81)), (16, range(12, 101))]:
        candidates = []
        for ring_tick in range(max(3, center_tick-spread), center_tick+spread+1):
            c.check_stop()
            for age in ages:
                first_tick = ring_tick-age
                if not 0 <= first_tick <= 220:
                    continue
                ok, margin = c.simulate(player, objects, (first_tick, ring_tick),
                    horizon=min(600, ring_tick+300), end_at_landing=True,
                    check_escape=False)
                if ok:
                    candidates.append((margin, -abs(ring_tick-center_tick), first_tick, ring_tick))
        if candidates:
            # Escape feasibility does not affect the candidate's margin. Check
            # candidates in score order instead of recursively expanding every
            # landing before knowing whether it could be the selected route.
            for margin, _, first_tick, ring_tick in sorted(candidates,reverse=True):
                if check_escape and not c.simulate(player,objects,(first_tick,ring_tick),
                        horizon=min(600,ring_tick+300),end_at_landing=True,
                        check_escape=True)[0]:
                    continue
                return {'first_tick':first_tick, 'ring_tick':ring_tick,
                        'ring_x':ring['x'], 'ring_y':ring['y'], 'predicted_margin':margin}
    return None


def execute_plan(state, objects, ring):
    plan = choose(state['p1'], objects, ring)
    if plan is None:
        return {'reason':'no_safe_yellow_ring_plan','state':state,'nearby_objects':objects}
    print(json.dumps({'ring_plan':plan,'percent':state['percent']}),flush=True)
    if plan['first_tick']:
        current = c.execute(plan['first_tick'],False,'Lining up the jump ring')
        if current['p1']['dead']:
            return {'reason':'died','state':current}
    current = c.execute(1,True,'Jumping toward the yellow ring')
    if current['p1']['dead']:
        return {'reason':'died','state':current}
    current = c.execute(plan['ring_tick']-plan['first_tick']-1,False,
                        'Releasing before the second ring input')
    if current['p1']['dead']:
        return {'reason':'died','state':current}
    observed = b.request({'op':'observe'})
    if not observed.get('ok'):
        raise RuntimeError(str(observed))
    current = observed['state']; p=current['p1']
    matches=[o for o in observed['nearby_objects'] if o['type']==11 and o['id']==36
             and abs(o['x']-plan['ring_x']) < .01 and abs(o['y']-plan['ring_y']) < .01]
    if len(matches)!=1 or p['dead'] or p['on_ground'] or p['ship'] or p['upside_down']:
        return {'reason':'ring_alignment_requires_plan','state':current,'nearby_objects':observed['nearby_objects']}
    rx,ry,rw,rh=matches[0]['rect']
    if not (p['x']+14>rx and p['x']-14<rx+rw and p['y']+14>ry and p['y']-14<ry+rh):
        return {'reason':'ring_alignment_requires_plan','state':current,'nearby_objects':observed['nearby_objects']}
    current=c.execute(1,True,'Tapping the yellow ring for the second jump')
    if current['p1']['dead']:
        return {'reason':'died','state':current}
    if abs(current['p1']['vy']-10.964)>.02:
        return {'reason':'unexpected_ring_response','state':current,'plan':plan}
    with (r.ROOT/'runtime/ring-inputs.jsonl').open('a',encoding='utf-8') as out:
        out.write(json.dumps({'time':time.time(),'plan':plan,'state':current})+'\n')
    # Release the button before returning to the ordinary landing controller.
    current=c.execute(1,False,'Ring launch confirmed; preparing for the landing')
    return {'reason':'ring_launched','state':current}
