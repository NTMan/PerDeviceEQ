"""The mechanical HIG floor: every rule gets a violating tree
and a conforming one, plus the field regressions that minted the
rules in the first place -- the band-actions row as built after
round three must pass clean."""

from perdeviceeq import hig


def _btn(label=None, css=(), icon_only=False, tooltip=None,
         in_bar=False):
    return {"class": "GtkButton",
            "props": {"label": label, "css": list(css),
                      "icon_only": icon_only, "tooltip": tooltip,
                      "in_bar": in_bar},
            "children": []}


def _box(children, css=(), halign="fill", spacing=6,
         in_bar=False):
    return {"class": "GtkBox",
            "props": {"css": list(css), "halign": halign,
                      "spacing": spacing, "in_bar": in_bar},
            "children": list(children)}


def _rules(findings):
    return sorted({f["rule"] for f in findings})


def test_h1_linked_holds_facets_of_one_control():
    mixed = _box([_btn("Undo"), {"class": "GtkToggleButton",
                                 "props": {}, "children": []}],
                 css=["linked"])
    assert _rules(hig.lint(mixed)) == ["H1"]
    lone = _box([_btn("Undo")], css=["linked"])
    assert _rules(hig.lint(lone)) == ["H1"]
    pair = _box([_btn("Undo"), _btn("Redo")], css=["linked"],
                spacing=0)
    assert hig.lint(pair) == []


def test_h1_a_menu_button_wearing_a_word_is_the_value():
    """The device picker became a GtkMenuButton when it grew a second
    level, and its pin did not change meaning by a hair. A menu
    button wearing a GLYPH is still an action clicker."""
    tog = {"class": "GtkToggleButton", "props": {}, "children": []}
    picker = {"class": "GtkMenuButton",
              "props": {"label": "Adam D3V"}, "children": []}
    assert hig.lint(_box([picker, tog], css=["linked"],
                         spacing=0)) == []
    plus = {"class": "GtkMenuButton",
            "props": {"icon_only": True, "tooltip": "Add a target"},
            "children": []}
    assert _rules(hig.lint(_box([plus, tog], css=["linked"],
                                spacing=0))) == ["H1"]


def test_h1_one_value_control_with_its_toggles_is_an_instrument():
    spin = {"class": "GtkSpinButton", "props": {}, "children": []}
    tog = {"class": "GtkToggleButton", "props": {}, "children": []}
    inst = _box([spin, tog], css=["linked"], spacing=0)
    assert hig.lint(inst) == []
    dd = {"class": "GtkDropDown", "props": {}, "children": []}
    picker = _box([dd, tog], css=["linked"], spacing=0)
    assert hig.lint(picker) == []
    lbl = {"class": "GtkLabel", "props": {}, "children": []}
    trio = _box([dd, lbl, tog], css=["linked"], spacing=0)
    assert _rules(hig.lint(trio)) == ["H1"]
    action = _box([_btn("Apply"), tog], css=["linked"], spacing=0)
    assert _rules(hig.lint(action)) == ["H1"]


def test_h1_the_letter_a_linked_box_has_no_spacing():
    spaced = _box([_btn("Undo"), _btn("Redo")], css=["linked"],
                  spacing=6)
    got = hig.lint(spaced)
    assert _rules(got) == ["H1"]
    assert "no spacing" in got[0]["msg"]


def test_h8_labels_dress_from_the_type_scale():
    def lab(css):
        return {"class": "GtkLabel",
                "props": {"css": list(css)}, "children": []}
    for good in ((), ("heading",), ("caption", "dim-label"),
                 ("title-2",), ("error", "caption"),
                 ("chantab-count", "caption"),
                 ("chantab-count", "done"),
                 ("title",), ("subtitle",), ("dimmed", "title"),
                 ("body", "description", "dimmed"), ("h4",),
                 ("bottom",)):
        assert hig.lint(lab(good)) == []
    got = hig.lint(lab(("big-text",)))
    assert _rules(got) == ["H8"]
    assert "big-text" in got[0]["msg"]
    got = hig.lint(lab(("heading", "hero")))
    assert _rules(got) == ["H8"]
    assert "hero" in got[0]["msg"]


