"""세계 시계.

캐릭터의 일과와 메시지 시각은 timezone.now() 대신 world_now()로 읽는다.
발표 때 "목요일 21:58"처럼 시각을 옮기거나 배속(WORLD_CLOCK_SPEED)을 걸려면
이 함수 하나만 고치면 되게 하려는 것이다. 지금은 실제 시각을 그대로 돌려준다.
"""
from django.db.models import Q
from django.utils import timezone


def world_now():
    return timezone.now()


def routine_at(character, at):
    local = timezone.localtime(at)
    return (character.routines.filter(weekday=local.weekday(), start_time__lte=local.time())
            .filter(Q(end_time__gt=local.time()) | Q(end_time='00:00'))
            .order_by('-start_time').first())
