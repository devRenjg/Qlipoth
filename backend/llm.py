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
- keywords: 提取 3-6 个最相关的搜索关键词
- 🔴 拆出最小核心实体词,不要带年份/序数前缀:"26年春晚"应拆成"春晚",而非整体保留;"第三轮压测"拆成"压测"。长复合词会导致召回率骤降
- 当问题涉及年份、同比、对比时,把相关年份单独作为关键词列出(如"2025"、"2026"、"去年"对应的具体年份),因为数据常与年份并列出现
- keywords 应包含问题中最具区分度的词(如"版本覆盖率"这类专有指标),避免过于宽泛的词(如单独的"CNY"、"活动")
- file_pattern: 文件名匹配模式,默认 "*" 表示搜索所有文件。如果问题提到了具体文档名,用 "*关键词*" 缩小范围
- need_full_file: 如果问题需要完整上下文(如"总结某文档"),设为 true
- 当用户问"多少"、"几个"、"人数"等数量问题时,keywords 应包含"总计"、"统计"、"合计"等汇总关键词
- 文档中 @人名 表示该人是对应事项的负责人(Owner)。当用户问"谁负责XX"、"XX是谁跟进的"时,keywords 应包含相关事项关键词,搜索结果中 @后面的人名即为负责人

用户问题："""


COREF_PROMPT = """你是一个对话指代消解助手。给定一段对话历史和用户的当前问题，判断当前问题是否是依赖上文的追问，如果是，把它改写成一个不依赖上文、可独立检索的完整问题。

请返回 JSON 格式（不要包含 markdown 代码块标记）：
{
    "is_followup": true 或 false,
    "resolved_question": "改写后的完整问题，或原样返回的问题"
}

规则：
- 仅当当前问题含有代词（"这个""那个""他""它"）、省略（"那市场侧呢""再细化下""还有呢"）等依赖上文才能理解的情况时，is_followup 设为 true，并补全成完整问题。
- 如果当前问题本身语义自包含、不依赖上文（哪怕和上文同一话题），is_followup 设为 false，resolved_question 必须逐字等于原问题，一个字都不要改。
- 改写时只补最小必要的实体词，保持简洁，不要堆砌年份/序数等长前缀（呼应搜索关键词哲学）。
- 不确定时，倾向 is_followup=false 原样返回，避免过度改写污染检索。

示例1（省略式追问）：
对话历史：
用户：26年春晚市场侧保障人数是多少
保障负责人：市场侧共投入12人……
当前问题：那研发侧呢
输出：{"is_followup": true, "resolved_question": "春晚研发侧保障人数"}

示例2（自包含新问题）：
对话历史：
用户：26年春晚市场侧保障人数是多少
保障负责人：市场侧共投入12人……
当前问题：今年S赛的压测计划是怎么安排的
输出：{"is_followup": false, "resolved_question": "今年S赛的压测计划是怎么安排的"}"""


ANSWER_PROMPT_WITH_HISTORY = """你是 大型活动保障知识库助手，统筹所有参与保障的团队（研发、产品、测试、市场、销售、审核、运营、设计、客服等），具备公司顶层全局视角。

{soul}

以下是你与同事之前的对话历史，仅供你理解上下文、保持回答连贯，不要把历史里的内容当作权威资料，也不要复述历史：
{conversation_history}

以下是你掌握的相关资料：
{search_results}

同事问你：{question}

