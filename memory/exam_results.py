"""Source-grounded exam results and their dialogue-safe projection."""

import re
from datetime import datetime, timedelta

from django.utils import timezone


# A bare number needs a recognizable course. With an explicit exam name, accept
# other course names too. Match the entire message so goals or attributed speech
# cannot become a confirmed personal result.
COURSES = r'통계학?|수학|영어|국어|과학|역사|물리|화학|경제학?|생물|자료구조|프로그래밍|도예|디자인'
EXAM = r'중간고사|기말고사|중간|기말|시험|퀴즈'
LABEL = rf'(?:(?:{COURSES})(?:\s*(?:{EXAM}))?|[가-힣A-Za-z]{{2,16}}\s+(?:{EXAM}))'
PIN = r'(?:이\s*(?:점수|결과)(?:는|를)?\s*)?(?:꼭\s*)?기억해\s*(?:줘|둬)'
SCORE = re.compile(
    rf'(?:(?:아|맞아|아니|정정|사실|이번에|어제|오늘|나|난|나는|내가|내)[,\s]+)*'
    rf'(?:(?P<label>{LABEL})(?:에서|은|는|이|가)?\s*)?'
    r'(?:(?P<old>100|[0-9]{1,2})\s*점?\s*(?:이?\s*아니라|말고)\s*)?'
    r'(?P<score>100|[0-9]{1,2})\s*(?(old)점?|점)(?:을)?\s*'
    r'(?:받았어|받았다|받음|나왔어|나왔다|나옴|이었어|이었지|였어|야|이야|이다|임|이었음)?'
    rf'[.!~,\sㅋㅎ]*(?P<pin>{PIN})?[.!~\sㅋㅎ]*'
)
PIN_ONLY = re.compile(rf'{PIN}[.!~\s]*')
RESULT_CUE = re.compile(r'몇\s*점|점수|성적|결과|점대|점이|점\s*(?:받|나)|시험.*(?:어땠|어떻게|기억)')
OTHER = re.compile(r'친구|동생|형은|언니|걔|(?:^|\s)(?:네|너|넌|니|쟤)(?:가|는|의|도)?(?:\s|$)')
# Questions about grades have to ask for the value or a comparison. A bare
# mention of grades is common when telling a friend about something else.
RECALL = re.compile(
    r'(?:몇\s*점(?:대)?\s*(?:이었(?:더라|지|어|나|는지|을까)?|인지|일까|이더라|이라고|이랬지|이라\s*했지|이야|이지|인데|임'
    r'|받았(?:더라|지|어|나|는지)|나왔(?:더라|지|어|나|는지))?(?:\s*(?:기억(?:나|해)|알아|말했지|알려\s*줘))?'
    r'|(?:점수|성적|결과)(?:가|는|를|이)?\s*(?:얼마(?:였지|였어|였더라)?|뭐였(?:지|어|더라)|기억(?:나|해)|알려\s*줘)'
    r'|(?:점수|성적|결과)[^?？.!]*?비교(?:해\s*줘|하면\s*어때|해볼래)'
    r'|(?:점수|성적|결과)[^?？.!]*?(?:올랐|떨어졌|높았|낮았)(?:나|지|어(?=\s*[?？]))'
    r'|(?:점수|성적|[0-9]+\s*점)[^?？.!]*?보다\s*(?:더\s*)?(?:높아|낮아|오른|떨어진|높은|낮은|잘\s*본)(?:\s*(?:거야|거지|건가))?'
    r'|(?:점수|성적)[^?？.!]*?어땠(?:더라|지|어)|[0-9]+\s*점(?:이었(?:나|지)|\s*맞(?:아|지)))'
    r'[?？!.~ㅋㅎㅠㅜ\s]*$'
)
GRADE_DETAIL = re.compile(r'(?:[0-9]{1,3}(?:몇)?|(?<![가-힣])[일이삼사오육칠팔구십백]+)\s*점(?:대)?|한 자릿수|만점')
BARE_GRADE = re.compile(r'(?<![0-9])[0-9]{1,3}(?=(?:이었|였|이야|이라|이잖|이지|일걸|이더라|[?!.,~\s]*$))')
NUMBER = re.compile(r'(?<!\d)(?:100|[0-9]{1,2})(?!\d)')


