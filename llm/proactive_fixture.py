"""Synthetic Korean chat cases shared by offline tests and provider comparisons."""

from llm.proactive import FollowupContext


SEOYUN = {
    'personality': '친구가 한 말을 잘 기억하고 리액션이 빠른 편.',
    'speech_rules': '편하게 반말. 무조건 감탄사로 시작하지 않으며, 궁금하면 자연스럽게 말을 건다.',
}
DOHYUN = '무심한 현실파. 반말로 짧게 톡하고 ‘했음’, ‘뭐함’ 같은 어미를 종종 쓴다. 이모티콘은 드물다.'


def _context(name, schedule, *turns) -> FollowupContext:
    return {
        'name': name,
        'persona': SEOYUN if name == '서윤' else DOHYUN,
        'activity': '공강',
        'schedule': schedule,
        'event_date': '2026-11-23',
        'recent': [{'sender': sender, 'text': text} for sender, text in turns],
    }


EXAM_SETUP = (
    ('user', '나 내일 통계 시험 봐. 범위 실화냐'),
    ('character', '오 그거 내일이구나. 잘 보고 와'),
    ('user', 'ㅇㅋ 땡큐'),
)
CASES = {
    'seoyun_open_exam': (True, _context('서윤', '통계 시험', *EXAM_SETUP)),
    'dohyun_open_exam': (True, _context('도현', '통계 시험', *EXAM_SETUP)),
    'unanswered_earlier_question': (False, _context('서윤', '통계 시험', *EXAM_SETUP,
        ('character', '통계는 잘 봤어?'),
        ('user', '아 그 사진 두부야? 귀엽다'),
        ('character', '응 두부 맞아 ㅋㅋ'),
    )),
    'user_volunteered_outcome': (False, _context('도현', '통계 시험', *EXAM_SETUP,
        ('user', '아까 본 거 걍 말아먹음 ㅋㅋ 뒤에 두 문제는 손도 못 댔어'),
        ('character', '아이고 밥은 먹었냐'),
    )),
    'rescheduled_without_topic': (False, _context('서윤', '조별 발표',
        ('user', '내일 조별 발표 있어. 나 첫 순서래'),
        ('character', '떨리겠다. 잘하고 와'),
        ('user', '아 그거 담주로 밀렸어'),
    )),
    'ambiguous_after_topic_change': (False, _context('도현', '영상 발표',
        ('user', '나 내일 영상 발표 있어. 그리고 동방에서 초밥 시킬 듯'),
        ('character', '좋겠다. 거기 연어 괜찮더라'),
        ('user', '그건 좀 애매했음 ㅋㅋ'),
    )),
    'event_explicitly_cancelled': (False, _context('도현', '풋살 경기',
        ('user', '내일 풋살 경기 있어'),
        ('user', '비 와서 경기 취소됐대'),
    )),
    'completion_without_result': (False, _context('서윤', '도예 워크숍',
        ('user', '내일 도예 워크숍 예약했어'),
        ('user', '방금 끝났어! 이제 지하철 탔어'),
    )),
    'elliptical_earlier_question': (False, _context('도현', '통계 시험', *EXAM_SETUP,
        ('character', '통계는?'),
    )),
    'user_declines_topic': (False, _context('서윤', '통계 시험', *EXAM_SETUP,
        ('user', '통계 얘긴 나중에 하자'),
    )),
    'neutral_topic_change': (True, _context('서윤', '통계 시험', *EXAM_SETUP,
        ('user', '오늘 아침 진짜 춥다'),
        ('character', '그치 바람 엄청 불어'),
    )),
}

SMOKE_CASES = tuple(CASES)[:6]
