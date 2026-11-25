"""Unit tests for the pure logic: streaks, topic merging, difficulty buckets,
AtCoder rank colours, and the stats cache. No network, no database."""
from datetime import datetime, timedelta, timezone

import pytest

import cache
from activity import compute_streaks
from platforms import atcoder_rank, bucket_from_index, bucket_from_rating
from topics import merge_topics, normalize


def day(offset: int) -> str:
    """ISO date `offset` days from today (UTC): day(0) is today, day(-1) yesterday."""
    return (datetime.now(timezone.utc).date() + timedelta(days=offset)).isoformat()


# ---------- streaks ----------

def test_streak_empty():
    assert compute_streaks({}) == (0, 0)


def test_streak_running_through_today():
    assert compute_streaks({day(0): 1, day(-1): 4, day(-2): 2}) == (3, 3)


def test_streak_still_alive_if_last_day_was_yesterday():
    assert compute_streaks({day(-1): 1, day(-2): 1}) == (2, 2)


def test_streak_broken_if_last_day_is_older():
    # three days in a row, but they ended 3 days ago: no current streak, longest is 3
    assert compute_streaks({day(-5): 1, day(-4): 1, day(-3): 1}) == (0, 3)


def test_streak_current_is_shorter_than_longest():
    activity = {day(0): 1, day(-2): 1, day(-3): 1, day(-4): 1}
    assert compute_streaks(activity) == (1, 3)


# ---------- topics ----------

def test_normalize_uses_aliases_and_lowercases():
    assert normalize("DP") == "dynamic programming"
    assert normalize("Sortings") == "sorting"
    assert normalize("Hash Table") == "hash table"  # unknown names are only lowercased


def test_merge_topics_combines_aliases_across_platforms():
    codeforces = [{"name": "dp", "count": 2}, {"name": "math", "count": 5}]
    leetcode = [{"name": "Dynamic Programming", "count": 3}, {"name": "Math", "count": 1}]

    merged = merge_topics([codeforces, leetcode])

    assert merged == [
        {"name": "Math", "count": 6},
        {"name": "Dynamic Programming", "count": 5},
    ]


def test_merge_topics_limit():
    lists = [[{"name": "a", "count": 3}, {"name": "b", "count": 2}, {"name": "c", "count": 1}]]
    assert len(merge_topics(lists, limit=2)) == 2


def test_merge_topics_empty():
    assert merge_topics([]) == []


# ---------- difficulty buckets and AtCoder colours ----------

@pytest.mark.parametrize(
    "rating, expected",
    [(800, "easy"), (1200, "easy"), (1201, "medium"), (1800, "medium"), (1801, "hard")],
)
def test_bucket_from_rating(rating, expected):
    assert bucket_from_rating(rating) == expected


@pytest.mark.parametrize(
    "index, expected",
    [("A", "easy"), ("B2", "easy"), ("C1", "medium"), ("D", "hard"), ("F", "hard")],
)
def test_bucket_from_index(index, expected):
    assert bucket_from_index(index) == expected


@pytest.mark.parametrize(
    "rating, expected",
    [
        (None, None),
        (0, "gray"),
        (399, "gray"),
        (400, "brown"),
        (1199, "green"),
        (1999, "blue"),
        (2799, "orange"),
        (2800, "red"),
        (3672, "red"),
    ],
)
def test_atcoder_rank(rating, expected):
    assert atcoder_rank(rating) == expected


# ---------- cache ----------

@pytest.fixture(autouse=True)
def empty_cache():
    cache._store.clear()
    yield
    cache._store.clear()


def make_fetch(result):
    calls = []

    def fetch(handle):
        calls.append(handle)
        return result

    fetch.calls = calls
    return fetch


def test_cache_second_call_does_not_fetch_again():
    fetch = make_fetch(({"rating": 1}, None))

    first = cache.cached_fetch("atcoder", "tourist", fetch)
    second = cache.cached_fetch("atcoder", "tourist", fetch)

    assert first == second == ({"rating": 1}, None)
    assert len(fetch.calls) == 1


def test_cache_handle_is_case_insensitive():
    fetch = make_fetch(({"rating": 1}, None))
    cache.cached_fetch("atcoder", "Tourist", fetch)
    cache.cached_fetch("atcoder", "tourist", fetch)
    assert len(fetch.calls) == 1


def test_cache_force_refetches():
    fetch = make_fetch(({"rating": 1}, None))
    cache.cached_fetch("atcoder", "tourist", fetch)
    cache.cached_fetch("atcoder", "tourist", fetch, force=True)
    assert len(fetch.calls) == 2


def test_cache_does_not_store_errors_or_partial_results():
    failed = make_fetch((None, "rate_limited"))
    cache.cached_fetch("github", "bob", failed)
    cache.cached_fetch("github", "bob", failed)
    assert len(failed.calls) == 2  # retried, because failures are not cached

    partial = make_fetch(({"name": "Bob"}, "rate_limited"))  # stats present, but error set
    cache.cached_fetch("github", "carol", partial)
    cache.cached_fetch("github", "carol", partial)
    assert len(partial.calls) == 2


def test_cache_entries_expire(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(cache.time, "monotonic", lambda: now[0])
    fetch = make_fetch(({"rating": 1}, None))

    cache.cached_fetch("atcoder", "tourist", fetch)
    now[0] += cache.TTL_SECONDS - 1
    cache.cached_fetch("atcoder", "tourist", fetch)  # still fresh
    assert len(fetch.calls) == 1

    now[0] += 2  # now older than the TTL
    cache.cached_fetch("atcoder", "tourist", fetch)
    assert len(fetch.calls) == 2


def test_cache_is_per_platform():
    fetch = make_fetch(({"x": 1}, None))
    cache.cached_fetch("leetcode", "same", fetch)
    cache.cached_fetch("atcoder", "same", fetch)
    assert len(fetch.calls) == 2
