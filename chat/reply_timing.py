"""Plan a phone check, a short typing window, and delivery on the character's clock."""

import random
from math import ceil
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, time, timedelta

from django.utils import timezone

from world.models import RoutineTemplate, State


AWAKE_LIMIT = timedelta(hours=3)
CONVERSATION_WINDOW = timedelta(minutes=5)
PHONE_OPEN_WINDOW = timedelta(seconds=30)
# Seconds until the next phone check, before the character's speed multiplier.
CHECK_DELAY = {
    State.FREE: (16, 100),
    State.CLASS: (300, 2400),
    State.MOVING: (60, 600),
    State.LEISURE: (600, 3600),
}


@dataclass(frozen=True)
class ReplyTiming:
    read_at: datetime
    typing_at: datetime
    reply_at: datetime


def followup_timing(delivered_at, text, persona=None, rng=random):
    typing_at = delivered_at + timedelta(seconds=rng.randint(1, 2))
    cps = persona.typing_speed if persona else 6
    duration = timedelta(seconds=max(2, min(8, ceil(len(text) / cps))))
    return typing_at, typing_at + duration


def plan_reply(
    received_at: datetime,
    routines: Iterable[RoutineTemplate],
    *,
    persona=None,
    continuing=False,
    phone_open=False,
    rng=random,
) -> ReplyTiming:
    now = timezone.localtime(received_at, timezone.get_default_timezone())
    rows = tuple(routines)
    current = next(
        (
            r
            for r in sorted(rows, key=lambda r: r.start_time, reverse=True)
            if r.weekday == now.weekday()
            and r.start_time <= now.time()
            and (r.end_time == time.min or now.time() < r.end_time)
        ),
        None,
    )
    state = current.state if current else State.FREE
    speed = float(persona.reply_speed) if persona else 1
    cps = persona.typing_speed if persona else 6
    # Estimate a short reply. Model generation is independent from the visible typing signal.
    typing = timedelta(seconds=max(3, min(16, round(rng.randint(26, 55) / cps))))
    pause = timedelta(seconds=rng.randint(1, 3))
    composing = pause + typing
    delay_range = CHECK_DELAY.get(state, (6, 30))
    if state == State.SLEEP:
        delay_range = (15, 240)
    elif phone_open and state != State.FOCUS:
        delay_range = (1, 3)
    elif continuing and state == State.FREE:
        delay_range = (3, 10)
    elif continuing and state == State.LEISURE:
        delay_range = (10, 60)
    delay = timedelta(seconds=max(2, round(rng.randint(*delay_range) * speed)))

    blocked = []
    for offset in range(8):
        day = now.date() + timedelta(days=offset)
        for row in rows:
            if row.weekday != day.weekday() or row.state not in (State.FOCUS, State.SLEEP):
                continue
            end_day = day + timedelta(days=1) if row.end_time == time.min else day
            start = datetime.combine(day, row.start_time, tzinfo=now.tzinfo)
            end = datetime.combine(end_day, row.end_time, tzinfo=now.tzinfo)
            blocked.append((start, end, row.state))

    deadline = now + AWAKE_LIMIT
    available_from = now
    read_at = now + delay
    for start, end, block_state in sorted(blocked):
        if end <= now or (end <= read_at and start > now):
            continue
        if start >= min(read_at + composing, deadline):
            break
        if block_state == State.SLEEP:
            # If a phone check fits before bedtime, finish the turn before sleep.
            if start - available_from > composing + timedelta(seconds=2):
                read_at = min(read_at, start - composing)
                break
            deadline = max(deadline, end + AWAKE_LIMIT)
        available_from = max(available_from, end)
        read_at = max(read_at, end + delay)

    read_at = min(read_at, deadline - composing)
    return ReplyTiming(read_at, read_at + pause, read_at + composing)
