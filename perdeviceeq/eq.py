# -*- coding: utf-8 -*-
"""EQ model: bands, the inline PipeWire filter-graph builders, the biquad
frequency response (GUI plot + the tier-1 headroom / clip estimate) and
REW/AutoEQ text import/export.

Pure computation -- no GTK, no subprocess, no filesystem.
"""

import math, cmath, re

from .config import FS, FMIN, FMAX, TYPE_TO_LABEL


# ============================ EQ model ============================
class Band:
    __slots__ = ("type", "freq", "gain", "q", "enabled")
    def __init__(self, type="PK", freq=1000.0, gain=0.0, q=1.0, enabled=True):
        self.type = type if type in TYPE_TO_LABEL else "PK"
        self.freq = float(freq); self.gain = float(gain)
        self.q = float(q); self.enabled = bool(enabled)
    def to_dict(self):
        return {"type": self.type, "freq": self.freq, "gain": self.gain,
                "q": self.q, "enabled": self.enabled}
    @classmethod
    def from_dict(cls, d):
        return cls(d.get("type", "PK"), d.get("freq", 1000.0),
                   d.get("gain", 0.0), d.get("q", 1.0), d.get("enabled", True))


def filter_entry(b):
    return "{ type = %s, freq = %g, gain = %g, q = %g }" % (
        TYPE_TO_LABEL[b.type], b.freq, b.gain, b.q)


def build_filter_array(preamp, bands):
    """The inline '[ ... ]' array of param_eq filters (no double quotes ->
    inline-safe). Preamp is emitted as a highshelf at freq 0 (== flat gain)."""
    filters = []
    if abs(preamp) > 1e-9:
        filters.append("{ type = bq_highshelf, freq = 0, gain = %g, q = 1.0 }" % preamp)
    for b in bands:
        if b.enabled:
            filters.append(filter_entry(b))
    if not filters:  # never emit an empty filter list -> one transparent filter
        filters.append("{ type = bq_peaking, freq = 1000, gain = 0.0, q = 1.0 }")
    return "[ %s ]" % " ".join(filters)


def build_graph(preamp, bands):
    """Single param_eq applied to all channels (config.filters)."""
    cfg = "filters = %s" % build_filter_array(preamp, bands)
    return ("{ nodes = [ { type = builtin name = eq label = param_eq "
            "config = { %s } } ] }" % cfg)


def build_graph_channels(channel_sets):
    """Per-channel param_eq. channel_sets is a list of (preamp, bands) in
    channel order; emitted as config.filters1, filters2, ... (1-based)."""
    parts = []
    for i, (preamp, bands) in enumerate(channel_sets, start=1):
        parts.append("filters%d = %s" % (i, build_filter_array(preamp, bands)))
    cfg = " ".join(parts)
    return ("{ nodes = [ { type = builtin name = eq label = param_eq "
            "config = { %s } } ] }" % cfg)


# The device floor: Taste asks, the zone disposes. The device
# renders the whole chain -- Taste included -- only inside its
# measured trust zone; below the zone's lower edge an LR8
# high-pass cascade (four biquads, the Butterworth-squared Q
# pair twice) protects the driver at 48 dB/oct. The frequency
# is not a new field: it IS the stored fit's f_lo, so a
# re-measure moves the floor by itself and no hand-kept copy
# can drift. Engaged only when the zone starts at or above
# FLOOR_MIN_HZ -- a zone reaching into deep bass means the
# device rendered it and there is nothing to protect.
FLOOR_MIN_HZ = 30.0
FLOOR_QS = (0.5412, 1.3066, 0.5412, 1.3066)


