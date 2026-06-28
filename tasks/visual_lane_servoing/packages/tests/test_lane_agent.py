"""Lane agent slice/curve tests. Skipped automatically where cv2/numpy are
absent (e.g. the dev venv); run on the bot or any cv2-enabled environment.

Run:  python -m pytest tasks/visual_lane_servoing/packages/tests/test_lane_agent.py
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")))

np = pytest.importorskip("numpy")
pytest.importorskip("cv2")

from tasks.visual_lane_servoing.packages.agent import LaneServoingAgent, detect_lines_in_slices  # noqa: E402
from tasks.visual_lane_servoing.packages.curve_behavior import detect_curve  # noqa: E402

H, W = 480, 640
# Slice strips for h=480: centers at rows 253/309/365, +-5 rows.
_FULL = slice(245, 376)     # rows covering all three strips
_MID_ONLY = slice(300, 320)  # rows covering only the middle strip


def _mask(*col_ranges, rows=_FULL):
    m = np.zeros((H, W), dtype=np.uint8)
    for c0, c1 in col_ranges:
        m[rows, c0:c1] = 255
    return m


def test_two_white_lines_picks_rightmost():
    # Both road edges in view: the old all-pixel mean landed mid-road and
    # steered the bot onto the left lane. Must pick the right edge.
    white = _mask((80, 96), (500, 516))
    _, white_xs = detect_lines_in_slices(_mask(), white, H)
    assert white_xs == [507, 507, 507]


def test_speck_rejected():
    white = _mask((500, 516), (630, 632))  # real line + 2-column speck
    _, white_xs = detect_lines_in_slices(_mask(), white, H)
    assert white_xs == [507, 507, 507]

    speck_only = _mask((630, 632))
    _, white_xs = detect_lines_in_slices(_mask(), speck_only, H)
    assert white_xs == [None, None, None]


def test_white_left_of_yellow_discarded():
    # Everything white left of the centerline is the opposite road edge.
    yellow = _mask((295, 306))
    white = _mask((100, 116))
    yellow_xs, white_xs = detect_lines_in_slices(yellow, white, H)
    assert yellow_xs == [300, 300, 300]
    assert white_xs == [None, None, None]


def test_lists_stay_aligned():
    white = _mask((500, 516), rows=slice(300, 376))  # mid + near strips only
    yellow_xs, white_xs = detect_lines_in_slices(_mask(), white, H)
    assert len(yellow_xs) == len(white_xs) == 3
    assert white_xs == [None, 507, 507]


def test_far_only_pick_dropped():
    # A line visible only in far strips is another road's marking seen across
    # a corner, not a line the bot is following — must not steer off it.
    white = _mask((500, 516), rows=slice(245, 320))  # far + mid strips only
    _, white_xs = detect_lines_in_slices(_mask(), white, H)
    assert white_xs == [None, None, None]


def test_detect_curve_straight_perspective_silent():
    # Linear-in-row positions = a straight line converging with perspective.
    # Huge near-far shift, zero bend: must NOT fire (regression guard for the
    # old shift metric, which only ever fired on mask contamination).
    assert detect_curve([100, 150, 200], [466, 553, 640], 15) == (False, 0)


def test_detect_curve_left_bend_fires():
    # Far slice pulled left relative to the straight-line extrapolation.
    is_curve, direction = detect_curve([100, 220, 320], [200, 320, 420], 15)
    assert is_curve is True
    assert direction > 0  # positive = road curves left


def test_detect_curve_needs_full_line():
    assert detect_curve([None, 233, 321], [None, 300, 400], 15) == (False, 0)


def _agent():
    return LaneServoingAgent(config_path="/nonexistent")  # forces code defaults


def test_half_width_learning_blend():
    agent = _agent()
    assert agent._half_widths == [175.0, 200.0, 270.0]
    agent._calculate_error([100, 100, 100], [500, 500, 500], True, True, W)
    # measured 200 px per strip, blended 10% into each slice's value
    assert agent._half_widths == pytest.approx(
        [0.9 * 175 + 0.1 * 200, 200.0, 0.9 * 270 + 0.1 * 200])


def test_half_width_rejects_narrow():
    agent = _agent()
    agent._calculate_error([300, 300, 300], [350, 350, 350], True, True, W)
    assert agent._half_widths == [175.0, 200.0, 270.0]  # measured 25 px: not a lane


def test_half_width_clamped():
    agent = _agent()
    for _ in range(100):  # EMA toward 330 px, but far slice clamps at 1.4x init
        agent._calculate_error([0, 0, 0], [660, 660, 660], True, True, W)
    assert agent._half_widths[0] == pytest.approx(1.4 * 175.0)


def test_single_line_uses_nearest_strip():
    agent = _agent()
    err = agent._calculate_error([None, None, 100], [None] * 3, True, False, W)
    # target = nearest yellow + near-slice half width
    assert err == pytest.approx((W / 2 - (100 + 270.0)) / (W / 2))


def test_fallback_not_renormalized():
    agent = _agent()
    agent._prev_error = 0.5
    assert agent._calculate_error([None] * 3, [None] * 3, False, False, W) == 0.5
