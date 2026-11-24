"""Editorial inputs and validation for generated candidates and operator review."""

import re
from datetime import date, datetime, time, timedelta
from hashlib import blake2s
from unicodedata import normalize
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from llm.weekly import FIELDS, WeeklyError
from .models import Character, FeedPost, PostPhoto, SchoolEvent


KST = ZoneInfo('Asia/Seoul')


def due_week_start(now):
    local = timezone.localtime(now, KST)
    monday = local.date() - timedelta(days=local.weekday())
    if local >= datetime.combine(monday + timedelta(days=4), time(18), KST):
        return monday + timedelta(weeks=1)
    return monday


def calendar_entry(event):
    return {
        'id': event.pk,
        'revision': event.revision,
        'label': event.label,
        'kind': event.kind,
        'start_date': event.start_date.isoformat(),
        'end_date_exclusive': event.end_date_exclusive.isoformat(),
    }


def calendar_for(start):
    end = start + timedelta(days=7)
    rows = SchoolEvent.objects.filter(end_date_exclusive__gt=start).filter(
        Q(start_date__lt=end) | Q(kind=SchoolEvent.Kind.SPORTS, start_date=end)
    )
    return [calendar_entry(e) for e in rows.order_by('pk')]


def routines_for(person, start):
    return [
        {
            'date': (start + timedelta(days=r.weekday)).isoformat(),
            'weekday': '월화수목금토일'[r.weekday],
            'start': r.start_time.isoformat(),
            'end': '24:00' if r.end_time == time.min else r.end_time.isoformat(),
            'activity': r.activity,
            'state': r.state,
        }
        for r in person.routines.all()
    ]


def publish_windows(start, routines, now):
    local = timezone.localtime(now, KST)
    windows = {}
    for offset in range(7):
        day_date = start + timedelta(days=offset)
        day = day_date.isoformat()
        if day_date < local.date():
            continue
        cursor = local.time().isoformat() if day_date == local.date() else '00:00:00'
        spans = []
        for slot in routines:
            if slot['date'] != day or slot['state'] not in ('sleep', 'class'):
                continue
            if slot['start'] > cursor:
                spans.append([cursor, slot['start']])
            cursor = max(cursor, slot['end'])
        if cursor < '24:00':
            spans.append([cursor, '24:00'])
        windows[day] = spans
    return windows


