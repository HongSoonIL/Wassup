"""Keyless examples, including the exam-week publication regression scenes."""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .weekly import WeeklyError


def sample_week(context):
    from world.weekly import content_keys, timing_issues

    start = date.fromisoformat(context['week_start'])
    tz = ZoneInfo(context['timezone'])
    now = datetime.fromisoformat(context['now'])
    owner = context['character']
    calendar = context['school_calendar']
    assets = {photo['key']: photo['alt'] for photo in context['assets']}
    seen = set()
    for past in context['recent_posts']:
        seen.update(content_keys(past['caption'], past['public_facts']))
    rows = []

    def add(day, clock, key, caption, facts, event=None):
        publish = datetime.combine(day, clock, tz)
        at = publish - timedelta(minutes=10)
        keys = content_keys(caption, facts)
        if (
            seen & keys
            or any(p['publish_at'] == publish.isoformat() for p in rows)
            or timing_issues(start, at, publish, now, context['routines'], [event] if event else [])
        ):
            return
        seen.update(keys)
        rows.append(
            {
                'character_id': owner['id'],
                'calendar_ids': [event['id']] if event else [],
                'event_at': at.isoformat(),
                'publish_at': publish.isoformat(),
                'asset_key': key if key in assets else None,
                'photo_alt': assets.get(key, ''),
                'caption': caption,
                'public_facts': facts,
                'editor_note': '',
            }
        )

    artist = owner['name'] == '서윤'
    if artist and 'studio-print' not in assets and 'dubu-home' in assets:
        add(start + timedelta(days=4), time(19, 50), 'dubu-home',
            '담요 펴자마자 와서 자리 잡음', ['두부가 겨자색 담요 위에 앞발을 포개고 앉았다.'])
    elif not artist and 'cafe-interior' not in assets and 'cafe-close' in assets:
        add(start + timedelta(days=3), time(22, 5), 'cafe-close',
            '피처까지 엎어 놓고 퇴근', ['카페에서 마감하며 씻은 피처를 바에 엎어 두었다.'])
    for event in calendar:
        first = date.fromisoformat(event['start_date'])
        if artist and event['kind'] == 'exams':
            day = max(start + timedelta(days=2), first)
            add(
                day,
                time(16, 30),
                'studio-print',
                '둘 다 뽑아 보길 잘했다. 오늘은 주황 🍊',
                ['군청색과 주황색으로 포스터를 한 장씩 인쇄했다.', '최종 색으로 주황을 골랐다.'],
                event,
            )
            add(
                day + timedelta(days=1),
                time(18),
                None,
                '책상 치우다가 작년 스케치북 발견',
                ['책상을 정리하다가 스케치북을 찾았다.'],
                event,
            )
        elif not artist and event['kind'] == 'sports':
            add(
                first - timedelta(days=1),
                time(17, 50),
                'cafe-interior',
                '내일 체육대회 몇 시에 모이냐… 커피부터 사야겠다',
                ['체육대회 전날 카페에 들렀다.'],
                event,
            )
        if len(rows) >= 2:
            break

    # Prefer separate scenes; a late-week request can still use two nearby time slots.
    for offset, clock in [(2, time(17, 55)), (6, time(15, 12))] + [
        (d, t) for d in range(7) for t in (time(13, 20), time(17, 45), time(20, 15), time(22, 40))
    ]:
        if len(rows) >= 2:
            break
        day = start + timedelta(days=offset)
        mark = f'{day.month}월 {day.day}일'
        if artist:
            if rows:
                add(
                    day,
                    clock,
                    None,
                    f'{mark} 낙서 구석에 두부 발자국 ㅎㅎ',
                    [f'{mark} 스케치북에 두부 발자국을 그렸다.'],
                )
            else:
                add(
                    day,
                    clock,
                    'studio-print',
                    f'{mark} 작업 기록 🍊',
                    [f'{mark} 책상에 포스터 시안을 펼쳤다.'],
                )
        elif rows:
            add(day, clock, 'cafe-interior', f'{mark} 창가 자리 겨우 잡음', [f'{mark} 카페 창가에 앉았다.'])
        else:
            add(
                day, clock, 'cafe-interior', f'{mark} 또 아아 ㅋㅋ', [f'{mark} 카페에서 아이스커피를 마셨다.']
            )
    if len(rows) < 2:
        raise WeeklyError('이번 주 남은 일과에 후보 두 개를 배치할 수 없어요. 다음 주 계획을 열어 주세요.')
    return {'candidates': rows[:3]}
