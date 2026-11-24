import time
from concurrent.futures import ThreadPoolExecutor, wait

from django.core.management.base import BaseCommand, CommandError
from django.db import OperationalError, close_old_connections, connections

from world.publication import available_weeks, dispatch_publications, generate_week


def generate_batch():
    # Each worker has its own connection and runs at most one paid request at a time.
    close_old_connections()
    try:
        for week_id in list(available_weeks().values_list('pk', flat=True)[:10]):
            generate_week(week_id)
    finally:
        connections.close_all()


class Command(BaseCommand):
    help = '생성 대기 중인 주간 계획과 발행 시각이 된 게시물을 처리한다.'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true')
        parser.add_argument('--interval', type=float, default=1)

    def handle(self, *args, **options):
        interval = options['interval']
        if not 0 < interval <= 60:
            raise CommandError('interval must be between 0 and 60 seconds.')
        with ThreadPoolExecutor(max_workers=1) as generator:
            batch = None
            while True:
                close_old_connections()
                try:
                    dispatch_publications()
                    if batch is None:
                        batch = generator.submit(generate_batch)
                    if batch.done():
                        completed, batch = batch, None
                        completed.result()
                        if options['once']:
                            return
                except OperationalError as exc:
                    if options['once']:
                        raise
                    self.stderr.write(str(exc))
                if options['once'] and batch is not None:
                    # Keep checking due posts while inference is in flight, even for --once.
                    wait([batch], timeout=interval)
                else:
                    time.sleep(interval)
