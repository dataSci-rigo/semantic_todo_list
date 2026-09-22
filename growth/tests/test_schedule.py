import random

from growth import sched


def test_jitter_within_bounds_and_spacing():
    rng = random.Random(7)
    for _ in range(200):
        times = sched.draw_refocus_times(["10:30", "13:30", "16:30"], jitter_min=20, min_spacing_min=45, rng=rng)
        mins = [int(t[:2]) * 60 + int(t[3:]) for t in times]
        assert mins == sorted(mins)
        for centre, m in zip([630, 810, 990], mins):
            assert abs(m - centre) <= 20 + 45  # jitter, plus spacing push at most once
        assert all(b - a >= 45 for a, b in zip(mins, mins[1:]))
        assert all(6 * 60 <= m <= 23 * 60 for m in mins)


def test_seeded_is_deterministic():
    a = sched.draw_refocus_times(["10:30", "13:30", "16:30"], rng=random.Random(1))
    b = sched.draw_refocus_times(["10:30", "13:30", "16:30"], rng=random.Random(1))
    assert a == b
