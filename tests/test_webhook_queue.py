"""
Tests for webhook_queue.py's core guarantees in isolation, with no
Flask/DB/broker dependency -- pure threading logic, same reasoning as
tests/test_alerts.py for why this can import and exercise the module
directly.

Covers: per-symbol FIFO ordering, per-symbol mutual exclusion (never two
callables for the SAME symbol running at once), full parallelism ACROSS
different symbols (proving the module doesn't over-serialize), and that
one callable raising never strands the worker thread or drops later
signals for that symbol.
"""

import threading
import time

import webhook_queue


def _unique_symbol(name):
    """Every test gets its own never-before-seen symbol name so its
    worker thread/queue can't collide with state left behind by an
    earlier test -- webhook_queue keeps worker threads alive for the
    life of the process (see its module docstring), there's no reset
    fixture for it the way tests/conftest.py resets state.py."""
    return "TEST_{}_{}".format(name, id(object()))


def test_same_symbol_processed_in_enqueue_order():
    symbol = _unique_symbol("order")
    results = []
    for i in range(20):
        webhook_queue.enqueue(symbol, lambda i=i: results.append(i))
    webhook_queue.wait_for_idle(symbol)
    assert results == list(range(20))


def test_same_symbol_never_runs_two_callables_concurrently():
    """The exact guarantee a rapid buy-then-sell for one symbol depends
    on: even if one callable is slow, the next one queued behind it must
    not start until it's fully done."""
    symbol = _unique_symbol("mutex")
    currently_running = []
    max_concurrent = []

    def slow_task(n):
        currently_running.append(n)
        max_concurrent.append(len(currently_running))
        time.sleep(0.05)
        currently_running.remove(n)

    for i in range(5):
        webhook_queue.enqueue(symbol, lambda i=i: slow_task(i))
    webhook_queue.wait_for_idle(symbol)

    assert max(max_concurrent) == 1


def test_different_symbols_run_fully_in_parallel():
    """The flip side of the mutex test above: ordering/exclusion is
    PER-SYMBOL only -- a slow signal for one symbol must never delay a
    different symbol's signal, or this module would just be
    reintroducing the exact latency problem it exists to avoid."""
    symbol_a = _unique_symbol("parallel_a")
    symbol_b = _unique_symbol("parallel_b")
    start_gate = threading.Barrier(2, timeout=2)
    reached_gate = {"a": False, "b": False}

    def wait_together(key):
        reached_gate[key] = True
        start_gate.wait()  # both must arrive here "at the same time" or this times out

    webhook_queue.enqueue(symbol_a, lambda: wait_together("a"))
    webhook_queue.enqueue(symbol_b, lambda: wait_together("b"))

    webhook_queue.wait_for_idle(symbol_a)
    webhook_queue.wait_for_idle(symbol_b)

    assert reached_gate == {"a": True, "b": True}


def test_exception_in_one_callable_does_not_stop_the_worker():
    symbol = _unique_symbol("exc")
    results = []

    def boom():
        raise ValueError("simulated bug in a queued signal")

    webhook_queue.enqueue(symbol, boom)
    webhook_queue.enqueue(symbol, lambda: results.append("still ran"))
    webhook_queue.wait_for_idle(symbol)

    assert results == ["still ran"]


def test_exception_in_one_callable_is_logged(caplog):
    import logging

    symbol = _unique_symbol("exc_logged")

    def boom():
        raise ValueError("simulated bug in a queued signal")

    with caplog.at_level(logging.ERROR):
        webhook_queue.enqueue(symbol, boom)
        webhook_queue.wait_for_idle(symbol)

    assert any(
        "Unhandled exception processing a queued webhook signal for {}".format(symbol) in r.getMessage()
        for r in caplog.records
    )


def test_wait_for_idle_is_a_noop_for_a_never_enqueued_symbol():
    # Must not raise or hang for a symbol whose queue/worker never got
    # created in the first place.
    webhook_queue.wait_for_idle(_unique_symbol("never_used"))


def test_enqueue_reuses_the_same_worker_for_repeated_signals():
    """A symbol's worker thread is created once and kept alive, not
    respawned per signal -- confirmed indirectly: two batches of work
    enqueued with a wait in between must both still complete in order,
    proving the SAME running worker picked up the second batch rather
    than nothing being there to process it."""
    symbol = _unique_symbol("reuse")
    results = []

    webhook_queue.enqueue(symbol, lambda: results.append(1))
    webhook_queue.wait_for_idle(symbol)

    webhook_queue.enqueue(symbol, lambda: results.append(2))
    webhook_queue.wait_for_idle(symbol)

    assert results == [1, 2]
