"""Compare synthetic follow-ups with `python -m llm.proactive_eval --backend openai`.

Without --model or --effort this uses the configured chat values. Each run
makes at most six provider calls, and JSON output preserves the Korean text.
"""

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import django
from django.conf import settings

from config.llm import model_options
from llm.proactive import INSTRUCTIONS, FollowupError, generate_followup
from llm.proactive_fixture import CASES, SMOKE_CASES


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend', choices=('fake', 'openai'), default='fake')
    parser.add_argument('--model', help='Override the configured chat model for this run.')
    parser.add_argument('--effort', help='Override the configured chat reasoning effort for this run.')
    parser.add_argument('--case', action='append', choices=CASES, help='Run the named case. Repeat for up to six cases.')
    parser.add_argument('--output', type=Path, help='Write the JSON report to this file.')
    args = parser.parse_args()
    case_ids = list(dict.fromkeys(args.case or SMOKE_CASES))
    if len(case_ids) > len(SMOKE_CASES):
        parser.error('Select up to six cases per run.')

    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
    os.environ['LLM_BACKEND'] = args.backend
    if args.model is not None:
        os.environ['LLM_CHAT_MODEL'] = args.model
    if args.effort is not None:
        os.environ['LLM_CHAT_EFFORT'] = args.effort
    django.setup()
    options = model_options('chat')
    if args.backend == 'openai' and (not settings.LLM_API_KEY or not options['model']):
        parser.error('Set the chat model and LLM_API_KEY before a live run.')

    report = {
        'started_at': datetime.now(timezone.utc).isoformat(),
        'backend': args.backend,
        'chat_options': options,
        'prompt_sha256': hashlib.sha256(INSTRUCTIONS.encode()).hexdigest(),
        'cases': [],
    }
    for case_id in case_ids:
        expected, context = CASES[case_id]
        row = {'case': case_id, 'expected_send': expected, 'context': context}
        try:
            message = generate_followup(context)
        except FollowupError as error:
            row['error'] = str(error)
            row['passed'] = False
        else:
            row['message'] = message
            row['passed'] = (message is not None) == expected
        report['cases'].append(row)

    report['passed'] = sum(row['passed'] for row in report['cases'])
    raw = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(raw + '\n', encoding='utf-8')
    print(raw)
    return int(report['passed'] != len(case_ids))


if __name__ == '__main__':
    raise SystemExit(main())