def test_h2_a_button_group_is_never_stretched():
    """Fill hands each button an equal share of the width, so two
    buttons become two half-window slabs and stop looking like
    buttons. That is the fault."""
    stretched = _box([_btn("Add"), _btn("Replace")], halign="fill")
    got = hig.lint(stretched)
    assert _rules(got) == ["H2"]
    assert "stretched" in got[0]["msg"]
    for place in ("start", "end"):
        placed = _box([_btn("Add"), _btn("Replace")], halign=place)
        assert hig.lint(placed) == [], place
    # centred WORDS are still an action row adrift
    assert _rules(hig.lint(_box([_btn("Add"), _btn("Replace")],
                                halign="center"))) == ["H2"]
    # a bar supplies its own geometry -- no finding there
    in_bar = _box([_btn("A", in_bar=True),
                   _btn("B", in_bar=True)],
                  halign="center", in_bar=True)
    assert hig.lint(in_bar) == []


def test_h2_centre_is_a_place_a_page_action_may_take():
    """His transport -- play and stop under the measurement card --
    is centred on purpose: that is where a hand comes back to. The
    rule used to call centre floating and it was wrong to."""
    play = _btn(None, icon_only=True, tooltip="Measure Front Left")
    stop = _btn(None, icon_only=True, tooltip="Stop the sweep")
    assert hig.lint(_box([play, stop], halign="center")) == []
    # and the same icons stretched are still wrong
    assert _rules(hig.lint(_box([play, stop],
                                halign="fill"))) == ["H2"]


def test_h3_flat_needs_a_structured_container():
    long_flat = _btn("Replace bands from file\u2026",
                     css=["flat"])
    got = hig.lint(long_flat)
    assert _rules(got) == ["H3"]
    assert "Calculator" in got[0]["fix"]
    short_flat = _btn("Add band", css=["flat"])
    assert hig.lint(short_flat) == []
    in_bar = _btn("Replace bands from file\u2026", css=["flat"],
                  in_bar=True)
    assert hig.lint(in_bar) == []
    raised = _btn("Replace bands from file\u2026")
    assert hig.lint(raised) == []


def test_h4_icon_only_button_describes_itself():
    mute = _btn(icon_only=True)
    assert _rules(hig.lint(mute)) == ["H4"]
    spoken = _btn(icon_only=True, tooltip="Export this profile")
    assert hig.lint(spoken) == []


def test_h5_spacing_sits_on_the_grid():
    off = _box([], spacing=7)
    got = hig.lint(off)
    assert _rules(got) == ["H5"]
    on = _box([], spacing=12)
    assert hig.lint(on) == []
    margins = {"class": "GtkBox",
               "props": {"margins": [6, 6, 5, 6]},
               "children": []}
    assert _rules(hig.lint(margins)) == ["H5"]


def test_h6_dialog_buttons_name_the_action():
    dlg = {"class": "AdwAlertDialog",
           "props": {"responses": [{"id": "y", "label": "Yes"},
                                   {"id": "n", "label": "No"}]},
           "children": []}
    got = hig.lint(dlg)
    assert len(got) == 2 and _rules(got) == ["H6"]
    verbs = {"class": "AdwAlertDialog",
             "props": {"responses": [
                 {"id": "cancel", "label": "Cancel"},
                 {"id": "import", "label": "Import"}]},
             "children": []}
    assert hig.lint(verbs) == []


def test_field_regression_band_actions_row_passes():
    """The row as built after design round three: raised
    icon+label buttons, one box, halign start, spacing 6 --
    the shape that minted H2 and H3 must itself pass clean."""
    row = _box([_btn("Add band"),
                _btn("Replace bands from file\u2026",
                     tooltip="Replace this channel's bands "
                             "from a parametric-EQ text file")],
               halign="start", spacing=6)
    assert hig.lint(row) == []


def test_field_regression_round_one_would_have_fired():
    """Round one as it shipped: two long flat labels in a
    grid-strand box mid-card. The floor must catch exactly the
    two mistakes the human eye caught."""
    row = _box([_btn("Add band", css=["flat"]),
                _btn("Replace bands from file\u2026",
                     css=["flat"])],
               halign="center", spacing=6)
    assert _rules(hig.lint(row)) == ["H2", "H3"]


