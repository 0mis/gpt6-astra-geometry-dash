"""Recorded, bounded miniature-cube control using measured local prediction."""
import json
import time

import bridge_client as b
import cube_assist as c
import mixed_ring_chain as mixed
import recording as r


def supported(player):
    return abs(player['size']-.6) < .001 and abs(player['speed']-.9) < .001 \
        and not any(player[k] for k in ('ship', 'ball', 'ufo', 'wave', 'robot', 'spider', 'swing'))


def run(actions=20, coin_distance=0):
    return run_mode(actions, supported, 'mini', 16)


def run_mode(actions, supported, mode_name, max_advance):
    for index in range(actions):
        c.check_stop()
        observation = b.request({'op': 'observe'})
        state, objects = observation['state'], observation['nearby_objects']
        player = state['p1']
        if player['dead'] or state['completed']:
            return {'reason': 'died' if player['dead'] else 'completed', 'state': state}
        if not supported(player):
            return {'reason': 'mode_requires_new_plan', 'state': state}
        result = mixed.execute_plan(state, objects, max_advance=max_advance)
        after = result['state']
        if result['reason'] == 'coast_advanced' and supported(after['p1']):
            prediction = mixed.simulate(player, mixed.World(objects),
                                        horizon=after['ticks']-state['ticks'],
                                        excluded=mixed.used(state))
            errors = None if prediction is None else {
                k: abs(prediction[k]-after['p1'][k]) for k in ('x', 'y', 'vy')}
            check = {'time': time.time(), 'before': state, 'after': after,
                     'prediction': prediction, 'errors': errors}
            with (r.ROOT/f'runtime/{mode_name}-live-validation.jsonl').open('a', encoding='utf-8') as out:
                out.write(json.dumps(check)+'\n')
            if prediction is None or errors['x'] > .15 or errors['y'] > .6 or errors['vy'] > .03 \
                    or prediction['upside_down'] != after['p1']['upside_down'] \
                    or prediction['on_ground'] != mixed.physical_ground(after['p1']):
                result = {'reason': mode_name+'_response_requires_calibration', 'state': after,
                          'prediction': prediction, 'errors': errors}
        r.atomic_json(r.ROOT/f'runtime/{mode_name}-last-result.json', result)
        print(json.dumps({mode_name+'_action': index+1, 'reason': result['reason'],
                          'percent': after['percent'], 'tick': after['ticks']}), flush=True)
        if after['p1']['dead']:
            return {'reason': 'died', 'state': after}
        if result['reason'] not in ('coast_advanced', 'jump_launched', 'ring_launched'):
            return result
    return {'reason': 'batch_limit', 'state': b.request({'op': 'status'})['state']}
