"""A live per-column peak meter for the MEASUREMENT INPUT.

His problem, in his words: a card with sixteen capture columns and no
way to learn which one the microphone is on except by guessing and
measuring. His answer, which is the right one: knock on the capsule and
watch the list.

So this is deliberately not MeterEngine. That one taps a sink's MONITOR
and runs the profile's filter chain per channel, because its job is to
show what an EQ is doing. This one taps a SOURCE and runs nothing at
all: the input is what it is, and a filter between the capsule and the
number would be a lie in a window whose whole purpose is measurement.

Peaks only, no ballistics. A meter for reading a level over seconds
wants a decay; a meter for answering "which column moved when I
knocked" wants the truth of the last few milliseconds and a decay slow
enough for an eye to catch it. The decay lives in the widget, not here.
"""

import struct
import threading

FS = 48000
BLOCK = 2048            # ~43 ms at 48k: fast enough for a knock
FLOOR_DB = -90.0        # a meter has nothing to draw below this
QUIET_DB = -200.0       # a NOISE FLOOR must not be clamped there


def peak_db(x):
    """dBFS of a peak amplitude, floored rather than -inf."""
    if x <= 0.0:
        return FLOOR_DB
    import math
    return max(FLOOR_DB, 20.0 * math.log10(min(1.0, x)))


def level_db(x):
    """dBFS of a level, with room underneath.

    peak_db() stops at -90 because a meter has nothing to draw below
    that, but a noise floor lives down there: clamp it and a gain
    ladder reads a false plateau where the chain was simply quiet.
    """
    if x <= 0.0:
        return QUIET_DB
    import math
    return max(QUIET_DB, 20.0 * math.log10(min(1.0, x)))


class InputMeter:
    """Owns a capture subprocess and a worker thread.

    latest() hands back one peak per column, in the source's own column
    order, or None before the first block arrives. The caller polls it;
    nothing is pushed, because the only consumer is a redraw that has
    its own clock anyway.

    latest_rms() hands back the same columns as RMS. A meter wants the
    peak, but a NOISE FLOOR wants the RMS: the peak of a stationary
    hiss wanders with the block length while its RMS does not, and a
    gain ladder read off peaks would spend its rungs arguing with that
    wander. The RMS is taken ABOUT THE MEAN, because a constant offset
    is not noise and a chain carrying one would otherwise report its
    offset as its floor.

    latest_dc() hands back that mean, as a level. Together the three
    say what is on a column without a spectrum: a crest of 11 to 13 dB
    over an insignificant mean is noise, a crest near 3 with the same
    is a tone, and a mean that stands up to the RMS is an offset --
    which is a fault to fix rather than a floor to measure.

    All three come out of the one pass the worker already makes, so
    they cost two accumulators and no extra capture.
    """

    def __init__(self, fs=FS, block=BLOCK):
        self.fs = int(fs)
        self.block = int(block)
        self._proc = None
        self._thread = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._peaks = None
        self._rms = None
        self._dc = None
        self._n = 0

    # ---- lifecycle ---------------------------------------------------
    def start(self, node, channels):
        """Open the capture and begin reading. `node` is the id the
        backend pins with, `channels` the source's real width."""
        self.stop()
        n = int(channels)
        if n < 1:
            raise ValueError("a source has at least one column")
        from . import pw_backend
        self._n = n
        self._proc = pw_backend.backend().capture(node, n, self.fs)
        self._stop.clear()
        self._thread = threading.Thread(target=self._run,
                                        args=(self._proc, n),
                                        daemon=True)
        self._thread.start()

    def alive(self):
        return self._thread is not None and self._thread.is_alive()

    def stop(self):
        self._stop.set()
        proc, self._proc = self._proc, None
        if proc is not None:
            try:
                proc.terminate()
            except Exception:
                pass
        thread, self._thread = self._thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        with self._lock:
            self._peaks = None
            self._rms = None
            self._dc = None

    def latest(self):
        with self._lock:
            return None if self._peaks is None else list(self._peaks)

    def latest_rms(self):
        """One RMS per column in dBFS, about the mean, or None before
        the first block."""
        with self._lock:
            return None if self._rms is None else list(self._rms)

    def latest_dc(self):
        """The mean of each column as a level in dBFS, signed by
        nothing -- only its size matters. None before the first
        block."""
        with self._lock:
            return None if self._dc is None else list(self._dc)

    # ---- the worker --------------------------------------------------
    def _run(self, proc, n):
        # a StreamHandle: read(nbytes) off the capture's stdout
        want = self.block * n * 4          # f32 interleaved
        buf = b""
        try:
            while not self._stop.is_set():
                chunk = proc.read(want - len(buf))
                if not chunk:
                    break
                buf += chunk
                if len(buf) < want:
                    continue
                self._digest(buf, n)
                buf = b""
        except Exception:
            pass
        finally:
            with self._lock:
                self._peaks = None
                self._rms = None
                self._dc = None

    def _digest(self, raw, n):
        frames = len(raw) // (4 * n)
        vals = struct.unpack("<%df" % (frames * n), raw[:frames * n * 4])
        peaks = [0.0] * n
        sq = [0.0] * n
        tot = [0.0] * n
        for i, v in enumerate(vals):
            c = i % n
            a = -v if v < 0.0 else v
            if a > peaks[c]:
                peaks[c] = a
            sq[c] += v * v
            tot[c] += v
        rms, dc = [], []
        for c in range(n):
            if not frames:
                rms.append(QUIET_DB)
                dc.append(QUIET_DB)
                continue
            mean = tot[c] / frames
            var = sq[c] / frames - mean * mean
            rms.append(level_db(var ** 0.5 if var > 0.0 else 0.0))
            dc.append(level_db(-mean if mean < 0.0 else mean))
        with self._lock:
            self._peaks = [peak_db(p) for p in peaks]
            self._rms = rms
            self._dc = dc
