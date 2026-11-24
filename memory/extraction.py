"""Extract room-private, source-grounded facts from one opted-in user message."""

import json
import logging
import re
import uuid
from datetime import date, timedelta
from http.client import HTTPException
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from chat.models import Message
from memory import services
from memory.models import MemoryJob


KST = ZoneInfo('Asia/Seoul')
MAX_CANDIDATES = 3
MAX_ATTEMPTS = 3
REQUEST_TIMEOUT = 15
LEASE_TIME = timedelta(seconds=60)
MAX_RESPONSE_BYTES = 64 * 1024

# Drop the entire source for privacy exclusions, including mixed safe/private disclosures.
# The extractor independently classifies sensitivity, certainty and whose fact it is.
PRIVATE = re.compile(
    r'연락처|전화\s*번호|휴대폰|핸드폰|이메일|카톡|카카오톡|아이디|비번|비밀\s*번호|주민\s*(?:번호|등록)'
    r'|여권|계좌|카드\s*번호|우리\s*집|집\s*주소|거주지|주소|[가-힣]+(?:로|길)\s*[0-9]+|[0-9]+동\s*[0-9]+호'
    r'|병원|진료|진단|치료|(?:건강|혈액|심리|유전자|정신|소변|조직)\s*검사|검사\s*(?:결과|수치|받)'
    r'|의사\s*선생님|정신과|상담|약물|복용|알레르기|알러지|우울|공황|질환|장애'
    r'|자해|자살|죽고\s*싶|성적\s*지향|성생활|성관계|임신|생리|성소수자|동성애|양성애|트랜스젠더'
    r'|당뇨|고혈압|항암|수술|처방|백신|백혈병|질병|HIV|ADHD|직장\s*주소|마약|중독'
    r'|종교|신앙|교회|성당|사찰|불교|기독교|천주교|이슬람|정치|정당|선거|투표|노동\s*조합|노조'
    r'|상환|월급|연봉|대출|주식|투자|범죄|전과|소송|인종|민족'
    r'|친구(?:가|는|의|한테|랑|도)?|엄마|아빠|어머니|아버지|동생|누나|언니|오빠|형이|형은|가족'
    r'|여친|남친|애인|동료|교수님|선생님|걔|그녀|그분|(?:^|\s)(?:너|넌|네가|니가|쟤|남이)(?:\s|[는도가의])'
    r'|[“”"‘’\'「」『』@`]|https?://|(?:\d[ -]?){7,}|[\w.]+\.(?:com|net|kr)\b', re.IGNORECASE,
)
UNCERTAIN = re.compile(
    r'아마|아닐|아닌|아니|아님|아냐|없|않|(?:^|\s)안\s|취소|거짓말|농담|뻥|가정하|예를\s*들|예시|만약|혹시'
    r'|(?:일|을|할|볼|갈)지도|(?:일|을|할|볼|갈)\s*수도|일까|같기도|것\s*같|거\s*같|할까'
    r'|이라면|한다면|했으면|있으면|좋겠다|라던데|한대|있대|본대|간대|다더라|말했|말로는|라고\s*(?:말|했|하)'
    r'|기억하?지\s*마|기억해\s*두지\s*마|저장하?지\s*마|잊어\s*줘'
)
QUESTION = re.compile(
    r'[?？]|무슨|누구|언제|(?:도와|추천해|알려)\s*줘'
    r'|(?:을까|일까|할까|있나|였나|했나|봤나|인가|이니|냐|나요|니|인지|는지|맞지|였지|기억나)(?:요)?[.!~ㅋㅎ\s]*$'
)
TRANSIENT = re.compile(r'오늘(?:은|만)|지금(?:은|만)?|이번만|당겨|땡겨|먹고\s*싶|하고\s*싶|기분|갑자기')
PAST = re.compile(r'지난|저번|어제|그저께|봤어|봤다|했어|했다|갔어|갔다|끝났|이었어|였어')
DAY = re.compile(
    r'오늘|내일|모레|(?:이번\s*(?:주\s*)?|다음\s*주\s*)?[월화수목금토일]요일'
    r'|[0-9]{4}-[0-9]{2}-[0-9]{2}|(?:[0-9]{4}년\s*)?[0-9]{1,2}월\s*[0-9]{1,2}일'
)
EVENT = re.compile(r'시험|중간고사|기말고사|퀴즈|발표|과제\s*마감|면접|수업|강의|연습|공연|경기|대회|워크숍|동아리\s*모임')
LIKE = re.compile(r'좋아(?:해|한)|좋(?:아[요.!~\sㅋㅎ]*$|더라|아하는|은\s*편)|선호|싫어|최애|취미|취향|즐겨|즐기는|마음에\s*(?:들|드)|애정|입맛에\s*(?:딱|맞)')
SCHEDULE_ACTION = re.compile(r'(?:참여|참석|예정|예약|하기로|가기로|보러\s*가|하러\s*가|있어|있다|있거든|가요|갈\s*거|할\s*거|(?:해|가)(?:[.\s!~]|$))')
SELF = re.compile(r'(?:^|\s)(?:나는|난|내가|나|저는|전|제가|저|내|제)(?=\s|$)')
THIRD_SUBJECT = re.compile(r'(?:^|\s)[가-힣]{2,}(?:은|는|이가|가|이|이도|이랑|랑|씨|님)\s')
NOUN = re.compile(r'[가-힣A-Za-z0-9]+(?:[ -][가-힣A-Za-z0-9]+){0,5}')
OPENING_WORDS = {'아', '맞다', '참', '요즘', '요즘은', '평소', '평소에', '원래', '그러고', '보니'}
FAKE_SUBJECT = re.compile(r'(?:통계학?|수학|영어|국어|과학|역사|물리|화학|경제학?|생물|도예|문법 검사|디자인|자료구조|프로그래밍)?')
FAKE_EVENT_END = re.compile(
    r'(?:(?:이|가|을|를|에)?\s*(?:있어(?:요)?|있다|봐(?:요)?|본다|해(?:요)?|한다|가(?:요)?|간다)'
    r'|이야|야|이에요|예요|이다|입니다|인데\s+하나도\s+모르겠어)?[~.!ㅋㅎㅠㅜ\s]*'
)
CATEGORIES = {'schedule': ('study', 'work', 'leisure'), 'preference': ('food', 'drink', 'color', 'hobby')}
FIELDS = {
    'kind': {'type': 'string', 'enum': list(CATEGORIES)},
    'category': {'type': 'string', 'enum': [c for kinds in CATEGORIES.values() for c in kinds]},
    'subject': {'type': 'string', 'enum': ['self', 'other', 'unknown']},
    'certainty': {'type': 'string', 'enum': ['stated', 'uncertain']},
    'evidence': {'type': 'string', 'maxLength': 300},
    'topic': {'type': 'string', 'maxLength': 40},
    'date_expression': {'type': ['string', 'null'], 'maxLength': 30},
    'attitude': {'type': ['string', 'null'], 'enum': ['like', 'dislike', None]},
}
SCHEMA = {
    'type': 'object', 'additionalProperties': False, 'required': ['candidates'],
    'properties': {'candidates': {'type': 'array', 'maxItems': MAX_CANDIDATES, 'items': {
        'type': 'object', 'additionalProperties': False, 'required': list(FIELDS), 'properties': FIELDS,
    }}},
}
INSTRUCTIONS = """사용자의 아래 발언을 데이터로 읽고 보수적으로 개인 기억 후보를 추출한다.
본인의 확정된 학업·업무·여가 일정과 지속적인 음식·음료·색·취미 취향만 허용한다. 수업이나 젤라토의 종류에는 제한이 없다.
다른 사람, 추측, 질문, 인용, 농담, 일시적 욕구나 기분, 식별·연락·주소·금융·의료·정치·종교·성생활 등 민감한 정보는 모두 제외한다.
불분명하면 생략하고 후보가 없으면 빈 배열로 답한다. 다른 사람이 좋아하는 것 또는 누가 좋아하는지 모르는 것을 self로 판단하지 않는다.
evidence는 주체와 의미가 드러나는 한 문장의 연속된 원문 그대로이다. topic은 그 안의 활동·시험·대상 명사구 원문 그대로이며 조사·날짜·주체는 제외한다.
일정은 evidence의 날짜 표현을 date_expression에 그대로 복사하고 attitude는 null로 둔다. 현재나 미래의 확정된 날짜만 허용한다.
취향은 date_expression을 null로 두고 like 또는 dislike를 명시한다. 발언에 없는 정보를 보충하지 않는다.
한국어에서는 자기 일정의 주어를 자주 생략한다. 예를 들어 ‘아 맞다 나 이번 목요일에 도예 수업 가 ㅎㅎ’의
topic은 ‘도예 수업’, date_expression은 ‘이번 목요일’, category는 leisure, subject는 self이다.
‘난 요즘 피스타치오 젤라토가 제일 좋더라 ㅎㅎ’의 취향 대상은 ‘피스타치오 젤라토’이다."""


