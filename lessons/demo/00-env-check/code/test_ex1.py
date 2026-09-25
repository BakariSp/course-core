# 判题用测试，不要改。
import pytest

from ex1 import recent_average


def test_only_last_window_counts():
    assert recent_average([0, 1, 1, 1]) == 1.0


def test_fewer_than_window():
    assert recent_average([1, 0]) == 0.5


def test_empty_is_zero():
    assert recent_average([]) == 0.0


def test_custom_window():
    assert recent_average([1, 1, 0, 0], window=2) == 0.0


def test_partial_scores():
    assert recent_average([0.5, 1, 0]) == pytest.approx(0.5)
