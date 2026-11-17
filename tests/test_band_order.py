"""The table's order is its own; the owner gets its bands back in its.

The table sorts its rows on a load, and get_bands() used to hand that
sorted list to the owner at the first edit, an empty one included (a
click on a handle). The owner, its history and the profile took a
reading order for content: an undo then failed to give back the exact
fitted body, and the fit's hash of it -- bands in placement order --
kept the fit marked as edited, the auto-fit Auto un-pressed. Needs gi
for the view; a GTK-less sandbox skips."""
import pytest

gi = pytest.importorskip("gi")
try:
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
except ValueError as e:                        # pragma: no cover
    pytest.skip(str(e), allow_module_level=True)

from perdeviceeq import eq, profiles           # noqa: E402
from perdeviceeq.peq_view import PeqView       # noqa: E402

# a fit hands its bands over in placement order, not by frequency
PLACED = [eq.Band.from_dict(b).to_dict() for b in (
    {"type": "PK", "freq": 3475.8, "gain": -15.59, "q": 0.533},
    {"type": "PK", "freq": 822.4, "gain": 6.0, "q": 0.48},
    {"type": "HSC", "freq": 18417.2, "gain": -2.04, "q": 0.956},
    {"type": "PK", "freq": 63.0, "gain": -0.94, "q": 0.46})]


def _view():
    edits = []
    v = PeqView(lambda bands, final: edits.append(bands))
    v.set_bands(PLACED)
    return v, edits


def _freqs(bands):
    return [b["freq"] if isinstance(b, dict) else b.freq for b in bands]


def test_the_rows_sort_and_the_owner_s_order_stays():
    v, _ = _view()
    assert _freqs(v._bands) == sorted(_freqs(PLACED))
    assert v.get_bands() == PLACED


def test_a_sort_of_the_rows_writes_nothing():
    v, edits = _view()
    v._on_add()                                # a 1 kHz row, last
    v._on_sort_freq()
    assert _freqs(v._bands) == sorted(_freqs(PLACED) + [1000.0])
    assert _freqs(v.get_bands()) == _freqs(PLACED) + [1000.0]
    assert len(edits) == 1                     # the add; the sort none


def test_a_removed_band_leaves_the_rest_in_order():
    v, _ = _view()
    v._on_del(next(b for b in v._bands if b.freq == 822.4))
    assert v.get_bands() == [PLACED[0], PLACED[2], PLACED[3]]


def test_a_fitted_body_handed_back_is_no_edit_of_the_fit():
    stored = {"name": "P", "ch_keys": ["FL"],
              "channels": {"FL": {"bands": PLACED}}}
    stored["fit"] = {"output_sha256": profiles.playback_sha256(stored)}
    v, _ = _view()
    body = {"name": "P", "ch_keys": ["FL"],
            "channels": {"FL": {"bands": v.get_bands()}}}
    assert not profiles.editor_body(body, stored)["fit"].get("edited")
