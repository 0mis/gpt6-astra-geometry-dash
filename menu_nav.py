"""Navigate only the game's existing built-in level selection buttons."""
import argparse
import json
import time
import bridge_client as b


def current_name(status):
    candidates = [v['text'] for v in status.get('labels', [])
                  if 100 < v['x'] < 450 and 200 < v['y'] < 235]
    return candidates[0] if len(candidates) == 1 else None


def navigate(name, enter=False):
    for _ in range(30):
        state = b.request({'op': 'status'})
        if state['state']['in_level']:
            raise RuntimeError('Exit the active level through its normal pause menu first')
        current = current_name(state)
        if current == name:
            if enter:
                button = [v for v in state['buttons'] if abs(v['x'] - 284.5) < 1 and v['y'] == 220 and v['width'] == 340]
                if len(button) != 1:
                    raise RuntimeError('Expected existing level selection button')
                result = b.request({'op': 'activate', 'button_id': button[0]['id']})
                if not result.get('ok'):
                    raise RuntimeError(str(result))
                # Activation can answer before the scheduler registers the new
                # PlayLayer. Observe the initialized, recorded scene before
                # callers assign an attempt number or replay any inputs.
                deadline, stable = time.monotonic()+5, None
                while time.monotonic() < deadline:
                    entered = b.request({'op': 'status'})['state']
                    identity = (entered['pid'], entered['generation'])
                    if entered.get('in_level') and entered.get('level_name') == name \
                            and entered.get('recorded_current_frame') and entered['ticks'] == 0:
                        if stable == identity:
                            return {'selected': current, 'enter_requested': True, 'state': entered}
                        stable = identity
                    else:
                        stable = None
                    time.sleep(.05)
                raise RuntimeError('Activated once; initialized recorded level is not ready. Observe before retrying.')
            return {'selected': current, 'enter_requested': enter}
        buttons = [v for v in state['buttons'] if v['x'] == 544 and v['y'] == 160 and v['width'] == 30]
        if len(buttons) != 1:
            raise RuntimeError('Expected existing right-arrow button; inspect the menu')
        result = b.request({'op': 'activate', 'button_id': buttons[0]['id']})
        if not result.get('ok'):
            raise RuntimeError(str(result))
        time.sleep(0.85)
    raise RuntimeError('Requested built-in level was not found in one menu cycle')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('name')
    parser.add_argument('--enter', action='store_true')
    args = parser.parse_args()
    print(json.dumps(navigate(args.name, args.enter), indent=2))