def test_unknown_props_never_accuse():
    bare = {"class": "GtkButton", "props": {}, "children": []}
    assert hig.lint(bare) == []


def test_report_carries_rule_path_and_fix():
    got = hig.lint(_btn(icon_only=True))
    lines = hig.report(got)
    assert lines[0].startswith("H4 GtkButton")
    assert lines[1].startswith("   fix: ")


def _label(text, css=()):
    return {"class": "GtkLabel",
            "props": {"label": text, "css": list(css)},
            "children": []}


def test_h7_prose_needs_a_house():
    """Minted in the gone-state round: a free paragraph under
    the measuring card lost to the banner; the rule keeps loose
    prose from creeping back into bare columns."""
    prose = ("Its channel configuration changed, or it was "
             "unplugged.")
    loose = _box([_label(prose)])
    assert _rules(hig.lint(loose)) == ["H7"]
    housed = _box([_label(prose)], css=["card"])
    assert hig.lint(housed) == []
    listed = _box([_label(prose)], css=["boxed-list"])
    assert hig.lint(listed) == []
    caption = _box([_label("SNR 43.1 dB")])
    assert hig.lint(caption) == []
    heading = _box([_label(prose, css=["heading"])])
    assert hig.lint(heading) == []
    in_bar = _box([_label(prose)], in_bar=True)
    assert hig.lint(in_bar) == []


def _row(title, subtitle=None):
    # the described tree nests reads under "props" -- the rule
    # must look where describe() writes (its first live run
    # was blind: flat synthetic tests hid the nesting)
    p = {"title": title}
    if subtitle is not None:
        p["subtitle"] = subtitle
    return {"class": "AdwActionRow", "props": p,
            "children": []}


def test_h10_flags_the_overdressed_row():
    lb = {"class": "GtkListBox", "children": [
        _row("L calibration", "\u2713 a.txt"),
        _row("R calibration", "\u2713 b.txt"),
        _row("X calibration",
             "\u2713 c.txt\nrecorded with E.A.R.S Gain 0dB"
             "\nacross 6 profiles and then some more words"),
    ]}
    got = hig.lint({"class": "W", "children": [lb]})
    assert [f["rule"] for f in got] == ["H10"]


def test_h10_uniformly_verbose_archive_is_clean():
    long = ("6 takes \u00b7 E.A.R.S Gain 0dB Analog Stereo "
            "with a good many words of honest archive prose")
    lb = {"class": "GtkListBox", "children": [
        _row("R_RAW_8603052.txt", long),
        _row("7163423.txt", long),
        _row("ECM8000.txt", long),
    ]}
    assert hig.lint({"class": "W", "children": [lb]}) == []


def test_h10_needs_a_population():
    lb = {"class": "GtkListBox", "children": [
        _row("a", "x"),
        _row("b", "x\ny\nz and plenty of extra characters "
                  "to be sure of the mass outlier too"),
    ]}
    assert hig.lint({"class": "W", "children": [lb]}) == []


