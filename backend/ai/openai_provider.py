"""
OpenAI/兼容 API 提供者模块。

实现 AIProvider 抽象基类，通过 OpenAI SDK 调用兼容 API（OpenAI、DeepSeek 等），
提供流式生成、内容总结、主题提取、卡片生成等功能。
"""

import asyncio
import json
import logging
import re
from typing import AsyncIterator, Optional, List

from openai import AsyncOpenAI

from .provider import AIConfig, AIProvider, TopicCluster, GeneratedCard

logger = logging.getLogger(__name__)

_UNTRUSTED_MODULE = None


def _untrusted():
    """延迟导入 backend.agent.untrusted（复用其包裹机制，不新造）。

    backend.quality 包初始化会经 improver -> pipeline.defaults 反向 import 本模块，
    顶层导入会形成循环依赖；sys.modules 缓存后每次调用开销可忽略。
    """
    global _UNTRUSTED_MODULE
    if _UNTRUSTED_MODULE is None:
        from backend.agent import untrusted as _impl
        _UNTRUSTED_MODULE = _impl
    return _UNTRUSTED_MODULE


def _wrap_untrusted(text: str, label: str = "") -> str:
    """按开关把不可信外部内容包进边界块；legacy（默认）原样返回。"""
    return _untrusted().wrap_untrusted(text, label=label)


def _safe_meta(text: str) -> str:
    """不可信元数据（文档文件名等）：中和边界标记。

    仅在 KD_UNTRUSTED_WRAP=on 时生效；legacy 原样返回，保证默认路径逐字段不变。
    """
    module = _untrusted()
    return module.neutralize(text) if module.resolve_mode() == "on" else text


# 标题归一化统一在 backend/utils/titles.py（方案 B：pipeline/agent 标题预检复用）
from backend.utils.titles import normalize_title as _normalize_title  # noqa: E402


def normalize_api_base_url(url: str) -> str:
    """把用户可能填写的完整 endpoint 规范成 OpenAI SDK 的 base_url。

    OpenAI SDK 会在 base_url 后追加 /chat/completions 或 /responses，
    因此 base_url 不能以这些 endpoint 后缀结尾，否则会请求到
    .../responses/chat/completions 这类错误路径。
    """
    url = url.rstrip("/")
    for suffix in ("/chat/completions", "/responses"):
        if url.endswith(suffix):
            url = url[: -len(suffix)]
            break
    # 注意：不要继续剥 /v1。OpenAI SDK 会在 base_url 后追加
    # /chat/completions 或 /responses，因此 /v1 必须保留。
    return url.rstrip("/") or url


def _extract_json_from_response(text: str) -> str:
    """从 AI 响应中提取 JSON 字符串。

    处理多种格式：
    1. <think>...</think> 标签（deepseek-reasoner）
    2. Markdown 代码块：```json ... ``` 或 ``` ... ```
    3. 纯 JSON 文本
    4. 嵌入在任意文本中的 JSON（使用 raw_decode）
    """
    if not text:
        return ""

    cleaned = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    cleaned = cleaned.strip()

    code_block_pattern = r'```(?:json)?\s*\n?(.*?)\n?```'
    match = re.search(code_block_pattern, cleaned, flags=re.DOTALL)
    if match:
        candidate = match.group(1).strip()
        try:
            json.loads(candidate)
            return candidate
        except json.JSONDecodeError:
            pass

    decoder = json.JSONDecoder()

    for target_char in ('[', '{'):
        for i, ch in enumerate(cleaned):
            if ch != target_char:
                continue
            try:
                obj, end = decoder.raw_decode(cleaned[i:])
                return cleaned[i:i + end]
            except json.JSONDecodeError:
                continue

    return cleaned


