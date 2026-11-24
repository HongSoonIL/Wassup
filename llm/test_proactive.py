import io
import json
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from llm.proactive import FollowupError, generate_followup
from llm.proactive_fixture import CASES


def _context(case='seoyun_open_exam'):
    return CASES[case][1]


def _response(message=None, *, content=None, status='completed'):
    if content is None:
        content = [{'type': 'output_text', 'text': json.dumps({'message': message})}]
    return io.BytesIO(json.dumps({
        'status': status,
        'output': [
            {'type': 'reasoning'},
            {'type': 'message', 'role': 'assistant', 'status': 'completed', 'content': content},
        ],
    }).encode())


@override_settings(LLM_BACKEND='fake')
class FakeFollowupTests(SimpleTestCase):
    def test_synthetic_conversation_regressions(self):
        with patch('llm.proactive.urlopen') as request:
            for case, (should_send, context) in CASES.items():
                with self.subTest(case=case):
                    message = generate_followup(context)
                    self.assertEqual(message is not None, should_send)
                    self.assertEqual(generate_followup(context), message)
            request.assert_not_called()

    def test_open_events_use_distinct_character_voices(self):
        seoyun = generate_followup(_context())
        dohyun = generate_followup(_context('dohyun_open_exam'))
        self.assertEqual(seoyun, '그러고 보니 통계 시험 어땠어?')
        self.assertEqual(dohyun, '통계 시험 어땠음?')


@override_settings(LLM_BACKEND='openai', LLM_MODEL='shared', LLM_CHAT_MODEL='chat-model',
                   LLM_CHAT_EFFORT='low', LLM_API_KEY='test-key')
class OpenAIFollowupTests(SimpleTestCase):
    @patch('llm.proactive.urlopen')
    def test_transport_uses_chat_options_and_only_followup_fields(self, request):
        request.return_value = _response('통계 시험 어땠어?')
        context = _context()
        message = generate_followup({**context, 'unexpected': 'private'})
        self.assertEqual(message, '통계 시험 어땠어?')
        wire = request.call_args.args[0]
        self.assertEqual(wire.full_url, 'https://api.openai.com/v1/responses')
        self.assertEqual(wire.headers['Authorization'], 'Bearer test-key')
        self.assertLessEqual(request.call_args.kwargs['timeout'], 15)
        payload = json.loads(wire.data)
        self.assertEqual(payload['model'], 'chat-model')
        self.assertEqual(payload['reasoning'], {'effort': 'low'})
        self.assertFalse(payload['store'])
        self.assertEqual(json.loads(payload['input']), context)
        output = payload['text']['format']
        self.assertEqual(output['type'], 'json_schema')
        self.assertTrue(output['strict'])
        self.assertEqual(output['schema']['properties']['message']['type'], ['string', 'null'])

    @patch('llm.proactive.urlopen')
    def test_silence_and_refusal_are_valid_outcomes(self, request):
        request.side_effect = [
            _response(),
            _response(content=[{'type': 'refusal', 'refusal': 'refused'}]),
        ]
        self.assertIsNone(generate_followup(_context()))
        self.assertIsNone(generate_followup(_context()))

    @patch('llm.proactive.urlopen')
    def test_rejected_messages_are_silent(self, request):
        for message in (
            '통계 잘 봤어?\n답장해 줘', '통계 잘 봤어?\n', '**통계 시험** 어땠어?',
            '문제 푸는 거 도와줄까?', '서윤: 통계 잘 봤어?', 'How was the exam?', '가' * 81, '통계?\ud800',
        ):
            with self.subTest(message=message):
                request.return_value = _response(message)
                self.assertIsNone(generate_followup(_context()))

    @patch('llm.proactive.urlopen')
    def test_provider_failures_are_distinct_from_silence(self, request):
        request.side_effect = [
            TimeoutError(),
            _response('통계 잘 봤어?', status='incomplete'),
            _response(content=[{'type': 'output_text', 'text': 'garbage'}]),
            _response(content=[{'type': 'output_text', 'text': '{"message": ["통계 시험?"]}'}]),
        ]
        for attempt in range(4):
            with self.subTest(attempt=attempt), self.assertRaises(FollowupError):
                generate_followup(_context())

    @patch('llm.proactive.urlopen')
    def test_invalid_date_and_blank_schedule_skip_the_provider(self, request):
        self.assertIsNone(generate_followup({**_context(), 'event_date': '2026-W48-1'}))
        self.assertIsNone(generate_followup({**_context(), 'schedule': ' '}))
        request.assert_not_called()
