"""tools/convert_v8.py: a schema 7 body becomes a schema 8 one that plays
the same. The balance leaves the bands for a number of the channel, the
fit keeps its word on whether the hand touched it, and nothing else
changes (config.SCHEMA_VERSION)."""
import importlib.util
import json
import pathlib

from perdeviceeq import eq, pdeq, profiles
from perdeviceeq.profiles import playback_sha256

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _tool():
    spec = importlib.util.spec_from_file_location(
        "convert_v8", ROOT / "tools" / "convert_v8.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cv = _tool()

PK = {"type": "PK", "freq": 2000.0, "gain": 2.0, "q": 1.0,
      "enabled": True}


def _shelf(gain, **kw):
    """The balance as schema 7 kept it: a high shelf at 0 Hz."""
    return dict({"type": "HSC", "freq": 0.0, "gain": gain, "q": 1.0,
                 "enabled": True}, **kw)


def _v7(fl, fr, fitted=True):
    """A schema 7 body as its store wrote it. Fitted: the fit stamped
    the playback body it produced, so the body is unedited."""
    body = {"id": "p7", "name": "Buds", "version": 7,
            "ch_keys": ["FL", "FR"],
            "channels": {"FL": {"bands": fl}, "FR": {"bands": fr}},
            "provenance": {"kind": "measured"},
            "fit": {"algo": "greedy", "edited": False}}
    if fitted:
        body["fit"]["output_sha256"] = playback_sha256(body)
    return body


def _store(tmp_path, monkeypatch):
    monkeypatch.setattr(profiles, "SYS_PROFILE_DIRS", [])
    monkeypatch.setattr(profiles, "USER_PROFILES_DIR", str(tmp_path))
    monkeypatch.setattr(profiles, "BINDINGS_FILE",
                        str(tmp_path / "bindings.json"))
    return profiles.ProfileStore()


def test_the_shelf_becomes_the_balance_and_plays_the_same():
    old = _v7([_shelf(-1.3), PK], [dict(PK, gain=4.0)])
    new, moved = cv.convert(old)
    assert new["version"] == 8
    assert new["channels"]["FL"] == {"bands": [PK], "balance": -1.3}
    assert new["channels"]["FR"] == {"bands": [dict(PK, gain=4.0)]}
    assert moved == ["FL balance -1.30 dB"]
    # the shelf the band was is the shelf the balance is, in its place
    a, b = eq.correction_graph(old), eq.correction_graph(new)
    assert a.replace("freq = 0, gain = -1.3, q = 1 }",
                     "freq = 0, gain = -1.3, q = 1.0 }") == b
    assert old["version"] == 7             # the body given stays as it was


def test_the_fit_keeps_its_word():
    """Unedited stays unedited: the stamp is made again over the new body.
    Edited stays edited: the old stamp is kept, and the new body is not
    what it says."""
    new, _m = cv.convert(_v7([_shelf(-1.3), PK], [PK]))
    assert new["fit"]["output_sha256"] == playback_sha256(new)
    hand = _v7([_shelf(-1.3), PK], [PK])
    hand["channels"]["FR"]["bands"] = [dict(PK, gain=5.0)]
    new, _m = cv.convert(hand)
    assert new["fit"]["output_sha256"] == hand["fit"]["output_sha256"]
    assert new["fit"]["output_sha256"] != playback_sha256(new)


def test_shelves_add_up_and_a_disabled_one_goes():
    old = _v7([_shelf(-1.0), PK, _shelf(-0.25),
               _shelf(-3.0, enabled=False)], [PK], fitted=False)
    new, moved = cv.convert(old)
    assert new["channels"]["FL"] == {"bands": [PK], "balance": -1.25}
    assert moved == ["FL balance -1.25 dB (2 shelves), "
                     "1 disabled shelf dropped"]


def test_a_shelf_that_is_no_flat_gain_stays_a_band():
    """A low shelf at 0 Hz plays nothing in PipeWire, and a high shelf at
    10 Hz is a shelf: neither is a balance."""
    low = dict(_shelf(-2.0), type="LSC")
    ten = dict(_shelf(-2.0), freq=10.0)
    new, moved = cv.convert(_v7([low, ten], [PK], fitted=False))
    assert new["channels"]["FL"] == {"bands": [low, ten]}
    assert moved == []


def test_the_body_says_where_it_came_from():
    old = _v7([_shelf(-1.3), PK], [PK])
    new, _m = cv.convert(old)
    assert new["provenance"] == {"kind": "measured",
                                 "migrated_from": pdeq.payload_sha256(old)}


def test_the_store_is_converted_in_place(tmp_path, monkeypatch, capsys):
    old = _v7([_shelf(-1.3), PK], [PK])
    (tmp_path / "p7.json").write_text(json.dumps(old, indent=2))
    eight = dict(old, id="p8", version=8)
    (tmp_path / "p8.json").write_text(json.dumps(eight))
    (tmp_path / "p6.json").write_text(json.dumps(dict(old, id="p6",
                                                      version=6)))
    monkeypatch.setattr(cv, "USER_PROFILES_DIR", str(tmp_path))
    assert cv.main([]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out == [
        "%s: schema 6, which no converter carries over; re-measure "
        "the device" % (tmp_path / "p6.json"),
        "%s: FL balance -1.30 dB; the fit stays unedited; schema 7 "
        "kept as p7.json.v7" % (tmp_path / "p7.json"),
        "%s: already schema 8" % (tmp_path / "p8.json")]
    assert json.loads((tmp_path / "p7.json.v7").read_text()) == old
    st = _store(tmp_path, monkeypatch)
    p = st.get("p7")
    assert p["channels"]["FL"] == {"bands": [PK], "balance": -1.3}
    assert playback_sha256(p) == p["fit"]["output_sha256"]
    assert "p6" not in st.profiles
    # written as the store writes it: a save changes no byte
    before = (tmp_path / "p7.json").read_text()
    st.save_user(p)
    assert (tmp_path / "p7.json").read_text() == before
    # and once is all it takes
    cv.main([])
    assert "%s: already schema 8" % (tmp_path / "p7.json") in \
        capsys.readouterr().out


def test_a_package_becomes_a_new_one_beside_it(tmp_path, capsys):
    old = _v7([_shelf(-1.3), PK], [PK])
    seven = pdeq._canon(old).decode("utf-8")   # as a 7 build packed it
    src = tmp_path / "buds.pdeq"
    src.write_text(seven, encoding="utf-8")
    assert cv.main([str(src)]) == 0
    assert src.read_text(encoding="utf-8") == seven
    text = (tmp_path / "buds.v8.pdeq").read_text(encoding="utf-8")
    prof, _sha = pdeq.pdeq_unpack(text)
    assert prof["channels"]["FL"] == {"bands": [PK], "balance": -1.3}
    assert prof["provenance"]["migrated_from"] == \
        pdeq.payload_sha256(old)
    assert text == pdeq.pdeq_pack(prof)
    assert "buds.v8.pdeq: FL balance -1.30 dB" in capsys.readouterr().out


def test_a_dry_run_writes_nothing(tmp_path, capsys):
    path = tmp_path / "p7.json"
    path.write_text(json.dumps(_v7([_shelf(-1.3), PK], [PK])))
    before = path.read_text()
    assert cv.main(["-n", str(path)]) == 0
    assert path.read_text() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["p7.json"]
    out = capsys.readouterr().out
    assert out.startswith("dry run: nothing is written\n")
    assert "FL balance -1.30 dB" in out
