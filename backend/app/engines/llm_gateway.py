"""Existing OpenAI-compatible/Ollama channels; failures never masquerade as answers."""
from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit

import httpx


class ChannelUnavailable(RuntimeError):
    def __init__(self, message='生成模型暂时不可用', *, code='unavailable'):
        super().__init__(message)
        self.code = code


def _endpoint(base_url: str, *, ollama=False) -> str:
    parts = urlsplit(base_url.strip())
    if parts.scheme not in ('http', 'https') or not parts.netloc or parts.username or parts.password or parts.query or parts.fragment:
        raise ChannelUnavailable('模型地址格式不正确', code='configuration')
    path = parts.path.rstrip('/')
    if ollama:
        path = path if path.endswith('/api/chat') else path + '/api/chat'
    elif not path.endswith('/chat/completions'):
        path += '/chat/completions' if path.endswith('/v1') else '/v1/chat/completions'
    return urlunsplit((parts.scheme, parts.netloc, path, '', ''))


def _request(url, payload, headers, timeout_s):
    try:
        response = httpx.post(url, json=payload, headers=headers,
                              timeout=httpx.Timeout(float(timeout_s), connect=min(8., float(timeout_s))))
        response.raise_for_status()
        return response.json()
    except httpx.TimeoutException as exc:
        raise ChannelUnavailable('生成模型响应超时', code='timeout') from exc
    except httpx.HTTPStatusError as exc:
        code = 'authentication' if exc.response.status_code in (401, 403) else 'rate_limited' if exc.response.status_code == 429 else 'upstream'
        raise ChannelUnavailable('生成通道返回错误，请检查模型设置', code=code) from exc
    except (httpx.RequestError, ValueError) as exc:
        raise ChannelUnavailable('无法连接生成模型或响应格式异常', code='unavailable') from exc


def _openai_chat(base_url, api_key, model, messages, temperature, timeout_s):
    headers = {'Content-Type': 'application/json'}
    if api_key:
        headers['Authorization'] = f'Bearer {api_key}'
    data = _request(_endpoint(base_url), {
        'model': model, 'messages': messages, 'temperature': temperature, 'stream': False,
    }, headers, timeout_s)
    try:
        content = data['choices'][0]['message']['content']
        if not isinstance(content, str) or not content.strip():
            raise ValueError('empty content')
        return content
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise ChannelUnavailable('模型未返回可用的回答正文', code='empty_response') from exc


def _ollama_chat(base_url, model, messages, temperature, timeout_s):
    data = _request(_endpoint(base_url, ollama=True), {
        'model': model, 'messages': messages, 'stream': False, 'options': {'temperature': temperature},
    }, {'Content-Type': 'application/json'}, timeout_s)
    try:
        content = data['message']['content']
        if not isinstance(content, str) or not content.strip():
            raise ValueError('empty content')
        return content
    except (KeyError, TypeError, ValueError) as exc:
        raise ChannelUnavailable('模型未返回可用的回答正文', code='empty_response') from exc


def channel_chat(channel, messages):
    ctype = channel.get('channel_type')
    base = channel.get('base_url', '')
    path = urlsplit(base).path.rstrip('/')
    if ctype == 'local' and not (path.endswith('/v1') or path.endswith('/chat/completions')):
        return _ollama_chat(base, channel.get('model_name', ''), messages,
                            channel.get('temperature', .3), channel.get('timeout_s', 30))
    if ctype in ('online', 'local'):
        return _openai_chat(base, channel.get('api_key', ''), channel.get('model_name', ''), messages,
                            channel.get('temperature', .3), channel.get('timeout_s', 30))
    raise ChannelUnavailable('该通道不支持生成回答', code='configuration')


def degraded_answer(question, top_chunks, kb_name):
    """Return labelled retrieval evidence, never a pretend synthesized answer."""
    if not top_chunks:
        return '当前知识库没有检索到相关正文证据。', 'degraded'
    blocks = []
    for rank, chunk in enumerate(top_chunks, 1):
        page = f' · 第{chunk["page_no"]}页' if chunk.get('page_no') else ''
        excerpt = chunk['content'].strip()
        blocks.append(f'### [来源{rank}] {chunk["doc_name"]}{page}\n\n' + excerpt)
    return '\n\n'.join(blocks), 'degraded'


def _trim_excerpt(text, limit):
    clean = re.sub(r'\s+', ' ', text).strip()
    return clean if len(clean) <= limit else clean[:limit] + '…'