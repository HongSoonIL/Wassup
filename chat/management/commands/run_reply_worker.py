import time
from concurrent.futures import ThreadPoolExecutor

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import OperationalError, close_old_connections, connections

from chat.proactive import prepare_next_proactive
from chat.replies import dispatch_due, prepare_next_reply
from config.llm import model_for


def prepare(work):
    close_old_connections()
    try:
        return work()
    finally:
        connections.close_all()


class Command(BaseCommand):
    help = 'Dispatch due chat replies and optional follow-ups from PostgreSQL.'

    def add_arguments(self, parser):
        parser.add_argument('--interval', type=float, default=1.0)

    def handle(self, *args, **options):
        interval = options['interval']
        if not 0 < interval <= 60:
            raise CommandError('--interval must be greater than 0 and at most 60 seconds.')
        if settings.LLM_BACKEND not in ('fake', 'openai'):
            raise CommandError('LLM_BACKEND must be fake or openai.')
        if settings.LLM_BACKEND == 'openai' and not (model_for('chat') and settings.LLM_API_KEY):
            raise CommandError('Set LLM_CHAT_MODEL or LLM_MODEL, and LLM_API_KEY to use the OpenAI backend.')
        # Each kind has its own outstanding task, so a proactive model call
        # cannot occupy the reply generation slot or the delivery loop.
        executor = ThreadPoolExecutor(max_workers=2)
        generation = followup = None
        self.stdout.write('Reply worker started.')
        try:
            while True:
                close_old_connections()
                try:
                    dispatch_due()
                    reply_idle = False
                    if generation is None or generation.done():
                        previous, generation = generation, None
                        if previous:
                            reply_idle = not previous.result()
                        generation = executor.submit(prepare, prepare_next_reply)
                    if followup is not None and followup.done():
                        previous, followup = followup, None
                        try:
                            previous.result()
                        except OperationalError:
                            raise
                        except Exception as exc:
                            self.stderr.write(f'Proactive worker failed ({type(exc).__name__}). Ordinary replies continue.')
                    if reply_idle and followup is None:
                        followup = executor.submit(prepare, prepare_next_proactive)
                except OperationalError as exc:
                    self.stderr.write(f'Database unavailable. The worker will retry. {exc}')
                time.sleep(interval)
        except KeyboardInterrupt:
            pass
        finally:
            executor.shutdown(wait=True)
