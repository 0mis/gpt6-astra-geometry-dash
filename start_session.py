"""Launch one installed game and one silent recorder; send no gameplay inputs."""
import json
from pathlib import Path
import subprocess
import sys
import time

import recording as r
import bridge_client as b
import menu_control as m


def start():
    if m.CONNECTION.exists():
        old = json.loads(m.CONNECTION.read_text())
        try:
            process = r.psutil.Process(old['pid'])
            if process.create_time() == old['created']:
                return m.client({'action': 'health'})
        except r.psutil.NoSuchProcess:
            pass
    existing = [p for p in r.psutil.process_iter(['name']) if (p.info['name'] or '').lower() == 'geometrydash.exe']
    if not existing:
        subprocess.Popen([r'C:\Program Files (x86)\Steam\steam.exe', '-applaunch', '322170'],
                         creationflags=subprocess.CREATE_NO_WINDOW)
    last_error = None
    for _ in range(200):
        try:
            _, target = b.locate()
            break
        except Exception as exc:
            last_error = str(exc)
            time.sleep(.1)
    else:
        raise RuntimeError('Game bridge did not become ready: ' + str(last_error))
    with (r.ROOT / 'runtime/menu-controller.stdout.log').open('wb') as output, (r.ROOT / 'runtime/menu-controller.stderr.log').open('wb') as error:
        process = subprocess.Popen([sys.executable, '-X', 'utf8', '-u', str(r.ROOT/'menu_control.py'), 'serve'],
            creationflags=subprocess.CREATE_NO_WINDOW, stdout=output, stderr=error)
    last_error = None
    for _ in range(100):
        if process.poll() is not None:
            raise RuntimeError('Recorder process exited; inspect its existing stderr log')
        try:
            health = m.client({'action': 'health'})
            if health.get('ok') and health['result']['health'].get('healthy'):
                return health
            last_error = health
        except Exception as exc:
            last_error = str(exc)
        time.sleep(.1)
    raise RuntimeError('Recorder did not become healthy: ' + str(last_error))


if __name__ == '__main__':
    print(json.dumps(start(), indent=2))
