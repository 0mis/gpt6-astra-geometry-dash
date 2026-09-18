"""Silent capture of one verified GeometryDash.exe window; never the desktop.

Preparation only: live capture is deliberately unavailable until the Steam game
is installed and its process and window identity can be verified.
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
import winreg

ROOT = Path(__file__).resolve().parent
(ROOT / 'runtime').mkdir(parents=True, exist_ok=True)
import cv2
import numpy as np
import psutil

import imageio_ffmpeg
FFMPEG = Path(os.environ.get('ASTRA_FFMPEG') or imageio_ffmpeg.get_ffmpeg_exe())
FPS = 60
WIDTH, HEIGHT = 1280, 720
user32 = ctypes.WinDLL('user32', use_last_error=True)
user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsWindow.restype = wintypes.BOOL
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsIconic.restype = wintypes.BOOL
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
ENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [ENUMPROC, wintypes.LPARAM]
user32.EnumWindows.restype = wintypes.BOOL


def atomic_json(path: Path, data: dict):
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(data, indent=2), encoding='utf-8')
    os.replace(temp, path)


def installed_game() -> dict:
    """Find this game's manifest in Steam's configured local libraries."""
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Valve\Steam') as key:
        steam = Path(winreg.QueryValueEx(key, 'SteamPath')[0]).resolve()
    library_file = steam / 'steamapps/libraryfolders.vdf'
    libraries = {steam}
    if library_file.exists():
        text = library_file.read_text(encoding='utf-8')
        libraries.update(Path(s.replace('\\\\', '\\')).resolve()
            for s in re.findall(r'^\s*"path"\s*"([^"\r\n]+)"', text, re.MULTILINE))
    installations = []
    for library in libraries:
        manifest = library / 'steamapps/appmanifest_322170.acf'
        if not manifest.is_file():
            continue
        text = manifest.read_text(encoding='utf-8')
        fields = dict(re.findall(r'"([^"\r\n]+)"\s*"([^"\r\n]*)"', text))
        if fields.get('appid') != '322170' or not fields.get('installdir'):
            continue
        common = (library / 'steamapps/common').resolve()
        executable = (common / fields['installdir'] / 'GeometryDash.exe').resolve()
        if not executable.is_relative_to(common):
            raise RuntimeError('Invalid installation path in Steam manifest')
        installations.append({'executable': str(executable), 'executable_present': executable.is_file(),
            'state_flags': fields.get('StateFlags'), 'build_id': fields.get('buildid'),
            'bytes_to_download': fields.get('BytesToDownload'),
            'bytes_downloaded': fields.get('BytesDownloaded')})
    if len(installations) != 1:
        raise RuntimeError('Exactly one Geometry Dash installation manifest is required')
    return installations[0]


def game_target() -> dict:
    """Only inspect Geometry Dash processes, never unrelated window titles."""
    installed = installed_game()
    if not installed['executable_present']:
        raise RuntimeError('Steam has not finished installing the game executable')
    expected = Path(installed['executable'])
    candidates = []
    for proc in psutil.process_iter(['pid', 'name']):
        if (proc.info['name'] or '').lower() != 'geometrydash.exe':
            continue
        executable = Path(proc.exe()).resolve()
        if executable != expected:
            continue
        candidates.append((proc.pid, proc.create_time(), executable))
    if len(candidates) != 1:
        raise RuntimeError('Exactly one installed Steam Geometry Dash process is required')
    pid, created, executable = candidates[0]
    windows = []

    @ENUMPROC
    def callback(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            title = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, title, length + 1)
            if title.value == 'Geometry Dash':
                windows.append(int(hwnd))
        return True

    user32.EnumWindows(callback, 0)
    if len(windows) != 1:
        raise RuntimeError('Exactly one visible Geometry Dash game window is required')
    return {'pid': pid, 'created': created, 'hwnd': windows[0], 'executable': str(executable)}


def verify_target(target: dict):
    proc = psutil.Process(target['pid'])
    if proc.create_time() != target['created'] or Path(proc.exe()).resolve() != Path(target['executable']):
        raise RuntimeError('Game process identity changed')
    owner = wintypes.DWORD()
    user32.GetWindowThreadProcessId(target['hwnd'], ctypes.byref(owner))
    if not user32.IsWindow(target['hwnd']) or owner.value != target['pid']:
        raise RuntimeError('Game window identity changed')
    if user32.IsIconic(target['hwnd']):
        raise RuntimeError('Game window is minimized')


def encoder_command(output: Path) -> list[str]:
    return [str(FFMPEG), '-hide_banner', '-nostdin', '-n', '-loglevel', 'warning',
            '-f', 'rawvideo', '-pixel_format', 'bgr24', '-video_size', f'{WIDTH}x{HEIGHT}',
            '-framerate', str(FPS), '-i', 'pipe:0', '-map', '0:v:0', '-an',
            '-c:v', 'h264_nvenc', '-preset', 'p4', '-cq', '22', '-pix_fmt', 'yuv420p',
            '-g', str(FPS * 2), '-bf', '0', '-rc-lookahead', '0', '-zerolatency', '1',
            '-flush_packets', '1', '-progress', 'pipe:1', '-stats_period', '0.02', str(output)]


class SilentRecorder:
    def __init__(self, target: dict):
        verify_target(target)
        self.target = target
        run = time.strftime('%Y-%m-%dT%H-%M-%S') + '-' + uuid.uuid4().hex[:6]
        self.folder = ROOT / 'recordings' / run
        self.folder.mkdir(parents=True, exist_ok=False)
        self.latest = None
        self.capture_frames = 0
        self.last_capture = 0.0
        self.encoded_frames = 0
        self.submitted_frames = 0
        self.last_encode = 0.0
        self.capture_timestamp = None
        self.error = None
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.caption = 'GPT-6 ASTRA  |  GEOMETRY DASH  |  SILENT RECORDING'
        self.control = None
        self.capture = None
        self.capture_generation = 0
        self.capture_refreshes = 0
        self.refresh_thread = None
        self.writer_thread = None
        self.final_result = None
        self.log = (self.folder / 'encoder.log').open('wb')
        self.encoder = subprocess.Popen(encoder_command(self.folder / 'raw.mkv'),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log,
            creationflags=subprocess.CREATE_NO_WINDOW, bufsize=0)
        self.progress_thread = threading.Thread(target=self._progress, daemon=True)
        self.progress_thread.start()
        try:
            self._open_capture()
            self.writer_thread = threading.Thread(target=self._write, daemon=True)
            self.writer_thread.start()
            self.refresh_thread = threading.Thread(target=self._refresh_still_capture, daemon=True)
            self.refresh_thread.start()
        except Exception as exc:
            self.error = 'Capture initialization failed: ' + str(exc)
            self.stop()
            raise

    def _open_capture(self):
        from windows_capture import WindowsCapture
        self.capture_generation += 1
        generation = self.capture_generation
        capture = WindowsCapture(window_hwnd=self.target['hwnd'], monitor_index=None,
            cursor_capture=False, draw_border=True, secondary_window=False,
            minimum_update_interval=16, dirty_region=False)

        @capture.event
        def on_frame_arrived(frame, control):
            if self.stop_event.is_set() or generation != self.capture_generation:
                control.stop()
                return
            with self.lock:
                self.latest = frame.frame_buffer[:, :, :3].copy()
                self.capture_frames += 1
                self.capture_timestamp = frame.timespan
                self.last_capture = time.monotonic()

        @capture.event
        def on_closed():
            if not self.stop_event.is_set() and generation == self.capture_generation:
                self.error = 'Game capture closed'
                self.stop_event.set()

        self.capture = capture
        self.control = capture.start_free_threaded()

    def _refresh_still_capture(self):
        """WGC is change-driven. Reopen an idle pool to acquire a real fresh image.

        Never relabel the last buffered image as fresh. A restart must deliver a
        new native callback or health stays unhealthy and inputs stay forbidden.
        """
        try:
            while not self.stop_event.wait(0.2):
                if self.last_capture and time.monotonic() - self.last_capture > 0.8:
                    verify_target(self.target)
                    self.capture_generation += 1
                    self.control.stop()
                    if self.stop_event.is_set():
                        return
                    self._open_capture()
                    self.capture_refreshes += 1
                    self.stop_event.wait(0.3)
        except Exception as exc:
            self.error = 'Fresh window capture failed: ' + str(exc)
            self.stop_event.set()

    def _progress(self):
        for raw in self.encoder.stdout:
            line = raw.decode('utf-8', errors='replace').strip()
            if line.startswith('frame='):
                count = int(line.split('=', 1)[1])
                if count > self.encoded_frames:
                    self.encoded_frames = count
                    self.last_encode = time.monotonic()
                    self._encoded(count)

    def _encoded(self, count):
        pass

    def _submitted(self, count, metadata):
        pass

    def _write(self):
        deadline = time.monotonic()
        last_check = 0.0
        try:
            while not self.stop_event.is_set():
                now = time.monotonic()
                if now - last_check > 0.5:
                    verify_target(self.target)
                    if shutil.disk_usage(ROOT).free < 1_000_000_000:
                        raise RuntimeError('Less than 1 GB recording reserve remains')
                    if self.encoder.poll() is not None:
                        raise RuntimeError('Video encoder exited')
                    last_check = now
                with self.lock:
                    frame = self.latest
                    metadata = self.capture_timestamp
                if frame is None:
                    self.stop_event.wait(0.01)
                    deadline = time.monotonic()
                    continue
                h, w = frame.shape[:2]
                scale = min(WIDTH / w, (HEIGHT - 40) / h)
                rw, rh = max(1, round(w * scale)), max(1, round(h * scale))
                canvas = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)
                left, top = (WIDTH - rw) // 2, (HEIGHT - 40 - rh) // 2
                canvas[top:top+rh, left:left+rw] = cv2.resize(frame, (rw, rh))
                cv2.putText(canvas, self.caption[:105], (20, HEIGHT - 14),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (230, 235, 245), 1, cv2.LINE_AA)
                data = memoryview(canvas).cast('B')
                while data:
                    written = self.encoder.stdin.write(data)
                    if not written:
                        raise RuntimeError('Video pipe stopped accepting frames')
                    data = data[written:]
                self.submitted_frames += 1
                self._submitted(self.submitted_frames, metadata)
                deadline += 1 / FPS
                lag = time.monotonic() - deadline
                if lag > 0.25:
                    raise RuntimeError('Encoder fell more than 250 ms behind real time')
                self.stop_event.wait(max(0.0, deadline - time.monotonic()))
        except Exception as exc:
            self.error = str(exc)
            self.stop_event.set()

    def health(self) -> dict:
        now = time.monotonic()
        target_error = None
        try:
            verify_target(self.target)
        except Exception as exc:
            target_error = str(exc)
        data = {'recording': not self.stop_event.is_set(), 'capture_frames': self.capture_frames,
            'encoded_frames': self.encoded_frames, 'capture_age': now - self.last_capture,
            'encoder_age': now - self.last_encode, 'audio_streams': 0,
            'capture': 'game-window-only', 'error': self.error or target_error,
            'capture_refreshes': getattr(self, 'capture_refreshes', 0),
            'pid': self.target['pid'], 'hwnd': self.target['hwnd'],
            'encoder_pid': self.encoder.pid, 'encoder_alive': self.encoder.poll() is None}
        data['healthy'] = bool(data['recording'] and not data['error'] and data['encoder_alive']
            and data['capture_frames'] >= 2 and data['encoded_frames'] >= 2
            and data['capture_age'] < 2.0 and data['encoder_age'] < 2.0)
        return data

    def stop(self):
        if self.final_result is not None:
            return self.final_result
        self.stop_event.set()
        if self.refresh_thread is not None:
            self.refresh_thread.join(timeout=5)
        if self.control is not None:
            try:
                self.control.stop()
            except Exception as exc:
                self.error = self.error or 'Capture stop failed: ' + str(exc)
        if self.writer_thread is not None:
            self.writer_thread.join(timeout=5)
        if self.writer_thread is not None and self.writer_thread.is_alive():
            self.encoder.terminate()
            self.writer_thread.join(timeout=5)
            self.error = self.error or 'Encoder writer failed to stop promptly'
        try:
            self.encoder.stdin.close()
        except (BrokenPipeError, OSError):
            pass
        try:
            self.encoder.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.encoder.kill()
            self.encoder.wait()
            self.error = self.error or 'Encoder finalization timeout'
        self.progress_thread.join(timeout=2)
        self.log.close()
        result = self.health()
        output = self.folder / 'raw.mkv'
        result.update({'finalized': self.encoder.returncode == 0,
                       'bytes': output.stat().st_size if output.exists() else 0})
        atomic_json(self.folder / 'final.json', result)
        self.final_result = result
        return result


def doctor() -> dict:
    import windows_capture
    data = {'capture_library': 'windows-capture 2.0.1', 'opencv': cv2.__version__,
            'numpy': np.__version__, 'ffmpeg_exists': FFMPEG.is_file(),
            'free_bytes': shutil.disk_usage(ROOT).free, 'audio_inputs': 0,
            'game_found': False, 'live_capture_tested': False, 'controls_tested': False}
    try:
        data['installation'] = installed_game()
        data['target'] = game_target()
        data['game_found'] = True
    except Exception as exc:
        data['game_status'] = str(exc)
    return data


def verify_video(path: Path) -> dict:
    checked = subprocess.run([str(FFMPEG), '-hide_banner', '-v', 'info', '-xerror',
        '-err_detect', 'explode', '-i', str(path), '-map', '0', '-progress', 'pipe:1',
        '-f', 'null', '-'], capture_output=True,
        creationflags=subprocess.CREATE_NO_WINDOW, timeout=60)
    output = checked.stderr.decode(errors='replace')
    input_info = output.split('Stream mapping:', 1)[0]
    audio = re.findall(r'Stream #\d+:\d+.*Audio:', input_info)
    video = re.findall(r'Stream #\d+:\d+.*Video:', input_info)
    frames = re.findall(r'^frame=(\d+)', checked.stdout.decode(errors='replace'), re.MULTILINE)
    if checked.returncode or audio or len(video) != 1 or not frames or int(frames[-1]) < 2:
        raise RuntimeError('Export failed full decode, video presence, or zero-audio verification')
    return {'full_decode_passed': True, 'audio_streams': len(audio), 'video_streams': len(video),
            'decoded_video_frames': int(frames[-1]), 'bytes': path.stat().st_size}


def capture_probe(seconds: float = 10.0) -> dict:
    """Capture the game's menu without sending a single keyboard/mouse event."""
    target = game_target()
    # Focus the verified game window so its menu keeps rendering during the probe.
    # This sends no keyboard, mouse button, or gameplay event.
    user32.ShowWindow(target['hwnd'], 9)
    user32.SetForegroundWindow(target['hwnd'])
    time.sleep(0.5)
    recorder = SilentRecorder(target)
    started = time.monotonic()
    healthy_seen = False
    try:
        while time.monotonic() - started < seconds:
            state = recorder.health()
            if state['error'] or not state['encoder_alive']:
                raise RuntimeError(state['error'] or 'Encoder stopped')
            if state['healthy']:
                healthy_seen = True
                with recorder.lock:
                    snapshot = recorder.latest.copy()
                cv2.imwrite(str(recorder.folder / 'game-only-snapshot.png'), snapshot)
            atomic_json(recorder.folder / 'health.json', state)
            time.sleep(0.25)
        if not healthy_seen:
            raise RuntimeError('Capture never reached verified healthy status')
    finally:
        final = recorder.stop()
    if not final['finalized'] or final['error']:
        raise RuntimeError('Capture did not finalize cleanly: ' + str(final))
    result = {'test': 'live game-window capture only; no gameplay inputs',
              'healthy_seen': healthy_seen, **verify_video(recorder.folder / 'raw.mkv')}
    atomic_json(recorder.folder / 'probe-result.json', result)
    return result


def encoder_self_test() -> dict:
    """Test only generated color bars, never a screen or game."""
    folder = ROOT / 'runtime'
    folder.mkdir(exist_ok=True)
    out = folder / ('synthetic-capture-test-' + time.strftime('%H%M%S') + '.mkv')
    cmd = encoder_command(out)
    input_start = cmd.index('-f')
    map_start = cmd.index('-map')
    cmd[input_start:map_start] = ['-f', 'lavfi', '-i', 'testsrc2=size=1280x720:rate=60:duration=2']
    p = subprocess.run(cmd, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW, timeout=30)
    if p.returncode:
        raise RuntimeError(p.stderr.decode(errors='replace'))
    result = {'test': 'synthetic encoder only; no gameplay or screen capture',
        **verify_video(out), 'file': out.name}
    atomic_json(folder / 'encoder-self-test.json', result)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['doctor', 'self-test', 'probe'])
    args = parser.parse_args()
    functions = {'doctor': doctor, 'self-test': encoder_self_test, 'probe': capture_probe}
    print(json.dumps(functions[args.command](), indent=2))