def test_only_the_application_window_has_an_action_map():
    """Adw.Window is not an ApplicationWindow and implements no
    Gio.ActionMap, so self.add_action() on the measurement window is an
    AttributeError at construction -- which no test reaches, because
    none of them builds a window. Read from the source instead: the
    class that calls add_action must be the one that inherits a map."""
    import ast
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent / "perdeviceeq"
    for path in sorted(root.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for cls in [n for n in ast.walk(tree)
                    if isinstance(n, ast.ClassDef)]:
            bases = {ast.unparse(b) for b in cls.bases}
            if "Adw.ApplicationWindow" in bases:
                continue
            calls = [n for n in ast.walk(cls)
                     if isinstance(n, ast.Call)
                     and isinstance(n.func, ast.Attribute)
                     and n.func.attr == "add_action"
                     and isinstance(n.func.value, ast.Name)
                     and n.func.value.id == "self"]
            assert not calls, (
                "%s(%s) calls self.add_action but inherits no action "
                "map -- insert_action_group with a prefix of its own"
                % (cls.name, ", ".join(sorted(bases))))


def test_no_window_attribute_is_used_before_it_is_set():
    """An attribute set in ONE method only must be set there before it
    is read, or the first call raises AttributeError -- and no test
    reaches a window constructor, so it is read out of the source.

    "One method only" is the whole precision. An attribute the
    constructor's helpers also set may well exist by the time a later
    method reads it, and flagging that is noise; an attribute nothing
    else ever writes cannot.

    This complements the audit that asks whether an attribute is ever
    assigned at all. Both were needed on the same day: the first caught
    a block deleted with the code that created the faders, the second
    an add() written one line above the widget it added."""
    import ast
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent / "perdeviceeq"
    faults = []
    for path in sorted(root.glob("*window*.py")) + [root / "gui.py"]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for cls in [n for n in ast.walk(tree)
                    if isinstance(n, ast.ClassDef)]:
            methods = {n.name: n for n in cls.body
                       if isinstance(n, ast.FunctionDef)}
            where = {}
            for name, fn in methods.items():
                for n in ast.walk(fn):
                    if (isinstance(n, ast.Attribute)
                            and isinstance(n.value, ast.Name)
                            and n.value.id == "self"
                            and isinstance(n.ctx, ast.Store)):
                        where.setdefault(n.attr, set()).add(name)
            for name, fn in methods.items():
                stores, loads = {}, {}
                for n in ast.walk(fn):
                    if not (isinstance(n, ast.Attribute)
                            and isinstance(n.value, ast.Name)
                            and n.value.id == "self"):
                        continue
                    d = stores if isinstance(n.ctx, ast.Store) else loads
                    d.setdefault(n.attr, n.lineno)
                for attr, line in stores.items():
                    if len(where.get(attr, ())) != 1 or attr in methods:
                        continue
                    seen = loads.get(attr)
                    if seen is not None and seen < line:
                        faults.append(
                            "%s.%s.%s: self.%s read at line %d, set at %d"
                            % (path.name, cls.name, name, attr, seen, line))
    assert not faults, "; ".join(faults)


def test_a_wrapper_row_does_not_dress_the_list():
    """H10 asks whether a row hoards text against its sisters. A bare
    AdwPreferencesRow holding a slider or a tab strip carries no text
    at all -- but its title is the empty STRING, not None, so it used
    to count as a sister wearing nothing, drag the mode to zero, and
    make every honestly subtitled row look overdressed."""
    node = {"class": "GtkListBox", "children": [
        {"class": "AdwPreferencesRow", "props": {"title": ""}},
        {"class": "AdwPreferencesRow", "props": {"title": ""}},
        {"class": "AdwComboRow", "props": {"title": "Output",
                                           "subtitle": "Where the "
                                                       "sweep is played"}},
        {"class": "AdwComboRow", "props": {"title": "Measurement mic",
                                           "subtitle": "Input the sweep "
                                                       "is captured on"}},
        {"class": "AdwActionRow", "props": {"title": "Recorded",
                                            "subtitle": "0 calibrations"}},
    ]}
    out = []
    hig._findings_h10(node, "p", out)
    assert out == [], [o["msg"] for o in out]


def test_a_row_that_really_does_hoard_is_still_caught():
    node = {"class": "GtkListBox", "children": [
        {"class": "AdwActionRow", "props": {"title": "One"}},
        {"class": "AdwActionRow", "props": {"title": "Two"}},
        {"class": "AdwActionRow", "props": {"title": "Three"}},
        {"class": "AdwActionRow", "props": {"title": "Four",
                                            "subtitle": "and a whole "
                                                        "sentence"}},
    ]}
    out = []
    hig._findings_h10(node, "p", out)
    assert len(out) == 1 and "overdresses" in out[0]["msg"]


def test_the_toolkits_own_menu_is_not_ours_to_align():
    """A GtkPopoverMenu is built from a GMenuModel; its section boxes
    are GTK's, and no application can set halign on one. Nineteen of
    twenty-four findings in one field run came from inside one."""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent
           / "tools" / "hig_audit.py").read_text(encoding="utf-8")
    assert '"GtkPopoverMenu")' in src or '"GtkPopoverMenu",' in src
