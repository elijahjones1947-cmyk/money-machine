"""
Per-symbol sequential background processing for /webhook signals.

/webhook must acknowledge TradingView fast: two real delivery-timeout
incidents (see server.py's webhook() docstring) traced back to the route
synchronously chaining several broker network calls (a sanity-check bars
fetch, account info, price, order placement) before ever responding --
TradingView gave up waiting at ~2.7-2.85s in both cases, even though the
trade had ALREADY executed successfully server-side by then.

This module lets webhook() hand off that slow work to a background
thread and respond immediately, while still guaranteeing per-symbol
order: two signals for the SAME symbol (e.g. a buy immediately followed
by a sell) are processed strictly in the order enqueue() was called for
them, one at a time, never concurrently -- exactly the assumption
ce9360f's retry logic and the sell-side held-qty check already depend
on. Signals for DIFFERENT symbols run fully in parallel on their own
dedicated worker threads, so a slow AAPL signal can never delay a GBP_JPY
one.

Deliberately NOT built on APScheduler (used elsewhere in this codebase
for periodic jobs, e.g. server.py's run_position_safety_checks) -- that
scheduler has no concept of ordering between jobs targeting the same
key, and retrofitting one would be more machinery than this needs. A
queue.Queue per symbol, drained by one dedicated daemon thread per
symbol (spawned lazily on that symbol's first signal and kept alive
afterward), is the simplest structure that gives strict FIFO-per-symbol
ordering plus full parallelism across symbols with no extra
dependencies. The number of distinct symbols this bot ever watches is
small (state.watched_symbols), so leaving idle worker threads parked
between signals costs nothing meaningful.

THIS MODULE IS NOT THE DURABILITY LAYER. It's purely an in-process
scheduling mechanism -- the queues here live in memory and are gone on
any process restart. Durability (surviving a process kill/restart
between "accepted" and "executed") comes from server.py writing each
signal to Postgres's webhook_signals table SYNCHRONOUSLY before ever
calling enqueue() here, and from recover_pending_webhook_signals()
re-enqueueing anything left 'pending' at startup, in the same order,
through this exact same per-symbol FIFO mechanism -- see db.py's
webhook_signals comment and server.py's webhook()/
recover_pending_webhook_signals() for the full picture. Worker threads
here stay daemon=True (see _drain's infinite loop -- a non-daemon
version was tried and confirmed to hang the interpreter on exit
forever, waiting to join a thread that never finishes on its own); that
no longer risks losing an ACCEPTED signal, since the Postgres row (not
this in-memory queue) is what "accepted" durably means now. The one
remaining edge case durability can't paper over -- a crash in the
narrow window where a signal is already marked 'processing' but its
broker call's outcome is unknown -- is handled by NEVER auto-resuming
it (see recover_pending_webhook_signals()), not by anything in this
module.
"""

import logging
import queue
import threading

_queues = {}  # symbol -> queue.Queue of zero-arg callables
# Guards BOTH creating a new symbol's queue/worker AND the put() itself --
# see enqueue()'s docstring for why the whole call needs to be one
# critical section, not just the creation part.
_lock = threading.Lock()


def _drain(symbol, q):
    while True:
        func = q.get()
        try:
            func()
        except Exception:
            # Never let one signal's bug kill the worker thread for this
            # symbol -- that would silently strand every LATER signal for
            # it in the queue forever, which is worse than one bad
            # signal failing loudly. logging.exception (not .warning) so
            # this always lands in error_log via DBLogHandler with a
            # full traceback, same severity as any other unexpected
            # failure path in this codebase.
            logging.exception(
                'Unhandled exception processing a queued webhook signal for {}'.format(symbol)
            )
        finally:
            q.task_done()


def enqueue(symbol, func):
    """Schedule `func` (a zero-arg callable) to run on `symbol`'s
    dedicated worker thread, strictly after every callable already
    queued for that same symbol and never concurrently with them.
    Returns immediately -- callers must not assume `func` has run (or
    even started) by the time this returns; the eventual outcome is
    only observable via whatever `func` itself logs/persists (see
    server.py's _process_queued_webhook_signal).

    The entire lookup-or-create-then-put runs under one lock, not just
    the queue creation: if creation and put were two separate critical
    sections, two threads calling enqueue() for the same BRAND-NEW
    symbol back-to-back could interleave such that the second caller's
    put() lands in the queue before the first caller's does -- silently
    reordering two signals that arrived in the opposite order, exactly
    the bug this module exists to prevent. Holding the lock for the
    whole call keeps per-symbol ordering exactly as strict as the order
    in which webhook() itself calls enqueue() for that symbol.
    Microseconds of lock contention on a bot handling a handful of
    signals a day (see the Railway traffic audit that motivated this
    module) is not a real cost -- nothing broker/DB-bound ever happens
    while this lock is held.
    """
    with _lock:
        q = _queues.get(symbol)
        if q is None:
            q = queue.Queue()
            _queues[symbol] = q
            # daemon=True: these workers loop forever (see _drain) with
            # no shutdown signal, so a non-daemon thread would hang the
            # interpreter/gunicorn worker on exit forever, waiting to
            # join a thread that's never going to finish on its own
            # (verified directly -- a non-daemon version of this was
            # tried and does exactly that). daemon=True means an
            # in-flight queued signal CAN be lost if the process exits
            # while it's sitting here, which the old fully-synchronous
            # design didn't risk (there, "response sent" and "trade
            # executed" were atomic). That's a real, deliberately
            # accepted tradeoff, not an oversight -- see this module's
            # docstring and server.py's webhook() docstring for the
            # reasoning, and the report accompanying this change for the
            # magnitude (traffic is a handful of signals/day, and a
            # queued item's time-to-drain is normally sub-second).
            threading.Thread(
                target=_drain, args=(symbol, q), daemon=True,
                name='webhook-worker-{}'.format(symbol),
            ).start()
        q.put(func)


def wait_for_idle(symbol):
    """Block until every callable enqueued for `symbol` so far has
    finished running (including ones enqueued by an in-flight `func`
    itself, if any). No-op if `symbol` has never been enqueued. Not used
    by production code -- webhook() deliberately never waits on its own
    queue, that's the entire point of this module -- but tests need a
    deterministic way to know background work has completed before
    asserting on its effects (or before the next test's state resets
    out from under a still-running one)."""
    q = _queues.get(symbol)
    if q is not None:
        q.join()
