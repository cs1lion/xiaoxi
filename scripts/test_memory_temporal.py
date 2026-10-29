"""Conservative Chinese temporal-bound resolver; isolated, no app imports.

Run: python -B scripts/test_memory_temporal.py
The clock is fixed so relative resolution (去年/上个月/这周) is deterministic.
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server.memory.temporal import temporal_bounds

# 2026-06-15 12:00 UTC -> last year 2025, last month 2026-05.
NOW = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc).timestamp()


def epoch(y, mo, d, h=0, mi=0, s=0):
    return datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc).timestamp()


class TemporalBoundTests(unittest.TestCase):
    def bounds(self, text):
        return temporal_bounds(text, now=NOW)

    def test_from_marker_sets_start_only(self):
        vf, vu = self.bounds('从2024年3月起我在学Python')
        self.assertEqual(vf, epoch(2024, 3, 1))
        self.assertIsNone(vu)

    def test_from_start_marker_is_the_colloquial_twin_of_qi(self):
        # 开始 is more natural in speech than 起; both state the same explicit
        # start. (自从X already matched without a trailing particle, so 从X was
        # the asymmetric one.)
        for text in ('从2024年3月开始我在学Python', '从去年开始我在学Python'):
            vf, vu = self.bounds(text)
            self.assertIsNotNone(vf, text)
            self.assertIsNone(vu, text)
        self.assertEqual(self.bounds('从2024年3月开始记账')[0], epoch(2024, 3, 1))

    def test_discourse_from_speaking_is_still_not_a_bound(self):
        # 起/开始 must follow the date directly, so 从X说起/讲起 stays out.
        self.assertEqual(self.bounds('从去年说起我经历过很多事'), (None, None))
        self.assertEqual(self.bounds('从去年讲起都是回忆'), (None, None))

    def test_since_marker_sets_start(self):
        vf, vu = self.bounds('自从2020年起住在这里')
        self.assertEqual(vf, epoch(2020, 1, 1))
        self.assertIsNone(vu)

    def test_until_marker_sets_end_only(self):
        vf, vu = self.bounds('到2024年6月为止我在甲公司')
        self.assertIsNone(vf)
        self.assertEqual(vu, epoch(2024, 6, 30, 23, 59, 59))

    def test_until_alt_marker_relative(self):
        _vf, vu = self.bounds('直到去年我都住在这里')
        self.assertEqual(vu, epoch(2025, 12, 31, 23, 59, 59))

    def test_range_marker_sets_both(self):
        vf, vu = self.bounds('从2024年3月15日到2024年6月20日我住这里')
        self.assertEqual(vf, epoch(2024, 3, 15))
        self.assertEqual(vu, epoch(2024, 6, 20, 23, 59, 59))

    def test_absolute_day_resolves_exactly(self):
        vf, _ = self.bounds('从2019年5月2日起开始记账')
        self.assertEqual(vf, epoch(2019, 5, 2))

    def test_relative_from_last_year(self):
        vf, _ = self.bounds('从去年起我在学Python')
        self.assertEqual(vf, epoch(2025, 1, 1))

    def test_relative_until_last_month(self):
        _vf, vu = self.bounds('到上个月为止在备考')
        self.assertEqual(vu, epoch(2026, 5, 31, 23, 59, 59))

    def test_week_relative_bounds(self):
        now_dt = datetime.fromtimestamp(NOW, tz=timezone.utc)
        this_monday = (now_dt - timedelta(days=now_dt.weekday())).date()
        vf, _ = self.bounds('从这周起调整作息')
        self.assertEqual(vf, datetime(this_monday.year, this_monday.month, this_monday.day,
                                       tzinfo=timezone.utc).timestamp())
        last_monday = (now_dt - timedelta(days=now_dt.weekday() + 7)).date()
        _vf, vu = self.bounds('到上周为止收尾')
        expected = (datetime(last_monday.year, last_monday.month, last_monday.day, tzinfo=timezone.utc)
                    + timedelta(days=6, hours=23, minutes=59, seconds=59))
        self.assertEqual(vu, expected.timestamp())

    def test_open_ended_durations_stay_unresolved(self):
        for text in ('最近我在学Python', '这段时间很忙', '目前住在北京',
                     '这个月要考试', '现在在加班', '前段时间换了工作'):
            self.assertEqual(self.bounds(text), (None, None), text)

    def test_no_marker_is_unresolved(self):
        self.assertEqual(self.bounds('我喜欢Python编程'), (None, None))

    def test_discourse_from_speaking_is_not_a_bound(self):
        self.assertEqual(self.bounds('从去年说起我经历过很多事'), (None, None))

    def test_discourse_until_speaking_is_not_a_bound(self):
        self.assertEqual(self.bounds('说到去年为止都是回忆'), (None, None))

    def test_discourse_verbs_tan_ti_liao_are_not_bounds(self):
        for text in ('谈到今年为止是回忆', '提到上周为止的任务', '聊到昨天为止的话题'):
            self.assertEqual(self.bounds(text), (None, None), text)

    def test_unresolvable_date_is_unresolved(self):
        self.assertEqual(self.bounds('从萝卜年起住在北京'), (None, None))
        self.assertEqual(self.bounds('从2024年13月起准备'), (None, None))
        self.assertEqual(self.bounds('从2024年2月30日起准备'), (None, None))
        # A pre-1970 year cannot be represented in the store's nonnegative
        # contract. Resolving it would raise on the write path -- and in the
        # extraction loop one such candidate would drop the whole turn.
        self.assertEqual(self.bounds('从1969年起住在北京'), (None, None))
        self.assertEqual(self.bounds('到1000年为止住在长安'), (None, None))
        # The epoch boundary itself is representable.
        self.assertEqual(self.bounds('从1970年起记账')[0], 0.0)

    def test_contradictory_range_is_unresolved(self):
        # start after end -> no bound at all (never retires the row).
        self.assertEqual(self.bounds('从这周到上周都不顺'), (None, None))

    def test_empty_and_non_string(self):
        self.assertEqual(temporal_bounds('', now=NOW), (None, None))
        self.assertEqual(temporal_bounds(None, now=NOW), (None, None))

    def test_separate_from_and_until_both_set(self):
        vf, vu = self.bounds('从2024年1月起住这里，到2024年12月为止搬走')
        self.assertEqual(vf, epoch(2024, 1, 1))
        self.assertEqual(vu, epoch(2024, 12, 31, 23, 59, 59))

    def test_range_second_bound_inherits_first_year(self):
        # 从2024年3月到6月：第二界缺年份，承继首界年份是无歧义的。
        vf, vu = self.bounds('从2024年3月到6月在做那个项目')
        self.assertEqual(vf, epoch(2024, 3, 1))
        self.assertEqual(vu, epoch(2024, 6, 30, 23, 59, 59))
        vf, vu = self.bounds('从2024年3月15日到6月20日住这里')
        self.assertEqual(vf, epoch(2024, 3, 15))
        self.assertEqual(vu, epoch(2024, 6, 20, 23, 59, 59))

    def test_range_without_year_context_is_not_guessed(self):
        # 没有首界年份可承继时，不得猜一个年份。
        self.assertEqual(self.bounds('3月到6月在做项目'), (None, None))
        self.assertEqual(self.bounds('从3月到6月在做项目'), (None, None))

    def test_range_with_one_out_of_range_bound_is_wholly_unresolved(self):
        # 区间必须两界都解析成功；单界越界不能留下半开区间。
        for text in ('从2024年3月到13月做项目', '从2024年2月到2月30日做项目',
                     '从2024年13月到2025年1月住这里'):
            self.assertEqual(self.bounds(text), (None, None), text)


if __name__ == '__main__':
    unittest.main()
