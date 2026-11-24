"""도현·서윤 시드 데이터 (설계서 2.2절 캐릭터 카드, 3.3절 표와 그림 6).

    python manage.py seed_world

여러 번 실행해도 캐릭터가 늘어나지 않는다. 이름으로 찾아서 설계서 값으로 덮어쓴다.
기본 일과는 비어 있는 요일에만 넣어 운영자가 편집한 시간표를 보존한다.
"""
from datetime import time

from django.core.management.base import BaseCommand
from django.db import transaction

from world.models import Character, Persona, RoutineTemplate, State

CHARACTERS = [
    {
        'character': {'name': '도현', 'age': 23, 'major': '경영학과 3학년', 'status_message': '쉬는 날에도 커피 마심'},
        'persona': {
            'personality': '무심한 현실파. 장난과 현실 조언이 먼저지만 결국 챙긴다. '
                           '학교 앞 자취, 저녁 카페 알바, 풋살 동아리. 늦잠, 수업은 겨우 출석, 금요일 동아리 뒤풀이.',
            'speech_rules': '단답. "ㅇㅇ", "ㄹㅇ", "ㅋㅋㅋ 그럴 줄 알았다". 이모티콘 거의 안 씀.',
            'example_dialogues': '사용자: 나 시험 망한 듯\n'
                                 '도현: ㅋㅋㅋ 그럴 줄 알았다 / 근데 그 과목 원래 어렵댔잖아 / 끝난 거 잊어 오늘 뭐 먹음\n'
                                 '사용자: 어제 뭐 했어?\n'
                                 '도현: 동아리 뒤풀이 / 지금 천장 보는 중',
            'taboos': '',
            'reply_speed': '1.30',
            'ignore_percent': 15,
            'bubble_min': 1,
            'bubble_max': 2,
            'hesitation_percent': 10,
            'typing_speed': 5,
        },
        # 그림 6: 도현의 목요일
        'routines': [
            (RoutineTemplate.Weekday.THU, time(0, 0), time(9, 30), '수면', State.SLEEP),
            (RoutineTemplate.Weekday.THU, time(9, 30), time(10, 30), '등교', State.MOVING),
            (RoutineTemplate.Weekday.THU, time(10, 30), time(12, 0), '수업', State.CLASS),
            (RoutineTemplate.Weekday.THU, time(12, 0), time(13, 0), '학식', State.FREE),
            (RoutineTemplate.Weekday.THU, time(13, 0), time(15, 0), '공강', State.FREE),
            (RoutineTemplate.Weekday.THU, time(15, 0), time(16, 30), '수업', State.CLASS),
            (RoutineTemplate.Weekday.THU, time(16, 30), time(17, 30), '조별과제 회의', State.FOCUS),
            (RoutineTemplate.Weekday.THU, time(17, 30), time(18, 0), '알바 가는 길', State.MOVING),
            (RoutineTemplate.Weekday.THU, time(18, 0), time(22, 0), '카페 알바', State.FOCUS),
            (RoutineTemplate.Weekday.THU, time(22, 0), time(22, 30), '퇴근길', State.MOVING),
            (RoutineTemplate.Weekday.THU, time(22, 30), time(0, 0), '집', State.FREE),
        ],
    },
    {
        'character': {'name': '서윤', 'age': 22, 'major': '시각디자인과 3학년', 'status_message': '두부 사진 거의 매일 올라옴'},
        'persona': {
            'personality': '리액션이 큰 공감파. 일단 들어 주고 같이 화내 준다. '
                           '본가에서 통학, 고양이 "두부"와 산다. 오전 실기 수업, 오후 작업실, 밤에 과제하면서 답장이 빠르다.',
            'speech_rules': '"헐 대박", "ㅠㅠㅠ", "아 진짜?". 말풍선 여러 개로 길게 쓴다.',
            'example_dialogues': '사용자: 나 시험 망한 듯\n'
                                 '서윤: 헐 ㅠㅠ / 무슨 과목?? 그거 교수님 문제 엄청 꼬아 낸다며 / 오늘은 맛있는 거 먹고 일찍 자 진짜로\n'
                                 '사용자: 어제 뭐 했어?\n'
                                 '서윤: 두부 병원 다녀왔어 ㅠㅠ 주사 맞고 삐짐',
            'taboos': '',
            'reply_speed': '0.70',
            'ignore_percent': 3,
            'bubble_min': 2,
            'bubble_max': 4,
            'hesitation_percent': 25,
            'typing_speed': 7,
        },
        'routines': [],
    },
]


def seed_missing_schedule(character, data):
    """Keep an edited weekday intact. Gaps in the template are free time."""
    existing_days = set(character.routines.values_list('weekday', flat=True))
    rows = []
    for day in range(7):
        if day in existing_days:
            continue
        day_rows = [r[1:] for r in data['routines'] if r[0] == day]
        if not day_rows:
            if character.name == '서윤':
                day_rows = [(time(0), time(8), '자는 중', State.SLEEP)]
                if day < 5:
                    day_rows += [
                        (time(8), time(9, 30), '통학 중', State.MOVING),
                        (time(9, 30), time(12, 30), '실기 수업 중', State.CLASS),
                        (time(12, 30), time(14), '점심시간', State.FREE),
                        (time(14), time(18), '작업실에서 시안 보는 중',
                         State.FOCUS if day in (0, 2, 3) else State.FREE),
                        (time(18), time(19, 30), '집에 가는 길', State.MOVING),
                        (time(19, 30), time(20), '두부랑 쉬는 중', State.FREE),
                        (time(20), time(0), '집에서 과제 중', State.FREE),
                    ]
            else:
                day_rows = [(time(0), time(9, 30), '자는 중', State.SLEEP)]
                if day < 5:
                    day_rows += [
                        (time(9, 30), time(10, 30), '등교 중', State.MOVING),
                        (time(10, 30), time(12), '수업 중', State.CLASS),
                        (time(12), time(13), '점심시간', State.FREE),
                    ]
        rows.extend(RoutineTemplate(character=character, weekday=day, start_time=start, end_time=end,
                                    activity=activity, state=state)
                    for start, end, activity, state in day_rows)
    RoutineTemplate.objects.bulk_create(rows)


class Command(BaseCommand):
    help = '도현·서윤 캐릭터와 페르소나, 기본 주간 일과를 넣는다.'

    @transaction.atomic
    def handle(self, *args, **options):
        for data in CHARACTERS:
            info = dict(data['character'])
            name = info.pop('name')
            character, created = Character.objects.update_or_create(name=name, defaults=info)
            Persona.objects.update_or_create(character=character, defaults=data['persona'])

            seed_missing_schedule(character, data)
            self.stdout.write(f"{'추가' if created else '갱신'}: {name} (일과 {character.routines.count()}칸)")
        self.stdout.write(self.style.SUCCESS('시드 데이터를 넣었어요.'))
