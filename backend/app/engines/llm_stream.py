"""Synchronous, completion-verified streaming without exposing upstream details.

Only answer content is accumulated. ``control`` is deliberately duck-typed; its
checkpoint exceptions belong to the caller and must not become channel failures.
HTTPX read timeouts apply to individual idle reads, never the whole generation.
"""
from __future__ import annotations

import codecs
import json
import math
import re
from itertools import chain
from urllib.parse import urlsplit

import httpx

from .llm_gateway import ChannelUnavailable, _endpoint

__all__ = ['stream_chat']

# Never use a provider's code, message, URL, request or response in an exception.
_ERROR_MESSAGES = {
    'configuration': '生成通道配置不正确',
    'timeout': '生成模型响应超时',
    'authentication': '生成通道认证失败，请检查模型设置',
    'rate_limited': '生成通道请求受限，请稍后重试',
    'context_limit': '本次输入超过生成模型的上下文容量',
    'output_limit': '生成模型达到输出上限，回答未完整生成',
    'empty_response': '模型未返回可用的回答正文',
    'invalid_response': '生成模型响应格式异常',
    'incomplete_response': '生成连接中断，回答未完整生成',
    'upstream': '生成通道返回错误，请检查模型设置',
    'unavailable': '无法连接生成模型',
}
_ERROR_CODES = {
    'context_length_exceeded': 'context_limit',
    'context_window_exceeded': 'context_limit',
    'context_limit': 'context_limit',
    'prompt_too_long': 'context_limit',
    'input_too_long': 'context_limit',
    'rate_limit_exceeded': 'rate_limited',
    'rate_limited': 'rate_limited',
    'too_many_requests': 'rate_limited',
    'insufficient_quota': 'rate_limited',
    'invalid_api_key': 'authentication',
    'authentication_error': 'authentication',
    'unauthorized': 'authentication',
    'permission_denied': 'authentication',
    'access_denied': 'authentication',
    'timeout': 'timeout',
    'request_timeout': 'timeout',
    'request_timed_out': 'timeout',
    'timeout_error': 'timeout',
    'output_limit': 'output_limit',
    'output_limit_exceeded': 'output_limit',
    'max_output_tokens_exceeded': 'output_limit',
}
_NEWLINES = re.compile(r'\r\n|\r|\n')


def _failure(code):
    return ChannelUnavailable(_ERROR_MESSAGES[code], code=code)


def _checkpoint(control):
    if control is not None:
        control.checkpoint()


def _request_options(channel, messages):
    try:
        if not isinstance(channel, dict) or not isinstance(messages, list):
            raise ValueError
        ctype = channel.get('channel_type')
        base = channel.get('base_url', '')
        if ctype not in ('online', 'local') or not isinstance(base, str):
            raise ValueError
        path = urlsplit(base.strip()).path.rstrip('/')
        ollama = ctype == 'local' and not path.endswith(('/v1', '/chat/completions'))
        url = _endpoint(base, ollama=ollama)
        configured_timeout = float(channel.get('timeout_s', 30))
        if not math.isfinite(configured_timeout) or configured_timeout <= 0:
            raise ValueError
        timeout = httpx.Timeout(
            connect=8.0, read=max(180.0, configured_timeout), write=30.0, pool=8.0,
        )
        payload = {
            'model': channel.get('model_name', ''), 'messages': messages, 'stream': True,
        }
        headers = {'Content-Type': 'application/json'}
        temperature = channel.get('temperature', .3)
        if ollama:
            payload['options'] = {'temperature': temperature}
        else:
            payload['temperature'] = temperature
            headers['Accept'] = 'text/event-stream, application/json'
            if channel.get('api_key'):
                headers['Authorization'] = f"Bearer {channel['api_key']}"
        return url, payload, headers, timeout, ollama
    except (ChannelUnavailable, TypeError, ValueError, OverflowError):
        pass
    raise _failure('configuration')


