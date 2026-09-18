"""Plan an airborne chain of original yellow-ring taps, then execute one tap."""
import json
import time

import bridge_client as b
import cube_assist as c
import recording as r


def key(rect):
    return tuple(round(v,3) for v in rect)


def used(state):
    result=set()
    path=r.ROOT/'runtime/ring-inputs.jsonl'
    for line in path.read_text(encoding='utf-8').splitlines() if path.exists() else ():
        row=json.loads(line); prior=row['state']
        if prior['pid']!=state['pid'] or prior['generation']!=state['generation']:
            continue
        if prior['ticks'] > state['ticks']:
            continue
        plan=row['plan']; rect=plan.get('ring_rect')
        if rect is None:
            rect=[plan['ring_x']-18,plan['ring_y']-18,36,36]
        result.add(key(rect))
    path=r.ROOT/'runtime/mechanism-inputs.jsonl'
    for line in path.read_text(encoding='utf-8').splitlines() if path.exists() else ():
        row=json.loads(line); prior=row['after']
        if prior['pid']==state['pid'] and prior['generation']==state['generation'] \
                and prior['ticks']<=state['ticks'] and row['object']['type'] in (11,12,13):
            result.add(key(row['object']['rect']))
    return result


def choose(player, objects, excluded=(), beam_width=64):
    rings=sorted((o for o in objects if o['type']==11 and o['id']==36 and not o['disabled']
                  and key(o['rect']) not in excluded and player['x']-32<o['x']<player['x']+650),
                 key=lambda o:o['x'])
    started=time.perf_counter(); evaluations=0
    beam=[((),100.0,player)]
    if player['on_ground']:
        # Search ordinary ground jumps as well as walking off the ledge.
        # Only predictions change here; the live game remains frozen.
        for jump in range(0,201,2):
            predicted={}
            ok,margin=c.simulate(player,objects,(jump,),horizon=jump+1,
                                 check_escape=False,state_out=predicted)
            evaluations+=1
            if ok:
                beam.append(((jump,),margin,predicted))
    for ring in rings[:4]:
        c.check_stop()
        center=round((ring['x']-player['x'])/1.29825)
        expanded=[]; solutions=[]
        for prefix,_,_ in beam:
            for pulse in range(max(0,center-24),center+25,2):
                if prefix and pulse<=prefix[-1]+1:
                    continue
                pulses=prefix+(pulse,); predicted={}
                ok,margin=c.simulate(player,objects,pulses,horizon=pulse+1,
                                     check_escape=False,state_out=predicted)
                evaluations+=1
                if not ok or key(ring['rect']) not in {key(v) for v in predicted['triggered_rings']}:
                    continue
                final={}
                finishes,end_margin=c.simulate(player,objects,pulses,horizon=600,
                    end_at_landing=True,check_escape=False,state_out=final)
                evaluations+=1
                if finishes:
                    solutions.append((end_margin,pulses,final))
                expanded.append((pulses,margin,predicted))
        if solutions:
            margin,pulses,final=max(solutions,key=lambda v:v[0])
            ground_jump=player['on_ground'] and len(pulses)>len(final['triggered_rings'])
            return {'pulses':list(pulses),'ground_jump':ground_jump,
                    'first_ring':rings[0],'predicted_margin':margin,
                    'predicted_landing':final,'evaluations':evaluations,
                    'planning_seconds':time.perf_counter()-started}
        if not expanded:
            return None
        expanded.sort(key=lambda v:(abs(v[2]['y']-ring['y']),-v[1]))
        bins=set();beam=[]
        for item in expanded:
            cell=(round(item[2]['y']/2),item[0][-1]//2)
            if cell in bins:
                continue
            bins.add(cell);beam.append(item)
            if len(beam)>=beam_width:
                break
    return None


def execute_plan(state, objects):
    plan=choose(state['p1'],objects,used(state))
    if plan is None:
        return {'reason':'no_safe_airborne_ring_chain','state':state,'nearby_objects':objects}
    print(json.dumps({'airborne_ring_plan':plan,'percent':state['percent']}),flush=True)
    current=state
    if plan['ground_jump']:
        if plan['pulses'][0]:
            current=c.execute(plan['pulses'][0],False,'Lining up the jump into the ring chain')
        if current['p1']['dead'] or not current['p1']['on_ground']:
            return {'reason':'ground_jump_alignment_requires_plan','state':current}
        current=c.execute(1,True,'Jumping from the ledge toward the ring chain')
        if current['p1']['dead']:
            return {'reason':'died','state':current}
        current=c.execute(1,False,'Releasing before the next ring')
        return {'reason':'jump_launched','state':current}
    if plan['pulses'][0]:
        current=c.execute(plan['pulses'][0],False,'Timing the next tap in the ring chain')
    if current['p1']['dead']:
        return {'reason':'died','state':current}
    observed=b.request({'op':'observe'})
    if not observed.get('ok'):
        raise RuntimeError(str(observed))
    current=observed['state'];p=current['p1'];ring=plan['first_ring']
    rx,ry,rw,rh=ring['rect']
    if (current['pid']!=state['pid'] or current['generation']!=state['generation']
        or p['dead'] or p['on_ground'] or any(p[k] for k in ('ship','ball','ufo','wave','robot','spider','swing','upside_down'))
        or not (p['x']+14>rx and p['x']-14<rx+rw and p['y']+14>ry and p['y']-14<ry+rh)):
        return {'reason':'ring_alignment_requires_plan','state':current,'nearby_objects':observed['nearby_objects']}
    current=c.execute(1,True,'Tapping the next ring to stay above the spike floor')
    if current['p1']['dead']:
        return {'reason':'died','state':current}
    if abs(current['p1']['vy']-10.964)>.02:
        return {'reason':'unexpected_ring_response','state':current,'plan':plan}
    with (r.ROOT/'runtime/ring-inputs.jsonl').open('a',encoding='utf-8') as out:
        out.write(json.dumps({'time':time.time(),'plan':{'ring_x':ring['x'],'ring_y':ring['y'],
                       'ring_rect':ring['rect'],'chain':plan},'state':current})+'\n')
    current=c.execute(1,False,'Releasing before the next ring in the chain')
    return {'reason':'ring_launched','state':current}
