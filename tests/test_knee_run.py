"""Walking a gain ladder, against a card that exists only here.

knee.py decides and touches no hardware; knee_run.py touches hardware
and decides nothing. That seam is what lets a whole search run in a
test: a fake card whose noise floor is two noises adding in power, a
fake meter that reports it, and a gain control that is a dictionary.
"""

import math

import pytest

from perdeviceeq import inmeter, knee, knee_run, pw_backend


class FakeCard:
    """Floor + input noise, with a volume that behaves like PipeWire's:
    cubic, and never above one."""

    def __init__(self, floor_db=-118.0, mic_at_zero_db=-100.0):
        self.floor = floor_db
        self.mic = mic_at_zero_db
        self.cubic = 0.5
        self.writes = []

    def level(self):
        g = knee_run.cubic_to_db(self.cubic)
        g = -200.0 if g is None else g
        return 10.0 * math.log10(10 ** (self.floor / 10.0)
                                 + 10 ** ((self.mic + g) / 10.0))

    @property
    def knee_db(self):
        return self.floor - self.mic


@pytest.fixture
def card(monkeypatch):
    c = FakeCard()

    class Meter:
        def __init__(self, *a, **k):
            self.dead = False

        def start(self, node, channels):
            pass

        def alive(self):
            return not self.dead

        def stop(self):
            pass

        def latest_rms(self):
            return [c.level()] * 2

        def latest(self):
            return [c.level() + 11.0] * 2

        def latest_dc(self):
            return [-200.0] * 2

    src = {"id": 9, "name": "fake", "desc": "Fake",
           "channels": ["FL", "FR"], "gain": (c.cubic, "hardware"),
           "routes": [{"name": "mic", "active": True, "hw_volume": True,
                       "volume_base": 0.279892}]}

    def set_gain(node_id, cubic):
        c.writes.append(cubic)
        c.cubic = cubic

    monkeypatch.setattr(inmeter, "InputMeter", Meter)
    monkeypatch.setattr(pw_backend, "set_gain", set_gain)
    monkeypatch.setattr(pw_backend, "list_sources",
                        lambda dump=None: [dict(src,
                                                gain=(c.cubic, "hardware"))])
    c.src = src
    return c


# --- the axis ---------------------------------------------------------

def test_the_axis_agrees_with_what_pactl_prints():
    # a route's props are LINEAR and pactl shows 20*log10 of them;
    # wpctl speaks the cubic value, and linear = cubic**3
    assert knee_run.cubic_to_db(1.0) == pytest.approx(0.0)
    assert knee_run.cubic_to_db(0.5) == pytest.approx(-18.06, abs=0.01)
    linear = 0.287501                       # a real CM106 reading
    assert (20.0 * math.log10(linear)
            == pytest.approx(-10.83, abs=0.01))
    assert (knee_run.cubic_to_db(linear ** (1 / 3.0))
            == pytest.approx(-10.83, abs=0.01))


def test_the_axis_stops_at_unity_because_the_volume_does():
    assert knee_run.db_to_cubic(0.0) == 1.0
    assert knee_run.db_to_cubic(+20.0) == 1.0     # there is no above
    assert knee_run.cubic_to_db(0.0) is None


def test_unity_is_read_off_the_active_route(card):
    assert knee_run.unity_db(card.src) == pytest.approx(-11.06, abs=0.01)
    assert knee_run.unity_db({"routes": []}) is None


# --- the walk ---------------------------------------------------------

def test_a_whole_ladder_finds_the_knee_and_puts_the_gain_back(card):
    v, w = knee_run.ladder(card.src, 0, lo_db=-60.0, hi_db=0.0,
                           steps=10, dwell=0.05)
    assert v.kind == "knee"
    assert v.knee_db == pytest.approx(card.knee_db, abs=5.0)
    assert w.restored is True
    assert card.cubic == pytest.approx(0.5)       # exactly where it was
    assert len(w.rungs) >= 10
    assert all(r.blocks for r in w.rungs)


def test_a_walk_that_is_stopped_still_puts_the_gain_back(card):
    seen = {"n": 0}

    def should_stop():
        seen["n"] += 1
        return seen["n"] > 4

    v, w = knee_run.ladder(card.src, 0, lo_db=-60.0, hi_db=0.0,
                           steps=10, dwell=0.05, should_stop=should_stop)
    assert len(w.rungs) == 4
    assert w.restored is True
    assert card.cubic == pytest.approx(0.5)


def test_a_walk_that_raises_still_puts_the_gain_back(card):
    with pytest.raises(RuntimeError):
        with knee_run.Walk(card.src, 0, dwell=0.05) as w:
            w.visit(-30.0)
            raise RuntimeError("something went wrong mid-ladder")
    assert card.cubic == pytest.approx(0.5)


def test_the_caller_is_told_when_the_gain_could_not_be_put_back(
        card, monkeypatch):
    with knee_run.Walk(card.src, 0, dwell=0.05) as w:
        w.visit(-30.0)
        monkeypatch.setattr(pw_backend, "set_gain",
                            lambda *a: (_ for _ in ()).throw(OSError("no")))
    assert w.restored is False


def test_a_column_the_source_does_not_have_is_refused(card):
    with pytest.raises(ValueError):
        knee_run.Walk(card.src, 7).open()


def test_progress_is_reported_for_every_rung(card):
    seen = []
    knee_run.ladder(card.src, 0, lo_db=-60.0, hi_db=0.0, steps=6,
                    dwell=0.05, refine=False,
                    on_rung=lambda r, done, total: seen.append((done, total)))
    assert [d for d, _ in seen] == [1, 2, 3, 4, 5, 6]
    assert all(t == 6 for _, t in seen)


def test_a_rung_carries_the_gain_the_card_took(card):
    with knee_run.Walk(card.src, 0, dwell=0.05) as w:
        r = w.visit(-30.0)
    # the fake card takes exactly what it is given, so the read-back
    # matches -- what matters is that the rung's x came from the read
    assert r.raw == pytest.approx(knee_run.db_to_cubic(-30.0))
    assert r.gain_db == pytest.approx(-30.0, abs=0.01)


def test_listen_touches_no_gain(card):
    with knee_run.Walk(card.src, 0, dwell=0.05) as w:
        before = list(card.writes)
        rms, peak, dc = w.listen(0.05)
        assert card.writes == before
    assert rms is not None and peak > rms
    assert knee.noise_like(peak, rms)


# --- the width, in one place now ---------------------------------------

def test_source_width_reads_the_channel_list_not_a_count():
    assert pw_backend.source_width(
        {"channels": ["AUX%d" % i for i in range(16)], "name": "x"}) == 16
    assert pw_backend.source_width({"channels": ["MONO"], "name": "x"}) == 1


def test_source_width_falls_back_without_a_list(monkeypatch):
    monkeypatch.setattr(pw_backend, "backend",
                        lambda: (_ for _ in ()).throw(OSError("none")))
    assert pw_backend.source_width({"channels": [], "name": "x"}) == 2
