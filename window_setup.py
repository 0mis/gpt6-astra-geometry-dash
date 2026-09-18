"""One recorded display-configuration action, never level selection or gameplay."""
import ctypes
import json
import time

import recording as r


def main():
    target = r.game_target()
    u = r.user32
    u.ShowWindow(target['hwnd'], 9)
    u.SetForegroundWindow(target['hwnd'])
    time.sleep(0.5)
    r.verify_target(target)
    if int(u.GetForegroundWindow() or 0) != target['hwnd']:
        raise RuntimeError('Verified game did not become foreground; no event sent')
    recorder = r.SilentRecorder(target)
    recorder.caption = 'GPT-6 ASTRA | DISPLAY SETUP ONLY | NO GAMEPLAY ATTEMPT'
    u.keybd_event.argtypes = [r.wintypes.BYTE, r.wintypes.BYTE, r.wintypes.DWORD, ctypes.c_size_t]
    event_sent = False
    try:
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            state = recorder.health()
            if state['error']:
                raise RuntimeError(state['error'])
            # This exception is only for one display-mode configuration shortcut.
            # Gameplay remains forbidden until recording.health()['healthy'] is true.
            if state['capture_frames'] >= 1 and state['capture_age'] < 2 and state['encoded_frames'] >= 2:
                break
            time.sleep(0.02)
        else:
            raise RuntimeError('No fresh game frame and encoded video; no event sent')
        r.verify_target(target)
        if int(u.GetForegroundWindow() or 0) != target['hwnd']:
            raise RuntimeError('Focus changed before display setup; no event sent')
        r.atomic_json(recorder.folder / 'display-action.json', {'target': target,
            'action': 'Alt+Enter once to request windowed display', 'purpose': 'capture configuration',
            'gameplay_inputs': 0, 'recording_before': state, 'time': time.time()})
        u.keybd_event(0x12, 0x38, 0, 0)
        u.keybd_event(0x0D, 0x1C, 0, 0)
        event_sent = True
        time.sleep(0.07)
        u.keybd_event(0x0D, 0x1C, 2, 0)
        u.keybd_event(0x12, 0x38, 2, 0)
        for _ in range(40):
            state = recorder.health()
            r.atomic_json(recorder.folder / 'health.json', state)
            with recorder.lock:
                frame = None if recorder.latest is None else recorder.latest.copy()
            if frame is not None:
                r.cv2.imwrite(str(recorder.folder / 'snapshot.png'), frame)
            if state['error']:
                break
            time.sleep(0.2)
    finally:
        if event_sent:
            u.keybd_event(0x0D, 0x1C, 2, 0)
            u.keybd_event(0x12, 0x38, 2, 0)
        final = recorder.stop()
    print(json.dumps({'folder': str(recorder.folder), 'event_sent': event_sent,
                      'last_health': state, 'final': final}, indent=2))


if __name__ == '__main__':
    main()