def zone_floor_hz(p):
    """The frequency the floor WOULD stand at: the measured
    zone's lower edge from the stored fit (params fallback for
    pre-zone fits), or None below the gate. Computed
    regardless of the profile's floor_off flag, so the UI can
    offer the handle even while the stages sleep."""
    fit = (p or {}).get("fit") or {}
    zone = fit.get("zone") or {}
    params = fit.get("params") or {}
    try:
        lo = float(zone.get("lo", params.get("f_lo", 0.0)))
    except (TypeError, ValueError):
        return None
    return lo if lo >= FLOOR_MIN_HZ else None


FLOOR_SEED_HZ = 40.0     # where the handle first appears when
#                          the hand turns the floor on with no
#                          history: a place to grab, nothing more


def floor_hz_effective(p):
    """Where the floor stands for THIS profile: floor_hz, the
    architect's sweep-by-hearing handle, or None. The floor is
    a fully MANUAL organ -- the ceiling came down entirely and
    the trust zone lost its protection authority (it remains a
    fit organ: jurisdiction for the solver and honesty marks
    on the graph, never a blind gate on playback)."""
    p = p or {}
    try:
        ovr = p.get("floor_hz")
        if ovr is not None:
            return float(ovr)
    except (TypeError, ValueError):
        pass
    return None


def fit_zone(p):
    """Both edges of the measured trust zone from the stored
    fit (params fallback), UNGATED: the graph draws the honesty
    border everywhere, even where the floor organ sleeps -- the
    30 Hz gate belongs to speaker protection, not to telling
    the truth about where the measurement ends. None when the
    profile has no fit to testify."""
    fit = (p or {}).get("fit") or {}
    zone = fit.get("zone") or {}
    params = fit.get("params") or {}
    try:
        lo = float(zone.get("lo", params.get("f_lo")))
        hi = float(zone.get("hi", params.get("f_hi")))
    except (TypeError, ValueError):
        return None
    return (lo, hi)


def floor_bands(p):
    """The sealed floor stages for a profile dict, or []. Band
    dicts (type HP at the effective floor) that the graph
    builder and the preview both consume; they never enter the
    profile's band lists. The floor_off flag is the on/off
    half of the handle; floor_hz is the frequency half -- the
    ear sweeps it until the distortion is gone."""
    if (p or {}).get("floor_off"):
        return []
    lo = floor_hz_effective(p)
    if lo is None:
        return []
    return [{"type": "HP", "freq": lo, "gain": 0.0, "q": q,
             "enabled": True} for q in FLOOR_QS]


def resolve_slots(prof_keys, sink_keys):
    """Which profile channel feeds each channel of the sink: a list as long
    as `sink_keys`, holding a profile channel name or None.

    A profile's channels name the SIDES of a transducer, a sink's channels
    name ROUTES, and the two vocabularies agree only by luck.

    ONE channel spreads, and it is asked first, before any name is looked
    at: a profile with a single channel is a single curve -- one ear
    measured, or an imported correction that never had sides -- and the
    honest reading is that it applies to everything the sink has. Deciding
    by name first would send a lone channel called FL to FL alone and
    leave the other side dry, which is not what measuring one ear means.

    With two or more channels, names decide, since FL is FL wherever it
    appears and a partial agreement is still an agreement -- a sink
    channel with no namesake gets None and carries the tail alone. When
    NOTHING agrees, which is what a card handing out AUX0..AUX9 does to a
    stereo profile, fall back to position: side one to route one, side two
    to route two, the rest None. Matching by name alone would leave such a
    card silently uncorrected, and silence is the failure mode this whole
    area is being cured of.

    This is the default policy and it lives here only until the binding
    stores a map of its own; the graph builder below takes the answer, not
    the question.
    """
    sink = list(sink_keys or [])
    prof = list(prof_keys or [])
    if not sink:
        return []
    if len(prof) == 1:
        return [prof[0]] * len(sink)
    have = set(prof)
    by_name = [k if k in have else None for k in sink]
    if any(x is not None for x in by_name):
        return by_name
    return [prof[i] if i < len(prof) else None for i in range(len(sink))]


