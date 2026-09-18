"""Record the verified game's rendered buffer and acknowledge encoded frames.

The 128-byte shared header contains no controls that modify game physics. Its
acknowledgment lets the bridge freeze whenever a gameplay frame is unrecorded.
"""
import collections
import ctypes
import json
import struct
import threading
import time

import recording as r

SIZE = 32 * 1024 * 1024
HEADER = struct.Struct('<8sQQQQIIIIQQQQIIQ24x')
assert HEADER.size == 128
kernel = ctypes.WinDLL('kernel32', use_last_error=True)
kernel.OpenFileMappingW.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_wchar_p]
kernel.OpenFileMappingW.restype = ctypes.c_void_p
kernel.MapViewOfFile.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_size_t]
kernel.MapViewOfFile.restype = ctypes.c_void_p
kernel.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
kernel.CloseHandle.argtypes = [ctypes.c_void_p]
kernel.GetTickCount64.restype = ctypes.c_uint64


class NativeFrames:
    def __init__(self, target):
        r.verify_target(target)
        self.handle = kernel.OpenFileMappingW(0xF001F, False, 'Local\\AstraGDFrame_' + str(target['pid']))
        if not self.handle:
            raise RuntimeError('The live game has no native frame mapping')
        self.address = kernel.MapViewOfFile(self.handle, 0xF001F, 0, 0, SIZE)
        if not self.address:
            kernel.CloseHandle(self.handle)
            raise ctypes.WinError(ctypes.get_last_error())
        self.buffer = (ctypes.c_ubyte * SIZE).from_address(self.address)
        self.epoch = HEADER.unpack_from(self.buffer)[-1]
        self.closed = False
        if bytes(self.buffer[:8]) != b'ASTRAGD1':
            self.close()
            raise RuntimeError('Unrecognized native frame protocol')

    def read(self, previous_sequence):
        before = HEADER.unpack_from(self.buffer)
        magic, seq, tick, generation, uptime, width, height, size, valid = before[:9]
        if not valid or seq % 2 or seq == previous_sequence:
            return None
        if magic != b'ASTRAGD1' or before[-1] != self.epoch:
            raise RuntimeError('Native frame source identity changed')
        if not (1 <= width <= 7680 and 1 <= height <= 4320 and size == width * height * 3 and size <= SIZE - 128):
            raise RuntimeError('Invalid native game frame dimensions')
        age = kernel.GetTickCount64() - uptime
        if age < 0 or age > 1500:
            raise RuntimeError('Native game frame is stale')
        pixels = ctypes.string_at(self.address + 128, size)
        after = struct.unpack_from('<Q', self.buffer, 8)[0]
        if after != seq or after % 2:
            return None
        frame = r.np.frombuffer(pixels, dtype=r.np.uint8).reshape((height, width, 3))[::-1].copy()
        return frame, {'sequence': seq, 'tick': tick, 'generation': generation, 'uptime_ms': uptime}

    def acknowledge(self, metadata):
        if self.closed:
            return
        # Aligned 64-bit stores are atomic on the supported Windows x64 host.
        ctypes.c_uint64.from_address(self.address + 56).value = 0
        struct.pack_into('<QQQI', self.buffer, 64, metadata['tick'], metadata['generation'],
                         kernel.GetTickCount64(), r.os.getpid())
        ctypes.c_uint64.from_address(self.address + 56).value = metadata['sequence']

    def invalidate(self):
        if not self.closed:
            ctypes.c_uint64.from_address(self.address + 56).value = 0

    def close(self):
        if self.closed:
            return
        self.invalidate()
        self.closed = True
        kernel.UnmapViewOfFile(self.address)
        kernel.CloseHandle(self.handle)


class NativeSilentRecorder(r.SilentRecorder):
    def __init__(self, target):
        self.native = None
        self.reader_thread = None
        self.pending = collections.deque()
        self.acknowledged = None
        self.last_journal_state = None
        super().__init__(target)

    def _open_capture(self):
        self.native = NativeFrames(self.target)
        self.reader_thread = threading.Thread(target=self._read_native, daemon=True)
        self.reader_thread.start()

    def _read_native(self):
        sequence = 0
        try:
            while not self.stop_event.is_set():
                received = self.native.read(sequence)
                if received is None:
                    self.stop_event.wait(0.003)
                    continue
                frame, metadata = received
                sequence = metadata['sequence']
                with self.lock:
                    self.latest = frame
                    self.capture_timestamp = metadata
                    self.capture_frames += 1
                    self.last_capture = time.monotonic()
        except Exception as exc:
            self.error = 'Native game capture failed: ' + str(exc)
            self.stop_event.set()
            self.native.invalidate()

    def _refresh_still_capture(self):
        while not self.stop_event.wait(0.1):
            if self.last_capture and time.monotonic() - self.last_capture > 1.5:
                self.error = 'Native rendering stopped producing fresh frames'
                self.stop_event.set()
                self.native.invalidate()

    def _submitted(self, count, metadata):
        with self.lock:
            self.pending.append((count, metadata))

    def _encoded(self, count):
        if self.native is None or self.stop_event.is_set() or self.error:
            return
        latest = None
        with self.lock:
            while self.pending and self.pending[0][0] <= count:
                latest = self.pending.popleft()
        if latest is None or time.monotonic() - self.last_capture >= 1.5:
            return
        frame_number, metadata = latest
        if metadata is None:
            return
        self.native.acknowledge(metadata)
        self.acknowledged = {'encoded_frame': frame_number, **metadata}
        key = (metadata['generation'], metadata['tick'])
        if key != self.last_journal_state:
            with (self.folder / 'encoded-game-frames.jsonl').open('a', encoding='utf-8') as out:
                out.write(json.dumps(self.acknowledged) + '\n')
            self.last_journal_state = key

    def health(self):
        data = super().health()
        data['capture'] = 'verified-game-render-only'
        data['acknowledged'] = self.acknowledged
        data['healthy'] = bool(data['healthy'] and self.acknowledged)
        return data

    def stop(self):
        if self.native is not None:
            self.native.invalidate()
        result = super().stop()
        if self.reader_thread is not None:
            self.reader_thread.join(timeout=5)
        if self.native is not None and (self.reader_thread is None or not self.reader_thread.is_alive()):
            self.native.close()
        return result