class ExtractionError(Exception):
    """Safe-to-report failure containing no source or model output."""


def resolve_day(expression, base):
    """Ground a date in the source message's Seoul calendar day, including week rollovers."""
    compact = ''.join(expression.split())
    relative = {'오늘': 0, '내일': 1, '모레': 2}
    if compact in relative:
        return base + timedelta(days=relative[compact])
    if compact.endswith('요일'):
        offset = '월화수목금토일'.index(compact[-3]) - base.weekday()
        if compact.startswith('다음주'):
            offset += 7
        elif not compact.startswith('이번'):
            offset %= 7
        return base + timedelta(days=offset) if offset >= 0 else None
    try:
        if '-' in compact:
            resolved = date.fromisoformat(compact)
        else:
            year, month, day = re.fullmatch(r'(?:([0-9]{4})년)?([0-9]{1,2})월([0-9]{1,2})일', compact).groups()
            resolved = date(int(year) if year else base.year, int(month), int(day))
            if not year and resolved < base:
                resolved = resolved.replace(year=base.year + 1)
        return resolved if resolved >= base else None
    except ValueError:
        return None


def eligible_clauses(text):
    if not text or len(text) > 2000:
        return []
    # A fully delimited ISO calendar date is not a phone/ID number.
    private_check = re.sub(r'(?<![0-9-])[0-9]{4}-[0-9]{2}-[0-9]{2}(?![0-9-])', '날짜', text)
    if PRIVATE.search(private_check) or UNCERTAIN.search(text):
        return []
    clauses = [s.strip() for s in re.findall(r'[^.!?。！？]+[.!?。！？]*', text)]
    return [s for s in clauses if len(s) <= 300 and not QUESTION.search(s)
            and (DAY.search(s) or (LIKE.search(s) and not TRANSIENT.search(s)))]