def _lines(response, control):
    """Strict UTF-8, including split code points/BOM and LF, CRLF or CR lines.

    Parts are joined only at a line boundary, so a large frame arriving in tiny
    byte chunks does not repeatedly copy the entire unfinished frame.
    """
    decoder = codecs.getincrementaldecoder('utf-8-sig')('strict')
    parts = []
    skip_lf = False
    for chunk in response.iter_bytes():
        _checkpoint(control)
        text = decoder.decode(chunk)
        if not text:
            continue
        if skip_lf and text.startswith('\n'):
            text = text[1:]
        skip_lf = False
        start = 0
        for boundary in _NEWLINES.finditer(text):
            parts.append(text[start:boundary.start()])
            _checkpoint(control)
            yield ''.join(parts)
            parts.clear()
            start = boundary.end()
        skip_lf = text.endswith('\r')
        if start < len(text):
            parts.append(text[start:])
    tail = decoder.decode(b'', final=True)
    if tail:
        parts.append(tail)
    if parts:
        _checkpoint(control)
        yield ''.join(parts)


def _json_object(text):
    try:
        data = json.loads(text)
    except (ValueError, RecursionError):
        pass
    else:
        if isinstance(data, dict):
            return data
    raise _failure('invalid_response')


def _payload_error(data):
    if 'error' in data and data['error'] is not None:
        error = data['error']
    elif data.get('type') == 'error':
        error = data
    else:
        return None
    fields = [error] if isinstance(error, str) else []
    if isinstance(error, dict):
        fields = [error.get('code'), error.get('type')]
    for value in fields:
        if isinstance(value, str) and value.lower() in _ERROR_CODES:
            return _ERROR_CODES[value.lower()]
    # Some compatible servers/Ollama provide only an error message, not a code.
    message = error.get('message', '') if isinstance(error, dict) else error
    if isinstance(message, str) and any(marker in message.lower() for marker in (
        'context_length_exceeded', 'context_window_exceeded',
        'maximum context length', 'context length exceeded',
        'prompt is too long', 'input is too long',
    )):
        return 'context_limit'
    return 'upstream'


def _status_code(status):
    if status in (401, 403):
        return 'authentication'
    if status == 429:
        return 'rate_limited'
    if status in (408, 504):
        return 'timeout'
    return 'upstream'


def _check_status(response, control):
    if 200 <= response.status_code < 300:
        return
    code = _status_code(response.status_code)
    if code == 'upstream' and response.status_code >= 400:
        # Inspect standard error codes only; never attach the body to the error.
        try:
            data = _json_object('\n'.join(_lines(response, control)))
        except ChannelUnavailable:
            pass
        else:
            code = _payload_error(data) or code
    raise _failure(code)


class _Answer:
    def __init__(self, control):
        self.control = control
        self.parts = []
        self.received_chars = 0

    def add(self, content):
        if content is None:
            return
        if not isinstance(content, str):
            raise _failure('invalid_response')
        if content:
            self.parts.append(content)
            self.received_chars += len(content)
            if self.control is not None:
                self.control.progress('generating', received_chars=self.received_chars)

    def result(self):
        content = ''.join(self.parts)
        if not content.strip():
            raise _failure('empty_response')
        return content


def _openai_frame(data, answer, *, whole=False):
    error = _payload_error(data)
    if error:
        raise _failure(error)
    choices = data.get('choices')
    if not isinstance(choices, list) or (whole and not choices):
        raise _failure('invalid_response')
    selected = None
    for position, choice in enumerate(choices):
        if not isinstance(choice, dict):
            raise _failure('invalid_response')
        index = choice.get('index', position)
        if not isinstance(index, int) or isinstance(index, bool) or index < 0:
            raise _failure('invalid_response')
        if index == 0:
            if selected is not None:
                raise _failure('invalid_response')
            selected = choice
    if selected is None:
        if whole:
            raise _failure('invalid_response')
        return False  # Usage-only chunks or another choice are not answer text.
    reason = selected.get('finish_reason')
    if reason == 'length':
        raise _failure('output_limit')
    if reason not in (None, 'stop'):
        raise _failure('upstream')
    body = selected.get('message') if whole else selected.get('delta', selected.get('message'))
    if body is None:
        if whole or reason is None:
            raise _failure('invalid_response')
    elif not isinstance(body, dict):
        raise _failure('invalid_response')
    else:
        answer.add(body.get('content'))
    return reason == 'stop'


