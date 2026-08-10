"""Debug instruments, silent unless asked for.

One voice for every file: the trace helpers live here so no
module grows its own copy. Each instrument is gated by its own
environment variable and prints to stderr, costing nothing
when off.
"""

import os
import sys


def log(*a):
    """A line that is always worth printing, on stderr.

    Not gated: six places already called this from inside an `except`
    to report something that went wrong, and the function did not
    exist -- so every one of them raised AttributeError inside its own
    error handler, turning a warning into a crash. A trace is silent
    until asked for; a failure is not a trace."""
    print("per-device-eq:", *a, file=sys.stderr)


def sweep_trace(*a):
    """The sweep-aiming trace, PDEQ_TRACE_SWEEP=1.

    A mono sweep is aimed by NAME: pw-play routes it to the channel
    whose position matches, and a name the sink does not have matches
    nothing and spreads the sweep over every channel instead of one.
    Which channel a sweep actually reached cost an evening of
    reasoning; with this set, every take prints what it asked for."""
    if os.environ.get("PDEQ_TRACE_SWEEP"):
        print("sweep-trace:", *a, file=sys.stderr)


def vol_trace(*a):
    """Volume tracing during a take, PDEQ_TRACE_VOL=1.

    A take once showed a flat -14 dB drop while both device
    volumes were proven constant by independent checks; the
    only volumes left unobserved were the playback and
    capture streams' own. With this variable set, every take
    runs a sampler that prints any volume change with a
    timestamp and the node's name."""
    if os.environ.get("PDEQ_TRACE_VOL"):
        print("vol-trace:", *a, file=sys.stderr)


def mic_trace(*a):
    """The mic-binding trace, PDEQ_TRACE_MIC=1.

    Born from a field mystery that survived two rounds of code
    reading: a window born "mic not resolved" over a perfect
    prefs record and a live canonical node. The breadcrumbs
    name the actual path taken -- the prefill's decisions,
    every hand on the binding, the pult's verdicts -- instead
    of feeding a third theory."""
    if os.environ.get("PDEQ_TRACE_MIC"):
        print("mic-trace:", *a, file=sys.stderr)
