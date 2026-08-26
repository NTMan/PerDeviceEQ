#!/usr/bin/env python3
"""Read a USB device's STRING descriptors.

    python3 tools/usb_strings.py 152a:875c
    python3 tools/usb_strings.py 152a:875c 11 36

With no range it walks the table from index 1 and stops after eight
misses in a row. Give a FIRST and LAST index to read a run directly,
which is what an iChannelNames pointer names: a gap wider than eight
before the channel names would otherwise end the walk short of them.

Why this exists: macOS shows a multichannel interface's channels by
name -- Analogue 1, AUX 1, BT 1, Mobile 1, Loopback 1 -- because Core
Audio reads iChannelNames from the device's Input Terminal descriptor
and follows the consecutive string indices from there. The names live
in the DEVICE.

Linux does not surface them. ALSA has chmap, which carries channel
POSITIONS (FL, FR, RL), and no notion of an arbitrary channel name, so
snd-usb-audio has nowhere to put them and the host sees an anonymous
sixteen. lsusb -v prints the iChannelNames INDEX and stops there.

This walks the string table and prints what is actually in the card, so
a UCM configuration can quote the device rather than a screenshot of
someone else's operating system.

String descriptors are fetched with a control transfer on
/dev/bus/usb, which a logged-in session can usually do without root
(uaccess); if it cannot, it says so and sudo will. Nothing is written
to the device.

On his M62 the table reads: Playback 1..10, then Analogue 1, Analogue
2, AUX 1, AUX 2, BT 1, BT 2, Mobile 1, Mobile 2, Loopback 1..8 -- the
sixteen capture channels and ten playback channels, named, in order.
"""

import ctypes
import fcntl
import os
import re
import struct
import sys

DT_STRING = 0x03
GET_DESCRIPTOR = 0x06
DIR_IN = 0x80


class CtrlTransfer(ctypes.Structure):
    _fields_ = [("bRequestType", ctypes.c_uint8),
                ("bRequest", ctypes.c_uint8),
                ("wValue", ctypes.c_uint16),
                ("wIndex", ctypes.c_uint16),
                ("wLength", ctypes.c_uint16),
                ("timeout", ctypes.c_uint32),
                ("data", ctypes.c_void_p)]


def _ioc_control():
    """_IOWR('U', 0, struct usbdevfs_ctrltransfer), computed rather than
    copied: the size field is part of the number, so a hardcoded
    constant is a guess about this machine's padding."""
    size = ctypes.sizeof(CtrlTransfer)
    return ((2 | 1) << 30) | (size << 16) | (ord("U") << 8) | 0


def find_device(vid_pid):
    """The /dev/bus/usb path for a vendor:product pair."""
    want = vid_pid.lower().replace("0x", "")
    root = "/sys/bus/usb/devices"
    if not os.path.isdir(root):
        return None
    for entry in sorted(os.listdir(root)):
        base = os.path.join(root, entry)

        def field(n):
            with open(os.path.join(base, n)) as f:
                return f.read().strip()
        try:
            ident = "%s:%s" % (field("idVendor").lower(),
                               field("idProduct").lower())
            if ident != want:
                continue
            return "/dev/bus/usb/%03d/%03d" % (int(field("busnum")),
                                               int(field("devnum")))
        except Exception:
            continue
    return None


def get_descriptor(fd, dtype, index, langid=0):
    """One descriptor, raw. Strings and the LANGID table are the same
    request with a different index, so they share one reader instead of
    one of them being decoded through the other."""
    buf = ctypes.create_string_buffer(255)
    ctl = CtrlTransfer(DIR_IN, GET_DESCRIPTOR,
                       (dtype << 8) | index, langid,
                       len(buf), 1000, ctypes.cast(buf, ctypes.c_void_p))
    n = fcntl.ioctl(fd, _ioc_control(), ctl)
    return buf.raw[:n] if n >= 2 else b""


def get_string(fd, index, langid):
    raw = get_descriptor(fd, DT_STRING, index, langid)
    if len(raw) < 4:
        return None
    body = raw[2:min(raw[0], len(raw))]
    try:
        return body.decode("utf-16-le").rstrip("\x00").strip() or None
    except Exception:
        return None


def first_langid(fd):
    raw = get_descriptor(fd, DT_STRING, 0, 0)
    if len(raw) >= 4:
        return struct.unpack("<H", raw[2:4])[0]
    return 0x0409


def main():
    if len(sys.argv) < 2 or not re.match(r"^[0-9a-fA-F]{4}:[0-9a-fA-F]{4}$",
                                         sys.argv[1]):
        print(__doc__)
        return 2
    try:
        first = int(sys.argv[2]) if len(sys.argv) > 2 else 1
        last = int(sys.argv[3]) if len(sys.argv) > 3 else 255
    except ValueError:
        print("the range is two integers: FIRST LAST")
        return 2
    asked = len(sys.argv) > 2
    path = find_device(sys.argv[1])
    if path is None:
        print("no such device: %s" % sys.argv[1])
        print("try:  lsusb")
        return 1
    try:
        fd = os.open(path, os.O_RDWR)
    except PermissionError:
        print("%s needs root -- run with sudo" % path)
        return 1
    except OSError as e:
        print("cannot open %s: %s" % (path, e))
        return 1
    try:
        try:
            langid = first_langid(fd)
        except OSError as e:
            print("the device refused a control transfer: %s" % e)
            return 1
        print("%s -- string descriptors (langid 0x%04x)" % (path, langid))
        print()
        blank = 0
        for i in range(first, last + 1):
            try:
                s = get_string(fd, i, langid)
            except OSError:
                s = None
            if not s:
                blank += 1
                # a run of misses means the table has ended; a single
                # gap does not, because indices are not always dense.
                # An ASKED-FOR range is read to its end regardless: the
                # hand naming it knows something the walk does not
                if blank >= 8 and not asked:
                    break
                continue
            blank = 0
            print("  %3d  %s" % (i, s))
    finally:
        os.close(fd)
    print()
    print("channel names, if the device declares any, are among these --")
    print("iChannelNames in its Input Terminal descriptor points at the")
    print("first of a consecutive run.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
