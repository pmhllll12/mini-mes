"""열화 경보 평가의 에피소드 구분·지표 계산 (DB 없이)"""
import pytest

from drift_alarm_eval import analyze, find_episodes, parse_policy


def test_parse_policy():
    assert parse_policy("base=2/3") == ("base", {"raise_after": 2, "clear_after": 3})
    assert parse_policy("new=2/2,clear_z=1.5,k=1,h=4") == (
        "new", {"raise_after": 2, "clear_after": 2, "clear_z": 1.5, "cusum_k": 1.0, "cusum_h": 4.0})
    with pytest.raises(ValueError):
        parse_policy("x=2/2,foo=1")


def test_find_episodes_splits_on_level_reset_and_marks_complete():
    lv = [None, 0.25, 0.5, 0.75, 1.0, 0.25, 0.5, None, 0.5, 0.75]
    assert find_episodes(lv) == [(1, 4, True), (5, 6, False), (8, 9, False)]


def test_analyze_counts_detection_lag_and_causes():
    #        0     1     2     3     4     5     6     7     8     9
    levels = [None, 0.25, 0.5, 0.75, 1.0, None, None, None, None, None]
    spikes = [False] * 10
    spikes[7] = True
    active = [False, False, True, True, True, True, True, False, False, True]
    events = [(2, "raised"), (7, "cleared"), (9, "raised")]
    m = analyze(levels, spikes, active, events)
    assert m["detected"] == 1 and m["first_alarm"] == [2]          # 에피소드 2번째 구간에서 첫 경보
    assert m["clear_lag"] == [2]                                     # 끝난 뒤 2구간 더 켜짐
    assert m["causes"] == {"drift": 1, "after_spike": 1}             # 9번은 급변(7번) 직후
    assert m["active_normal"] == 3 and m["mid_clears"] == 0
    assert m["normal_intervals"] == 5
