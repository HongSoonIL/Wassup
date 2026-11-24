import time
from concurrent.futures import ThreadPoolExecutor

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import OperationalError, close_old_connections, connections

from chat.replies import dispatch_due, prepare_next_reply


def prepare():
    close_old_connections()
    try:
        prepare_next_reply()
    finally:
        connections.close_all()


class Command(BaseCommand):
    help = 'Dispatch due chat replies from PostgreSQL.'

    def add_arguments(self, parser):
        parser.add_argument('--interval', type=float, default=1.0)

    def handle(self, *args, **options):
        interval = options['interval']
        if not 0 < interval <= 60:
            raise CommandError('--interval must be greater than 0 and at most 60 seconds.')
        if settings.LLM_BACKEND not in ('fake', 'openai'):
            raise CommandError('LLM_BACKEND must be fake or openai.')
        if settings.LLM_BACKEND == 'openai' and not (settings.LLM_MODEL and settings.LLM_API_KEY):
            raise CommandError('Set LLM_MODEL and LLM_API_KEY to use the OpenAI backend.')
        executor = ThreadPoolExecutor(max_workers=1)
        generation = None
        self.stdout.write('Reply worker started.')
        try:
            while True:
                close_old_connections()
                try:
                    dispatch_due()
                    if settings.LLM_BACKEND == 'openai' and (generation is None or generation.done()):
                        previous, generation = generation, None
                        if previous:
                            previous.result()
                        generation = executor.submit(prepare)
                except OperationalError as exc:
                    self.stderr.write(f'Database unavailable. The worker will retry. {exc}')
                time.sleep(interval)
        except KeyboardInterrupt:
            pass
        finally:
            executor.shutdown(wait=True)
