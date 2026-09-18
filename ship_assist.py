"""Original short-horizon ship steering; predictions never alter the game."""
import argparse
import json
import time
import bridge_client as b
import cube_assist as c
import recording as r
import collision_shapes as shapes


def velocity_step(v, hold, upside_down=False, size=1):
    if abs(size-.6)<.001:
        if upside_down:
            delta = (-.127 if v>1.9 else -.101) if hold else (.122 if v<1.9 else .081)
        else:
            delta = (.127 if v<1.9 else .101) if hold else (-.122 if v>1.9 else -.081)
        # Native mini-ship motion exceeded the full-size 8.0 cap. Its actual
        # terminal speed remains unmeasured; keep searches inside the observed
        # velocity range instead of inventing a terminal-speed constant.
        return v+delta
    elif upside_down:
        # Original Clubstep measurements show speed-dependent inverted lift.
        # These are local estimates, checked against the next recorded state.
        delta = (-.108 if v > 1.92 else -.086) if hold else (.103 if v < 1.92 else .069)
    else:
        # The 1.92 threshold also fits the earlier normal-gravity recordings.
        delta = (.108 if v < 1.92 else .086) if hold else (-.103 if v > 1.92 else -.069)
    return max(-8.0, min(8.0, v+delta))


def choose(player, objects, target_y=255, bounds=(108, 432), beam_width=128):
    extent=10 if abs(player['size']-.6)<.001 else 17
    obstacles=[shapes.shape(o if o['type'] != 0 else dict(o, radius=0))
               for o in objects if o['type'] in (0,2,47) and not o['disabled']]
    x0=player['x']
    # Horizontal speed is fixed for this supported mode. Cache exactly the
    # obstacles intersecting each predicted column once for the entire search.
    columns=[]
    for tick in range(1,161):
        x=x0+tick*1.29825
        columns.append([interval for obstacle in obstacles
                        if (interval := shapes.vertical_interval(x, extent, obstacle)) is not None])
    # Cost, altitude, vertical velocity, first ordinary button choice.
    beam=[(0.0,player['y'],player['vy'],None)]
    for depth in range(20):
        expanded=[]
        for score, initial_y, initial_v, first in beam:
            for hold in (False,True):
                y,v=initial_y,initial_v
                valid=True
                local_cost=0.0
                for k in range(8):
                    v=velocity_step(v, hold, player.get('upside_down', False),player['size'])
                    if abs(player['size']-.6)<.001 and abs(v)>8.262001:
                        valid=False
                        break
                    y+=v*.225
                    if not bounds[0]<y<bounds[1]:
                        valid=False
                        break
                    for low,high in columns[depth*8+k]:
                        if low<y<high:
                            valid=False
                            break
                    if not valid:break
                    local_cost+=(y-target_y)**2*.001 + v*v*.08
                if valid:
                    expanded.append((score+local_cost,y,v,hold if first is None else first))
        if not expanded:
            return None
        expanded.sort(key=lambda q:q[0])
        # Retain diverse trajectories as well as the lowest-cost height.
        bins=set();beam=[]
        for entry in expanded:
            key=(round(entry[1]/3),round(entry[2]*2),entry[3])
            if key not in bins:
                bins.add(key);beam.append(entry)
            if len(beam)>=beam_width:break
    return min(beam,key=lambda q:q[0])[3]


def run(batches, coin_distance=500, target_y=255, stop_x=None, bounds=(108, 432)):
    for index in range(batches):
        observed=b.request({'op':'observe'})
        if not observed.get('ok'):raise RuntimeError(str(observed))
        s=observed['state'];p=s['p1']
        if p['dead'] or s['completed']:
            return {'reason':'died' if p['dead'] else 'completed','state':s}
        if stop_x is not None and p['x'] >= stop_x:
            return {'reason':'planned_route_stop','state':s,'nearby_objects':observed['nearby_objects']}
        if s.get('end_animation_started'):
            c.execute(240,False,'Letting the original completion screen finish')
            continue
        mini=abs(p['size']-.6)<.001
        if not p['ship'] or min(abs(p['size']-1),abs(p['size']-.6))>.001 \
                or (mini and s['level_id']!=14) or abs(p['speed']-.9)>.001:
            return {'reason':'mode_requires_new_plan','state':s}
        if s['ignore_damage'] or s['test_mode']:
            raise RuntimeError('Original collisions and damage are required')
        if not s['recorded_current_frame']:
            time.sleep(.05)
            continue
        objects=observed['nearby_objects']
        coins=[o for o in objects if o['type']==22 and not o['disabled'] and p['x']-20<o['x']<p['x']+coin_distance]
        if coin_distance > 0 and coins:
            return {'reason':'coin_route_requires_plan','state':s,'coins':coins,'nearby_objects':objects}
        hold=choose(p,objects,target_y=target_y,bounds=bounds)
        if hold is None:
            # Widen the same conservative search before declaring the route
            # unavailable. No live input is sent by either search.
            for width in (512,2048):
                hold=choose(p,objects,target_y=target_y,bounds=bounds,beam_width=width)
                if hold is not None:
                    break
        if hold is None:
            return {'reason':'no_safe_ship_plan','state':s,'nearby_objects':objects}
        purpose='Steering the inverted ship through the corridor' if p['upside_down'] else 'Steering the ship through the corridor'
        expected_y, expected_v = p['y'], p['vy']
        for _ in range(8):
            expected_v = velocity_step(expected_v, hold, p['upside_down'],p['size'])
            expected_y += expected_v*.225
        after=c.execute(8,hold,purpose,verbose=False)
        if (mini or p['upside_down']) and after['p1']['ship'] and after['p1']['upside_down'] == p['upside_down'] \
                and abs(after['p1']['size']-p['size'])<.001 \
                and not after['p1']['dead'] and (abs(after['p1']['vy']-expected_v)>.08 \
                                               or abs(after['p1']['y']-expected_y)>.2):
            return {'reason':'ship_response_requires_plan','state':after,
                    'expected':{'y':expected_y,'vy':expected_v}}
        if index%20==0:
            print(json.dumps({'ship_percent':round(after['percent'],3),'y':round(after['p1']['y'],2),'vy':after['p1']['vy']}),flush=True)
    return {'reason':'batch_limit','state':b.request({'op':'status'})['state']}


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--batches',type=int,default=100)
    args=parser.parse_args()
    if not 1<=args.batches<=500:raise ValueError('Use a bounded 1..500 batches')
    result=run(args.batches)
    r.atomic_json(r.ROOT/'runtime/ship-last-result.json',result)
    print(json.dumps({'reason':result['reason'],'state':result['state']},indent=2))
