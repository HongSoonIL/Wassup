"""One private editorial Responses request per character and week."""

import json
import logging
from time import monotonic
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from django.conf import settings


class WeeklyError(Exception):
    """A fixed operator-facing explanation safe to persist and display."""


INSTRUCTIONS = """한국 대학생인 이 캐릭터의 한 주를 준비하는 편집자다. 한국어 후보 2~3개를 쓴다.
인물의 말투·계절·학교 행사·실제 일과와 이번 주 생길 법한 작은 사건에서 글감을 찾는다.
방학 기간에는 평소 시간표의 수업·공강을 실제 사건으로 단정하지 않는다. 그 주에 명시된 수업만 소재로 쓴다.
게시 요일과 시각, 글 사이 간격을 균등하게 배분할 필요가 없다. 바쁜 날은 비워 두고,
이어지는 일이 생기면 가까운 시각에 올려도 좋다. 지난 4주에 이미 올린 사건과 표현을 반복하지 않는다.
한 사람의 후보도 서로 다른 소재와 기분에서 출발한다. 말끝·문장 구조·감탄사·이모지를 돌려 쓰지 않는다.
서윤의 모든 글을 ㅠㅠ로 끝내거나 도현의 모든 글에 ㅋㅋ를 붙이지 않는다. 조용한 관찰이나 담백한 문장도 어울린다.
공개 사실에는 일어난 일을 구체적으로, caption에는 이 친구가 직접 쓴 짧은 글을 넣는다.
event_at과 publish_at은 해당 주 한국 시간대(+09:00)의 ISO 일시로 쓴다. 사건 이후이면서
now보다 늦게 발행한다. 수면 중에는 사건·발행을 잡지 않고 수업 중에는 발행하지 않는다.
일과의 활동·장소와 사건이 맞아야 한다. 달력 ID는 실제 관련 있는 경우만 쓴다.
publish_at은 해당 날짜 publish_windows의 [시작, 끝) 중에서 고른다. 24:00은 그날 끝의 자정이다.
사건은 연결한 행사 기간 안에 둔다. 체육대회 준비는 전날도 가능하다. end_date_exclusive는 제외한다.
캐릭터 본인의 assets 중 장면에 맞는 사진을 고른다. photo_alt에는 사진에 보이는 내용만 쓴다.
한 장의 사진은 한 주에 한 후보에만 쓴다. 이미 고른 사진뿐이거나 어울리는 사진이 없으면
다른 일상을 쓰고 asset_key는 null, photo_alt는 빈 문자열로 둔다.
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
}
SCHEMA = {
    'type': 'object',
    'properties': {
        'candidates': {
            'type': 'array',
            'minItems': 2,
            'maxItems': 3,
            'items': {
                'type': 'object',
                'properties': FIELDS,
                'required': list(FIELDS),
                'additionalProperties': False,
            },
        },
    },
    'required': ['candidates'],
    'additionalProperties': False,
}
REQUEST_TIMEOUT = 40


def propose(context):
    if settings.LLM_BACKEND == 'fake':
        from .weekly_fixture import sample_week

        return sample_week(context)
    if settings.LLM_BACKEND != 'openai' or not settings.LLM_API_KEY:
        raise WeeklyError('서버의 LLM_BACKEND와 OpenAI 키 설정을 확인한 뒤 다시 시도해 주세요.')
    model = settings.LLM_MODEL or 'gpt-5.4-mini'
    payload = {
        'model': model,
        'store': False,
        'max_output_tokens': 2400,
        'instructions': INSTRUCTIONS,
        'input': json.dumps(context, ensure_ascii=False),
        'text': {
            'format': {'type': 'json_schema', 'name': 'weekly_candidates', 'strict': True, 'schema': SCHEMA}
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
        'weekly.v1 model=%s elapsed_ms=%d input_tokens=%s output_tokens=%s',
        model,
        round((monotonic() - started) * 1000),
        usage.get('input_tokens'),
        usage.get('output_tokens'),
    )
    return output