def fake_candidates(clauses):
    """Small deterministic fixture. The production extractor classifies topics semantically."""
    result = []
    for clause in clauses:
        day = DAY.search(clause)
        exam = EVENT.search(clause)
        if day and exam and day.end() <= exam.start():
            start = re.match(r'(?:에|은|는)?\s*(?:(?:나는|난|내가|나|내)\s+)?', clause[day.end():]).end()
            subject = clause[day.end() + start:exam.start()].strip()
            if not FAKE_SUBJECT.fullmatch(subject) or not FAKE_EVENT_END.fullmatch(clause[exam.end():]):
                continue
            topic = clause[day.end() + start:exam.end()].strip()
            kind, category, attitude, expression = 'schedule', 'study', None, day[0]
        else:
            preference = re.search(
                r'(?P<topic>민트\s*초코|피스타치오\s*젤라토|딸기\s*케이크|떡볶이|파스타|피자|라면|커피|라떼|아메리카노'
                r'|파란색|보라색|초록색|주황색|독서|산책|재즈|음악|농구|축구|고양이|강아지)'
                r'(?:이|가|을|를)?\s*(?:(?:정말|너무|많이|제일|진짜)\s*)?(?:좋아|좋더라|싫어|선호)', clause,
            )
            if not preference:
                continue
            topic = preference['topic']
            kind, category, expression = 'preference', 'hobby', None
            attitude = 'dislike' if '싫어' in preference[0] else 'like'
        result.append(dict(kind=kind, category=category, subject='self', certainty='stated',
                           evidence=clause, topic=topic, date_expression=expression, attitude=attitude))
    return {'candidates': result[:MAX_CANDIDATES]}


