from datetime import date
from pathlib import Path

from django.conf import settings
from django.core.files import File
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from world.models import Character, PostPhoto, SchoolEvent


class Command(BaseCommand):
    help = '가상 학교의 2027년 봄 일정과 캐릭터별 발행용 비공개 사진을 등록한다.'

    @transaction.atomic
    def handle(self, *args, **options):
        folder = Path(settings.BASE_DIR) / 'frontend/src/design/assets'
        for name, key, alt in [
            ('서윤', 'studio-print', '작업실 책상의 군청색과 주황색 포스터 시안'),
            ('서윤', 'dubu-home', '겨자색 담요 위에 앞발을 포개고 앉은 흰 고양이 두부'),
            ('도현', 'cafe-interior', '아이스커피 한 잔이 놓인 카페 창가'),
            ('도현', 'cafe-close', '어두운 창밖을 배경으로 카페 바에 엎어 둔 금속 피처 두 개'),
        ]:
            character = Character.objects.filter(name=name).first()
            if not character:
                raise CommandError('seed_world로 두 캐릭터를 먼저 등록해 주세요.')
            if not PostPhoto.objects.filter(pk=key).exists():
                asset = PostPhoto(key=key, character=character, alt=alt)
                with (folder / (key + '.webp')).open('rb') as photo:
                    asset.image.save(key + '.webp', File(photo), save=False)
                asset.save(force_insert=True)
        for kind, label, start, end in [
            ('exams', '2027 봄 중간고사', date(2027, 4, 19), date(2027, 4, 24)),
            ('sports', '2027 봄 체육대회', date(2027, 4, 24), date(2027, 4, 25)),
        ]:
            SchoolEvent.objects.get_or_create(
                kind=kind, label=label, defaults={'start_date': start, 'end_date_exclusive': end}
            )
        self.stdout.write('학교 일정과 발행용 사진을 준비했습니다.')