def matching_results(items, text):
    """Resolve explicit course and exam type, declining ambiguous references."""
    if OTHER.search(text):
        return []
    names = {item['text'].split()[0] for item in items if item['text'].split()[0] in text}
    if any(not any(course in name for name in names) for course in re.findall(COURSES, text)):
        return []
    if '중간' in text and '기말' in text:
        return []
    named_exam = re.search(EXAM, text)
    if named_exam and named_exam[0] == '시험':
        named_exam = None
    choices = [item for item in items if (not names or item['text'].split()[0] in names)
               and (not named_exam or named_exam[0].removesuffix('고사') in item['text'])]
    # A question about an unknown course cannot fall back to the only stored result.
    if not names and not re.fullmatch(
        r'(?:(?:정확히|그때|나|내|내가|그|시험|중간고사|기말고사|점수|성적)\s*)*'
        r'(?:몇\s*점|점수|성적)(?:이었더라|이었지|이었어|이야|였지|이더라|였더라|인지|는|가)?[?!.\s]*', text.strip()
    ):
        return []
    return choices


def asks_for_grade(text):
    return bool(RECALL.search(text))


def previous_exam_topic(turns, courses, sources=None):
    """Follow consecutive grade questions/replies backwards to an explicit exam."""
    if not courses:
        return ''
    for turn in turns:
        if sources and turn.pk in sources:
            return sources[turn.pk]
        if any(course in turn.text for course in courses):
            return turn.text
        if re.search(COURSES, turn.text) or OTHER.search(turn.text):
            break
        if turn.sender == 'user' and not (asks_for_grade(turn.text) or SCORE.fullmatch(turn.text.strip())):
            break
        if turn.sender == 'character' and not re.fullmatch(
            r'[^?？]*(?:점|기억|모르|몰라|숫자|떠올|생각 안 나)[^?？]*[?？]?', turn.text,
        ):
            break
    return ''


def referenced(items, text, previous=''):
    if OTHER.search(text) or not asks_for_grade(text):
        return None
    choices = matching_results(items, text)
    if choices and not any(item['text'].split()[0] in text for item in choices):
        # Even a room with just one stored exam may be discussing a different one.
        if not any(item['text'].split()[0] in previous for item in choices):
            return None
        choices = matching_results(choices, previous)
    return choices[0] if len(choices) == 1 else None


def omit_previous_grades(turns, courses):
    """Keep the current disclosure and the surrounding story, without older scores."""
    exam_topic = False
    for turn in turns[:-1]:
        text = turn['text']
        mentions_exam = bool(re.search(EXAM, text) or any(course in text for course in courses))
        mentions_grade = bool(RESULT_CUE.search(text) or GRADE_DETAIL.search(text))
        if mentions_exam or exam_topic:
            cleaned = GRADE_DETAIL.sub('몇 점', text)
            turn['text'] = BARE_GRADE.sub('몇 점', cleaned)
        exam_topic = mentions_exam or (exam_topic and mentions_grade)


