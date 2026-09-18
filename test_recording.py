"""Recording guard checks using fake counters and synthetic media only."""
import json
from pathlib import Path
import subprocess
import threading
import time
import unittest
from unittest.mock import Mock, patch

import recording


class HealthChecks(unittest.TestCase):
    def setUp(self):
        self.recorder = object.__new__(recording.SilentRecorder)
        self.recorder.target = {'pid': 123, 'hwnd': 456}
        self.recorder.capture_frames = 100
        self.recorder.encoded_frames = 99
        self.recorder.last_capture = time.monotonic()
        self.recorder.last_encode = time.monotonic()
        self.recorder.stop_event = threading.Event()
        self.recorder.error = None
        self.recorder.encoder = Mock(pid=789)
        self.recorder.encoder.poll.return_value = None
        self.target_check = patch.object(recording, 'verify_target')
        self.target_check.start()
        self.addCleanup(self.target_check.stop)

    def test_fresh_counters_are_healthy(self):
        self.assertTrue(self.recorder.health()['healthy'])

    def test_stalled_capture_refuses_gameplay(self):
        self.recorder.last_capture -= 3
        self.assertFalse(self.recorder.health()['healthy'])

    def test_stalled_encoder_refuses_gameplay(self):
        self.recorder.last_encode -= 3
        self.assertFalse(self.recorder.health()['healthy'])

    def test_exited_encoder_refuses_gameplay(self):
        self.recorder.encoder.poll.return_value = 1
        self.assertFalse(self.recorder.health()['healthy'])

    def test_latched_error_refuses_gameplay(self):
        self.recorder.error = 'disk write failed'
        self.assertFalse(self.recorder.health()['healthy'])

    def test_closed_capture_refuses_gameplay(self):
        self.recorder.stop_event.set()
        self.assertFalse(self.recorder.health()['healthy'])

    def test_initial_no_frames_refuses_gameplay(self):
        self.recorder.capture_frames = 0
        self.recorder.encoded_frames = 0
        self.assertFalse(self.recorder.health()['healthy'])

    def test_changed_target_refuses_gameplay(self):
        with patch.object(recording, 'verify_target', side_effect=RuntimeError('changed process')):
            self.assertFalse(self.recorder.health()['healthy'])


class MediaChecks(unittest.TestCase):
    def test_video_with_audio_is_rejected(self):
        folder = recording.ROOT / 'runtime'
        folder.mkdir(exist_ok=True)
        output = folder / ('synthetic-audio-rejection-' + str(time.time_ns()) + '.mkv')
        # Generated tone only: no microphone or system-audio device is used.
        command = [str(recording.FFMPEG), '-hide_banner', '-loglevel', 'error', '-n',
            '-f', 'lavfi', '-i', 'testsrc2=size=160x90:rate=10:duration=0.5',
            '-f', 'lavfi', '-i', 'sine=frequency=440:duration=0.5',
            '-c:v', 'libx264', '-c:a', 'aac', str(output)]
        subprocess.run(command, check=True, capture_output=True, timeout=20,
                       creationflags=subprocess.CREATE_NO_WINDOW)
        with self.assertRaisesRegex(RuntimeError, 'zero-audio verification'):
            recording.verify_video(output)


if __name__ == '__main__':
    suite = unittest.defaultTestLoader.loadTestsFromModule(__import__(__name__))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    recording.atomic_json(recording.ROOT / 'runtime/guard-tests.json',
        {'tests_run': result.testsRun, 'failures': len(result.failures),
         'errors': len(result.errors), 'passed': result.wasSuccessful(),
         'scope': 'simulated health counters and synthetic media; no live game capture or controls'})
    raise SystemExit(0 if result.wasSuccessful() else 1)