def auto_preamp_db(p, extra=None):
    """The attenuation that zeroes the tier-1 estimate for this body: the
    max of the WORST channel's summed band curve with no preamp, so one
    shared value clears every slot.
    Rounded UP to the 0.1 dB step the spin can express, so the result lands
    at or below 0 dBFS.

    Pure, and computed rather than stored: its inputs include the floor and
    the taste layer, and taste is not part of a profile. Storing the answer
    inside the profile meant that changing the taste rewrote every profile
    that had been opened -- a derived number kept in the wrong house.
    """
    tail = [Band.from_dict(b) for b in floor_bands(p) + list(extra or [])]
    chans = p.get("channels") or {}
    keys = list(p.get("ch_keys") or list(chans.keys()))
    peaks = [curve_max_db(0.0, [Band.from_dict(b) for b in
                                ((chans.get(k) or {}).get("bands")
                                 or [])] + tail)
             for k in keys] or [curve_max_db(0.0, tail)]
    return max(0.0, math.ceil(max(peaks) * 10.0 - 1e-9) / 10.0)


def profile_slots(slots, ch_map):
    """The editor's slots keyed by PROFILE channel.

    The tabs are keyed by SINK channel, because that is what a person sees
    and turns; the profile's channels carry its own names. While a card
    calls its outputs FL and FR the two coincide and nobody notices. Point
    AUX0 at FL and they part: the tab is AUX0, the correction is FL's.

    A mapped tab folds onto the profile channel it feeds -- several tabs
    may feed from one side, which is the same correction seen twice, so
    the last one written wins and they stay identical. A tab the map does
    not answer for keeps its own name: bands drawn there are a correction
    somebody typed, and the profile is the only house there is. With no
    map at all (no live sink to ask) that is every tab, which is also what
    an offline window has always done.
    """
    out = {}
    for ch, slot in (slots or {}).items():
        out[(ch_map or {}).get(ch) or ch] = slot
    return out


def sibling_tabs(ch, ch_map, keys):
    """Every tab in `keys` fed by the same profile channel as `ch`, `ch`
    itself excluded.

    Many-to-one is ordinary, not exotic: a profile with ONE channel
    spreads over the whole sink, so every tab on a ten-channel node feeds
    from that one side, and a card that sums its buses can point two
    routes at one side deliberately. Such tabs are several views of ONE
    correction, so an edit on any of them is an edit on all -- and the
    window has to say so, because profile_slots folds them together with
    the last write winning, and a stale sibling would fold over a fresh
    edit on the next save.
    """
    m = ch_map or {}
    tgt = m.get(ch) or ch
    return [k for k in (keys or [])
            if k != ch and (m.get(k) or k) == tgt]


def profile_graph(p, extra=None, slots=None):
    """Inline graph string for a schema-v2 profile dict: ONE shared preamp,
    the channels carry bands only. `extra` is a list
    of preference-layer band dicts appended after EVERY chain -- taste
    composed over correction, whatever the profile's channel layout; the
    shared preamp stays the profile's own, and headroom over the
    composition is the caller's job (curve_max_db on the concatenation).

    `slots` is the resolved answer from resolve_slots: one entry per SINK
    channel, a profile channel name or None. Given it, the graph is that
    wide and no wider. A profile legitimately holds more channels than the
    sink in front of it -- profiles.save_user never strips a stored
    channel, so a pair measured on one card keeps its keys after a visit
    to another -- but param_eq is handed a fixed channel count and refuses
    a config naming more, and it refuses in silence, taking the whole
    chain with it, taste included. Cutting to the sink's width happens
    here, at the one call that knows both sides.
    """
    g = float(p.get("preamp", 0.0))
    tail = [Band.from_dict(b)
            for b in floor_bands(p) + list(extra or [])]
    chans = p.get("channels") or {}
    keys = list(slots) if slots else (p.get("ch_keys") or list(chans.keys()))
    sets = []
    for k in keys:
        # a sink channel the profile does not reach carries the tail
        # alone -- it plays dry, and nothing else is kept for it
        bands = (chans.get(k) or {}).get("bands", []) if k else []
        sets.append((g, [Band.from_dict(b) for b in bands] + tail))
    if not sets:
        return build_graph(g, tail)
    return build_graph_channels(sets)


