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


# --- one column at a time ----------------------------------------------

class Route(dict):
    """An input route that publishes its channels, as a real one does."""

    def __init__(self, volumes):
        super().__init__(name="mic", active=True, hw_volume=True,
                         volume_base=0.279892, index=1, device_id=54,
                         card_device=0, channel_volumes=list(volumes))


def test_a_route_is_written_whole(monkeypatch):
    """A Route accepts nothing less than the full list, and only the
    caller knows what every column is meant to be -- carrying the
    others across from the graph's snapshot is what let a second drag
    hand the card the first column's pre-drag value."""
    sent = {}

    def run(cmd):
        sent["cmd"] = cmd
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(pw_backend, "_run", run)
    out = pw_backend.set_route_volumes(Route([0.287401, 0.535773]),
                                       [0.9, 0.5])
    # LINEAR on the wire, cubic in hand
    assert out[0] == pytest.approx(0.9 ** 3)
    assert out[1] == pytest.approx(0.5 ** 3)
    body = sent["cmd"][-1]
    assert "channelVolumes" in body and "index: 1" in body
    assert "0.729000" in body and "0.125000" in body


def test_an_empty_list_is_refused(monkeypatch):
    monkeypatch.setattr(pw_backend, "_run",
                        lambda c: (_ for _ in ()).throw(
                            AssertionError("must not run")))
    with pytest.raises(ValueError):
        pw_backend.set_route_volumes(Route([0.5, 0.5]), [])


def test_channel_gains_are_read_apart_not_averaged():
    node = {"info": {"params": {"Props": [
        {"channelVolumes": [0.287401, 0.535773]}]}}}
    both = pw_backend.channel_gains_of_node(node)
    assert both[0] == pytest.approx(0.287401 ** (1 / 3.0))
    assert both[1] == pytest.approx(0.535773 ** (1 / 3.0))
    # the folded reading really does sit between them, which is the
    # number a per-column caller must not use
    folded, _ = pw_backend.gain_of_node(node)
    assert both[0] < folded < both[1]


def test_the_ladder_moves_only_its_own_column(card, monkeypatch):
    written = []
    card.src["routes"] = [Route([0.5, 0.5])]

    def set_route_volumes(route, cubics):
        for i, c in enumerate(cubics):
            if abs(c ** 3 - route["channel_volumes"][i]) > 1e-9:
                written.append((i, c))
        route["channel_volumes"] = [c ** 3 for c in cubics]
        card.cubic = cubics[1]
        return route["channel_volumes"]

    monkeypatch.setattr(pw_backend, "set_route_volumes", set_route_volumes)
    monkeypatch.setattr(pw_backend, "set_gain",
                        lambda *a: (_ for _ in ()).throw(
                            AssertionError("the whole node must not move")))
    # the ladder reads the ROUTE, the same object it writes -- the
    # node's Props do not follow a Route write
    monkeypatch.setattr(pw_backend, "route_channel_cubic",
                        lambda r, ch: r["channel_volumes"][ch] ** (1 / 3.0))
    monkeypatch.setattr(pw_backend, "route_hw_position",
                        lambda r, ch: r["channel_volumes"][ch])
    knee_run.ladder(card.src, 1, lo_db=-40.0, hi_db=0.0, steps=4,
                    dwell=0.05, refine=False)
    assert written, "the ladder wrote nothing"
    assert all(ch == 1 for ch, _ in written)
    # and it put ITS channel back, leaving the other where it was
    assert card.src["routes"][0]["channel_volumes"][0] == pytest.approx(0.5)


# --- the walk starts where the card does --------------------------------

def test_the_card_is_asked_where_its_control_stops(monkeypatch):
    """channelVolumes is the request, softVolumes is what the graph makes
    up on top, and their quotient is the hardware. Below the floor the
    request keeps falling while the quotient stands still -- measured on
    a CM106 as 0.04436 from five different requests."""
    floor = 0.04436
    route = Route([0.5, 0.5])

    def set_route_volumes(r, cubics):
        r["channel_volumes"] = [max(0.0, min(1.0, c)) ** 3 for c in cubics]
        return r["channel_volumes"]

    monkeypatch.setattr(pw_backend, "set_route_volumes", set_route_volumes)
    monkeypatch.setattr(pw_backend, "route_hw_position",
                        lambda r, ch: max(floor, r["channel_volumes"][ch]))
    w = knee_run.Walk.__new__(knee_run.Walk)
    w.route, w.column, w.settle = route, 0, 0.0
    w.per_channel, w.others, w.before = True, [0.5, 0.5], 0.5
    assert w.hardware_floor_db() == pytest.approx(-27.06, abs=0.05)


