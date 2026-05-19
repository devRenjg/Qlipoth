import json
import asyncio
import httpx
from config import load_settings

SEARCH_STRATEGY_PROMPT = """你是一个知识库搜索助手。用户会提出一个问题，你需要分析问题并生成搜索策略。

请返回 JSON 格式（不要包含 markdown 代码块标记）：
{
    "keywords": ["关键词1", "关键词2", ...],
    "file_pattern": "*",
    "need_full_file": false,
    "reasoning": "简短说明搜索策略"
}

规则：
- keywords: 提取 2-5 个最相关的搜索关键词，包括同义词和相关术语
- file_pattern: 文件名匹配模式，默认 "*" 表示搜索所有文件
- need_full_file: 如果问题需要完整上下文（如"总结某文档"），设为 true

用户问题："""

ANSWER_PROMPT = """你是"克里珀"大型活动保障知识库的智能助手。基于以下从知识库中搜索到的内容，回答用户的问题。

要求：
1. 只基于提供的搜索结果回答，不要编造信息
2. 如果搜索结果不足以回答问题，明确告知用户
3. 在回答末尾标注信息来源（文件名和行号）
4. 使用清晰、自然的中文回答

搜索结果：
{search_results}

用户问题：{question}"""


async def chat_completion(messages: list[dict], temperature: float = 0) -> tuple[str, float]:
    """Returns (response_text, elapsed_seconds). Supports OpenAI and Anthropic API formats."""
    settings = load_settings()
    base_url = settings.llm_base_url.rstrip("/")
    api_format = settings.llm_api_format

    if api_format == "anthropic":
        url, headers, payload = _build_anthropic_request(settings, messages, temperature, base_url)
    else:
        url, headers, payload = _build_openai_request(settings, messages, temperature, base_url)

    last_err = None
    for attempt in range(3):
        if attempt > 0:
            await asyncio.sleep(2 ** attempt)
        try:
            t0 = asyncio.get_event_loop().time()
            async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0)) as client:
                resp = await client.post(url, json=payload, headers=headers)
                elapsed = asyncio.get_event_loop().time() - t0
                if resp.status_code >= 500:
                    last_err = f"LLM API error {resp.status_code}: {resp.text[:200]}"
                    continue
                if resp.status_code >= 400:
                    raise RuntimeError(f"LLM API {resp.status_code}: {resp.text[:300]}")
                if not resp.text:
                    last_err = "LLM API returned empty response"
                    continue
                data = resp.json()
                text = _extract_response_text(data, api_format)
                return text, elapsed
        except RuntimeError:
            raise
        except (httpx.TimeoutException, httpx.ConnectError) as e:
            last_err = str(e)
            continue
    raise RuntimeError(last_err or "LLM API failed after 3 attempts")


def _build_openai_request(settings, messages, temperature, base_url):
    if base_url.endswith("/v1") or "/v1/" in base_url:
        url = f"{base_url.rstrip('/')}/chat/completions"
    else:
        url = f"{base_url}/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.llm_api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": settings.llm_model,
        "messages": messages,
        "max_tokens": 4096,
        "temperature": temperature,
    }
    return url, headers, payload


def _build_anthropic_request(settings, messages, temperature, base_url):
    system_msg = None
    user_messages = []
    for msg in messages:
        if msg["role"] == "system":
            system_msg = msg["content"]
        else:
            user_messages.append(msg)

    if base_url.endswith("/v1") or "/v1/" in base_url:
        url = f"{base_url.rstrip('/')}/messages"
    else:
        url = f"{base_url}/v1/messages"
    headers = {
        "Authorization": f"Bearer {settings.llm_api_key}",
        "Content-Type": "application/json",
        "anthropic-version": "2023-06-01",
    }
    payload = {
        "model": settings.llm_model,
        "messages": user_messages,
        "max_tokens": 4096,
        "temperature": temperature,
    }
    if system_msg:
        payload["system"] = system_msg
    return url, headers, payload


def _extract_response_text(data: dict, api_format: str) -> str:
    if api_format == "anthropic":
        return data["content"][0]["text"].strip()
    return data["choices"][0]["message"]["content"].strip()


async def generate_search_strategy(question: str) -> tuple[dict, float]:
    """Returns (strategy_dict, llm_elapsed_seconds)."""
    content, elapsed = await chat_completion([
        {"role": "system", "content": SEARCH_STRATEGY_PROMPT},
        {"role": "user", "content": question},
    ], temperature=0)
    content = content.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        return json.loads(content), elapsed
    except json.JSONDecodeError:
        return {"keywords": [question], "file_pattern": "*", "need_full_file": False}, elapsed


async def generate_answer(question: str, search_results: str) -> tuple[str, float]:
    """Returns (answer_text, llm_elapsed_seconds)."""
    prompt = ANSWER_PROMPT.format(search_results=search_results, question=question)
    return await chat_completion([
        {"role": "user", "content": prompt},
    ], temperature=0.3)


async def stream_answer(question: str, search_results: str):
    """Yields text chunks as they arrive from the LLM."""
    settings = load_settings()
    base_url = settings.llm_base_url.rstrip("/")
    api_format = settings.llm_api_format

    prompt = ANSWER_PROMPT.format(search_results=search_results, question=question)
    messages = [{"role": "user", "content": prompt}]

    if api_format == "anthropic":
        url, headers, payload = _build_anthropic_request(settings, messages, 0.3, base_url)
        payload["stream"] = True
    else:
        url, headers, payload = _build_openai_request(settings, messages, 0.3, base_url)
        payload["stream"] = True

    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0)) as client:
        async with client.stream("POST", url, json=payload, headers=headers) as resp:
            if resp.status_code >= 400:
                text = ""
                async for chunk in resp.aiter_text():
                    text += chunk
                raise RuntimeError(f"LLM API {resp.status_code}: {text[:300]}")
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data_str = line[6:]
                if data_str == "[DONE]":
                    break
                try:
                    data = json.loads(data_str)
                    text = _extract_stream_delta(data, api_format)
                    if text:
                        yield text
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue


def _extract_stream_delta(data: dict, api_format: str) -> str:
    if api_format == "anthropic":
        if data.get("type") == "content_block_delta":
            return data.get("delta", {}).get("text", "")
        return ""
    choice = data.get("choices", [{}])[0]
    return choice.get("delta", {}).get("content", "")