def _set_has_content(s):
    return any(b.get("enabled", True) for b in (s or {}).get("bands", []))


def profile_has_content(p):
    """True if the profile actually changes the sound (some enabled band or a
    non-zero preamp). A flat profile is equivalent to Clean / no binding."""
    if abs(float(p.get("preamp", 0.0))) > 1e-9:   # schema v2 shared preamp
        return True
    chans = p.get("channels") or {}
    for k in (p.get("ch_keys") or chans.keys()):
        if _set_has_content(chans.get(k)):
            return True
    return False


# ---- biquad frequency response: FR plot + tier-1 headroom estimate ---------
# Audio EQ Cookbook with the Q parameterization for shelves -- coefficient-
# identical to PipeWire's biquad_{peaking,lowshelf,highshelf}
# (spa/plugins/audioconvert/biquad.c, linked into filter-graph's param_eq;
# verified against the 1.6.2 tag and master). Note this is NOT the RBJ "shelf
# slope" form sqrt((A+1/A)(1/S-1)+2): with S=q that one drifts up to ~2 dB
# from the real DSP on high-Q shelves.
def biquad(btype, f0, gain_db, q, fs=FS):
    f0 = min(max(f0, 1.0), fs / 2 - 1.0)
    q = max(q, 0.05)
    A = 10 ** (gain_db / 40.0)
    w0 = 2 * math.pi * f0 / fs
    cw, sw = math.cos(w0), math.sin(w0)
    alpha = sw / (2 * q)
    if btype == "PK":
        b0, b1, b2 = 1 + alpha * A, -2 * cw, 1 - alpha * A
        a0, a1, a2 = 1 + alpha / A, -2 * cw, 1 - alpha / A
    elif btype == "LSC":
        s = 2 * math.sqrt(A) * alpha
        b0 = A * ((A + 1) - (A - 1) * cw + s)
        b1 = 2 * A * ((A - 1) - (A + 1) * cw)
        b2 = A * ((A + 1) - (A - 1) * cw - s)
        a0 = (A + 1) + (A - 1) * cw + s
        a1 = -2 * ((A - 1) + (A + 1) * cw)
        a2 = (A + 1) + (A - 1) * cw - s
    elif btype == "HP":
        b0 = (1 + cw) / 2
        b1 = -(1 + cw)
        b2 = (1 + cw) / 2
        a0, a1, a2 = 1 + alpha, -2 * cw, 1 - alpha
    elif btype == "LP":
        b0 = (1 - cw) / 2
        b1 = 1 - cw
        b2 = (1 - cw) / 2
        a0, a1, a2 = 1 + alpha, -2 * cw, 1 - alpha
    else:  # HSC
        s = 2 * math.sqrt(A) * alpha
        b0 = A * ((A + 1) + (A - 1) * cw + s)
        b1 = -2 * A * ((A - 1) + (A + 1) * cw)
        b2 = A * ((A + 1) + (A - 1) * cw - s)
        a0 = (A + 1) - (A - 1) * cw + s
        a1 = 2 * ((A - 1) - (A + 1) * cw)
        a2 = (A + 1) - (A - 1) * cw - s
    return (b0 / a0, b1 / a0, b2 / a0, 1.0, a1 / a0, a2 / a0)


def mag_db(c, f, fs=FS):
    b0, b1, b2, a0, a1, a2 = c
    w = 2 * math.pi * f / fs
    z1, z2 = cmath.exp(-1j * w), cmath.exp(-2j * w)
    H = (b0 + b1 * z1 + b2 * z2) / (a0 + a1 * z1 + a2 * z2)
    m = abs(H)
    return 20 * math.log10(m) if m > 1e-12 else -120.0


