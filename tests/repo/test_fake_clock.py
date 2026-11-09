"""The shared fake_clock fixture of tests/conftest.py: both clocks move together, only when the test moves them."""


def test_the_fake_clock_moves_only_when_the_test_moves_it(fake_clock):
    t0, w0 = fake_clock.monotonic(), fake_clock.time()
    assert (fake_clock.monotonic(), fake_clock.time()) == (t0, w0)
    fake_clock.advance(2.5)
    fake_clock.sleep(1000)                  # never blocks: the suite would time out if it did
    assert (fake_clock.monotonic() - t0, fake_clock.time() - w0) == (1002.5, 1002.5)


def test_each_test_gets_a_fresh_clock(fake_clock):
    assert (fake_clock.monotonic(), fake_clock.time()) == (1000.0, 1_700_000_000.0)
