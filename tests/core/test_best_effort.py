"""best_effort: a side action beside a failure never replaces it (the helper's own contract; each
adopting site proves its outcome in its own module)."""

import pytest

from lhpc.core.best_effort import best_effort


def test_returns_the_side_actions_result_and_logs_nothing():
    lines = []
    assert best_effort(lambda: 7, what="x", log=lines.append) == 7
    assert lines == []


def test_an_ordinary_error_is_one_line_and_none():
    lines = []

    def boom():
        raise OSError(5, "I/O error")
    assert best_effort(boom, what="cleanup of 'a'", log=lines.append) is None
    assert lines == ["cleanup of 'a': OSError: [Errno 5] I/O error"]



def test_a_multi_line_message_is_flattened_to_one_stderr_line(capsys):
    def boom():
        raise ValueError("first\nsecond\r\n  third\r")
    assert best_effort(boom, what="x") is None
    assert capsys.readouterr().err == "x: ValueError: first second third\n"

@pytest.mark.parametrize("exc", [KeyboardInterrupt(), SystemExit(3)])
def test_a_base_exception_from_the_side_action_propagates(exc):
    lines = []

    def interrupted():
        raise exc
    with pytest.raises(type(exc)) as got:
        best_effort(interrupted, what="x", log=lines.append)
    assert got.value is exc and lines == []


def test_an_unprintable_error_is_still_one_line():
    lines = []

    class Unprintable(Exception):
        def __str__(self):
            raise RuntimeError("hostile __str__")

    def boom():
        raise Unprintable()
    assert best_effort(boom, what="x", log=lines.append) is None
    assert lines == ["x: Unprintable"]


def test_a_failing_log_is_ignored():
    def boom():
        raise RuntimeError("side")

    def closed(line):
        raise ValueError("I/O operation on closed file")
    assert best_effort(boom, what="x", log=closed) is None


@pytest.mark.parametrize("exc", [KeyboardInterrupt(), SystemExit(3)])
def test_a_base_exception_from_log_propagates(exc):
    # An ordinary Exception from `log` is ignored (above); a BaseException from it is not. The side
    # action's failure is still handled first: `log` receives its one line before it raises.
    seen = []

    def boom():
        raise RuntimeError("side")

    def interrupted(line):
        seen.append(line)
        raise exc
    with pytest.raises(type(exc)) as got:
        best_effort(boom, what="x", log=interrupted)
    assert got.value is exc
    assert seen == ["x: RuntimeError: side"]


@pytest.mark.parametrize("original", [ValueError("main"), KeyboardInterrupt()])
def test_a_bare_raise_after_it_reraises_the_original(original):
    lines = []

    def boom():
        raise RuntimeError("side")
    with pytest.raises(type(original)) as got:
        try:
            raise original
        except BaseException:
            best_effort(boom, what="unwind", log=lines.append)
            raise
    assert got.value is original
    assert lines == ["unwind: RuntimeError: side"]