def record(message, state, existing):
    """Called only during new message acceptance with consent and the room lock."""
    from .extraction import PRIVATE

    text = message.text.strip()
    if len(text) > 300 or PRIVATE.search(text):
        return False
    match = SCORE.fullmatch(text)
    pin_only = PIN_ONLY.fullmatch(text)
    if (not match and not pin_only) or OTHER.search(text):
        return False
    results = existing.filter(kind='result')
    label = match and match['label']
    if label:
        if re.match(r'(?:[^ ]+(?:는|은|이|가|의)|목표|예상|아마|이번|어제|오늘)\s', label):
            return False
        label = re.sub(r'\s*(중간(?:고사)?|기말(?:고사)?|시험|퀴즈)$',
                       lambda m: ' ' + {'중간': '중간고사', '기말': '기말고사'}.get(m[1], m[1]), label)
        if ' ' not in label:
            label += ' 시험'
        item = results.filter(text=label).first()
    else:
        # Resolve only from the immediately preceding exchange. A lone score
        # after a topic change must never overwrite a different exam result.
        previous = message.room.messages.filter(
            pk__gt=max(state.history_floor, state.result_history_floor), pk__lt=message.pk,
            created_at__gte=message.created_at - timedelta(minutes=30),
        ).order_by('-pk')
        rows = list(results.values('id', 'text', 'source_id'))
        topic = previous_exam_topic(previous[:16], {r['text'].split()[0] for r in rows},
                                    {r['source_id']: r['text'] for r in rows})
        choices = matching_results(rows, topic)
        if len(choices) != 1:
            return False
        item = results.get(pk=choices[0]['id'])
        label = item.text
    if pin_only:
        if item.pinned:
            return False
        item.pinned = True
        item.save(update_fields=['pinned'])
        return True
    if match['old'] and (item is None or item.score != int(match['old'])):
        return False
    if item is None:
        item = existing.model(text=label, kind='result')
    item.source = message
    item.score = int(match['score'])
    item.confirmed_at = message.created_at
    item.pinned = item.pinned or bool(match['pin'])
    item.save()
    return True


def project(item, today):
    confirmed = item['confirmed_at']
    if isinstance(confirmed, str):
        confirmed = datetime.fromisoformat(confirmed)
    age = (today - timezone.localdate(confirmed)).days
    if item['pinned'] or age < 14:
        precision, detail = 'exact', f"{item['score']}점"
    elif age < 90 and item['score'] < 100:
        precision = 'range'
        detail = f"{item['score'] // 10 * 10}점대" if item['score'] >= 10 else '한 자릿수'
    else:
        precision, detail = 'event', ''
    return dict(id=item['id'], kind='result', text=item['text'], detail=detail, precision=precision,
                event_date=None, days_from_now=None)


def sample(memory, name):
    if memory['precision'] == 'exact':
        return f"{memory['detail']} 받았잖아" if name == '서윤' else f"{memory['detail']}이었지"
    if memory['precision'] == 'range':
        return f"{memory['detail']}였던 거 같은데?" if name == '서윤' else f"{memory['detail']}로 기억함"
    return f"{memory['text']} 본 건 기억나" if name == '서윤' else f"{memory['text']} 봤던 건 생각나는데 점수까진 모르겠다"


def limit_history(turns, memories, today):
    """A reply may cross a precision boundary after the user sent the message.
    Hide old digits in both sides of the exchange, including quoted guesses."""
    hidden = set()
    old_ranges = set()
    courses = set()
    for memory in memories:
        if memory['kind'] != 'result':
            continue
        precision = project(memory, today)['precision']
        if precision == 'exact':
            continue
        hidden.add(str(memory['score']))
        courses.add(memory['text'].split()[0])
        if precision == 'event':
            old_ranges.add(str(memory['score'] // 10 * 10) + '점대')
            if memory['score'] < 10:
                old_ranges.add('한 자릿수')
    if not hidden:
        return
    for turn in turns:
        text = turn['text']
        if (turn['sender'] == 'character' or '점' in text or RESULT_CUE.search(text)
                or any(course in text for course in courses)):
            for detail in old_ranges:
                text = text.replace(detail, '몇 점')
            turn['text'] = NUMBER.sub(
                lambda m: '몇' if m[0] in hidden and not text[m.end():].startswith('점대') else m[0], text,
            )


def overprecise(bubbles, memory):
    if memory['precision'] == 'exact':
        return False
    for bubble in bubbles:
        remaining = bubble.replace(memory['detail'], '') if memory['detail'] else bubble
        if NUMBER.search(remaining) or re.search(r'(?:[일이삼사오육칠팔구십백]+\s*점|만점)', remaining):
            return True
    return False
