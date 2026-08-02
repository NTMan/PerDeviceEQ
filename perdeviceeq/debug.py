"""Debug instruments, silent unless asked for.

One voice for every file: the trace helpers live here so no
module grows its own copy. Each instrument is gated by its own
environment variable and prints to stderr, costing nothing
when off.
"""

import os
import sys


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
