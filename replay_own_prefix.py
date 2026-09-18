"""Replay only inputs produced and recorded by this task, checking each endpoint.

This never imports a third-party macro and never changes player state. Recorded
ordinary inputs are replayed through the same live-recording guard.
"""
import argparse
import json
from pathlib import Path
import time
import bridge_client as b
import cube_assist as c
import recording as r


def replay(generation, until_tick, source_pid=None):
    rows=[json.loads(line) for line in (r.ROOT/'runtime/cube-inputs.jsonl').read_text().splitlines()]
    live_pid=b.locate()[1]['pid']
    source_pid = live_pid if source_pid is None else source_pid
    rows=[row for row in rows if row['state']['generation']==generation and row['state']['pid']==source_pid
          and row['state']['ticks'] <= until_tick and not row['state']['p1']['dead']]
    if not rows:
        raise RuntimeError('No matching original recorded prefix')
    expected_tick=0
    segments=[]
    for row in rows:
        end=row['state']['ticks']
        duration=row['ticks_requested']
        if end-duration != expected_tick:
            raise RuntimeError('Original input journal is not a contiguous prefix')
        if (segments and segments[-1]['hold']==row['hold'] and segments[-1]['ticks']+duration<=480
            and segments[-1]['expected']['started']==row['state']['started']):
            segments[-1].update(ticks=segments[-1]['ticks']+duration, expected=row['state'])
        else:
            segments.append({'hold':row['hold'],'ticks':duration,'expected':row['state']})
        expected_tick=end
    if expected_tick != until_tick:
        raise RuntimeError('Choose a recorded request endpoint for replay')
    state=b.request({'op':'status'})['state']
    original = rows[0]['state']
    if state['ticks'] != 0 or state['p1']['x']!=0 or state['practice'] or state['level_name']!=original['level_name']:
        raise RuntimeError('Replay requires the same level freshly restarted in normal mode')
    if state['level_id'] != original['level_id'] or state['gd_version'] != original['gd_version']:
        raise RuntimeError('Replay requires the same level ID and original game version')
    if not original['started'] and state['started']:
        raise RuntimeError('Source includes the entry intro; re-enter through the level menu before replay. No inputs sent.')
    if any(row['state']['practice'] or row['state']['ignore_damage'] or row['state']['test_mode'] for row in rows):
        raise RuntimeError('Only original normal-mode recorded inputs may be replayed')
    deadline = time.monotonic() + 3
    while not state['recorded_current_frame']:
        if time.monotonic() >= deadline:
            raise RuntimeError('Recording acknowledgment did not become ready; no replay inputs sent')
        time.sleep(.05)
        state=b.request({'op':'status'})['state']
    for segment in segments:
        state=c.execute(segment['ticks'],segment['hold'],'Replaying inputs learned during this recorded attempt')
        expected=segment['expected']
        p,old=state['p1'],expected['p1']
        if p['dead'] or abs(p['x']-old['x'])>.2 or abs(p['y']-old['y'])>.2 or abs(p['vy']-old['vy'])>.02:
            raise RuntimeError('Replay diverged from its recorded endpoint; frozen for inspection')
    return {'source_pid':source_pid,'verified_prefix_ticks':expected_tick,'segments':len(segments),'state':state}


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--generation',type=int,required=True)
    parser.add_argument('--until-tick',type=int,required=True)
    parser.add_argument('--source-pid',type=int,help='Explicit earlier game process whose own recorded inputs are used')
    args=parser.parse_args()
    print(json.dumps(replay(args.generation,args.until_tick,args.source_pid),indent=2))
