from random import Random
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.test import SimpleTestCase

from chat.reply_timing import plan_reply
from world.management.commands.seed_world import CHARACTERS
from world.models import Persona, RoutineTemplate, State


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
        self.assertTrue(at(20, 10) < timing.read_at < timing.typing_at < timing.reply_at < at(20, 20))

    def test_adjacent_blocks_do_not_create_a_break(self):
        routines = [slot(3, time(19), time(20)), slot(3, time(18), time(19))]
        self.assertTrue(at(20) < plan_reply(at(18, 40), routines).read_at < at(20, 2))

    def test_sleep_continues_across_midnight(self):
        routines = [slot(3, time(22), time(0), State.SLEEP), slot(4, time(0), time(7), State.SLEEP)]
        timing = plan_reply(at(23, 40), routines)
        self.assertTrue(
            at(7, day=20) < timing.read_at < timing.typing_at < timing.reply_at < at(7, 30, day=20)
        )

    def test_persona_state_and_active_conversation(self):
        now = at(12, 15)
        fast = Persona(reply_speed=0.7, typing_speed=7)
        slow = Persona(reply_speed=1.3, typing_speed=5)

        def timing(persona, state=State.FREE, continuing=False):
            return plan_reply(
                now,
                [slot(3, time(12), time(16), state)],
                persona=persona,
                continuing=continuing,
                rng=Random(20),
            )

        available = timing(fast)
        self.assertTrue(now < available.read_at < available.typing_at < available.reply_at)
        self.assertLess(available.read_at, timing(slow).read_at)
        self.assertLess(
            available.reply_at - available.typing_at, timing(slow).reply_at - timing(slow).typing_at
        )
        self.assertLess(timing(fast, continuing=True).read_at, available.read_at)
        self.assertGreater(timing(fast, State.CLASS, continuing=True).read_at, available.read_at)
        self.assertGreater(timing(fast, State.MOVING).read_at, available.read_at)

    def test_planned_check_does_not_start_typing_when_focus_begins(self):
        now = at(18, 59) + timedelta(seconds=59)
        timing = plan_reply(now, [slot(3, time(19), time(19, 15))], rng=Random(20))
        self.assertGreater(timing.read_at, at(19, 15))

    def test_long_shift_before_bedtime_keeps_awake_deadline(self):
        routines = [slot(3, time(17), time(23)), slot(3, time(23), time(0), State.SLEEP)]
        timing = plan_reply(at(18), routines)
        self.assertEqual(timing.reply_at, at(21))
        self.assertTrue(at(18) < timing.read_at < timing.typing_at < timing.reply_at)
