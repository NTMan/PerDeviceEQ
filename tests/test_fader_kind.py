"""What a capture fader can do, decided from the route.

The four cases are his four real devices, with the numbers taken from
one pw-dump of his bench:

  M62 multichannel   device.routes = 0, no Input route at all
  OBSBOT Tiny 2      route present, route.hw-volume false
  UMIK-1             route present, hw-mute true but hw-volume FALSE --
                     miniDSP fixes the gain on purpose, it is written
                     into the product string and moving it would void
                     the calibration file
  CM106 line in      hw-volume true, volumeBase 0.279892: real travel
                     above unity, the only one worth automating
"""

from perdeviceeq import pw_backend


def route(name, active=True, hw_volume=None, volume_base=None):
    return {"name": name, "active": active,
            "hw_volume": hw_volume, "volume_base": volume_base}


def test_no_route_at_all_is_software():
    assert pw_backend.fader_kind([]) == "software"
    assert pw_backend.fader_kind(None) == "software"


def test_route_without_hardware_volume_is_software():
    assert pw_backend.fader_kind(
        [route("analog-input", hw_volume=False, volume_base=1.0)]
    ) == "software"


def test_umik_has_hardware_mute_but_no_hardware_volume():
    assert pw_backend.fader_kind(
        [route("analog-input-mic", hw_volume=False, volume_base=1.0)]
    ) == "software"


def test_unity_base_with_hardware_volume_is_an_attenuator():
    assert pw_backend.fader_kind(
        [route("mic", hw_volume=True, volume_base=1.0)]
    ) == "attenuator"


def test_base_below_unity_is_real_gain():
    assert pw_backend.fader_kind(
        [route("analog-input-linein", hw_volume=True,
               volume_base=0.279892)]
    ) == "analog"


def test_only_the_active_route_decides():
    routes = [route("mic", active=False, hw_volume=True,
                    volume_base=0.28),
              route("linein", active=True, hw_volume=False,
                    volume_base=1.0)]
    assert pw_backend.fader_kind(routes) == "software"


def test_unpublished_base_falls_back_to_the_live_reading():
    r = [route("mic", hw_volume=True)]
    assert pw_backend.fader_kind(r, (0.5, "hardware")) == "analog"
    assert pw_backend.fader_kind(r, (0.5, "software")) == "attenuator"
    # at unity the live reading has nothing to say and the cautious
    # answer is the one that cannot invent headroom
    assert pw_backend.fader_kind(r, (1.0, None)) == "attenuator"


def test_spa_info_parses_the_flat_array():
    info = [4, "port.type", "line", "card.profile.port", "1",
            "route.hw-mute", "true", "route.hw-volume", "true"]
    d = pw_backend._spa_info(info)
    assert d["route.hw-volume"] == "true"
    assert d["port.type"] == "line"
    assert pw_backend._spa_info(None) == {}
    assert pw_backend._spa_info([0]) == {}
