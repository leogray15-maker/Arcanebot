"""Phase 2 tests — FVG, Order Block, Volume Imbalance detection."""

from arcanebot.structure.pd_arrays import (
    find_fvgs,
    find_order_block,
    find_volume_imbalances,
)


def test_bullish_fvg(make_frame):
    # c1.high=10 < c3.low=11  -> bullish FVG zone [10, 11].
    df = make_frame([
        (9, 10, 8, 9),      # c1
        (10, 12, 9, 11),    # c2
        (11, 13, 11, 12),   # c3
    ])
    fvgs = find_fvgs(df, side="bullish")
    assert len(fvgs) == 1
    f = fvgs[0]
    assert (f.bottom, f.top) == (10.0, 11.0)
    assert f.index == 2 and f.confirmed_index == 2   # known after c3 closes


def test_bearish_fvg(make_frame):
    # c1.low=15 > c3.high=13 -> bearish FVG zone [13, 15].
    df = make_frame([
        (16, 17, 15, 15.5),      # c1  (low 15)
        (15, 15.5, 13.5, 14),    # c2
        (12.8, 13, 12, 12.5),    # c3  (high 13)
    ])
    fvgs = find_fvgs(df, side="bearish")
    assert len(fvgs) == 1
    f = fvgs[0]
    assert (f.bottom, f.top) == (13.0, 15.0)


def test_order_block_bullish_body(make_frame):
    # Displacement up-candle at index 3. Nearest down-candle before it is idx 1
    # (idx 2 is an up candle). +OB body = [close, open] = [10, 12].
    df = make_frame([
        (9, 11, 8, 10.5),    # 0 up
        (12, 12.5, 9, 10),   # 1 DOWN  -> the OB
        (10, 11, 9.5, 10.8), # 2 up
        (10.8, 15, 10.7, 14),# 3 displacement up
    ])
    ob = find_order_block(df, displacement_index=3, side="bullish", mode="body")
    assert ob is not None
    assert ob.index == 1
    assert (ob.bottom, ob.top) == (10.0, 12.0)
    assert ob.confirmed_index == 3   # known once displacement candle closes


def test_order_block_range_mode(make_frame):
    df = make_frame([
        (12, 12.5, 9, 10),   # 0 DOWN, full range [9, 12.5]
        (10, 15, 9.9, 14),   # 1 displacement up
    ])
    ob = find_order_block(df, displacement_index=1, side="bullish", mode="range")
    assert (ob.bottom, ob.top) == (9.0, 12.5)


def test_bullish_volume_imbalance(make_frame):
    # c2.open (10.2) > c1.close (10) with overlapping wicks (c1.high 10.5 >= c2.low 10.1).
    df = make_frame([
        (8, 10.5, 7.5, 10.0),    # c1
        (10.2, 11.0, 10.1, 10.8) # c2
    ])
    vis = find_volume_imbalances(df, side="bullish")
    assert len(vis) == 1
    v = vis[0]
    assert (v.bottom, v.top) == (10.0, 10.2)
    assert v.index == 1


def test_no_fvg_when_gap_absent(make_frame):
    df = make_frame([
        (9, 12, 8, 11),
        (10, 12, 9, 11),
        (10, 12, 9, 11),
    ])
    assert find_fvgs(df) == []
