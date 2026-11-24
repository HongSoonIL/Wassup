import logging
import time

from django.core.management.base import BaseCommand, CommandError
from django.db import OperationalError, close_old_connections

from world.models import PublicationWeek
from world.publication import dispatch_publications, generate_week


class Command(BaseCommand):
    help = '생성 대기 중인 주간 계획과 발행 시각이 된 게시물을 처리한다.'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true')
        parser.add_argument('--interval', type=float, default=1)

    def handle(self, *args, **options):
        interval = options['interval']
        if not 0 < interval <= 60:
            raise CommandError('interval must be between 0 and 60 seconds.')
        while True:
            close_old_connections()
            try:
                for week_id in list(
                    PublicationWeek.objects.filter(generation='pending')
                    .order_by('pk')
                    .values_list('pk', flat=True)[:10]
                ):
                    try:
                        generate_week(week_id)
                    except OperationalError:
                        raise
                    except Exception:
                        logging.getLogger(__name__).exception('Weekly generation failed for week %s', week_id)
                        PublicationWeek.objects.filter(pk=week_id, generation='pending').update(
                            generation='failed',
                            generation_error='후보를 준비하지 못했어요. 주간 계획을 다시 열어 주세요.',
                        )
                dispatch_publications()
            except OperationalError as exc:
                if options['once']:
                    raise
                self.stderr.write(str(exc))
            if options['once']:
                return
            time.sleep(interval)