def season(month):
    return ('봄', '여름', '가을', '겨울')[((month - 3) % 12) // 3]


def recent_publications(start, now):
    boundary = datetime.combine(start, time.min, KST)
    return FeedPost.objects.filter(
        state=FeedPost.State.PUBLISHED, published_at__gte=boundary - timedelta(weeks=4),
        published_at__lt=boundary, published_at__lte=now,
    )


def weekly_capacity(start, person, calendar, routines, recent_count):
    """A reproducible editorial ceiling, with more quiet draws during exams."""
    days = [(start + timedelta(days=offset)).isoformat() for offset in range(7)]
    kinds_by_day = {
        day: {event['kind'] for event in calendar if event['start_date'] <= day < event['end_date_exclusive']}
        for day in days
    }
    if sum('exams' in kinds for kinds in kinds_by_day.values()) >= 3:
        capacities = (0, 0, 1, 1, 1, 2, 2, 3)
    elif sum('break' in kinds for kinds in kinds_by_day.values()) >= 4:
        capacities = (0, 1, 1, 2, 2, 3, 3, 3)
    else:
        capacities = (0, 1, 1, 1, 2, 2, 3, 3)
    # Use the public persona, never database insertion order or process-randomized hash().
    seed = f'{start}|{season(start.month)}|{person.name}|{person.status_message}'.encode()
    draw = int.from_bytes(blake2s(seed, digest_size=4).digest(), 'big') % len(capacities)

    def minute(clock):
        return int(clock[:2]) * 60 + int(clock[3:5])

    busy_minutes = sum(
        minute(slot['end']) - minute(slot['start'])
        for slot in routines
        if slot['state'] == 'focus' or (slot['state'] == 'class' and 'break' not in kinds_by_day[slot['date']])
    )
    # Leave some breathing room after eight public posts in four weeks or a full work/study week.
    busy = recent_count >= 8 or busy_minutes >= 35 * 60
    return max(0, capacities[draw] - int(busy))


def contexts_for(start, calendar, now):
    people = list(Character.objects.filter(name__in=['도현', '서윤']).select_related('persona'))
    if (
        len(people) != 2
        or {p.name for p in people} != {'도현', '서윤'}
        or any(not hasattr(p, 'persona') for p in people)
    ):
        raise WeeklyError('seed_world로 도현과 서윤의 캐릭터·페르소나를 준비한 뒤 다시 시도해 주세요.')
    contexts = []
    for p in people:
        routines = routines_for(p, start)
        history = list(recent_publications(start, now).filter(character=p).order_by('published_at', 'pk'))
        # Generated job outputs belong to one event. Editors can still select them manually.
        assets = list(PostPhoto.objects.filter(character=p, generation_job__isnull=True)
                      .order_by('pk').values('key', 'alt'))
        previous_photos = {post.photo_id for post in history}
        contexts.append(
            {
                'week_start': start.isoformat(),
                'timezone': KST.key,
                'now': timezone.localtime(now, KST).isoformat(),
                'season': season(start.month),
                'school_calendar': calendar,
                'routines': routines,
                'publish_windows': publish_windows(start, routines, now),
                'character': {
                    'id': p.pk,
                    'name': p.name,
                    'age': p.age,
                    'major': p.major,
                    'profile_line': p.status_message,
                    'personality': p.persona.personality,
                    'voice': p.persona.speech_rules,
                    'examples': p.persona.example_dialogues,
                    'taboos': p.persona.taboos,
                },
                'assets': [asset for asset in assets if asset['key'] not in previous_photos],
                'recent_posts': [
                    {
                        'event_at': timezone.localtime(post.event_at, KST).isoformat() if post.event_at else None,
                        'published_at': timezone.localtime(post.published_at, KST).isoformat(),
                        'public_facts': post.public_facts,
                        'caption': post.caption,
                        'asset_key': post.photo_id,
                        'photo_alt': post.photo_alt if post.photo_id else '',
                    }
                    for post in history
                ],
            }
        )
        if settings.LLM_BACKEND == 'openai':
            contexts[-1]['candidate_limit'] = weekly_capacity(start, p, calendar, routines, len(history))
    return contexts


def timing_issues(start, event_at, publish_at, now, routines, calendar):
    event = timezone.localtime(event_at or publish_at, KST)
    publish = timezone.localtime(publish_at, KST)
    result = []
    if publish <= now:
        result.append('past_time')
    if event_at and event >= publish:
        result.append('event_order')
    if any(not start <= t.date() < start + timedelta(days=7) for t in (event, publish)):
        result.append('outside_week')
    for item in calendar:
        first = date.fromisoformat(item['start_date'])
        # Sports preparations can happen the day before the meet.
        distance = (event.date() - first).days
        if distance < (-1 if item['kind'] == 'sports' else 0) or event.date() >= date.fromisoformat(
            item['end_date_exclusive']
        ):
            result.append('calendar_conflict')
            break
    for slot in routines:
        for at, blocked in ((event, ('sleep',)), (publish, ('sleep', 'class'))):
            if (
                slot['date'] == at.date().isoformat()
                and slot['state'] in blocked
                and slot['start'] <= at.time().isoformat() < slot['end']
            ):
                return result + ['schedule_conflict']
    return result


def normalized(text):
    return ' '.join(normalize('NFKC', text).casefold().split())


def content_keys(caption, facts):
    # A new date on the same scene or caption does not make a new event.
    def without_dates(line):
        return normalized(re.sub(r'\d{4}-\d{2}-\d{2}|\d{1,2}월\s*\d{1,2}일', '', line))

    keys = {('fact', without_dates(fact)) for fact in facts}
    keys.add(('caption', without_dates(caption)))
    return keys


def mentions_photo_detail(description, scene):
    # This is a conservative hint for automatic pairing. A synonym-only match
    # can still be selected by an editor on the draft.
    scene = normalized(scene)
    placement_words = {'놓인', '놓여', '있는', '위에', '옆에', '앞에', '배경으로'}
    return any(len(word) >= 2 and word not in placement_words and word in scene
               for word in normalized(description).split())


def bounded_text(value, limit, allow_empty=False):
    return (
        isinstance(value, str)
        and len(value) <= limit
        and (allow_empty or bool(value.strip()))
        and '\x00' not in value
    )


def checked_time(value):
    try:
        dt = parse_datetime(value) if isinstance(value, str) else None
        if dt is None or timezone.is_naive(dt):
            raise ValueError
        return timezone.localtime(dt, KST)
    except (ValueError, OverflowError):
        raise WeeklyError(
            '사건·발행 시각에 날짜와 시간대가 필요해요. 후보 생성을 다시 시도해 주세요.'
        ) from None


def candidates_for(context, output, now):
    name = context['character']['name']

    def invalid(reason):
        raise WeeklyError(f'{name} 후보: {reason} 후보 생성을 다시 시도해 주세요.')

    if not isinstance(output, dict) or output.keys() != {'candidates'}:
        invalid('응답 형식이 맞지 않아요.')
    rows = output['candidates']
    limit = context.get('candidate_limit', 3)
    if not isinstance(rows, list) or len(rows) > limit:
        invalid(f'이번 주 후보는 0~{limit}개까지 가능해요.')
    events = {e['id']: e for e in context['school_calendar']}
    assets = {p['key']: p['alt'] for p in context['assets']}
    seen = set()
    for past in context['recent_posts']:
        seen.update(content_keys(past['caption'], past['public_facts']))
    times = set()
    used_assets = set()
    result = []
    for row in rows:
        if not isinstance(row, dict) or row.keys() != FIELDS.keys():
            invalid('필수 항목을 확인할 수 없어요.')
        owner = row['character_id']
        if type(owner) is not int or owner != context['character']['id']:
            invalid('캐릭터가 일치하지 않아요.')
        facts, caption, note = row['public_facts'], row['caption'], row['editor_note']
        if (
            not bounded_text(caption, 1000)
            or not isinstance(facts, list)
            or not 1 <= len(facts) <= 5
            or not all(bounded_text(f, 500) for f in facts)
            or not bounded_text(note, 1000, True)
        ):
            invalid('본문·공개 사실·운영 메모 형식이 맞지 않아요.')
        key, alt = row['asset_key'], row['photo_alt']
        if key is not None and (not isinstance(key, str) or key not in assets):
            invalid('캐릭터에게 등록되지 않은 사진이에요.')
        if not bounded_text(alt, 200):
            invalid('사진에 보일 구체적인 장면 설명이 필요해요.')
        ids = row['calendar_ids']
        if (
            not isinstance(ids, list)
            or not all(type(i) is int and i in events for i in ids)
            or len(ids) != len(set(ids))
        ):
            invalid('참조한 학사 일정이 잘못됐어요.')
        event, publish = checked_time(row['event_at']), checked_time(row['publish_at'])
        problems = timing_issues(
            date.fromisoformat(context['week_start']),
            event,
            publish,
            now,
            context['routines'],
            [events[i] for i in ids],
        )
        problems = [problem for problem in problems if problem != 'past_time']
        if problems:
            labels = {
                'outside_week': '선택한 주 밖의 시각이에요.',
                'event_order': '발행 시각이 사건 이후여야 해요.',
                'calendar_conflict': '학사 일정과 겹치지 않아요.',
                'schedule_conflict': '수면·수업 시간과 충돌해요.',
            }
            invalid(f'사건 {event:%m/%d %H:%M}, 예약 {publish:%m/%d %H:%M} KST. {labels[problems[0]]}')
        if event <= now:
            # A catch-up request can still yield usable scenes later in the week.
            continue
        keys = content_keys(caption, facts)
        if keys & seen or ('event', event) in times or ('publish', publish) in times:
            invalid('지난 글이나 이번 주 후보와 문구·사건·시각이 중복돼요.')
        seen.update(keys)
        times.update((('event', event), ('publish', publish)))
        # Only reserve the asset for a scene that actually mentions a detail
        # from its registered description. Models sometimes select the sole asset for an unrelated story.
        if key is not None:
            if key in used_assets or not mentions_photo_detail(assets[key], ' '.join([caption, *facts])):
                key = None
            else:
                used_assets.add(key)
                alt = assets[key]
        texts = [normalized(text) for text in [caption, *facts, alt]]
        if len(texts) != len(set(texts)):
            invalid('공개 사실·피드 문구·사진 설명을 각각 써 주세요.')
        result.append(
            {
                'character_id': owner,
                'calendar_ids': ids,
                'caption': caption.strip(),
                'public_facts': [f.strip() for f in facts],
                'event_at': event,
                'publish_at': publish,
                'photo_id': key,
                'photo_alt': alt.strip(),
                'operator_note': note.strip(),
            }
        )
    # A standing activity can recur. Avoid replaying a whole multi-post timetable.
    slots = {(r['publish_at'].weekday(), r['publish_at'].hour, r['publish_at'].minute) for r in result}
    if len(slots) >= 2:
        history_slots = {}
        for past in context['recent_posts']:
            published = checked_time(past['published_at'])
            week = published.date() - timedelta(days=published.weekday())
            history_slots.setdefault(week, set()).add((published.weekday(), published.hour, published.minute))
        if slots in history_slots.values():
            invalid('최근 한 주의 발행 요일과 시각을 그대로 반복했어요.')
    return result
