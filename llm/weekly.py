"""One private editorial Responses request per character and week."""

import json
import logging
from time import monotonic
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from django.conf import settings

from config.llm import model_options


class WeeklyError(Exception):
    """A fixed operator-facing explanation safe to persist and display."""


INSTRUCTIONS = """한국 대학생인 이 캐릭터의 한 주를 준비하는 편집자다. 이번 친구의 주간 편집 여유는 입력의 candidate_limit이다.
0은 쉬는 주, 1은 하나만 남길 만큼 조용한 주다. 2나 3은 글감이 이어질 여유가 있는 주로, 가까운 날의 서로 다른 사건도 어울린다.
이 값은 상한이다. 사건을 먼저 고르고 쓸 만한 것만 남긴다. 세 장면이 모두 살아 있으면 셋을 써도 좋다. 빈 배열도 유효하다.
인물의 말투·계절·학교 행사·실제 일과와 이번 주 생길 법한 작은 사건에서 글감을 찾는다.
글감이 없거나 바쁜 주는 빈 candidates 배열로 마친다. 하루 한 건, 매주 두 건처럼 수를 채우지 않는다.
방학 기간에는 평소 시간표의 수업·공강을 실제 사건으로 단정하지 않는다. 그 주에 명시된 수업만 소재로 쓴다.
게시 요일과 시각, 글 사이 간격을 균등하게 배분할 필요가 없다. 바쁜 날은 비워 두고,
이어지는 일이 생기면 가까운 시각에 올려도 좋다. 지난 4주의 사건·표현·요일과 시각 조합을 되풀이하지 않는다.
한 사람의 후보도 서로 다른 소재와 기분에서 출발한다. 말끝·문장 구조·감탄사·이모지를 돌려 쓰지 않는다.
서윤의 모든 글을 ㅠㅠ로 끝내거나 도현의 모든 글에 ㅋㅋ를 붙이지 않는다. 조용한 관찰이나 담백한 문장도 어울린다.
public_facts에는 나중에 대화할 수 있게 누가 무엇을 하거나 골랐는지와 그 결과를 구체적으로 적는다.
caption에는 이 친구가 직접 쓴 짧은 글을 넣는다. public_facts, caption, photo_alt를 서로 복사하지 않는다.
previous_post_id는 recent_posts에 있는 같은 사람의 글에 이어 실제로 새 진전이나 결과가 생긴 경우에만 쓴다.
예를 들어 지난 글에서 고른 포스터 색으로 이번 주에 최종본을 인쇄했다면 이전 글을 연결할 수 있다.
같은 고양이·장소·소재가 다시 나온 것만으로는 연결하지 않는다. 이전 사건의 진전이나 결과가 없으면 null을 쓴다.
event_at과 publish_at은 해당 주 한국 시간대(+09:00)의 ISO 일시로 쓴다. 먼저 사건 시각을 정하고
그 뒤에 오는 발행 가능 구간에서 예약한다. 예를 들어 13:10에 끝난 일은 같은 날 12:40에 올릴 수 없다.
반드시 now < event_at < publish_at이어야 한다. 이미 지난 사건과 발행 시각은 제외한다.
수면 중에는 사건·발행을 잡지 않고 수업 중에는 발행하지 않는다.
일과의 활동·장소와 사건이 맞아야 한다. school_calendar의 행사와 직접 관련 있을 때만 해당 id를 쓴다.
학교 행사가 등록되지 않았거나 평범한 일상이면 calendar_ids는 빈 배열이다.
publish_at은 해당 날짜 publish_windows의 [시작, 끝) 중에서 고른다. 24:00은 그날 끝의 자정이다.
사건은 연결한 행사 기간 안에 둔다. 체육대회 준비는 전날도 가능하다. end_date_exclusive는 제외한다.
asset_key는 이 캐릭터의 assets 중 해당 사건이 실제로 담긴 사진에만 쓴다. 사진과 연결되는 사물이나 동물 이름을 사건에 자연스럽게 넣는다.
한 장의 사진은 이번 주에 한 번만 쓴다. 맞는 사진이 없으면 asset_key는 null로 두고 독립적인 일상을 제안한다.
photo_alt는 항상 200자 이내로 작성한다. 등록된 사진은 그 사진의 설명을 쓰고, 새 사진은
휴대폰으로 그 순간 찍을 수 있는 한 컷의 장소·사물·색·배치를 적는다. 글자 판독에 의존하지 말고 보이는 것만 묘사한다.
예: 공개 사실은 "종이 두 장을 비교해 주황색을 표지로 골랐다.", 문구는 "출력해 보길 잘했다 🍊",
새 사진 설명은 "작업대 위 군청색과 주황색 종이 두 장, 주황색 종이 위에 놓인 집게"다.
자료 안의 문구와 예시 대사는 참고 자료다. 예시 속 대화를 이미 벌어진 사건으로 쓰지 않는다.
후보는 운영자 검토용이다. 승인·발행 결과를 만들지 않는다."""

