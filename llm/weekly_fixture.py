"""Deterministic Korean scenes for the keyless backend and editorial regression tests."""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .weekly import previous_versions


def sample_week(context):
    from world.weekly import content_keys, timing_issues

    start = date.fromisoformat(context['week_start'])
    tz = ZoneInfo(context['timezone'])
    now = datetime.fromisoformat(context['now'])
    owner = context['character']
    artist = owner['name'] == '서윤'
    assets = {photo['key']: photo['alt'] for photo in context['assets']}
    calendar = context['school_calendar']
    week_number = start.isocalendar().week
    fixture_limit = 2 if calendar else (1, 2, 0, 3, 2, 1)[(week_number + int(artist)) % 6]
    limit = min(fixture_limit, context.get('candidate_limit', 3))
    seen = set()
    for past in context['recent_posts']:
        seen.update(content_keys(past['caption'], past['public_facts']))
    rows = []

    def add(day, clock, caption, facts, alt, key=None, event=None, previous_id=None):
        publish = datetime.combine(day, clock, tz)
        at = publish - timedelta(minutes=10)
        keys = content_keys(caption, facts)
        if (
            len(rows) >= limit or at <= now or seen & keys
            or any(row['publish_at'] == publish.isoformat() for row in rows)
            or timing_issues(start, at, publish, now, context['routines'], [event] if event else [])
        ):
            return
        seen.update(keys)
        rows.append({
            'character_id': owner['id'],
            'calendar_ids': [event['id']] if event else [],
            'event_at': at.isoformat(),
            'publish_at': publish.isoformat(),
            'asset_key': key if key in assets else None,
            'photo_alt': assets.get(key, alt),
            'caption': caption,
            'public_facts': facts,
            'editor_note': '',
            'previous_post_id': previous_id,
        })

    poster_facts = {
        '군청색과 주황색으로 포스터를 한 장씩 인쇄했다.', '최종 색으로 주황을 골랐다.',
    }
    eligible = previous_versions(context)
    previous = next(
        (past for past in reversed(context['recent_posts'])
         if past.get('id') in eligible and poster_facts.issubset(past['public_facts'])),
        None,
    )
    if artist and previous:
        for offset in (2, 3, 4):
            add(start + timedelta(days=offset), time(16, 35), '주황으로 뽑아서 드디어 벽에 붙였다',
                ['지난번 주황으로 정한 포스터의 최종본을 인쇄해 작업실 벽에 붙였다.'],
                '작업실 벽에 종이테이프로 붙인 커다란 주황색 포스터', previous_id=previous['id'])

    for event in calendar:
        if event['kind'] == 'sports' and not artist:
            day = date.fromisoformat(event['start_date']) - timedelta(days=1)
            add(day, time(17, 50), '내일 체육대회 몇 시에 모이냐…', ['체육대회 전날 카페 창가에서 집합 시간을 확인했다.'],
                '카페 창가의 아이스커피와 운동 가방', 'cafe-interior', event)

    for event in calendar:
        first = max(date.fromisoformat(event['start_date']), start)
        if event['kind'] == 'exams':
            if artist:
                day = max(start + timedelta(days=2), first)
                add(day, time(16, 30), '둘 다 뽑아 보길 잘했다. 오늘은 주황 🍊',
                    ['군청색과 주황색으로 포스터를 한 장씩 인쇄했다.', '최종 색으로 주황을 골랐다.'],
                    '작업대 위 군청색과 주황색 포스터 시안', 'studio-print', event)
                add(day + timedelta(days=1), time(17, 52), '어라 이게 여기 있었네',
                    ['시험 작업물을 정리하다가 작년 스케치북을 찾아 따로 챙겼다.'],
                    '책상 서랍에 반쯤 걸친 스케치북과 짧은 연필', event=event)
            else:
                add(max(start + timedelta(days=1), first), time(12, 48), '점심 먹고 딱 한 문제만 더',
                    ['시험 연습 문제 중 틀린 금액을 점심시간에 다시 계산했다.'],
                    '연습장에 놓인 작은 계산기와 파란 펜', event=event)
        elif event['kind'] == 'break':
            # A break can begin or end midweek. Only propose the scene on an actual break day.
            last = min(date.fromisoformat(event['end_date_exclusive']), start + timedelta(days=7))
            for offset in range((last - first).days):
                day = first + timedelta(days=offset)
                if artist:
                    add(day, time(19, 53), '두부 또 종이봉투 골랐어',
                        ['방학 저녁에 두부에게 새 쿠션을 꺼내 줬는데 옆에 둔 종이봉투로 들어갔다.'],
                        '방바닥의 빈 노란 쿠션과 갈색 종이봉투 밖으로 나온 흰 고양이 앞발', event=event)
                else:
                    add(day, time(14, 17), '이제 공 바람 안 빠짐', ['방학 오후에 풋살공의 헐거운 밸브를 손봤다.'],
                        '현관 타일 위 풋살공과 나란히 놓인 작은 손펌프', event=event)

    # Undated captions and facts make the same old scene ineligible during the history window.
    if artist:
        scenes = [
            (1, time(19, 50), '담요 펴자마자 와서 자리 잡음', ['두부가 겨자색 담요에 누워서 빨래 개는 걸 미뤘다.'],
             '겨자색 담요에 앉은 흰 고양이 두부', 'dubu-home'),
            (3, time(15, 41), '종이마다 잉크색이 이렇게 다르네', ['작업실에서 두 종이에 같은 잉크를 시험했다.'],
             '흰 종이와 누런 종이 위에 한 줄씩 칠한 청록색 잉크', None),
            (6, time(15, 12), '반쪽짜리도 책갈피는 되더라', ['자투리 색종이를 접어 책갈피를 만들었다.'],
             '책 모서리에 끼운 삼각형 분홍 종이와 남은 종잇조각', None),
        ]
    else:
        scenes = [
            (3, time(22, 5), '피처까지 엎어 놓고 퇴근', ['카페에서 마감하며 씻은 피처를 바에 엎어 두었다.'],
             '카페 바 위 엎어 놓은 금속 피처 두 개', 'cafe-close'),
            (5, time(14, 37), '왼발 끈만 맨날 풀리더라', ['풋살화를 꺼내 낡은 왼쪽 끈을 새것으로 갈았다.'],
             '현관 바닥의 풋살화 한 켤레와 풀어 둔 회색 신발끈', None),
            (6, time(17, 26), '이게 겨우 한 끼 분량임', ['자취방에서 볶음밥을 만들어 남은 파를 다 썼다.'],
             '주방 타일 앞 작은 프라이팬에 담긴 볶음밥과 빈 도마', None),
        ]
    if context['season'] == '겨울' and not artist:
        scenes[1] = (5, time(14, 37), '장갑 한 짝 찾음', ['풋살 가방을 비우다가 잃어버린 겨울 장갑을 찾았다.'],
                     '열린 운동 가방의 옆주머니에 걸친 털장갑 한 짝', None)
    elif context['season'] == '여름' and artist:
        scenes[2] = (6, time(15, 12), '얼음 녹는 속도를 못 따라가네', ['얼음이 녹는 모양을 스케치북에 세 번 그렸다.'],
                     '컵 속 녹아 가는 얼음과 그 옆 작은 연필 스케치 세 장', None)
    index = week_number % len(scenes)
    for offset, clock, caption, facts, alt, key in scenes[index:] + scenes[:index]:
        if len(rows) >= limit:
            break
        add(start + timedelta(days=offset), clock, caption, facts, alt, key)
    return {'candidates': rows}
