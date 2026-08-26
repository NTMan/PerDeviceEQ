"""The warning band under the floor handle."""
import numpy as np
from perdeviceeq import measure_build as mb


class R:
    pass


def test_a_take_with_no_confession_abstains():
    r = R()
    for k in ("thd_db", "h2_db", "h3_db", "thd_noise_db"):
        setattr(r, k, None)
    assert mb.audible_distortion([r], [20.0, 40.0]) is None


def test_the_bass_is_forgiven_and_the_midband_is_not():
    """Whole percents pass unnoticed in the bass and tenths of
    one do not in the midband, so the threshold the strip
    measures against has to move with frequency."""
    lo = mb._threshold_db(np.array([30.0]))[0]
    mid = mb._threshold_db(np.array([1000.0]))[0]
    assert lo > mid + 15.0
    assert -22.0 < lo < -16.0
    assert -42.0 < mid < -38.0


def test_the_third_weighs_more_than_the_second():
    f = [1000.0]
    a = R()
    a.thd_db = [-40.0]; a.h2_db = [-40.0]
    a.h3_db = [None]; a.thd_noise_db = None
    b = R()
    b.thd_db = [-40.0]; b.h2_db = [None]
    b.h3_db = [-40.0]; b.thd_noise_db = None
    only2 = mb.audible_distortion([a], f)[0]
    only3 = mb.audible_distortion([b], f)[0]
    assert only3 > only2 + 3.0


def test_a_single_bin_spike_does_not_paint():
    """One bin of a 1/96-octave grid is far inside a critical
    band, so a spike there is not heard as distortion and must
    not colour the strip like a mountain that spans octaves."""
    n = 200
    f = [100.0 * 2 ** (i / 96.0) for i in range(n)]
    r = R()
    r.thd_db = [-60.0] * n
    r.h2_db = [-60.0] * n
    r.h3_db = [None] * n
    r.thd_noise_db = None
    r.h2_db[100] = -10.0          # the spike
    out = mb.audible_distortion([r], f, ppo=96)
    assert out[100] < -10.0
    # against a NEIGHBOUR, so the frequency weighting -- which
    # varies across the sweep -- is not mistaken for the spike
    assert abs(out[100] - out[112]) < 1.0


def test_a_broad_hill_survives():
    n = 200
    f = [100.0 * 2 ** (i / 96.0) for i in range(n)]
    r = R()
    r.thd_db = [-60.0] * n
    r.h2_db = [-60.0] * n
    r.h3_db = [None] * n
    r.thd_noise_db = None
    for i in range(60, 140):      # most of an octave
        r.h2_db[i] = -10.0
    out = mb.audible_distortion([r], f, ppo=96)
    assert out[100] > 10.0


def test_a_clean_midband_says_nothing():
    """The strip is a warning. Under a percent at 1 kHz is
    inaudible on a loudspeaker, and a colour there would read as
    'it will rasp' across the whole spectrum."""
    n = 300
    f = [100.0 * 2 ** (i / 96.0) for i in range(n)]
    r = R()
    r.thd_db = [-42.0] * n        # about 0.8 per cent
    r.h2_db = [-42.0] * n
    r.h3_db = [None] * n
    r.thd_noise_db = None
    out = mb.audible_distortion([r], f, ppo=96)
    assert max(out) < 0.0         # under the audible threshold
