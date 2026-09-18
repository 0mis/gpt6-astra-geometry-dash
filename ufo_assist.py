"""Recorded miniature-UFO control fitted to original Clubstep inputs."""
import json
import time

import bridge_client as b
import collision_shapes as shapes
import cube_assist as c
import recording as r

DX = 1.29825


def supported(player):
    return player['ufo'] and not player['upside_down'] and \
        abs(player['size']-.6)<.001 and abs(player['speed']-.9)<.001


def velocity_step(v):
    return max(-7.529, v-(.152 if v>1.92 else .101))


def advance(y, v, flap, solids):
    """Measured mini-UFO flight plus original top-of-block landings."""
    old_bottom = y-9
    if flap:
        v=6.8
    v=velocity_step(v)
    y+=v*.225
    if v <= 0:
        tops=[ry+rh for _,ry,_,rh in solids
              if old_bottom >= ry+rh-.05 and y-9 <= ry+rh]
        if tops:
            y=max(tops)+9
            v=0.
    if any(y-9 < ry+rh-.001 and y+9 > ry+.001 for _,ry,_,rh in solids):
        return None
    return y,v


def predict(player, ticks, flap=False, objects=None):
    x, y, v = player['x'], player['y'], player['vy']
    solids=[] if objects is None else [o['rect'] for o in objects
                                      if o['type']==0 and not o['disabled']]
    for tick in range(ticks):
        x += DX
        column=[q for q in solids if x+9>q[0] and x-9<q[0]+q[2]]
        result=advance(y,v,flap and tick==0,column)
        if result is None:
            return None
        y,v=result
    return {'x':x, 'y':y, 'vy':v}


def choose(player, objects, beam_width=192, target_y=300, bounds=(160,440), quantum=8):
    if quantum not in (2,4,8):
        raise ValueError('Use a 2, 4 or 8 tick decision interval')
    obstacles=[shapes.shape(o) for o in objects if o['type'] in (2,47) and not o['disabled']]
    solids=[o['rect'] for o in objects if o['type']==0 and not o['disabled']]
    columns=[]
    for tick in range(1,161):
        x=player['x']+tick*DX
        columns.append(([q for q in solids if x+9>q[0] and x-9<q[0]+q[2]],
                        [v for o in obstacles if (v:=shapes.vertical_interval(x,10,o)) is not None]))
    beam=[(0.,player['y'],player['vy'],None)]
    for depth in range(160//quantum):
        c.check_stop()
        expanded=[]
        for cost,initial_y,initial_v,first in beam:
            for flap in (False,True):
                y,v=initial_y,initial_v
                local=1.5 if flap else 0.
                valid=True
                for tick in range(quantum):
                    blocks,hazards=columns[depth*quantum+tick]
                    result=advance(y,v,flap and tick==0,blocks)
                    if result is None:
                        valid=False
                        break
                    y,v=result
                    if not bounds[0]<y<bounds[1] or any(
                            low<y<high for low,high in hazards):
                        valid=False
                        break
                    local+=(y-target_y)**2*.001+v*v*.05
                if valid:
                    expanded.append((cost+local,y,v,flap if first is None else first))
        if not expanded:
            return None
        expanded.sort(key=lambda q:q[0])
        bins=set();beam=[]
        for row in expanded:
            cell=(round(row[1]/2),round(row[2]*4),row[3])
            if cell not in bins:
                bins.add(cell);beam.append(row)
            if len(beam)>=beam_width:
                break
    return min(beam,key=lambda q:q[0])[3]


def run(batches=60, coin_distance=0, target_y=300, bounds=(160,440)):
    for index in range(batches):
        c.check_stop()
        observed=b.request({'op':'observe'})
        s=observed['state'];p=s['p1']
        if p['dead'] or s['completed']:
            return {'reason':'died' if p['dead'] else 'completed','state':s}
        if not supported(p) or s['level_id']!=14:
            return {'reason':'mode_requires_new_plan','state':s}
        if s['ignore_damage'] or s['test_mode'] or s['practice'] or s['dual']:
            raise RuntimeError('Original single-player normal-mode collisions required')
        quantum=8
        flap=choose(p,observed['nearby_objects'],target_y=target_y,bounds=bounds)
        if flap is None:
            flap=choose(p,observed['nearby_objects'],beam_width=768,target_y=target_y,bounds=bounds)
        if flap is None:
            for quantum in (4,2):
                flap=choose(p,observed['nearby_objects'],beam_width=768,
                            target_y=target_y,bounds=bounds,quantum=quantum)
                if flap is not None:
                    break
        if flap is None:
            return {'reason':'no_safe_ufo_plan','state':s,'nearby_objects':observed['nearby_objects']}
        expected=predict(p,quantum,flap,observed['nearby_objects'])
        if expected is None:
            return {'reason':'ufo_prediction_requires_plan','state':s}
        if flap:
            after=c.execute(1,True,'Tapping the UFO through the next opening',verbose=False)
            if after['p1']['dead'] or not supported(after['p1']):
                return {'reason':'died' if after['p1']['dead'] else 'mode_requires_new_plan','state':after}
            after=c.execute(quantum-1,False,'Releasing the UFO flap',verbose=False)
        else:
            after=c.execute(quantum,False,'Coasting the UFO toward the next opening',verbose=False)
        errors={k:abs(expected[k]-after['p1'][k]) for k in ('x','y','vy')}
        with (r.ROOT/'runtime/ufo-live-validation.jsonl').open('a',encoding='utf-8') as out:
            out.write(json.dumps({'time':time.time(),'before':s,'after':after,'flap':flap,
                                  'prediction':expected,'errors':errors})+'\n')
        if after['p1']['dead'] or not supported(after['p1']):
            return {'reason':'died' if after['p1']['dead'] else 'mode_requires_new_plan','state':after}
        if errors['x']>.15 or errors['y']>.3 or errors['vy']>.03:
            return {'reason':'ufo_response_requires_calibration','state':after,'prediction':expected,'errors':errors}
        if index%10==0:
            print(json.dumps({'ufo_percent':after['percent'],'tick':after['ticks']}),flush=True)
    return {'reason':'batch_limit','state':b.request({'op':'status'})['state']}
