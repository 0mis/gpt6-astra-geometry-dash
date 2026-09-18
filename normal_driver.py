"""Bounded handoff between the original supported-mode controllers."""
import argparse
import json
import bridge_client as b
import cube_assist as cube
import ship_assist as ship
import inverted_cube as inverted
import mixed_ring_chain as mixed
import mini_cube as mini
import ball_assist as ball
import ufo_assist as ufo
import recording as r


def controller_for(player):
    if ufo.supported(player):
        return ufo
    if ball.supported(player):
        return ball
    if mini.supported(player):
        return mini
    if any(player[k] for k in ('ball', 'ufo', 'wave', 'robot', 'spider', 'swing')) \
            or player['size'] != 1 or abs(player['speed']-.9) > .001:
        return None
    if player['upside_down']:
        return None if player['ship'] else inverted
    return ship if player['ship'] else cube


def run(rounds, coin_distance=500):
    for index in range(rounds):
        observed = b.request({'op': 'status'})
        if not observed.get('ok'):
            raise RuntimeError(str(observed))
        state = observed['state']
        player = state.get('p1', {})
        if not state['in_level'] or state.get('dual'):
            return {'reason': 'unsupported_state', 'state': state}
        if player['dead'] or state['completed']:
            return {'reason': 'died' if player['dead'] else 'completed', 'state': state}
        controller = controller_for(player)
        if controller is None:
            return {'reason': 'mode_requires_new_plan', 'state': state}
        result = controller.run(100, coin_distance=coin_distance)
        if controller in (cube, inverted) and result['reason'] in (
                'mechanism_requires_plan', 'no_safe_cube_plan',
                'no_safe_inverted_cube_plan', 'no_safe_airborne_ring_chain'):
            fresh = b.request({'op': 'observe'})
            result = mixed.execute_plan(fresh['state'], fresh['nearby_objects'])
        r.atomic_json(r.ROOT / 'runtime/driver-last-result.json', result)
        print(json.dumps({'round': index + 1, 'mode': controller.__name__,
                          'reason': result['reason'], 'percent': result['state']['percent']}), flush=True)
        if result['reason'] in ('batch_limit', 'ring_launched', 'jump_launched', 'coast_advanced'):
            continue
        if result['reason'] == 'mode_requires_new_plan':
            next_player = result['state']['p1']
            next_controller = controller_for(next_player)
            if next_controller is not None and next_controller is not controller:
                continue
        return result
    return {'reason': 'batch_limit', 'state': b.request({'op': 'status'})['state']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--rounds', type=int, default=5)
    parser.add_argument('--coin-distance', type=int, default=500)
    args = parser.parse_args()
    if not 1 <= args.rounds <= 10:
        raise ValueError('Use 1..10 bounded rounds')
    if not 0 <= args.coin_distance <= 1000:
        raise ValueError('Use a coin lookahead of 0..1000; zero skips optional coins')
    result = run(args.rounds, args.coin_distance)
    r.atomic_json(r.ROOT / 'runtime/driver-last-result.json', result)
    print(json.dumps({'reason': result['reason'], 'state': result['state']}, indent=2), flush=True)
