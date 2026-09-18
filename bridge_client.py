"""Idempotent local requests; state reads are separate from recorded game actions."""
import argparse
import ctypes
import json
from pathlib import Path
import time
import uuid

import menu_control
import recording as r

ROOT = Path(r.installed_game()['executable']).parent / 'geode' / 'mods' / 'aiwrld.astra_recorded_bridge' / 'recorded-bridge'
SAVE_ROOT = Path(r.os.environ['LOCALAPPDATA']) / 'GeometryDash' / 'geode' / 'mods' / 'aiwrld.astra_recorded_bridge' / 'recorded-bridge'
runtimes = [ROOT, SAVE_ROOT]
kernel = ctypes.WinDLL('kernel32')
kernel.GetTickCount64.restype = ctypes.c_ulonglong


def locate():
    matches = [folder for folder in runtimes if (folder / 'ready.json').is_file()]
    current = r.game_target()
    matches = [folder for folder in matches if json.loads((folder / 'ready.json').read_text())['pid'] == current['pid']
               and (folder / 'ready.json').stat().st_mtime >= current['created']]
    if len(matches) != 1:
        # Geode may put per-mod saves under its config directory in newer builds.
        game = Path(current['executable']).parent
        for base in [game / 'geode', Path(r.os.environ['LOCALAPPDATA']) / 'GeometryDash' / 'geode']:
            if base.is_dir():
                for file in base.rglob('recorded-bridge/ready.json'):
                    if json.loads(file.read_text())['pid'] == current['pid'] and file.stat().st_mtime >= current['created'] and file.parent not in matches:
                        matches.append(file.parent)
    if len(matches) != 1:
        raise RuntimeError('Exactly one ready bridge for the live game is required')
    if json.loads((matches[0] / 'ready.json').read_text()).get('protocol') != 2:
        raise RuntimeError('Restart the game with bridge protocol 2 before making requests')
    return matches[0], current


def request(command):
    folder, target = locate()
    command.setdefault('request_id', uuid.uuid4().hex)
    command.setdefault('game_pid', target['pid'])
    command.setdefault('game_epoch_ms', json.loads((folder / 'ready.json').read_text())['process_epoch_ms'])
    if command['op'] not in {'status', 'observe'}:
        observed = menu_control.client({'action': 'health'})
        health = observed.get('result', {}).get('health', {})
        if not observed.get('ok') or not health.get('healthy') or health.get('pid') != target['pid']:
            raise RuntimeError('Fresh live recording not verified; no game action requested')
        command['recording_healthy'] = True
        command['recording_lease_until_ms'] = kernel.GetTickCount64() + 29000
    record = r.ROOT / 'runtime' / ('native-request-' + command['request_id'] + '.json')
    if record.exists() and json.loads(record.read_text()) != command:
        raise RuntimeError('Request ID already belongs to different exact arguments')
    r.atomic_json(record, command)
    r.atomic_json(folder / 'requests' / (command['request_id'] + '.json'), command)
    started = time.monotonic()
    while time.monotonic() - started < 35:
        r.verify_target(target)
        response = folder / 'responses' / (command['request_id'] + '.json')
        if response.exists():
            try:
                data = json.loads(response.read_text())
            except (OSError, json.JSONDecodeError):
                time.sleep(0.01)
                continue
            if data.get('request_id') == command['request_id']:
                data['round_trip_seconds'] = time.monotonic() - started
                r.atomic_json(record.with_name(record.stem + '-response.json'), data)
                return data
        time.sleep(0.01)
    raise RuntimeError(f'Uncertain request {command["request_id"]}; inspect response before any retry')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('op', choices=['status', 'observe', 'windowed', 'activate', 'step', 'restart', 'pause'])
    parser.add_argument('--button-id', type=int)
    parser.add_argument('--ticks', type=int, default=1)
    parser.add_argument('--hold', action='store_true')
    parser.add_argument('--hold2', action='store_true')
    args = parser.parse_args()
    command = {'op': args.op}
    if args.op == 'activate': command['button_id'] = args.button_id
    if args.op == 'step': command.update(ticks=args.ticks, hold=args.hold, hold2=args.hold2)
    print(json.dumps(request(command), indent=2))
