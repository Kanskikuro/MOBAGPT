from ingestion.riot_api.client import RateLimiter


def _fake_clock_and_sleep():
    now = [0.0]
    sleeps: list[float] = []

    def clock() -> float:
        return now[0]

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    return clock, sleep, sleeps


def test_rate_limiter_waits_once_window_is_full() -> None:
    clock, sleep, sleeps = _fake_clock_and_sleep()
    limiter = RateLimiter(((2, 1),), clock=clock, sleep=sleep)

    limiter.acquire()
    limiter.acquire()
    limiter.acquire()  # third request within the same second must wait

    assert sleeps == [1.0]


def test_rate_limiter_enforces_every_window_simultaneously() -> None:
    """A generous short window shouldn't let requests through faster than a
    stricter long window allows - Riot's dev key enforces both its 1s and
    2min windows at once, not whichever is more permissive."""
    clock, sleep, sleeps = _fake_clock_and_sleep()
    limiter = RateLimiter(((5, 1), (2, 10)), clock=clock, sleep=sleep)

    limiter.acquire()
    limiter.acquire()
    limiter.acquire()  # the 10s window (max 2) binds, not the 1s window (max 5)

    assert sleeps == [10.0]


def test_rate_limiter_allows_immediate_requests_under_the_limit() -> None:
    clock, sleep, sleeps = _fake_clock_and_sleep()
    limiter = RateLimiter(((20, 1), (100, 120)), clock=clock, sleep=sleep)

    for _ in range(20):
        limiter.acquire()

    assert sleeps == []
