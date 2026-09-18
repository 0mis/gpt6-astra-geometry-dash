"""Local, silent-recording-gated setup controller for the verified Steam game."""
import argparse
import ctypes
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import secrets
import threading
import time
import urllib.request
import uuid

import recording as r
from native_recording import NativeSilentRecorder

RUNTIME = r.ROOT / 'runtime'
CONNECTION = RUNTIME / 'menu-connection.json'
u = r.user32
u.GetWindowRect.argtypes = [r.wintypes.HWND, ctypes.POINTER(r.wintypes.RECT)]
u.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
u.mouse_event.argtypes = [r.wintypes.DWORD, r.wintypes.DWORD, r.wintypes.DWORD, r.wintypes.DWORD, ctypes.c_size_t]
u.keybd_event.argtypes = [r.wintypes.BYTE, r.wintypes.BYTE, r.wintypes.DWORD, ctypes.c_size_t]


def serve():
    target = r.game_target()
    u.ShowWindow(target['hwnd'], 9)
    u.SetForegroundWindow(target['hwnd'])
    time.sleep(0.5)
    recorder = NativeSilentRecorder(target)
    token = secrets.token_hex(32)
    lock = threading.Lock()
    cache = {}
    observations = {}
    journal = recorder.folder / 'menu-actions.jsonl'

    def observe():
        with recorder.lock:
            frame = None if recorder.latest is None else recorder.latest.copy()
        if frame is None:
            return {'health': recorder.health(), 'ready': False}
        oid = uuid.uuid4().hex
        path = recorder.folder / f'observe-{oid}.png'
        r.cv2.imwrite(str(path), frame)
        rect = r.wintypes.RECT()
        if not u.GetWindowRect(target['hwnd'], ctypes.byref(rect)):
            raise RuntimeError('Cannot inspect game window bounds')
        result = {'ready': True, 'observation_id': oid, 'time': time.time(), 'image': str(path),
            'image_shape': list(frame.shape), 'rect': [rect.left, rect.top, rect.right, rect.bottom],
            'health': recorder.health()}
        observations.clear()
        observations[oid] = result
        r.atomic_json(RUNTIME / 'latest-menu-observation.json', result)
        return result

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            if self.headers.get('Authorization') != 'Bearer ' + token or self.headers.get('Origin'):
                self.send_error(403)
                return
            size = int(self.headers.get('Content-Length', 0))
            if size <= 0 or size > 8192:
                self.send_error(400)
                return
            request = json.loads(self.rfile.read(size))
            rid = request.get('request_id')
            signature = json.dumps(request, sort_keys=True)
            with lock:
                try:
                    if rid in cache:
                        saved_signature, result = cache[rid]
                        if saved_signature != signature:
                            raise RuntimeError('request_id reused with different arguments')
                    else:
                        if not isinstance(rid, str) or len(rid) < 10:
                            raise RuntimeError('A unique request_id is required')
                        action = request['action']
                        if action == 'observe':
                            result = observe()
                        elif action == 'health':
                            result = {'health': recorder.health(), 'recording_folder': str(recorder.folder)}
                        elif action == 'caption':
                            recorder.caption = str(request['text'])[:105]
                            result = {'caption': recorder.caption, 'health': recorder.health()}
                        elif action == 'stop':
                            result = recorder.stop()
                            threading.Thread(target=self.server.shutdown, daemon=True).start()
                        elif action in {'click', 'key'}:
                            old = observations.get(request.get('observation_id'))
                            if old is None or time.time() - old['time'] > 180:
                                raise RuntimeError('Fresh inspected observation required')
                            r.verify_target(target)
                            u.SetForegroundWindow(target['hwnd'])
                            time.sleep(0.15)
                            if int(u.GetForegroundWindow() or 0) != target['hwnd']:
                                raise RuntimeError('Game did not become foreground')
                            state = recorder.health()
                            if not state['healthy']:
                                raise RuntimeError('Recording is not healthy: ' + str(state))
                            rect = r.wintypes.RECT()
                            u.GetWindowRect(target['hwnd'], ctypes.byref(rect))
                            current = [rect.left, rect.top, rect.right, rect.bottom]
                            if old['rect'] != current:
                                raise RuntimeError('Game bounds changed; observe again')
                            # Save the started action before input; a refresh failure must not repeat it.
                            result = {'input_sent': False, 'action': action, 'request_id': rid}
                            cache[rid] = (signature, result)
                            if action == 'click':
                                x, y = int(request['x']), int(request['y'])
                                h, w = old['image_shape'][:2]
                                if (w, h) != (rect.right-rect.left, rect.bottom-rect.top):
                                    raise RuntimeError('Capture/window dimensions differ')
                                if not (0 <= x < w and 0 <= y < h):
                                    raise RuntimeError('Click outside observed game image')
                                u.SetCursorPos(rect.left+x, rect.top+y)
                                u.mouse_event(2, 0, 0, 0, 0)
                                try:
                                    time.sleep(0.055)
                                finally:
                                    u.mouse_event(4, 0, 0, 0, 0)
                            else:
                                keys = {'escape': (0x1B, 1), 'left': (0x25, 0x4B), 'right': (0x27, 0x4D)}
                                key, scan = keys[request['key']]
                                u.keybd_event(key, scan, 0, 0)
                                try:
                                    time.sleep(0.05)
                                finally:
                                    u.keybd_event(key, scan, 2, 0)
                            result['input_sent'] = True
                            with journal.open('a', encoding='utf-8') as out:
                                out.write(json.dumps({'time': time.time(), 'request': request,
                                    'recording_before': state, 'input_sent': True}) + '\n')
                            observations.clear()
                            time.sleep(0.4)
                            result['after'] = observe()
                        else:
                            raise RuntimeError('Unsupported action')
                        cache[rid] = (signature, result)
                    response = {'ok': True, 'result': result}
                except Exception as exc:
                    response = {'ok': False, 'error': str(exc), 'cached_action': cache.get(rid, (None, None))[1]}
                raw = json.dumps(response).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

    server = ThreadingHTTPServer(('127.0.0.1', 8768), Handler)
    r.atomic_json(CONNECTION, {'port': 8768, 'token': token, 'pid': r.os.getpid(),
        'created': r.psutil.Process().create_time(), 'target': target, 'recording': str(recorder.folder)})
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        recorder.stop()
        server.server_close()


def client(payload):
    connection = json.loads(CONNECTION.read_text())
    proc = r.psutil.Process(connection['pid'])
    if proc.create_time() != connection['created']:
        raise RuntimeError('Controller identity changed')
    payload.setdefault('request_id', uuid.uuid4().hex)
    request = urllib.request.Request(f"http://127.0.0.1:{connection['port']}/", data=json.dumps(payload).encode(),
        headers={'Authorization': 'Bearer ' + connection['token'], 'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['serve', 'observe', 'stop', 'request'])
    parser.add_argument('json_file', nargs='?')
    args = parser.parse_args()
    if args.command == 'serve':
        serve()
    else:
        payload = json.loads(Path(args.json_file).read_text()) if args.command == 'request' else {'action': args.command}
        print(json.dumps(client(payload), indent=2))