def response_db(preamp, bands, freqs):
    coeffs = [biquad(b.type, b.freq, b.gain, b.q) for b in bands if b.enabled]
    out = []
    for f in freqs:
        s = preamp
        for c in coeffs:
            s += mag_db(c, f)
        out.append(s)
    return out


# ---- headroom / clip estimate (ROADMAP Task 2, tier 1) ---------------------
def curve_max_db(preamp, bands, n=240, fmin=FMIN, fmax=FMAX):
    """Max of the total EQ curve (preamp + enabled bands) in dB: the largest
    gain the chain applies to any single frequency. Evaluated on an n-point
    log grid PLUS every enabled band's center frequency, so narrow (high-Q)
    peaks cannot fall between grid points. Pinned to the scipy reference
    (perdeviceeq.pde_audit.chain_curve) by tests/test_headroom_bound.py."""
    la, lb = math.log10(fmin), math.log10(fmax)
    freqs = [10 ** (la + (lb - la) * i / (n - 1)) for i in range(n)]
    freqs += [min(max(b.freq, fmin), fmax) for b in bands if b.enabled]
    return max(response_db(preamp, bands, freqs))


def headroom_bound_db(preamp, bands, monitor_peak_db=0.0):
    """Instant post-EQ peak estimate in dBFS (ROADMAP Task 2, tier 1):
    monitor_peak + max(total EQ curve). Until the live capture meter
    (tier 2) exists the monitor peak is taken as 0 dBFS -- legal full-scale
    content, the worst case. Two honest limitations, both handled by the
    capture tiers: inputs can overshoot FS on their own (hot lossy masters
    after any resampler -- the hot_master fixture sits at pre-EQ +1.7 dBFS),
    and a broadband crest can in principle recombine above this sine-gain
    figure; on the fixtures the estimate stays conservative (see tests)."""
    return monitor_peak_db + curve_max_db(preamp, bands)


# ============================ REW / AutoEQ text ============================
_RE_PREAMP = re.compile(r"Preamp:\s*(-?\d+(?:\.\d+)?)\s*dB", re.I)
_RE_FILTER = re.compile(
    r"Filter\s+\d+:\s*ON\s+(PK|LS|LSC|HS|HSC|LP|HP|LPQ|HPQ)\s+"
    r"Fc\s+(\d+(?:\.\d+)?)\s*Hz"
    r"(?:\s+Gain\s+(-?\d+(?:\.\d+)?)\s*dB)?"
    r"(?:\s+Q\s+(\d+(?:\.\d+)?))?", re.I)


def parse_autoeq(text):
    preamp = 0.0
    m = _RE_PREAMP.search(text)
    if m:
        preamp = float(m.group(1))
    bands = []
    for mt in _RE_FILTER.finditer(text):
        kind = mt.group(1).upper()
        fc = float(mt.group(2))
        gain = float(mt.group(3)) if mt.group(3) else 0.0
        q = float(mt.group(4)) if mt.group(4) else 1.0
        if kind in ("LS", "LSC"):
            btype = "LSC"
        elif kind in ("HS", "HSC"):
            btype = "HSC"
        else:
            btype = "PK"
        bands.append(Band(btype, fc, gain, q, True))
    return preamp, bands


def eq_text(preamp, bands):
    """REW/AutoEQ ParametricEQ text (re-importable). Only enabled bands."""
    lines = ["Preamp: %.1f dB" % preamp]
    i = 1
    for b in bands:
        if not b.enabled:
            continue
        lines.append("Filter %d: ON %s Fc %g Hz Gain %.2f dB Q %.4f"
                     % (i, b.type, b.freq, b.gain, b.q))
        i += 1
    return "\n".join(lines) + "\n"