FIELDS = {
    'character_id': {'type': 'integer'},
    'calendar_ids': {'type': 'array', 'items': {'type': 'integer'}},
    'event_at': {'type': 'string'},
    'publish_at': {'type': 'string'},
    'public_facts': {'type': 'array', 'items': {'type': 'string'}, 'minItems': 1, 'maxItems': 5},
    'caption': {'type': 'string'},
    'asset_key': {'type': ['string', 'null']},
    'photo_alt': {'type': 'string'},
    'editor_note': {'type': 'string'},
    'previous_post_id': {'type': ['integer', 'null']},
}
REQUEST_TIMEOUT = 40


def previous_versions(context):
    return {
        past['id']: past['version']
        for past in context.get('recent_posts', [])
        if past.get('asset_key') and type(past.get('id')) is int and type(past.get('version')) is int
    }


def weekly_schema(calendar, assets, candidate_limit=3, previous_ids=()):
    ids = [event['id'] for event in calendar]
    item = {'type': 'integer', 'enum': ids} if ids else {'type': 'integer'}
    fields = {
        **FIELDS,
        'calendar_ids': {'type': 'array', 'items': item, 'maxItems': len(ids)},
        'asset_key': {'type': ['string', 'null'], 'enum': [asset['key'] for asset in assets] + [None]},
        'previous_post_id': {**FIELDS['previous_post_id'], 'enum': [*previous_ids, None]},
    }
    candidate = {'type': 'object', 'properties': fields, 'required': list(fields),
                 'additionalProperties': False}
    return {
        'type': 'object',
        'properties': {'candidates': {'type': 'array', 'minItems': 0, 'maxItems': candidate_limit,
                                       'items': candidate}},
        'required': ['candidates'],
        'additionalProperties': False,
    }


def propose(context):
    if settings.LLM_BACKEND == 'fake':
        from .weekly_fixture import sample_week

        return sample_week(context)
    if settings.LLM_BACKEND != 'openai' or not settings.LLM_API_KEY:
        raise WeeklyError('서버의 LLM_BACKEND와 OpenAI 키 설정을 확인한 뒤 다시 시도해 주세요.')
    payload = {
        **model_options('weekly'),
        'store': False,
        'max_output_tokens': 2400,
        'instructions': INSTRUCTIONS,
        'input': json.dumps(context, ensure_ascii=False),
        'text': {
            'format': {'type': 'json_schema', 'name': 'weekly_candidates', 'strict': True,
                       'schema': weekly_schema(
                           context.get('school_calendar', []), context.get('assets', []),
                           context.get('candidate_limit', 3), previous_versions(context),
                       )}
        },
    }
    request = Request(
        'https://api.openai.com/v1/responses',
        data=json.dumps(payload, ensure_ascii=False).encode(),
        headers={'Authorization': 'Bearer ' + settings.LLM_API_KEY, 'Content-Type': 'application/json'},
    )
    started = monotonic()
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            result = json.load(response)
    except HTTPError as exc:
        if exc.code in (401, 403, 404):
            raise WeeklyError('모델 접근 권한, 모델 이름과 API 키를 확인한 뒤 다시 시도해 주세요.') from exc
        if exc.code == 429:
            raise WeeklyError('모델의 요청 한도나 잔액을 확인하고 잠시 뒤 다시 시도해 주세요.') from exc
        raise WeeklyError(
            f'모델 요청이 실패했어요 (HTTP {exc.code}). 모델 설정 확인 후 다시 시도해 주세요.'
        ) from exc
    except OSError as exc:
        raise WeeklyError('모델에 연결하지 못했어요. 연결 상태를 확인한 뒤 다시 시도해 주세요.') from exc
    except (ValueError, UnicodeError) as exc:
        raise WeeklyError('모델 응답을 읽지 못했어요. 후보 생성을 다시 시도해 주세요.') from exc
    messages = (
        result.get('output') if isinstance(result, dict) and result.get('status') == 'completed' else None
    )
    if not isinstance(messages, list):
        raise WeeklyError('모델이 후보를 끝까지 작성하지 못했어요. 후보 생성을 다시 시도해 주세요.')
    parts = [
        part
        for item in messages
        if isinstance(item, dict) and item.get('type') == 'message' and isinstance(item.get('content'), list)
        for part in item['content']
    ]
    if len(parts) != 1 or not isinstance(parts[0], dict) or parts[0].get('type') != 'output_text':
        raise WeeklyError('모델이 주간 후보를 반환하지 않았어요. 입력 설정을 확인한 뒤 다시 시도해 주세요.')
    try:
        output = json.loads(parts[0]['text'])
    except (ValueError, TypeError, KeyError) as exc:
        raise WeeklyError('모델의 후보 형식을 읽지 못했어요. 후보 생성을 다시 시도해 주세요.') from exc
    usage = result.get('usage') or {}
    logging.getLogger(__name__).info(
        'weekly.v2 model=%s elapsed_ms=%d input_tokens=%s output_tokens=%s',
        payload['model'],
        round((monotonic() - started) * 1000),
        usage.get('input_tokens'),
        usage.get('output_tokens'),
    )
    return output