def _sse_frames(lines, control):
    data = []
    event = ''
    for line in lines:
        _checkpoint(control)
        if not line:
            if data:
                yield event, '\n'.join(data)
            data = []
            event = ''
        elif not line.startswith(':'):
            field, _, value = line.partition(':')
            if value.startswith(' '):
                value = value[1:]
            if field == 'data':
                data.append(value)
            elif field == 'event':
                event = value
            # SSE comments, id, retry and extension fields carry no answer.
    if data:
        yield event, '\n'.join(data)


def _read_openai(response, answer, control):
    lines = iter(_lines(response, control))
    first = next((line for line in lines if line.strip()), None)
    if first is None:
        raise _failure('empty_response')
    media_type = response.headers.get('content-type', '').split(';', 1)[0].strip().lower()
    sse_prefix = first.startswith((':', 'data:', 'event:', 'id:', 'retry:'))
    whole_json = first.lstrip().startswith(('{', '[')) or (
        (media_type == 'application/json' or media_type.endswith('+json')) and not sse_prefix
    )
    if whole_json:
        data = _json_object('\n'.join(chain((first,), lines)))
        _checkpoint(control)
        if not _openai_frame(data, answer, whole=True):
            raise _failure('incomplete_response')
        return
    for event, text in _sse_frames(chain((first,), lines), control):
        _checkpoint(control)
        if event == 'error':
            data = _json_object(text)
            raise _failure(_payload_error(data) or _payload_error({'error': data}))
        if text.strip() == '[DONE]':
            return  # Compatibility: DONE proves completion if reason is absent.
        if _openai_frame(_json_object(text), answer):
            return  # Do not wait for EOF/another idle timeout after a valid stop.
    raise _failure('incomplete_response')


def _read_ollama(response, answer, control):
    for line in _lines(response, control):
        _checkpoint(control)
        if not line.strip():
            continue
        data = _json_object(line)
        error = _payload_error(data)
        if error:
            raise _failure(error)
        if data.get('done_reason') == 'length' or data.get('finish_reason') == 'length':
            raise _failure('output_limit')
        done = data.get('done', False)
        if not isinstance(done, bool):
            raise _failure('invalid_response')
        message = data.get('message')
        if message is None:
            if not done:
                raise _failure('invalid_response')
        elif not isinstance(message, dict):
            raise _failure('invalid_response')
        else:
            answer.add(message.get('content'))
        if done:
            return
    raise _failure('incomplete_response')


def stream_chat(channel: dict, messages: list, *, control=None) -> str:
    """Return a complete answer, or a sanitized ChannelUnavailable/cancellation.

    No application input/output cap, redirect, retry or total stream deadline is
    introduced. The response-close callback becomes available after HTTP headers;
    cancellation before that point is checked before sending and after opening.
    """
    _checkpoint(control)
    url, payload, headers, timeout, ollama = _request_options(channel, messages)
    answer = _Answer(control)
    close_callback = None
    error_code = None
    try:
        _checkpoint(control)
        with httpx.stream('POST', url, json=payload, headers=headers, timeout=timeout,
                          follow_redirects=False) as response:
            close_callback = response.close  # Keep this exact bound-method identity.
            if control is not None:
                control.set_abort(close_callback)
            _checkpoint(control)
            _check_status(response, control)
            if ollama:
                _read_ollama(response, answer, control)
            else:
                _read_openai(response, answer, control)
            result = answer.result()
            _checkpoint(control)
    except ChannelUnavailable as exc:
        _checkpoint(control)
        error_code = exc.code if exc.code in _ERROR_MESSAGES else 'upstream'
    except httpx.TimeoutException:
        _checkpoint(control)
        error_code = 'timeout'
    except httpx.HTTPStatusError as exc:
        _checkpoint(control)
        error_code = _status_code(exc.response.status_code)
    except (httpx.InvalidURL, httpx.LocalProtocolError):
        _checkpoint(control)
        error_code = 'configuration'
    except (httpx.DecodingError, UnicodeError):
        _checkpoint(control)
        error_code = 'invalid_response'
    except (httpx.ReadError, httpx.RemoteProtocolError, httpx.StreamError):
        _checkpoint(control)
        error_code = 'incomplete_response'
    except httpx.RequestError:
        _checkpoint(control)
        error_code = 'unavailable'
    finally:
        if control is not None and close_callback is not None:
            control.clear_abort(close_callback)
    _checkpoint(control)
    if error_code is not None:
        # Outside the except block: no upstream exception context/cause escapes.
        raise _failure(error_code)
    return result
