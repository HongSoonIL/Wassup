import time

from django.core.management.base import BaseCommand, CommandError
from django.db import OperationalError, close_old_connections

from world.photo_jobs import drain


class Command(BaseCommand):
    help = '주간 초안의 비공개 후보 사진을 생성한다.'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true', help='대기 중인 사진을 최대 6장 처리하고 종료')
        parser.add_argument('--interval', type=float, default=1)

    def handle(self, *args, **options):
        interval = options['interval']
        if not 0 < interval <= 60:
            raise CommandError('interval must be between 0 and 60 seconds.')
        while True:
            close_old_connections()
            try:
                drain()
            except OperationalError as exc:
                if options['once']:
                    raise
                self.stderr.write(str(exc))
            if options['once']:
                return
            time.sleep(interval)