回答要求：
- 以活动保障知识库助手的身份直接回答，不要暴露任何搜索或检索过程
- 涉及数量、人数等问题时，先给出整体/总计数据，再按需给出分类明细
- 不要只回答某个部门或某个工种的数据，除非用户明确指定了范围
- 结合对话历史保持指代连贯，但所有结论必须以"相关资料"为准"""


async def resolve_coreference(recent_turns_block: str, question: str) -> tuple[str, bool, float]:
    """指代消解：仅在有上文时调用。返回 (resolved_question, is_followup, elapsed)。

    失败/解析异常一律回退 standalone：(question, False, elapsed)。
    历史/问题作为独立 user message 传入，绝不拼进含花括号的 COREF_PROMPT 模板。
    """
    user_content = f"对话历史：\n{recent_turns_block}\n当前问题：{question}"
    try:
        content, elapsed = await chat_completion(
            [
                {"role": "system", "content": COREF_PROMPT},
                {"role": "user", "content": user_content},
            ],
            temperature=0,
        )
    except Exception:  # noqa: BLE001
        return question, False, 0.0
    content = content.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return question, False, elapsed
    is_followup = bool(data.get("is_followup"))
    resolved = data.get("resolved_question") or question
    if not is_followup or not isinstance(resolved, str) or not resolved.strip():
        return question, False, elapsed
    return resolved.strip(), True, elapsed


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


async def chat_completion(messages: list[dict], temperature: float = 0, model: str = "") -> tuple[str, float]:
    """Returns (response_text, elapsed_seconds). Supports OpenAI and Anthropic API formats.

    model 非空时覆盖 settings.llm_model（用于问题分类路由按类选模型）。"""
    settings = load_settings()
    base_url = settings.llm_base_url.rstrip("/")
    api_format = settings.llm_api_format

    if api_format == "anthropic":
        url, headers, payload = _build_anthropic_request(settings, messages, temperature, base_url)
    else:
        url, headers, payload = _build_openai_request(settings, messages, temperature, base_url)
    if model:
        payload["model"] = model

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


async def generate_answer(question: str, search_results: str, history_block: str = "", model: str = "") -> tuple[str, float]:
    """Returns (answer_text, llm_elapsed_seconds). history_block 为空时走原 ANSWER_PROMPT，字节不变。

    model 非空时覆盖回答模型（问题分类路由用）。"""
    if history_block:
        prompt = ANSWER_PROMPT_WITH_HISTORY.format(
            soul=SOUL, conversation_history=history_block,
            search_results=search_results, question=question,
        )
    else:
        prompt = ANSWER_PROMPT.format(soul=SOUL, search_results=search_results, question=question)
    return await chat_completion([
        {"role": "user", "content": prompt},
    ], temperature=0.3, model=model)


async def stream_answer(question: str, search_results: str, history_block: str = "", model: str = ""):
    """Yields text chunks as they arrive from the LLM. history_block 为空时走原 ANSWER_PROMPT。

    若一次流式返回 0 字符（DeepSeek 偶发空补全），自动重试一次；仍为空则退回非流式
    generate_answer 兜底，避免前端拿到空白答案（曾导致整轮"答非所问"）。
    model 非空时覆盖回答模型（问题分类路由用）。
    """
    settings = load_settings()
    base_url = settings.llm_base_url.rstrip("/")
    api_format = settings.llm_api_format

    if history_block:
        prompt = ANSWER_PROMPT_WITH_HISTORY.format(
            soul=SOUL, conversation_history=history_block,
            search_results=search_results, question=question,
        )
    else:
        prompt = ANSWER_PROMPT.format(soul=SOUL, search_results=search_results, question=question)
    messages = [{"role": "user", "content": prompt}]

    yielded_chars = 0
    async for text in _stream_once(messages, settings, base_url, api_format, model):
        yielded_chars += len(text)
        yield text
    if yielded_chars:
        return

    # 空补全：重试一次流式
    async for text in _stream_once(messages, settings, base_url, api_format, model):
        yielded_chars += len(text)
        yield text
    if yielded_chars:
        return

    # 仍为空：非流式兜底
    try:
        answer, _ = await generate_answer(question, search_results, history_block, model=model)
    except RuntimeError:
        answer = ""
    yield answer.strip() or "抱歉，本次未能生成回答，请重试。"


async def _stream_once(messages, settings, base_url, api_format, model: str = ""):
    """一次流式请求，逐块产出文本。HTTP >=400 抛 RuntimeError；空补全则不产出任何块。"""
    if api_format == "anthropic":
        url, headers, payload = _build_anthropic_request(settings, messages, 0.3, base_url)
        payload["stream"] = True
    else:
        url, headers, payload = _build_openai_request(settings, messages, 0.3, base_url)
        payload["stream"] = True
    if model:
        payload["model"] = model

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
