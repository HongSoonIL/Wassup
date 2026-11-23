from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.test import SimpleTestCase

from chat.reply_timing import plan_reply
from world.management.commands.seed_world import CHARACTERS
from world.models import RoutineTemplate, State


def at(hour, minute=0, day=19):
    return datetime(2026, 11, day, hour, minute, tzinfo=ZoneInfo('Asia/Seoul'))


def slot(weekday, start, end, state=State.FOCUS):
    return RoutineTemplate(weekday=weekday, start_time=start, end_time=end, state=state)


class ReplyTimingTests(SimpleTestCase):
    def test_dohyun_shift_from_seed_respects_awake_deadline(self):
        routines = [
            slot(weekday, start, end, state)
            for weekday, start, end, _, state in CHARACTERS[0]['routines']
        ]
        timing = plan_reply(at(18, 40), routines)
        self.assertEqual(timing.reply_at, at(21, 40))
        self.assertTrue(at(18, 40) <= timing.read_at <= timing.typing_at <= timing.reply_at)

    def test_short_break_is_used_before_deadline(self):
        routines = [slot(3, time(18), time(20, 10)), slot(3, time(20, 20), time(22))]
        timing = plan_reply(at(18, 40), routines)
        self.assertEqual(timing.read_at, at(20, 10))
        self.assertEqual(timing.reply_at, at(20, 10) + timedelta(seconds=30))

    def test_adjacent_blocks_do_not_create_a_break(self):
        routines = [slot(3, time(19), time(20)), slot(3, time(18), time(19))]
        self.assertEqual(plan_reply(at(18, 40), routines).read_at, at(20))

    def test_sleep_continues_across_midnight(self):
        routines = [slot(3, time(22), time(0), State.SLEEP), slot(4, time(0), time(7), State.SLEEP)]
        timing = plan_reply(at(23, 40), routines)
        self.assertEqual(timing.read_at, at(7, day=20))
        self.assertEqual(timing.reply_at, at(7, day=20) + timedelta(seconds=30))

    def test_no_routine_is_available(self):
        timing = plan_reply(at(18, 40), [])
        self.assertEqual(timing.read_at, at(18, 40))
        self.assertEqual(timing.reply_at, at(18, 40) + timedelta(seconds=30))
