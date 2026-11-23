import time

from django.core.management.base import BaseCommand, CommandError
from django.db import OperationalError, close_old_connections

from chat.replies import dispatch_due


class Command(BaseCommand):
    help = 'Dispatch due chat replies from PostgreSQL.'

    def add_arguments(self, parser):
        parser.add_argument('--interval', type=float, default=1.0)

    def handle(self, *args, **options):
        interval = options['interval']
        if not 0 < interval <= 60:
            raise CommandError('--interval must be greater than 0 and at most 60 seconds.')
        self.stdout.write('Reply worker started.')
        try:
            while True:
                close_old_connections()
                try:
                    dispatch_due()
                except OperationalError as exc:
                    self.stderr.write(f'Database unavailable. The worker will retry. {exc}')
                time.sleep(interval)
        except KeyboardInterrupt:
            pass
