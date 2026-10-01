"""Defer catchable shutdown until a short local ownership transaction ends."""
from contextlib import contextmanager
import signal
import threading


@contextmanager
def defer_shutdown():
    # Do not change SIG_IGN (nohup) or default dispositions. Children inherit
    # no blocked mask; exec restores their ordinary signal dispositions.
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    previous = {number: signal.getsignal(number)
                for number in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
                if callable(signal.getsignal(number))}
    pending = []
    try:
        for number in previous:
            signal.signal(number, lambda number, frame: pending.append((number, frame)))
        yield
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)
        if pending:
            number, frame = pending[0]
            previous[number](number, frame)