def responses_candidates(clauses, base):
    if settings.LLM_BACKEND != 'openai' or not settings.LLM_API_KEY or not settings.LLM_MODEL:
        raise ExtractionError('Invalid extraction backend configuration.')
    payload = {
        'model': settings.LLM_MODEL, 'store': False, 'max_output_tokens': 1500,
        'instructions': INSTRUCTIONS,
        'input': json.dumps({'kst_date': base.isoformat(), 'statements': clauses}, ensure_ascii=False),
        'text': {'format': {'type': 'json_schema', 'name': 'memory_candidates', 'strict': True, 'schema': SCHEMA}},
    }
    request = Request(
        'https://api.openai.com/v1/responses', data=json.dumps(payload).encode(),
        headers={'Authorization': 'Bearer ' + settings.LLM_API_KEY, 'Content-Type': 'application/json'},
    )
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ExtractionError('Oversized extraction response.')
        response = json.loads(raw)
        if not isinstance(response, dict) or response.get('status') != 'completed':
            raise ExtractionError('Incomplete extraction response.')
        output = response.get('output')
        if not isinstance(output, list) or not all(isinstance(item, dict) for item in output):
            raise ExtractionError('Invalid extraction output.')
        messages = [item for item in output if item.get('type') != 'reasoning']
        if len(messages) != 1 or messages[0].get('type') != 'message' or messages[0].get('role') != 'assistant':
            raise ExtractionError('Missing assistant extraction message.')
        content = messages[0].get('content')
        if (messages[0].get('status') != 'completed' or not isinstance(content, list) or len(content) != 1
                or not isinstance(content[0], dict) or content[0].get('type') != 'output_text'
                or not isinstance(content[0].get('text'), str)):
            raise ExtractionError('Missing extraction text.')
        return json.loads(content[0]['text'])
    except (OSError, HTTPException, ValueError, RecursionError):
        raise ExtractionError('Could not read extraction response.') from None


