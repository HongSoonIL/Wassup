from typing import Literal

from django.conf import settings


Purpose = Literal['chat', 'memory', 'weekly']


def model_for(purpose: Purpose) -> str:
    model = getattr(settings, f'LLM_{purpose.upper()}_MODEL') or settings.LLM_MODEL
    # Weekly generation historically also worked with just an API key.
    return model or ('gpt-5.4-mini' if purpose == 'weekly' else '')


def model_options(purpose: Purpose) -> dict:
    options = {'model': model_for(purpose)}
    effort = getattr(settings, f'LLM_{purpose.upper()}_EFFORT')
    if effort:
        options['reasoning'] = {'effort': effort}
    return options
