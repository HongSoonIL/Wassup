"""Calculate the first reply's timing using the character's weekly routine."""
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, time, timedelta

from django.utils import timezone

from world.models import RoutineTemplate, State


AWAKE_LIMIT = timedelta(hours=3)
PREPARATION_TIME = timedelta(seconds=30)


@dataclass(frozen=True)
class ReplyTiming:
    read_at: datetime
    typing_at: datetime
    reply_at: datetime


def plan_reply(received_at: datetime, routines: Iterable[RoutineTemplate]) -> ReplyTiming:
    """Use the first opening after focus or sleep, subject to the awake limit."""
    now = timezone.localtime(received_at, timezone.get_default_timezone())
    days = range(8)
    rows = tuple(routines)
    blocks = []
    for offset in days:
        day = now.date() + timedelta(days=offset)
        for row in rows:
            if row.weekday != day.weekday() or row.state not in (State.FOCUS, State.SLEEP):
                continue
            end_day = day + timedelta(days=1) if row.end_time == time.min else day
            start = datetime.combine(day, row.start_time, tzinfo=now.tzinfo)
            end = datetime.combine(end_day, row.end_time, tzinfo=now.tzinfo)
            blocks.append((start, end, row.state))

    asleep_at_receipt = any(start <= now < end and state == State.SLEEP for start, end, state in blocks)
    opens_at = now
    for start, end, _ in sorted(blocks):
        if start <= opens_at < end:
            opens_at = end

    # A gap represents availability. The proof of concept uses a fixed lead time.
    deadline = opens_at + AWAKE_LIMIT if asleep_at_receipt else now + AWAKE_LIMIT
    reply_at = min(opens_at + PREPARATION_TIME, deadline)
    read_at = min(opens_at, reply_at)
    return ReplyTiming(read_at, read_at, reply_at)
