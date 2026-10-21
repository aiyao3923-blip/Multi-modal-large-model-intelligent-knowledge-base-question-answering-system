"""Offline contracts; no settings, storage, database or real credentials.

Run from backend: python -B -m unittest discover -s tests -p test_llm_stream.py -v
MockTransport and a socket guard prohibit real traffic. Virtual-clock checks
verify timeout policy, not the behaviour of a real server or connection.
"""
from __future__ import annotations

import copy
import json
import socket
import time
import traceback
import unittest
from contextlib import contextmanager
from unittest.mock import patch

import httpx
from app.engines import llm_stream
from app.engines.llm_gateway import ChannelUnavailable

FIXTURE_URL = 'https://stream-fixture.invalid/tenant/v1/chat/completions'
FIXTURE_KEY = 'synthetic-token-not-a-real-credential'
PRIVATE_BODY = 'synthetic-private-upstream-body'
CHANNEL = {
    'channel_type': 'online', 'base_url': FIXTURE_URL, 'api_key': FIXTURE_KEY,
    'model_name': 'fixture-model', 'temperature': .27, 'timeout_s': 30,
}
MESSAGES = [{'role': 'user', 'content': '请完整回答。'}]
OLLAMA = {**CHANNEL, 'channel_type': 'local', 'base_url': 'http://ollama-fixture.invalid:11434/api/chat'}
SAFE_CODES = {
    'configuration', 'timeout', 'authentication', 'rate_limited', 'context_limit',
    'output_limit', 'empty_response', 'invalid_response', 'incomplete_response',
    'upstream', 'unavailable',
}


def encoded(data):
    return json.dumps(data, ensure_ascii=False).encode('utf-8')


def sse(data):
    body = data.encode('utf-8') if isinstance(data, str) else encoded(data)
    return b'data: ' + body + b'\n\n'


def delta(content='', finish=None, **extra):
    return sse({'choices': [{
        'index': 0, 'delta': {'content': content, **extra}, 'finish_reason': finish,
    }]})


def whole(content='完整回答', finish='stop', **extra):
    return {'choices': [{'message': {'content': content, **extra}, 'finish_reason': finish}]}


def ndjson(content='', done=False, **extra):
    return encoded({'message': {'content': content}, 'done': done, **extra}) + b'\n'


class GenerationCancelled(RuntimeError):
    """Caller-owned cancellation stand-in; the adapter must not wrap it."""


class FakeControl:
    def __init__(self):
        self.cancelled = False
        self.cancellation = GenerationCancelled('fixture generation cancelled')
        self.checkpoints = 0
        self.progress_events = []
        self.abort = None
        self.registered = []
        self.cleared = []
        self.on_progress = None
        self.on_register = None
        self.on_clear = None

    def checkpoint(self):
        self.checkpoints += 1
        if self.cancelled:
            raise self.cancellation

    def progress(self, stage, **details):
        self.progress_events.append((stage, details))
        if self.on_progress:
            self.on_progress()

    def set_abort(self, callback):
        self.abort = callback
        self.registered.append(callback)
        if self.on_register:
            self.on_register()
        if self.cancelled:
            callback()

    def clear_abort(self, callback):
        self.cleared.append(callback)
        if self.abort is callback:
            self.abort = None
        if self.on_clear:
            self.on_clear()

    def cancel(self):
        self.cancelled = True
        if self.abort is not None:
            self.abort()


class WireStream(httpx.SyncByteStream):
    def __init__(self, chunks, *, on_read=None, failure=None):
        self.chunks = chunks
        self.on_read = on_read
        self.failure = failure
        self.reads = 0
        self.closed = False

    def __iter__(self):
        for index, chunk in enumerate(self.chunks):
            self.reads += 1
            if self.on_read:
                self.on_read(index)
            if self.closed:
                raise httpx.ReadError(PRIVATE_BODY + FIXTURE_URL + FIXTURE_KEY)
            yield chunk
        if self.failure:
            raise self.failure

    def close(self):
        self.closed = True


