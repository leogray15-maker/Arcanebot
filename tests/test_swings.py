"""Phase 2 tests — fractal swing detection and no-look-ahead confirmation."""

from arcanebot.structure.swings import find_swings, last_swing, swings_known_at

# Hand-crafted M5 series (open/high/low/close). N=2.
#   idx:   0    1    2    3    4    5    6    7    8
#   high: 10   11   14   11   10   11   13   12   11
#   low:   8    7    9    6    3    6    9    7    8
# Expected swing highs: idx 2 (14), idx 6 (13).
# Expected swing low:   idx 4 (3).
ROWS = [
    (9, 10, 8, 9),
    (8, 11, 7, 10),
    (10, 14, 9, 13),
    (10, 11, 6, 7),
    (7, 10, 3, 9),
    (7, 11, 6, 10),
    (10, 13, 9, 12),
    (9, 12, 7, 11),
    (9, 11, 8, 10),
]


def test_swing_highs_and_lows_detected(make_frame):
    df = make_frame(ROWS)
    swings = find_swings(df, n=2)

    highs = [s for s in swings if s.is_high]
    lows = [s for s in swings if s.is_low]

    assert [(s.index, s.price) for s in highs] == [(2, 14.0), (6, 13.0)]
    assert [(s.index, s.price) for s in lows] == [(4, 3.0)]


def test_confirmation_index_is_n_bars_later(make_frame):
    df = make_frame(ROWS)
    swings = find_swings(df, n=2)
    by_index = {s.index: s for s in swings}

    assert by_index[2].confirmed_index == 4   # 2 + N
    assert by_index[4].confirmed_index == 6
    assert by_index[6].confirmed_index == 8


def test_swings_known_at_respects_confirmation(make_frame):
    df = make_frame(ROWS)
    swings = find_swings(df, n=2)

    # At bar 3 the swing high at idx 2 is NOT yet confirmed (needs bar 4).
    assert swings_known_at(swings, 3) == []
    # At bar 4 it becomes known.
    known4 = swings_known_at(swings, 4)
    assert [s.index for s in known4] == [2]


def test_last_swing_lookup(make_frame):
    df = make_frame(ROWS)
    swings = find_swings(df, n=2)

    hi = last_swing(swings, "high")
    assert hi.index == 6 and hi.price == 13.0

    # Restricting to what is known by bar 5 gives only the idx-2 high.
    hi_known = last_swing(swings, "high", known_at=5)
    assert hi_known.index == 2


def test_edges_are_never_swings(make_frame):
    df = make_frame(ROWS)
    swings = find_swings(df, n=2)
    # First/last N candles can't be swings.
    assert all(2 <= s.index <= 6 for s in swings)
