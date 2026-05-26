import json
import asyncio
import httpx
from pathlib import Path
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
- keywords: 提取 2-5 个最相关的搜索关键词，优先使用精确短语而非单字拆分
- keywords 应包含问题中最具区分度的词，避免过于宽泛的词（如单独的"2026"、"CNY"）
- file_pattern: 文件名匹配模式，默认 "*" 表示搜索所有文件。如果问题提到了具体文档名，用 "*关键词*" 缩小范围
- need_full_file: 如果问题需要完整上下文（如"总结某文档"），设为 true
- 当用户问"多少"、"几个"、"人数"等数量问题时，keywords 应包含"总计"、"统计"、"合计"等汇总关键词
- 文档中 @人名 表示该人是对应事项的负责人(Owner)。当用户问"谁负责XX"、"XX是谁跟进的"时，keywords 应包含相关事项关键词，搜索结果中 @后面的人名即为负责人

用户问题："""


def _load_soul() -> str:
    soul_path = Path(__file__).parent / "Soul.md"
    if soul_path.exists():
        return soul_path.read_text(encoding="utf-8")
    return ""


SOUL = _load_soul()

ANSWER_PROMPT = """你是 大型活动保障知识库助手，统筹所有参与保障的团队（研发、产品、测试、市场、销售、审核、运营、设计、客服等），具备公司顶层全局视角。

{soul}

以下是你掌握的相关资料：
{search_results}

同事问你：{question}

回答要求：
- 以活动保障知识库助手的身份直接回答，不要暴露任何搜索或检索过程
- 涉及数量、人数等问题时，先给出整体/总计数据，再按需给出分类明细
- 不要只回答某个部门或某个工种的数据，除非用户明确指定了范围"""


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
    prompt = ANSWER_PROMPT.format(soul=SOUL, search_results=search_results, question=question)
    return await chat_completion([
        {"role": "user", "content": prompt},
    ], temperature=0.3)


async def stream_answer(question: str, search_results: str):
    """Yields text chunks as they arrive from the LLM."""
    settings = load_settings()
    base_url = settings.llm_base_url.rstrip("/")
    api_format = settings.llm_api_format

    prompt = ANSWER_PROMPT.format(soul=SOUL, search_results=search_results, question=question)
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