class StreamChatTests(unittest.TestCase):
    def setUp(self):
        connect = patch.object(socket.socket, 'connect', side_effect=AssertionError('Real network forbidden'))
        post = patch.object(httpx, 'post', side_effect=AssertionError('Non-stream post forbidden'))
        self.connect_guard = connect.start()
        self.post_guard = post.start()
        self.addCleanup(connect.stop)
        self.addCleanup(post.stop)
        self.requests = []
        self.responses = []

    def tearDown(self):
        self.connect_guard.assert_not_called()
        self.post_guard.assert_not_called()

    @contextmanager
    def wire(self, body, *, status=200, media='text/event-stream', headers=None,
             on_read=None, failure=None):
        chunks = [body] if isinstance(body, bytes) else body
        feed = WireStream(chunks, on_read=on_read, failure=failure)
        self.requests = []
        self.responses = []

        def respond(request):
            self.requests.append(request)
            response_headers = dict(headers or {})
            if media:
                response_headers['Content-Type'] = media
            response = httpx.Response(status, headers=response_headers, stream=feed)
            self.responses.append(response)
            return response

        transport = httpx.MockTransport(respond)

        @contextmanager
        def mocked_stream(method, url, **kwargs):
            with httpx.Client(transport=transport, trust_env=False) as client:
                with client.stream(method, url, **kwargs) as response:
                    yield response

        with patch.object(llm_stream.httpx, 'stream', side_effect=mocked_stream) as request:
            yield request, feed

    def run_wire(self, body, *, channel=None, control=None, **wire_options):
        with self.wire(body, **wire_options):
            return llm_stream.stream_chat(CHANNEL if channel is None else channel,
                                          MESSAGES, control=control)

    def assert_safe(self, error, code):
        self.assertIsInstance(error, ChannelUnavailable)
        self.assertEqual(error.code, code)
        self.assertIn(error.code, SAFE_CODES)
        self.assertIsNone(error.__cause__)
        self.assertIsNone(error.__context__)
        for private in (PRIVATE_BODY, FIXTURE_KEY, FIXTURE_URL):
            self.assertNotIn(private, str(error))
            self.assertNotIn(private, repr(error))
        self.assertFalse(hasattr(error, 'request'))
        self.assertFalse(hasattr(error, 'response'))

    def fails(self, body, code, **kwargs):
        with self.assertRaises(ChannelUnavailable) as caught:
            self.run_wire(body, **kwargs)
        self.assert_safe(caught.exception, code)
        self.assertEqual(len(self.requests), 1)
        self.assertTrue(self.responses[0].is_closed)
        return caught.exception

    def test_openai_request_preserves_endpoint_headers_and_payload(self):
        with self.wire(delta('正文', 'stop')) as (request, feed):
            self.assertEqual(llm_stream.stream_chat(CHANNEL, MESSAGES), '正文')
        request.assert_called_once()
        self.assertEqual(request.call_args.args, ('POST', FIXTURE_URL))
        self.assertIs(request.call_args.kwargs['follow_redirects'], False)
        sent = self.requests[0]
        self.assertEqual(str(sent.url), FIXTURE_URL)
        self.assertEqual(sent.headers['Authorization'], 'Bearer ' + FIXTURE_KEY)
        self.assertEqual(json.loads(sent.content), {
            'model': 'fixture-model', 'messages': MESSAGES,
            'temperature': .27, 'stream': True,
        })
        self.assertTrue(feed.closed)

    def test_existing_channel_routing_and_configured_paths(self):
        cases = [
            ('online', 'https://route-fixture.invalid/custom', '/custom/v1/chat/completions', False),
            ('online', 'https://route-fixture.invalid/custom/v1/', '/custom/v1/chat/completions', False),
            ('local', 'http://route-fixture.invalid:11434/v1', '/v1/chat/completions', False),
            ('local', 'http://route-fixture.invalid:11434/proxy/chat/completions', '/proxy/chat/completions', False),
            ('local', 'http://route-fixture.invalid:11434', '/api/chat', True),
            ('local', 'http://route-fixture.invalid:11434/proxy/api/chat/', '/proxy/api/chat', True),
        ]
        for ctype, base, expected, ollama in cases:
            with self.subTest(channel_type=ctype, path=expected):
                channel = {**CHANNEL, 'channel_type': ctype, 'base_url': base}
                body = ndjson('完成', True) if ollama else delta('完成', 'stop')
                self.assertEqual(self.run_wire(body, channel=channel), '完成')
                self.assertEqual(self.requests[0].url.path, expected)
                payload = json.loads(self.requests[0].content)
                if ollama:
                    self.assertEqual(payload['options'], {'temperature': .27})
                    self.assertNotIn('temperature', payload)
                    self.assertNotIn('Authorization', self.requests[0].headers)

    def test_read_timeout_has_180_second_floor_without_total_deadline(self):
        for configured, expected in [(None, 180.), (.01, 180.), (30, 180.), ('240', 240.)]:
            with self.subTest(configured=configured):
                channel = dict(CHANNEL)
                if configured is None:
                    channel.pop('timeout_s')
                else:
                    channel['timeout_s'] = configured
                with self.wire(delta('ok', 'stop')) as (request, _):
                    self.assertEqual(llm_stream.stream_chat(channel, MESSAGES), 'ok')
                timeout = request.call_args.kwargs['timeout']
                self.assertEqual((timeout.connect, timeout.read, timeout.write, timeout.pool),
                                 (8., expected, 30., 8.))
                self.assertEqual(self.requests[0].extensions['timeout'],
                                 {'connect': 8., 'read': expected, 'write': 30., 'pool': 8.})
                self.assertNotIn('deadline', request.call_args.kwargs)

    def test_invalid_configuration_fails_before_sending(self):
        bad_channels = [
            {**CHANNEL, 'channel_type': 'embedding'},
            {**CHANNEL, 'base_url': 'file:///synthetic-fixture'},
            {**CHANNEL, 'base_url': 'https://fixture.invalid/v1?private=' + PRIVATE_BODY},
            {**CHANNEL, 'base_url': 'https://fixture.invalid/v1#private'},
            {**CHANNEL, 'base_url': 'https://synthetic-user@fixture.invalid/v1'},
            {**CHANNEL, 'base_url': 'http://[broken'},
            {**CHANNEL, 'base_url': None},
            *[{**CHANNEL, 'timeout_s': value} for value in (0, -1, None, 'bad', float('inf'), float('nan'))],
        ]
        for index, channel in enumerate(bad_channels):
            with self.subTest(case=index):
                with patch.object(llm_stream.httpx, 'stream') as request:
                    with self.assertRaises(ChannelUnavailable) as caught:
                        llm_stream.stream_chat(channel, MESSAGES)
                self.assert_safe(caught.exception, 'configuration')
                request.assert_not_called()

    def test_input_over_2000_and_long_output_are_not_cut_or_mutated(self):
        long_input = '完整输入🙂\n' * 2000 + 'INPUT-END'
        fragments = [' 原样输出🙂\n' * 800, 'x' * 32000, '\nOUTPUT-END  ']
        messages = [
            {'role': 'system', 'content': '系统约束'},
            {'role': 'user', 'content': long_input},
            {'role': 'user', 'content': [{'type': 'text', 'text': long_input}]},
        ]
        before = copy.deepcopy(messages)
        channel = copy.deepcopy(CHANNEL)
        with self.wire([*(delta(text) for text in fragments), delta('', 'stop')]):
            answer = llm_stream.stream_chat(channel, messages)
        self.assertGreater(len(long_input), 2000)
        self.assertGreater(len(answer), 32000)
        self.assertEqual(answer, ''.join(fragments))
        self.assertEqual(json.loads(self.requests[0].content)['messages'], before)
        self.assertEqual(messages, before)
        self.assertEqual(channel, CHANNEL)
        self.assertEqual(set(json.loads(self.requests[0].content)),
                         {'model', 'messages', 'temperature', 'stream'})

    def test_sse_multiline_data_comments_and_split_utf8_bom_crlf(self):
        content = '多行 JSON、汉字与🙂'
        wire = ('\ufeff: keepalive\r\nid: fixture\r\nretry: 1000\r\n\r\n'
                'event: message\r\ndata: {"choices": [\r\n'
                'data: {"delta": {"content": ' + json.dumps(content, ensure_ascii=False) +
                '}, "finish_reason": null}]}\r\n\r\n'
                'data: [DONE]\r\n\r\n').encode('utf-8')
        self.assertEqual(self.run_wire([wire[i:i + 1] for i in range(len(wire))]), content)

    def test_sse_cr_lines_unknown_fields_and_lf_inside_content(self):
        body = b'extension: ignored\r: heartbeat\r\r' + delta('A\nB🙂').replace(b'\n', b'\r')
        body += b'data:[DONE]\r\r'
        self.assertEqual(self.run_wire([body[:17], body[17:33], body[33:]]), 'A\nB🙂')

    def test_utf8_ignores_misleading_legacy_charset(self):
        self.assertEqual(self.run_wire(delta('中文🙂', 'stop'),
                                       media='text/event-stream; charset=iso-8859-1'), '中文🙂')

    def test_reasoning_role_and_usage_never_leak_or_count_as_content(self):
        control = FakeControl()
        frames = [
            sse({'choices': [{'delta': {'role': 'assistant', 'content': None}}]}),
            delta(None, reasoning_content=PRIVATE_BODY),
            delta('', reasoning_content=PRIVATE_BODY, reasoning={'text': PRIVATE_BODY}),
            sse({'choices': [], 'usage': {'total_tokens': 123}}),
            delta('甲🙂', reasoning_content=PRIVATE_BODY),
            delta('\n乙', 'stop', reasoning_content=PRIVATE_BODY),
        ]
        self.assertEqual(self.run_wire(frames, control=control), '甲🙂\n乙')
        self.assertEqual(control.progress_events, [
            ('generating', {'received_chars': 2}), ('generating', {'received_chars': 4}),
        ])
        self.assertNotIn(PRIVATE_BODY, repr(control.progress_events))

    def test_only_choice_zero_is_accumulated(self):
        frames = [
            sse({'choices': [{'index': 1, 'delta': {'content': PRIVATE_BODY}}]}),
            sse({'choices': [
                {'index': 1, 'delta': {'content': PRIVATE_BODY}},
                {'index': 0, 'delta': {'content': '选中'}, 'finish_reason': 'stop'},
            ]}),
        ]
        self.assertEqual(self.run_wire(frames), '选中')

    def test_stop_proves_completion_without_waiting_for_done_or_eof(self):
        with self.wire([delta('完成'), sse({'choices': [{'finish_reason': 'stop'}]})],
                       failure=AssertionError('Read after proven completion')) as (_, feed):
            self.assertEqual(llm_stream.stream_chat(CHANNEL, MESSAGES), '完成')
        self.assertEqual(feed.reads, 2)

    def test_done_accepts_missing_finish_reason_and_unterminated_final_line(self):
        body = sse({'choices': [{'delta': {'content': '兼容回答'}}]}) + b'data: [DONE]'
        self.assertEqual(self.run_wire(body), '兼容回答')

    def test_stop_on_final_sse_event_without_blank_line_is_recognized(self):
        self.assertEqual(self.run_wire(delta('最终正文', 'stop').rstrip(b'\n')), '最终正文')

    def test_json_whole_response_preserves_text_and_ignores_reasoning(self):
        content = '  完整 JSON 回答🙂\n' * 400 + 'TAIL  '
        body = json.dumps(whole(content, reasoning_content=PRIVATE_BODY),
                          ensure_ascii=False, indent=2).encode('utf-8')
        for media in ('application/json', 'application/problem+json', 'text/event-stream', None):
            with self.subTest(media=media):
                control = FakeControl()
                chunks = [body[i:i + 7] for i in range(0, len(body), 7)]
                self.assertEqual(self.run_wire(chunks, media=media, control=control), content)
                self.assertEqual(control.progress_events,
                                 [('generating', {'received_chars': len(content)})])

    def test_sse_is_accepted_despite_json_content_type(self):
        self.assertEqual(self.run_wire(delta('兼容', 'stop'), media='application/json'), '兼容')

    def test_json_without_stop_cannot_claim_completion(self):
        for data in (whole('部分内容', None), {'choices': [{'message': {'content': '部分内容'}}]}):
            with self.subTest(data=data):
                self.fails(encoded(data), 'incomplete_response', media='application/json')

    def test_eof_without_end_marker_never_returns_partial_text(self):
        for body in (delta('部分正文'), delta('部分正文') + b': heartbeat\n\n',
                     sse({'choices': [], 'usage': {}})):
            with self.subTest(body=body):
                self.fails(body, 'incomplete_response')

    def test_empty_whitespace_and_reasoning_only_completion_are_failures(self):
        for body in (b'', sse('[DONE]'), delta('', 'stop'), delta(' \n\t', 'stop'),
                     delta(None, 'stop', reasoning_content=PRIVATE_BODY)):
            with self.subTest(body=body):
                self.fails(body, 'empty_response')

    def test_length_is_output_limit_for_sse_and_whole_json(self):
        self.fails(delta('部分') + delta('不能交付', 'length') + sse('[DONE]'), 'output_limit')
        self.fails(encoded(whole('部分', 'length')), 'output_limit', media='application/json')

    def test_non_stop_finish_reasons_cannot_be_rescued_by_done(self):
        for reason in ('tool_calls', 'function_call', 'content_filter', '', 'unknown-provider-code'):
            with self.subTest(reason=reason):
                self.fails(delta('不能交付', reason) + sse('[DONE]'), 'upstream')

    def test_malformed_sse_frames_are_sanitized(self):
        bad = [
            sse('{"choices":'), sse('[]'), sse('null'), sse('{}'),
            sse({'choices': 'not-a-list'}), sse({'choices': [None]}),
            sse({'choices': [{'delta': {'content': ['not-a-string']}}]}),
            sse({'choices': [{'delta': 'bad'}]}),
            sse({'choices': [{'index': '0', 'delta': {'content': 'bad'}}]}),
            sse({'choices': [{'index': 0}, {'index': 0}]}),
            sse({'choices': [{}]}), b'data: {"choices":["\xff"]}\n\n',
            delta('部分') + b'data: \xe4\xb8',
        ]
        for body in bad:
            with self.subTest(body=body):
                self.fails(body, 'invalid_response')

    def test_malformed_json_and_invalid_whole_shapes_are_sanitized(self):
        for body in (b'{', b'not-json-' + PRIVATE_BODY.encode(), encoded([]),
                     encoded({'choices': []}), encoded({'choices': [{'finish_reason': 'stop'}]})):
            with self.subTest(body=body):
                self.fails(body, 'invalid_response', media='application/json')

    def test_standard_error_objects_map_only_allowlisted_codes(self):
        cases = [
            ('context_length_exceeded', 'context_limit'),
            ('context_window_exceeded', 'context_limit'),
            ('rate_limit_exceeded', 'rate_limited'),
            ('invalid_api_key', 'authentication'),
            ('request_timeout', 'timeout'),
            ('output_limit_exceeded', 'output_limit'),
            ('provider-arbitrary-code-' + PRIVATE_BODY, 'upstream'),
        ]
        for upstream, safe in cases:
            for status in (200, 400):
                with self.subTest(code=upstream, status=status):
                    error = {'error': {'code': upstream, 'message': PRIVATE_BODY + FIXTURE_KEY + FIXTURE_URL}}
                    self.fails(encoded(error), safe, status=status, media='application/json')

    def test_sse_error_payload_and_event_error_are_sanitized(self):
        data = {'error': {'type': 'context_length_exceeded', 'message': PRIVATE_BODY}}
        self.fails(delta('部分') + sse(data), 'context_limit')
        self.fails(b'event: error\n' + sse({'code': 'invalid_api_key', 'message': PRIVATE_BODY}),
                   'authentication')
        self.fails(sse({'type': 'error', 'code': 'rate_limit_exceeded', 'message': PRIVATE_BODY}),
                   'rate_limited')

    def test_message_only_context_error_is_safely_classified(self):
        data = {'error': 'maximum context length exceeded: ' + PRIVATE_BODY}
        self.fails(encoded(data), 'context_limit', status=400, media='application/json')
        self.fails(encoded(data) + b'\n', 'context_limit', channel=OLLAMA,
                   media='application/x-ndjson')

    def test_http_status_mapping_no_redirects_and_no_retries(self):
        for status, code in [(401, 'authentication'), (403, 'authentication'), (429, 'rate_limited'),
                             (408, 'timeout'), (504, 'timeout'), (400, 'upstream'), (500, 'upstream'),
                             (301, 'upstream'), (302, 'upstream'), (307, 'upstream'), (308, 'upstream')]:
            with self.subTest(status=status):
                self.fails(PRIVATE_BODY.encode(), code, status=status,
                           headers={'Location': 'https://redirect-fixture.invalid/private'})
                self.assertEqual(str(self.requests[0].url), FIXTURE_URL)

    def test_transport_exceptions_are_sanitized_and_never_retried(self):
        cases = [
            (httpx.ReadTimeout, 'timeout'), (httpx.ConnectTimeout, 'timeout'),
            (httpx.WriteTimeout, 'timeout'), (httpx.PoolTimeout, 'timeout'),
            (httpx.ConnectError, 'unavailable'), (httpx.ProxyError, 'unavailable'),
            (httpx.ReadError, 'incomplete_response'), (httpx.RemoteProtocolError, 'incomplete_response'),
            (httpx.DecodingError, 'invalid_response'), (httpx.InvalidURL, 'configuration'),
            (httpx.LocalProtocolError, 'configuration'),
        ]
        for exception_type, code in cases:
            with self.subTest(exception=exception_type.__name__):
                error = exception_type(PRIVATE_BODY + FIXTURE_KEY + FIXTURE_URL)
                with patch.object(llm_stream.httpx, 'stream', side_effect=error) as request:
                    try:
                        llm_stream.stream_chat(CHANNEL, MESSAGES)
                    except ChannelUnavailable as safe:
                        self.assert_safe(safe, code)
                        trace = traceback.format_exc()
                        for private in (PRIVATE_BODY, FIXTURE_KEY, FIXTURE_URL):
                            self.assertNotIn(private, trace)
                    else:
                        self.fail('Transport error was treated as success')
                request.assert_called_once()

    def test_midstream_read_failure_cannot_return_partial_content(self):
        self.fails([delta('部分')], 'incomplete_response',
                   failure=httpx.RemoteProtocolError(PRIVATE_BODY + FIXTURE_URL))

    def test_midstream_timeout_is_timeout_and_cleans_abort(self):
        control = FakeControl()
        self.fails([delta('部分')], 'timeout', control=control,
                   failure=httpx.ReadTimeout(PRIVATE_BODY + FIXTURE_KEY))
        self.assertIs(control.registered[0], control.cleared[0])
        self.assertIsNone(control.abort)

    def test_ollama_ndjson_utf8_multiple_frames_and_terminal_content(self):
        control = FakeControl()
        body = b'\n' + ndjson('完整🙂') + b'  \r\n' + ndjson('回答\n', True, done_reason='stop')
        chunks = [body[i:i + 1] for i in range(len(body))]
        self.assertEqual(self.run_wire(chunks, channel=OLLAMA, control=control,
                                      media='application/x-ndjson'), '完整🙂回答\n')
        self.assertEqual(control.progress_events, [
            ('generating', {'received_chars': 3}), ('generating', {'received_chars': 6}),
        ])
        self.assertEqual(set(json.loads(self.requests[0].content)),
                         {'model', 'messages', 'stream', 'options'})

    def test_ollama_done_without_message_and_without_final_newline(self):
        self.assertEqual(self.run_wire(ndjson('已完成') + b'{"done":true}',
                                      channel=OLLAMA, media='application/x-ndjson'), '已完成')

    def test_ollama_reasoning_is_ignored(self):
        body = encoded({'message': {'content': '仅正文', 'thinking': PRIVATE_BODY,
                                    'reasoning_content': PRIVATE_BODY},
                        'thinking': PRIVATE_BODY, 'done': True})
        control = FakeControl()
        self.assertEqual(self.run_wire(body, channel=OLLAMA, control=control), '仅正文')
        self.assertEqual(control.progress_events, [('generating', {'received_chars': 3})])

    def test_ollama_eof_without_done_never_succeeds(self):
        for body in (ndjson('部分'), b'', encoded({'message': {'content': '部分'}})):
            with self.subTest(body=body):
                self.fails(body, 'incomplete_response', channel=OLLAMA)

    def test_ollama_done_must_be_boolean_true(self):
        for done in ('true', 1, None):
            with self.subTest(done=done):
                self.fails(encoded({'message': {'content': '部分'}, 'done': done}),
                           'invalid_response', channel=OLLAMA)

    def test_ollama_length_empty_and_error_frames_are_not_answers(self):
        self.fails(ndjson('部分', True, done_reason='length'), 'output_limit', channel=OLLAMA)
        self.fails(ndjson(' \n', True), 'empty_response', channel=OLLAMA)
        self.fails(ndjson('部分') + encoded({'error': PRIVATE_BODY + FIXTURE_KEY}),
                   'upstream', channel=OLLAMA)
        self.fails(b'{"message":', 'invalid_response', channel=OLLAMA)
        self.fails(encoded({'done': False, 'message': ['bad']}), 'invalid_response', channel=OLLAMA)

    def test_active_stream_has_no_total_timeout_using_virtual_clock(self):
        clock = [0.]
        frames = [delta(None, reasoning_content=PRIVATE_BODY) for _ in range(12)]
        frames.extend([delta('最终'), delta('完整', 'stop')])

        def on_read(_):
            clock[0] += 90.  # Each idle interval is above old 30s, below 180s.

        with patch.object(time, 'monotonic', side_effect=lambda: clock[0]), \
             patch.object(time, 'time', side_effect=lambda: clock[0]), \
             self.wire(frames, on_read=on_read):
            self.assertEqual(llm_stream.stream_chat(CHANNEL, MESSAGES), '最终完整')
        self.assertGreater(clock[0], 180. * 6)
        self.assertEqual(self.requests[0].extensions['timeout']['read'], 180.)

    def test_cancel_before_send_propagates_original_exception(self):
        control = FakeControl()
        control.cancel()
        with patch.object(llm_stream.httpx, 'stream') as request:
            with self.assertRaises(GenerationCancelled) as caught:
                llm_stream.stream_chat(CHANNEL, MESSAGES, control=control)
        self.assertIs(caught.exception, control.cancellation)
        request.assert_not_called()
        self.assertEqual(control.registered, [])

    def test_registered_and_cleared_callback_have_identical_identity(self):
        control = FakeControl()
        self.assertEqual(self.run_wire(delta('ok', 'stop'), control=control), 'ok')
        self.assertEqual(len(control.registered), 1)
        self.assertEqual(len(control.cleared), 1)
        self.assertIs(control.registered[0], control.cleared[0])
        self.assertIs(control.registered[0].__self__, self.responses[0])
        self.assertIsNone(control.abort)
        self.assertTrue(self.responses[0].is_closed)

    def test_cleanup_does_not_clear_newer_abort_callback(self):
        control = FakeControl()
        newer = lambda: None
        control.on_progress = lambda: setattr(control, 'abort', newer)
        self.assertEqual(self.run_wire(delta('ok', 'stop'), control=control), 'ok')
        self.assertIs(control.registered[0], control.cleared[0])
        self.assertIs(control.abort, newer)

    def test_cancel_on_registration_closes_and_propagates(self):
        control = FakeControl()
        control.on_register = control.cancel
        with self.wire([delta('must not read', 'stop')]) as (request, feed):
            with self.assertRaises(GenerationCancelled) as caught:
                llm_stream.stream_chat(CHANNEL, MESSAGES, control=control)
        self.assertIs(caught.exception, control.cancellation)
        self.assertEqual(feed.reads, 0)
        self.assertTrue(feed.closed)
        self.assertIs(control.registered[0], control.cleared[0])
        request.assert_called_once()

    def test_each_buffered_frame_checks_cancellation(self):
        control = FakeControl()
        # No close: the frame checkpoint itself must stop the buffered body.
        control.on_progress = lambda: setattr(control, 'cancelled', True)
        body = delta('first') + delta('must not process', 'stop')
        with self.assertRaises(GenerationCancelled) as caught:
            self.run_wire(body, control=control)
        self.assertIs(caught.exception, control.cancellation)
        self.assertEqual(control.progress_events, [('generating', {'received_chars': 5})])
        self.assertTrue(self.responses[0].is_closed)

    def test_cancel_closes_read_and_is_not_translated_to_transport_failure(self):
        control = FakeControl()

        def on_read(index):
            if index == 1:
                control.cancel()

        with self.wire([delta('部分'), delta('不可交付', 'stop')], on_read=on_read) as (request, feed):
            with self.assertRaises(GenerationCancelled) as caught:
                llm_stream.stream_chat(CHANNEL, MESSAGES, control=control)
        self.assertIs(caught.exception, control.cancellation)
        self.assertTrue(feed.closed)
        self.assertIs(control.registered[0], control.cleared[0])
        self.assertIsNone(control.abort)
        request.assert_called_once()

    def test_cancel_during_open_overrides_httpx_error(self):
        for failure in (httpx.ReadTimeout(PRIVATE_BODY), httpx.ConnectError(PRIVATE_BODY),
                        httpx.StreamClosed()):
            with self.subTest(exception=type(failure).__name__):
                control = FakeControl()

                def interrupted(*args, **kwargs):
                    control.cancel()
                    raise failure

                with patch.object(llm_stream.httpx, 'stream', side_effect=interrupted) as request:
                    with self.assertRaises(GenerationCancelled) as caught:
                        llm_stream.stream_chat(CHANNEL, MESSAGES, control=control)
                self.assertIs(caught.exception, control.cancellation)
                self.assertEqual(control.cleared, [])
                request.assert_called_once()

    def test_checkpoint_before_return_after_response_cleanup(self):
        control = FakeControl()
        control.on_clear = lambda: setattr(control, 'cancelled', True)
        with self.assertRaises(GenerationCancelled) as caught:
            self.run_wire(delta('complete but cancelled', 'stop'), control=control)
        self.assertIs(caught.exception, control.cancellation)
        self.assertTrue(self.responses[0].is_closed)

    def test_abort_cleanup_also_runs_on_parse_errors(self):
        control = FakeControl()
        self.fails(sse('{'), 'invalid_response', control=control)
        self.assertIs(control.registered[0], control.cleared[0])
        self.assertIsNone(control.abort)

    def test_unexpected_control_exception_is_not_swallowed(self):
        control = FakeControl()
        bug = RuntimeError('fixture control failure')

        def broken_progress():
            raise bug

        control.on_progress = broken_progress
        with self.assertRaises(RuntimeError) as caught:
            self.run_wire(delta('正文', 'stop'), control=control)
        self.assertIs(caught.exception, bug)
        self.assertIs(control.registered[0], control.cleared[0])
        self.assertTrue(self.responses[0].is_closed)


if __name__ == '__main__':
    unittest.main()