def test_a_card_that_does_not_stop_gives_no_floor(monkeypatch):
    route = Route([0.5, 0.5])
    monkeypatch.setattr(pw_backend, "set_route_volumes",
                        lambda r, c: r.__setitem__(
                            "channel_volumes", [x ** 3 for x in c]))
    monkeypatch.setattr(pw_backend, "route_hw_position",
                        lambda r, ch: r["channel_volumes"][ch])
    w = knee_run.Walk.__new__(knee_run.Walk)
    w.route, w.column, w.settle = route, 0, 0.0
    w.per_channel, w.others, w.before = True, [0.5, 0.5], 0.5
    # it took the probe value exactly, so it has not shown a floor: it
    # may simply go lower than the probe
    assert w.hardware_floor_db() is None


def test_a_route_without_channels_has_no_floor_to_ask_for():
    w = knee_run.Walk.__new__(knee_run.Walk)
    w.route, w.column, w.settle = {}, 0, 0.0
    w.per_channel, w.others, w.before = False, [], 0.5
    assert w.hardware_floor_db() is None


def test_starting_at_the_floor_spends_no_rung_below_it():
    """A walk from -60 on a card whose control stops at -27 puts five of
    its ten coarse rungs where the card is standing still and the graph
    is multiplying -- which is exactly the stretch of slope one the
    reading has twice had to be taught to discount."""
    floor = -27.06
    wasted = [p for p in knee.plan(-60.0, 0.0, 10) if p < floor]
    assert len(wasted) == 5
    assert all(p >= floor - 1e-9 for p in knee.plan(floor, 0.0, 10))


def test_the_floor_probe_asks_for_something_the_card_can_hold(monkeypatch):
    """Zero is MUTE, not a floor. The first version asked for zero, got
    zero back, and concluded every card it ever ran on had no floor."""
    route = Route([0.125, 0.125])
    monkeypatch.setattr(pw_backend, "set_route_volumes",
                        lambda r, c: r.__setitem__(
                            "channel_volumes", [x ** 3 for x in c]))
    monkeypatch.setattr(pw_backend, "route_hw_position",
                        lambda r, ch: max(0.04436, r["channel_volumes"][ch]))
    w = knee_run.Walk.__new__(knee_run.Walk)
    w.route, w.column, w.settle = route, 0, 0.0
    w.per_channel, w.others, w.before = True, [0.5, 0.5], 0.5
    assert knee_run.FLOOR_PROBE > 0.0
    assert w.hardware_floor_db() == pytest.approx(-27.06, abs=0.05)


def test_the_floor_probe_puts_the_column_back(monkeypatch):
    """The silence check runs next. Left at the probe value it measured
    the probe rather than the room -- and read digital zero, which is
    how a rig with a 33 dB DC offset came back reporting -200 dBFS."""
    route = Route([0.125, 0.125])
    monkeypatch.setattr(pw_backend, "set_route_volumes",
                        lambda r, c: r.__setitem__(
                            "channel_volumes", [x ** 3 for x in c]))
    monkeypatch.setattr(pw_backend, "route_hw_position",
                        lambda r, ch: max(0.04436, r["channel_volumes"][ch]))
    w = knee_run.Walk.__new__(knee_run.Walk)
    w.route, w.column, w.settle = route, 0, 0.0
    w.per_channel, w.others, w.before = True, [0.5, 0.5], 0.732
    w.hardware_floor_db()
    assert route["channel_volumes"][0] == pytest.approx(0.732 ** 3)
    assert route["channel_volumes"][1] == pytest.approx(0.5 ** 3)


def test_the_floor_probe_can_hand_straight_over_to_the_walk(monkeypatch):
    """Restoring between the probe and the first rung is a round trip
    the card can be watched making: down to the floor, back up to where
    it was, straight back down. The caller that walks immediately says
    so, and the route watch showed the trip before anyone thought to
    look for it."""
    route = Route([0.125, 0.125])
    seen = []

    def set_route_volumes(r, cubics):
        r["channel_volumes"] = [x ** 3 for x in cubics]
        seen.append(round(cubics[0], 4))
        return r["channel_volumes"]

    monkeypatch.setattr(pw_backend, "set_route_volumes", set_route_volumes)
    monkeypatch.setattr(pw_backend, "route_hw_position",
                        lambda r, ch: max(0.04436, r["channel_volumes"][ch]))
    w = knee_run.Walk.__new__(knee_run.Walk)
    w.route, w.column, w.settle = route, 0, 0.0
    w.per_channel, w.others, w.before = True, [0.5, 0.5], 0.732
    w.hardware_floor_db(restore=False)
    assert seen == [knee_run.FLOOR_PROBE], "it went somewhere else as well"
    seen.clear()
    w.hardware_floor_db(restore=True)
    assert seen == [knee_run.FLOOR_PROBE, 0.732], "it did not come back"
