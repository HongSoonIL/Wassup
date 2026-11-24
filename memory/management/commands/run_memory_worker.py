import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import OperationalError, close_old_connections

from config.llm import model_for
from memory.extraction import process_next


class Command(BaseCommand):
    help = '동의한 방의 개인 기억 추출 작업을 처리한다.'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true', help='Process up to ten ready jobs and exit.')
        parser.add_argument('--interval', type=float, default=1, help='Seconds between queue polls.')

    def handle(self, *args, **options):
        interval = options['interval']
        if not 0 < interval <= 60:
            raise CommandError('--interval must be greater than 0 and at most 60 seconds.')
        if settings.LLM_BACKEND not in ('fake', 'openai'):
            raise CommandError('LLM_BACKEND must be fake or openai.')
        if settings.LLM_BACKEND == 'openai' and not (model_for('memory') and settings.LLM_API_KEY):
            raise CommandError('Set LLM_MEMORY_MODEL or LLM_MODEL, and LLM_API_KEY for the OpenAI backend.')
        while True:
            close_old_connections()
            try:
                for _ in range(10):
                    if not process_next():
                        break
            except OperationalError:
                if options['once']:
                    raise CommandError('Memory database unavailable.') from None
                self.stderr.write('Memory database unavailable. Retrying.')
            if options['once']:
                return
            time.sleep(interval)
