"""One channel row, for both windows.

A tab is a PAIR: a channel of the card and a channel of the profile.
Which of the two names a window leads with is not a setting -- it falls
out of the data. The main window's keys are SINK channels, because it
edits the correction an OUTPUT plays; the measurement window's keys are
TARGETS, because it measures a side of a transducer. Either way the KEY
leads and its partner sits under it in small type, and only when the two
names differ, since FL over FL is noise.

The row also carries a STATUS BUBBLE per tab. That is the ring's own
invention and the one thing worth keeping from it: how many clean takes
a channel has, in three colours. The main window has no takes and simply
never sets it.

Nothing here knows what a pair means, what a take is, or which window it
is in. It draws a row, reports clicks, and shows what it is told.
"""

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk          # noqa: E402

_CSS = """
.chantab-count { padding: 0px 5px; border-radius: 9px;
                 background-color: alpha(currentColor, 0.15); }
.chantab-count.done { background-color: @success_bg_color;
                      color: @success_fg_color; }
.chantab-count.warn { background-color: @warning_bg_color;
                      color: @warning_fg_color; }
.chantab-count.bad  { background-color: @error_bg_color;
                      color: @error_fg_color; }
"""

_TONES = ("done", "warn", "bad")
_css_done = False


def _install_css():
    global _css_done
    if _css_done:
        return
    _css_done = True
    from gi.repository import Gdk
    css = Gtk.CssProvider()
    if hasattr(css, "load_from_string"):
        css.load_from_string(_CSS)
    else:
        css.load_from_data(_CSS.encode())
    display = Gdk.Display.get_default()
    if display is not None:
        Gtk.StyleContext.add_provider_for_display(
            display, css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)


class ChannelTabs:
    """The row of channel tabs inside `box`.

    on_pick(key) is called when a tab is chosen by hand -- never while
    the row is being rebuilt or moved from code, so a caller can rely on
    it meaning "a person did this".
    """

    def __init__(self, box, on_pick):
        _install_css()
        self.box = box
        self._on_pick = on_pick
        self._buttons = {}
        self._counts = {}
        self._keys = []
        self._selected = None
        self._quiet = False

    # ---- building ------------------------------------------------------
    def rebuild(self, keys, partner=None, selected=None):
        """Draw one tab per key. `partner` maps a key to the other half
        of its pair; a partner equal to the key, or missing, prints
        nothing."""
        child = self.box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.box.remove(child)
            child = nxt
        self._buttons, self._counts = {}, {}
        self._keys = list(keys or [])
        want = selected if selected in self._keys else (
            self._keys[0] if self._keys else None)
        first = None
        self._quiet = True
        try:
            for key in self._keys:
                btn = Gtk.ToggleButton()
                body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                               spacing=0)
                body.set_valign(Gtk.Align.CENTER)
                lbl = Gtk.Label(label=key)
                body.append(lbl)
                mate = (partner or {}).get(key)
                if mate and mate != key:
                    small = Gtk.Label()
                    small.set_markup(
                        "<small>%s</small>"
                        % GLib.markup_escape_text(str(mate)))
                    small.add_css_class("dim-label")
                    body.append(small)
                count = Gtk.Label()
                count.add_css_class("caption")
                count.add_css_class("chantab-count")
                count.set_visible(False)
                body.append(count)
                btn.set_child(body)
                btn.set_active(key == want)
                btn.connect("toggled", self._on_toggled, key)
                if first is None:
                    first = btn
                else:
                    btn.set_group(first)
                self.box.append(btn)
                self._buttons[key] = btn
                self._counts[key] = count
            if len(self._keys) == 1:
                first.set_can_target(False)   # a lone tab is not a control
            # the style groups SIBLINGS, so one child in a linked box is
            # costume rather than clothing (the CI floor called this on a
            # single-channel profile)
            if len(self._keys) > 1:
                self.box.add_css_class("linked")
            else:
                self.box.remove_css_class("linked")
        finally:
            self._quiet = False
        self._selected = want
        return want

    # ---- state ---------------------------------------------------------
    def select(self, key):
        """Move the selection from code. Silent: on_pick is for hands."""
        if key not in self._buttons or key == self._selected:
            self._selected = key if key in self._buttons else self._selected
            return
        self._quiet = True
        try:
            self._buttons[key].set_active(True)
        finally:
            self._quiet = False
        self._selected = key

    @property
    def selected(self):
        return self._selected

    @property
    def keys(self):
        return list(self._keys)

    def widget(self, key):
        return self._buttons.get(key)

    def set_sensitive(self, on):
        for btn in self._buttons.values():
            btn.set_sensitive(bool(on))

    def set_status(self, key, text=None, tone=None):
        """The bubble on one tab: `text` or nothing, in one of three
        tones. An unknown tone is drawn plain rather than refused --
        a caller's typo must not cost the count."""
        lbl = self._counts.get(key)
        if lbl is None:
            return
        for t in _TONES:
            lbl.remove_css_class(t)
        if not text:
            lbl.set_visible(False)
            return
        if tone in _TONES:
            lbl.add_css_class(tone)
        lbl.set_text(str(text))
        lbl.set_visible(True)

    def clear_status(self):
        for key in self._counts:
            self.set_status(key, None)

    def set_tooltip(self, key, text):
        btn = self._buttons.get(key)
        if btn is not None:
            btn.set_tooltip_text(text)

    # ---- plumbing ------------------------------------------------------
    def _on_toggled(self, btn, key):
        if self._quiet or not btn.get_active():
            return
        self._selected = key
        if self._on_pick is not None:
            self._on_pick(key)