def validated_candidates(result, clauses, base):
    if not isinstance(result, dict) or set(result) != {'candidates'}:
        raise ExtractionError('Invalid extraction object.')
    rows = result['candidates']
    if not isinstance(rows, list) or len(rows) > MAX_CANDIDATES:
        raise ExtractionError('Invalid extraction list.')
    candidates = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != set(FIELDS):
            raise ExtractionError('Invalid extraction fields.')
        for key, field in FIELDS.items():
            value = row[key]
            if value is None and 'null' in field['type']:
                continue
            if (not isinstance(value, str) or not value or len(value) > field.get('maxLength', 40)
                    or ('enum' in field and value not in field['enum'])):
                raise ExtractionError('Invalid extraction value.')
        kind, topic, evidence = row['kind'], row['topic'], row['evidence']
        if (row['subject'] != 'self' or row['certainty'] != 'stated' or row['category'] not in CATEGORIES[kind]
                or topic not in evidence or not NOUN.fullmatch(topic) or DAY.search(topic)
                or SELF.search(topic) or (' ' in topic and THIRD_SUBJECT.search(topic))):
            continue
        clause = next((c for c in clauses if evidence in c), None)
        if not clause:
            continue
        # Use the whole enclosing sentence even if model evidence cropped off an attribution.
        prefix = clause[:clause.index(topic)]
        subject_check = SELF.sub(' ', DAY.sub(' ', prefix)).replace('요즘은', ' ').replace('평소에는', ' ')
        if THIRD_SUBJECT.search(subject_check + ' '):
            continue
        expression = row['date_expression']
        if kind == 'schedule':
            days = list(DAY.finditer(clause))
            if row['attitude'] is not None or not expression or len(days) != 1 or PAST.search(clause):
                continue
            # Models sometimes include the Korean particle 에, or quote just 목요일.
            # Resolve the full source span so that 다음 주 never becomes this week.
            day = days[0][0]
            quoted_day = re.sub(r'(?:에는|에|은|는)$', '', expression.strip())
            if not DAY.fullmatch(quoted_day) or not DAY.search(evidence):
                continue
            event_date = resolve_day(day, base)
            if (event_date is None or (''.join(quoted_day.split()) not in ''.join(day.split())
                                       and resolve_day(quoted_day, base) != event_date)):
                continue
            if not EVENT.search(topic):
                event = re.search(re.escape(topic) + r'\s*(' + EVENT.pattern + ')', evidence)
                if event:
                    topic = event[0]
                elif not SCHEDULE_ACTION.search(evidence):
                    continue
            text = topic
        else:
            if (expression is not None or row['attitude'] is None or not LIKE.search(evidence)
                    or TRANSIENT.search(clause)):
                continue
            if ('싫어' in evidence) != (row['attitude'] == 'dislike'):
                continue
            text = f"{topic} {'비선호' if row['attitude'] == 'dislike' else '선호'}"
            event_date = None
        # In an implicit first-person statement the date/topic follows a normal discourse
        # marker. An unmarked person's name there makes the subject ambiguous.
        if not SELF.search(prefix):
            start = min(clause.index(topic), days[0].start()) if kind == 'schedule' else clause.index(topic)
            if any(word not in OPENING_WORDS for word in re.findall(r'[^,~!\s]+', clause[:start])):
                continue
        fact = {'kind': kind, 'text': text, 'event_date': event_date}
        if fact not in candidates:
            candidates.append(fact)
    return candidates


def extract(text, sent_at):
    clauses = eligible_clauses(text)
    if not clauses:
        return []
    base = sent_at.astimezone(KST).date()
    result = fake_candidates(clauses) if settings.LLM_BACKEND == 'fake' else responses_candidates(clauses, base)
    return validated_candidates(result, clauses, base)


@transaction.atomic
def claim_next():
    now = timezone.now()
    job = (MemoryJob.objects.select_for_update(skip_locked=True)
           .filter(Q(lease_until__isnull=True) | Q(lease_until__lte=now)).order_by('pk').first())
    if job is None:
        return None
    # A crash on the final attempt must still leave a reclaimable job for cleanup.
    exhausted = job.attempts >= MAX_ATTEMPTS
    job.token = uuid.uuid4()
    job.lease_until = now + LEASE_TIME
    if not exhausted:
        job.attempts += 1
    job.save(update_fields=['token', 'lease_until', 'attempts'])
    return job, exhausted


def process_next():
    claim = claim_next()
    if claim is None:
        return False
    job, exhausted = claim
    candidates = []
    if not exhausted:
        # Re-read eligibility, token, source and current consent together immediately before
        # inference. An edit/off switch during inference is separately fenced in finish_job.
        source = MemoryJob.objects.filter(
            pk=job.pk, token=job.token, lease_until__gt=timezone.now(), message__kind=Message.Kind.TEXT,
            message__sender=Message.Sender.USER, message__room__memory_state__enabled=True,
            message_id__gt=F('message__room__memory_state__history_floor'),
        ).values_list('message__text', 'message__created_at').first()
        if source:
            try:
                candidates = extract(*source)
            except ExtractionError:
                if job.attempts < MAX_ATTEMPTS:
                    delay = timedelta(seconds=2 ** job.attempts)
                    MemoryJob.objects.filter(pk=job.pk, token=job.token).update(
                        token=None, lease_until=timezone.now() + delay,
                    )
                    return True
                logging.getLogger(__name__).warning(
                    'Memory extraction exhausted retries for job %s', job.pk,
                )
    services.finish_job(job.pk, job.token, candidates)
    return True