class OpenAIProvider(AIProvider):
    """OpenAI/兼容 API 的 AI 提供者实现。

    支持流式和非流式生成，自动重试（指数退避），
    提供总结、元数据提取、主题聚类和卡片生成等知识处理功能。

    class-level _api_semaphore 作为全局限流器，所有实例共享。
    """

    _api_semaphore: Optional[asyncio.Semaphore] = None
    _api_concurrency: int = 10  # 默认并发数

    def __init__(self, config: AIConfig, http_client: Optional[AsyncOpenAI] = None, retry_limit: int = 3, timeout: float = 180.0):
        self.config = config
        self._http_client = http_client
        self.retry_limit = retry_limit    # 最大重试次数
        self.timeout = timeout            # 请求超时（秒）：首 token 等待上限，长输入生成时 DeepSeek 排队可达 30s+，10s 会误杀
        if OpenAIProvider._api_semaphore is None:
            OpenAIProvider._api_semaphore = asyncio.Semaphore(config.api_concurrency)

    def _get_client(self) -> AsyncOpenAI:
        """获取或创建 OpenAI 客户端（延迟初始化）。"""
        if self._http_client is not None:
            return self._http_client
        return AsyncOpenAI(
            api_key=self.config.api_key,
            base_url=self._normalize_url(self.config.api_url),
            timeout=self.timeout,
            max_retries=0,
        )

    @staticmethod
    def _normalize_url(url: str) -> str:
        """规范化 API URL（兼容历史调用，委托给公共函数）。"""
        return normalize_api_base_url(url)

    async def test_connection(self) -> bool:
        """测试 AI API 连接是否可用（发送一条最短消息）。"""
        try:
            client = self._get_client()
            response = await client.chat.completions.create(
                model=self.config.model,
                messages=[{"role": "user", "content": "Hello"}],
                max_tokens=1,
                stream=False,
            )
            return response.choices is not None
        except Exception:
            return False

    async def generate(self, prompt: str, **kwargs) -> str:
        """非流式生成：完整获取 AI 响应文本。"""
        parts = []
        async for chunk in self.generate_stream(prompt, **kwargs):
            parts.append(chunk)
        return "".join(parts)

    async def generate_stream(self, prompt: str, **kwargs) -> AsyncIterator[str]:
        """流式生成 AI 响应，失败时自动重试（指数退避）。

        由 class-level Semaphore 限流，所有 Pipeline 实例共享同一槽位池。
        失败重试时不释放槽位，避免加重 API 拥塞。
        """
        sem = OpenAIProvider._api_semaphore
        if sem is None:
            OpenAIProvider._api_semaphore = asyncio.Semaphore(OpenAIProvider._api_concurrency)
            sem = OpenAIProvider._api_semaphore
        async with sem:
            messages = [{"role": "user", "content": prompt}]
            retries = 0
            while True:
                try:
                    logger.info(f"[AI] Calling API: {self.config.api_url}, model={self.config.model}")
                    async for chunk in self._create_stream(messages):
                        yield chunk
                    logger.info("[AI] Streaming completed successfully")
                    return
                except Exception as e:
                    logger.error(f"[AI] Request failed: type={type(e).__name__}, message={str(e)}")
                    if retries < self.retry_limit:
                        wait = 5 * (2 ** retries)
                        retries += 1
                        logger.info(f"[AI] Retrying in {wait}s (attempt {retries}/{self.retry_limit})")
                        await asyncio.sleep(wait)
                        continue
                    else:
                        logger.error(f"[AI] Max retries exceeded, raising error")
                        raise

    async def _create_stream(self, messages: list[dict]) -> AsyncIterator[str]:
        """创建 OpenAI 流式请求并逐 chunk 产出内容。"""
        client = self._get_client()
        logger.info(f"[AI] Starting stream request")
        stream = await client.chat.completions.create(
            model=self.config.model,
            messages=messages,
            stream=True,
        )
        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

    async def summarize(self, text: str) -> str:
        """总结内容为精简知识文本（仅含摘要，不含元数据）。"""
        if not text or len(text.strip()) < 100:
            return ""
        prompt = f"""请将以下内容整理成结构化的知识摘要。

核心原则：你是知识综合器，阅读原文后用自己的语言组织，保留关键信息但不要逐句照抄。

要求：
1. 保留所有关键事实、具体数据、时间节点、地点、人物
2. 保留重要细节、技术参数、对比信息、背景脉络
3. 保留原文中的具体案例和例证
4. 保留原文中特有的概念定义和论证逻辑，但用自己的语言重新表述
5. 使用清晰的段落结构，必要时用小标题组织
6. 输出必须是中文
7. 数学公式使用 $...$（行内公式）或 $$...$$（独立公式）格式

内容：
{text[:15000]}"""
        return await self.generate(prompt)

    async def summarize_with_metadata(self, text: str) -> tuple[str, dict]:
        """总结内容并提取元数据（返回 (summary, metadata) 元组）。

        summary 采用「信息保全式」长摘要：保留原文全部关键事实、数据、定义、
        案例与时间线，用小节组织，而不是压缩成简要概述（避免信息丢失）。
        """
        if not text or len(text.strip()) < 100:
            return "", {}
        # T16：抓取正文进入提示词前做不可信内容隔离（legacy 默认原样返回）
        untrusted_text = _wrap_untrusted(text, label="web-page")
        prompt = f"""请将以下内容整理成完整的信息保全式记录，并提取元数据，返回JSON对象。

核心原则：你的任务是**完整记录**原文信息，而不是写简要摘要。目标是让只读这份记录的人
获得与读原文几乎等量的信息。禁止为求简短而删减内容。

内容格式（summary 字段）：
- 按原文的章节/主题自然划分小节，用 ### 小节标题组织（如 ### 工作原理、### 主要类型、### 应用场景）
- 每个小节内完整保留：核心定义、关键事实、具体数据、公式、人名机构名、时间节点、案例例证、对比信息、背景脉络
- 原文中的具体例子和细节必须保留，不要概括成抽象表述
- 适当给重点词和核心概念加粗（用 **文字** 标记）
- 信息量大时允许输出长文本（通常 1500-3000 字），以完整为准，不要设长度上限

要求：
1. summary: 按上述格式完整记录原文信息，不要逐句照抄但要尽量保全细节
2. metadata: 提取作者、出版社、发行日期、类型、平台、主要角色等关键信息，键名用中文
3. 如果没有可提取的元数据，metadata返回空对象 {{}}
4. 所有数学公式必须使用 $...$（行内公式）或 $$...$$（独立公式）格式
5. 如果原文内容本身较短，如实记录全部内容即可，不要注水

内容：
{untrusted_text}

JSON格式：
{{"summary": "### 小节标题\\n完整内容...", "metadata": {{"键": "值"}}}}"""
        result = await self.generate(prompt)
        try:
            data = json.loads(_extract_json_from_response(result))
            if not isinstance(data, dict):
                # AI 偶发返回 JSON 数组（如 [{...}]），取首元素防御
                data = data[0] if isinstance(data, list) and data and isinstance(data[0], dict) else {}
            summary = data.get("summary", "")
            metadata = data.get("metadata", {})
            if isinstance(metadata, dict):
                flattened = {}
                for k, v in metadata.items():
                    if isinstance(v, (str, int, float)):
                        flattened[k] = v
                    elif isinstance(v, list):
                        flattened[k] = [str(item) for item in v]
                    elif isinstance(v, dict):
                        flattened[k] = json.dumps(v, ensure_ascii=False)
                    else:
                        flattened[k] = str(v)
                return summary, flattened
            return summary, {}
        except json.JSONDecodeError:
            return result, {}

    async def extract_metadata(self, text: str) -> dict:
        """仅提取元数据（作者、日期、类型等），不含摘要。"""
        prompt = f"""从以下内容中提取关键元数据，返回JSON对象。

提取规则：
1. 键名使用中文
2. 提取：作者、出版社、发行日期、类型、平台、主要角色等关键信息
3. 值必须是字符串、数字或字符串数组
4. 如果没有可提取的元数据，返回空对象 {{}}

内容：
{text[:8000]}

JSON："""
        result = await self.generate(prompt)
        try:
            raw = json.loads(_extract_json_from_response(result))
            if not isinstance(raw, dict):
                raw = raw[0] if isinstance(raw, list) and raw and isinstance(raw[0], dict) else {}
            flattened = {}
            for k, v in raw.items():
                if isinstance(v, (str, int, float)):
                    flattened[k] = v
                elif isinstance(v, list):
                    flattened[k] = [str(item) for item in v]
                elif isinstance(v, dict):
                    flattened[k] = json.dumps(v, ensure_ascii=False)
                else:
                    flattened[k] = str(v)
            return flattened
        except json.JSONDecodeError:
            return {}

    def _build_extract_prompt(self, text: str, max_topics: int, search_level: str, exclude_title: str | None = None, source_title: str | None = None) -> str:
        """构建主题提取的 prompt，根据搜索层级选择不同指令。

        source_title: 源卡标题——领域锚定职责所在。方案 B 起搜索词不再拼接
        源卡标题（query == topic），歧义消解前移到提取阶段：prompt 声明源卡领域，
        要求歧义短词必须自带领域限定（如「猎人（杀戮尖塔2）」），输出即锚定。
        """
        base_instruction = """分析以下内容，识别值得单独创建知识卡片的相关主题。

规则：
- 主题应该是独立的概念、实体或对象（不只是关键词）
- 根据内容丰富程度自行决定提取多少个主题，内容丰富可多提取，内容简单则少提取
- 只返回JSON数组格式的主题名称（字符串），不要解释
- 主题名称必须是中文
- 如果没有值得提取的相关主题，返回空数组 []
- **领域约束**：主题必须属于当前内容的领域，是领域内的具体子概念。禁止提取跨领域的通用多义词或泛化裸词——同一词汇在不同领域含义可能完全不同（如"稳定性"在控制理论/算法/医疗中含义各异，"卷积"在信号处理与深度学习中不同，"注意力"在心理学与机器学习中不同）。应提取带领域语境的具体词（如"系统稳定性"而非"稳定性"）；若多义词无法完全避免，必须在主题词中加领域限定（如"注意力（机器学习）"）"""

        exclude_rule = ""
        if exclude_title:
            exclude_rule = f"""\n- **禁止**提取与「{exclude_title}」相同、近义或属于它上位概括的主题——它是当前卡片本身，不是子主题（如卡片是"机器学习"，不要提取"机器学习""机器学习概述""什么是机器学习"）；与源卡标题仅差空格、标点或大小写的变体（如「Transformer架构」与「Transformer 架构」）也视为相同，禁止提取"""

        source_rule = ""
        if source_title:
            source_rule = f"""\n- **领域锚定**：当前卡片属于领域「{source_title}」。所有主题必须与该领域一致；歧义短词/跨领域多义词**必须**自带领域限定（如「猎人（杀戮尖塔2）」、「梯度（深度学习）」），禁止输出无领域限定的裸短词（如单独的「猎人」、「梯度」）——裸短词联网搜索会返回同名异义内容，主题自带限定后搜索词即锚定，无需任何拼接"""

        level_specific = {
            "default": """- 主题应该足够具体（例如"法拉利"而不是"汽车品牌"）
- 主题应该与当前内容的主要主题不同""",

            "downstream": """- 优先提取内容中出现的具体专业术语、特定事件、核心技术点
- 对于STEM学科内容（科学、技术、工程、数学），优先提取概念和定义，其次才是人名
- 主题应该比当前内容更具体、更细化、更深入
- 例如：如果内容是"机器学习概述"，提取"梯度下降"、"反向传播"、"过拟合"等具体概念""",

            "peer": """- 提取与当前内容处于同一抽象层次或相似水平的主题
- 主题应该与当前内容具有可比性，属于同一类别、领域或范畴
- 例如：如果内容是"保时捷"，提取"法拉利"、"兰博基尼"、"迈凯伦"等同类品牌
- 例如：如果内容是"Python"，提取"Java"、"JavaScript"、"Go"等同类编程语言""",

            "upstream": """- 提取比当前内容更高层次的概念、类别或定义
- 主题应该是当前内容的父类、上位概念或所属更大领域
- 例如：如果内容是"保时捷"，提取"汽车"、"机动车"、"德国工业"等
- 例如：如果内容是"机器学习"，提取"人工智能"、"计算机科学"、"数据科学"等""",
        }

        instruction = level_specific.get(search_level, level_specific["default"])

        return f"""{base_instruction}
{instruction}
{exclude_rule}
{source_rule}
内容：
{text[:12000]}

JSON数组："""

    async def extract_related_topics(self, text: str, max_topics: int = 10, search_level: str = "default", exclude_title: str | None = None, source_title: str | None = None) -> list[str]:
        """从内容中提取相关的延申搜索主题。

        Args:
            text: 源内容
            max_topics: 最大主题数
            search_level: 搜索层级（default/downstream/peer/upstream）
            exclude_title: 源卡片标题，禁止提取与其相同/近义的主题（防重复卡）
            source_title: 源卡标题，声明领域锚定——歧义短词必须自带领域限定
                （方案 B：搜索词不再拼接源卡标题，输出主题即锚定）

        Returns:
            主题名称列表
        """
        prompt = self._build_extract_prompt(text, max_topics, search_level, exclude_title, source_title)
        result = await self.generate(prompt)
        try:
            topics = json.loads(_extract_json_from_response(result))
            if isinstance(topics, list):
                topics = [str(t) for t in topics[:max_topics] if t]
                # 代码层兜底：LLM 可能漏掉与源卡标题仅差空格/标点/大小写的变体
                # （实测 expand 提取出「Transformer架构」与根卡「Transformer 架构」重复）
                if exclude_title:
                    norm_ex = _normalize_title(exclude_title)
                    topics = [t for t in topics if _normalize_title(t) != norm_ex]
                return topics
            return []
        except json.JSONDecodeError:
            return []

    async def translate_to_chinese(self, text: str) -> str:
        """将文本翻译为中文（已是中文则原文返回）。"""
        if not text:
            return text
        prompt = f"""将以下文本翻译成中文。如果已经是中文则直接返回原文，不要翻译成其他语言。只输出翻译结果，不要解释：

{text}"""
        return await self.generate(prompt)

    async def cluster_summaries(self, summaries: List[dict]) -> List[TopicCluster]:
        """将多个来源的摘要按主题聚类，避免重复卡片。

        Args:
            summaries: 摘要列表（每项含 title/summary）

        Returns:
            聚类后的 TopicCluster 列表
        """
        if not summaries:
            return []

        if len(summaries) == 1:
            return [TopicCluster(
                topic_name=summaries[0].get("title", "未命名主题"),
                source_indices=[0],
                description=summaries[0].get("summary", "")[:200]
            )]

        sources_info = []
        for i, s in enumerate(summaries):
            title = s.get("title", f"来源{i+1}")
            summary = s.get("summary", "")[:500]
            sources_info.append(f"[{i}] 标题: {title}\n摘要: {summary}")

        sources_text = "\n\n".join(sources_info)

        prompt = f"""分析以下多个信息来源，将它们按主题聚类。不同的事件、概念或实体应该分开。

规则：
- 如果来源讨论的是完全不同的主题（如不同历史事件、不同人物、不同概念），必须分开
- 只有真正相关的来源才应该合并（如同一事件的不同角度）
- 每个聚类需要一个中文主题名称
- 返回JSON数组，每个元素包含: topic_name(主题名称), source_indices(来源索引数组), description(简短描述)

示例输出格式：
```json
[
  {{"topic_name": "战锤40000", "source_indices": [0, 2], "description": "桌面战棋游戏"}},
  {{"topic_name": "星际战士2", "source_indices": [1, 3], "description": "电子游戏"}}
]
```

来源列表：
{sources_text}

JSON数组："""

        result = await self.generate(prompt)
        logger.debug(f"[AI] Cluster raw response: {result[:500]}...")
        try:
            cleaned_result = _extract_json_from_response(result)
            logger.debug(f"[AI] Cluster cleaned JSON: {cleaned_result[:500]}...")
            clusters_data = json.loads(cleaned_result)
            clusters = []
            for c in clusters_data:
                if isinstance(c, dict) and "topic_name" in c and "source_indices" in c:
                    clusters.append(TopicCluster(
                        topic_name=str(c["topic_name"]),
                        source_indices=[int(i) for i in c["source_indices"] if 0 <= int(i) < len(summaries)],
                        description=str(c.get("description", ""))
                    ))

            if not clusters:
                logger.warning("[AI] No valid clusters parsed, attempting title-based grouping")
                return self._fallback_cluster_by_title(summaries)

            covered = set()
            for cluster in clusters:
                covered.update(cluster.source_indices)

            for i in range(len(summaries)):
                if i not in covered:
                    clusters.append(TopicCluster(
                        topic_name=summaries[i].get("title", f"来源{i+1}"),
                        source_indices=[i]
                    ))

            return clusters
        except (json.JSONDecodeError, ValueError, KeyError) as e:
            logger.warning(f"[AI] Failed to parse cluster result: {e}, attempting title-based grouping")
            return self._fallback_cluster_by_title(summaries)

    def _fallback_cluster_by_title(self, summaries: List[dict]) -> List[TopicCluster]:
        """回退聚类：基于标题相似度分组，避免重复卡片。"""
        if not summaries:
            return []

        title_groups: dict[str, list[int]] = {}

        for i, s in enumerate(summaries):
            title = s.get("title", f"来源{i+1}")
            normalized = self._normalize_title(title)

            matched_key = None
            for existing_key in title_groups:
                if self._titles_similar(normalized, existing_key):
                    matched_key = existing_key
                    break

            if matched_key:
                title_groups[matched_key].append(i)
            else:
                title_groups[normalized] = [i]

        clusters = []
        for normalized_title, indices in title_groups.items():
            original_title = summaries[indices[0]].get("title", normalized_title)
            clusters.append(TopicCluster(
                topic_name=original_title,
                source_indices=indices,
                description=summaries[indices[0]].get("summary", "")[:200]
            ))

        return clusters

    def _normalize_title(self, title: str) -> str:
        """标准化标题用于比对（去除空格、括号、大小写）。"""
        import unicodedata
        normalized = unicodedata.normalize('NFKC', title.lower())
        normalized = re.sub(r'[\s\-_：:]+', '', normalized)
        normalized = re.sub(r'[（\(].*?[）\)]', '', normalized)
        return normalized.strip()

    def _titles_similar(self, title1: str, title2: str) -> bool:
        """判断两个标准化标题是否足够相似（包含关系或 SequenceMatcher ≥ 0.7）。"""
        if title1 == title2:
            return True
        if title1 in title2 or title2 in title1:
            return True
        from difflib import SequenceMatcher
        ratio = SequenceMatcher(None, title1, title2).ratio()
        return ratio >= 0.7

    async def generate_cards_from_sources(self, sources: List[dict], keyword: str = "") -> List[GeneratedCard]:
        """从多个来源生成知识卡片（非流式版本）。"""
        if not sources:
            return []

        sources_text = ""
        for i, s in enumerate(sources):
            title = s.get("title", f"来源{i+1}")
            content = s.get("content", "")[:30000]
            sources_text += f"\n---\n[来源 {i}]\n标题: {title}\n内容:\n{content}\n"

        # T16：抓取原文进入提示词前整体包裹（单一边界，避免逐来源重复声明）
        sources_text = _wrap_untrusted(sources_text, label="web-sources")
        keyword_hint = f"\n用户搜索关键词：{keyword}" if keyword else ""

        prompt = f"""你是一个知识综合器。请阅读以下多个来源的信息，将它们有机融合为一张统一的知识卡片。{keyword_hint}

核心原则：
- 你的任务是**综合**多个来源的信息，形成一篇完整、连贯的知识文章
- 不要区分或提及来源，将所有信息融合为统一的叙述，读起来应该像一篇百科条目
- 保留原文中的关键数据、公式、人名、机构名、时间节点，但用自己的语言重新组织
- 将不同来源中讨论同一概念的内容融合在一起，消除冗余和重复

内容格式：以自然连贯的段落直接、完整地总结该概念本身——先给定义，再按概念自身的逻辑展开原理/机制、关键要点、典型应用、注意事项等。用 ### 子标题按需分段。**不要套用固定模板**（禁止"概述/核心内容/关键细节"三段式），内容组织应贴合主题本身，避免为了结构而压缩信息。

具体规则：
1. 标题必须是独立的领域概念（名词性短语，如"提示词工程"、"反向传播算法"）。禁止使用完整句子、动词短语或疑问句（如"什么是提示词工程"、"如何学习深度学习"）；若搜索关键词是句子，将其压缩为最短的概念名。**标题必须与用户搜索关键词一致或为其直接子主题，禁止泛化为上位概念**（如搜索"串级PID"不得生成"PID控制器"——父概念已存在或无关紧要，标题漂移会导致重复卡）。**歧义词必须加领域限定（硬性要求）**：若标题词是多义词或存在同名不同义的概念（如"元素"在游戏/网页开发/化学中含义不同，"梯度"在数学/机器学习/图像处理中不同，"卷积"在信号处理/深度学习中不同，"注意力"在心理学/机器学习中不同，"状态"在计算机/物理/医学中不同），**必须**在标题末尾用括号加领域限定词消歧（如"元素互动机制（神界原罪2）"、"梯度（深度学习）"、"卷积（信号处理）"）。**禁止输出无领域限定的裸多义词标题**——裸词会导致后续扩展/搜索发生语义偏移，生成无关领域的卡片。若无法确定是否多义，宁可加上领域限定也不要裸化；既不得泛化为上位概念，也不得与其他卡片标题混淆
2. 内容直接围绕概念展开，信息密度优先——把所有关键知识点、数据、公式完整保留，不要为了凑结构而省略或缩写
3. 绝对不要在正文中出现"来源0"、"来源1"、"根据某来源"等字样——所有信息融合为统一的叙述
4. 英文专有名词保留原文并括号标注中文
5. 适当给重点词和核心概念加粗（用 **文字** 标记），增强可读性
6. 所有数学公式必须使用 $...$（行内公式）或 $$...$$（独立公式）格式

7. 评估你对生成内容的把握程度，输出 confidence（0.0~1.0）：
   - 1.0：多个可靠来源高度一致、事实明确
   - 0.8：来源充分但有少量推断
   - 0.6：来源可靠但信息不完整
   - 0.4：来源单一或涉及推测
   - 0.2：来源不足、大量推测成分

输出格式（纯JSON）：
{{
  "title": "关键词名称",
  "content": "## 定义\\n自然段落…\\n\\n### 原理\\n…\\n\\n### 应用\\n…",
  "source_indices": [0, 1, 2],
  "tags": ["标签1", "标签2"],
  "confidence": 0.8
}}

信息来源：
{sources_text}

JSON："""

        result = await self.generate(prompt)
        logger.debug(f"[AI] Generate cards raw response: {result[:800]}...")

        try:
            cleaned = _extract_json_from_response(result)
            logger.debug(f"[AI] Generate cards cleaned JSON: {cleaned[:800]}...")
            cards_data = json.loads(cleaned)

            if isinstance(cards_data, list):
                cards_data = cards_data[0] if cards_data else {}

            if not cards_data or not isinstance(cards_data, dict):
                logger.info("[AI] AI determined card already exists, skipping")
                return []

            if "title" in cards_data and "content" in cards_data:
                title = str(cards_data["title"])
                content = str(cards_data["content"])

                source_indices = cards_data.get("source_indices", [])
                source_indices = [int(i) for i in source_indices if 0 <= int(i) < len(sources)]

                raw_conf = cards_data.get("confidence")
                confidence = 0.5
                if raw_conf is not None:
                    try:
                        confidence = max(0.0, min(1.0, float(raw_conf)))
                    except (ValueError, TypeError):
                        pass

                card = GeneratedCard(
                    title=title,
                    content=content,
                    source_indices=source_indices if source_indices else list(range(len(sources))),
                    tags=[str(t) for t in cards_data.get("tags", []) if t],
                    confidence=confidence,
                )
                return [card]

            logger.warning("[AI] No valid card generated, creating fallback")
            raw_conf = cards_data.get("confidence") if isinstance(cards_data, dict) else None
            fallback_conf = 0.5
            if raw_conf is not None:
                try:
                    fallback_conf = max(0.0, min(1.0, float(raw_conf)))
                except (ValueError, TypeError):
                    pass
            return [GeneratedCard(
                title=keyword or sources[0].get("title", "综合信息"),
                content=sources[0].get("content", "")[:2000],
                source_indices=[0],
                tags=[],
                confidence=fallback_conf,
            )]

        except (json.JSONDecodeError, ValueError, KeyError) as e:
            logger.warning(f"[AI] Failed to parse generated card: {e}")
            return [GeneratedCard(
                title=keyword or sources[0].get("title", "综合信息"),
                content=result[:2000] if result else "生成失败",
                source_indices=list(range(len(sources))),
                tags=[],
            )]

    async def generate_cards_from_sources_stream(
        self, sources: List[dict], keyword: str = ""
    ) -> AsyncIterator[str]:
        """从多个来源生成知识卡片（流式版本），产出 AI 响应文本 chunks。"""
        if not sources:
            return

        sources_text = ""
        for i, s in enumerate(sources):
            title = s.get("title", f"来源{i+1}")
            content = s.get("content", "")[:30000]
            sources_text += f"\n---\n[来源 {i}]\n标题: {title}\n内容:\n{content}\n"

        # T16：抓取原文进入提示词前整体包裹（单一边界，避免逐来源重复声明）
        sources_text = _wrap_untrusted(sources_text, label="web-sources")
        keyword_hint = f"\n用户搜索关键词：{keyword}" if keyword else ""

        prompt = f"""你是一个知识综合器。请阅读以下多个来源的信息，先判断它们是否属于同一主题，再生成知识卡片。{keyword_hint}

核心原则：
- 若所有来源都在讲同一个主题 → 生成 1 张卡片，融合全部信息
- 若来源横跨多个不同的子主题（例如"OpenCode 介绍"、"MCP 协议"、"AI 工具对比"）→ 为每个子主题各生成 1 张独立卡片（最多 3 张），每张卡只讲一个主题
- 每张卡片综合该主题下所有相关来源的信息，形成完整、连贯的知识文章
- 不要区分或提及来源，将所有信息融合为统一的叙述，读起来应该像一篇百科条目
- 保留原文中的关键数据、公式、人名、机构名、时间节点，但用自己的语言重新组织
- 将不同来源中讨论同一概念的内容融合在一起，消除冗余和重复

内容格式：以自然连贯的段落直接、完整地总结该概念本身——先给定义，再按概念自身的逻辑展开原理/机制、关键要点、典型应用、注意事项等。用 ### 子标题按需分段。**不要套用固定模板**（禁止"概述/核心内容/关键细节"三段式），内容组织应贴合主题本身，避免为了结构而压缩信息。

具体规则：
1. 标题必须是独立的领域概念（名词性短语，如"提示词工程"、"反向传播算法"）。禁止使用完整句子、动词短语或疑问句（如"什么是提示词工程"、"如何学习深度学习"）；若搜索关键词是句子，将其压缩为最短的概念名。**标题必须与用户搜索关键词一致或为其直接子主题，禁止泛化为上位概念**（如搜索"串级PID"不得生成"PID控制器"——父概念已存在或无关紧要，标题漂移会导致重复卡）。**歧义词必须加领域限定（硬性要求）**：若标题词是多义词或存在同名不同义的概念（如"元素"在游戏/网页开发/化学中含义不同，"梯度"在数学/机器学习/图像处理中不同，"卷积"在信号处理/深度学习中不同，"注意力"在心理学/机器学习中不同，"状态"在计算机/物理/医学中不同），**必须**在标题末尾用括号加领域限定词消歧（如"元素互动机制（神界原罪2）"、"梯度（深度学习）"、"卷积（信号处理）"）。**禁止输出无领域限定的裸多义词标题**——裸词会导致后续扩展/搜索发生语义偏移，生成无关领域的卡片。若无法确定是否多义，宁可加上领域限定也不要裸化；既不得泛化为上位概念，也不得与其他卡片标题混淆
2. 内容直接围绕概念展开，信息密度优先——把所有关键知识点、数据、公式完整保留，不要为了凑结构而省略或缩写
3. 绝对不要在正文中出现"来源0"、"来源1"、"根据某来源"等字样——所有信息融合为统一的叙述
4. 英文专有名词保留原文并括号标注中文
5. 适当给重点词和核心概念加粗（用 **文字** 标记），增强可读性
6. 所有数学公式必须使用 $...$（行内公式）或 $$...$$（独立公式）格式
7. 每张卡片评估你对生成内容的把握程度，输出 confidence（0.0~1.0）：
   - 1.0：多个可靠来源高度一致、事实明确
   - 0.8：来源充分但有少量推断
   - 0.6：来源可靠但信息不完整
   - 0.4：来源单一或涉及推测
   - 0.2：来源不足、大量推测成分
8. 每张卡片必须列出它实际使用的来源序号（source_indices），未使用的来源不要列入

输出格式（纯JSON数组，1-3 张卡片）：
[
  {{
    "title": "卡片主题名称",
    "content": "## 定义\\n自然段落…\\n\\n### 原理\\n…\\n\\n### 应用\\n…",
    "source_indices": [0, 1],
    "tags": ["标签1", "标签2"],
    "confidence": 0.8
  }}
]

信息来源：
{sources_text}

JSON："""

        async for chunk in self.generate_stream(prompt):
            yield chunk

    async def analyze_document_stream(
        self, text: str, filename: str = "", existing_cards: List[str] = None
    ) -> AsyncIterator[str]:
        """分析上传的文档，流式返回主卡片 + 章节卡片 + 知识点卡片三层 JSON。"""
        existing_cards = existing_cards or []

        # Truncate to fit context; keep a reasonable portion for quality analysis
        max_chars = 50000
        if len(text) > max_chars:
            truncated_note = f"\n\n[文档过长，已截取前 {max_chars} 字符进行分析]"
            text = text[:max_chars] + truncated_note
        # T16：上传文档正文进入提示词前做不可信内容隔离（filename 同样可能不可信）
        safe_filename = _safe_meta(filename)
        untrusted_text = _wrap_untrusted(text, label=f"document:{safe_filename}")

        existing_hint = ""
        if existing_cards:
            existing_hint = f"\n\n已存在的卡片标题（如果与这些重复，跳过该卡片）：\n{chr(10).join(existing_cards[:50])}"

        prompt = f"""你是一个知识综合器。请分析以下文档，提取结构化知识并生成三层卡片体系。

核心原则：
- 你的任务是**综合**文档信息，用自己的语言重新组织，但保留文档中的关键数据、公式、人名、机构名
- 文档中特有的概念定义、分类方式、论证逻辑必须保留，但不要逐句照抄
- 不要编造文档中没有的内容

文档来源：{safe_filename}

{existing_hint}

内容格式：以自然连贯的段落直接总结内容本身——先给定义/主题，再按内容自身的逻辑展开。用 ### 子标题按需分段。**不要套用固定模板**（禁止"概述/核心内容/关键细节"三段式），信息密度优先。

具体规则：
1. 生成一张主卡片（main）：提取文档的核心主题和关键结论
2. 将文档按自身章节/主题划分为 3-8 个部分（sections），每个部分生成一张章节卡片
3. 每个章节内提取 1-3 个核心知识点（key_points），每个知识点生成一张重点卡片
4. 主卡片、章节卡片、重点卡片均按内容本身组织，禁止三段式模板
5. 去重：同一信息只保留一次
6. 不要编造文档中没有的内容
7. 英文专有名词保留原文并括号标注中文
8. 所有数学公式必须使用 $...$（行内公式）或 $$...$$（独立公式）格式
9. **歧义词必须加领域限定（硬性要求）**：若卡片标题是多义词或存在同名不同义的概念（如"元素"在游戏/网页开发/化学中含义不同，"梯度"在数学/机器学习/图像处理中不同，"卷积"在信号处理/深度学习中不同），**必须**在标题末尾用括号加领域限定词消歧（如"元素互动机制（神界原罪2）"、"梯度（深度学习）"）。**禁止输出无领域限定的裸多义词标题**——裸词会导致后续扩展/搜索发生语义偏移，生成无关领域的卡片。若无法确定是否多义，宁可加上领域限定也不要裸化

输出格式（严格 JSON，不要输出任何其他内容）：
{{
  "main": {{
    "title": "文档标题",
    "content": "## 定义\\n自然段落…\\n\\n### 要点\\n…",
    "tags": ["标签1", "标签2"]
  }},
  "sections": [
    {{
      "title": "章节标题",
      "content": "自然段落…\\n\\n### 子主题\\n…",
      "tags": ["标签1"],
      "key_points": [
        {{
          "title": "知识点名称",
          "content": "自然段落…",
          "tags": ["标签"]
        }}
      ]
    }}
  ]
}}

文档内容：
{untrusted_text}

JSON："""

        async for chunk in self.generate_stream(prompt):
            yield chunk

