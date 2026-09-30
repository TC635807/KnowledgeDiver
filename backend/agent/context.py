"""Agent 上下文管理 — per (username, session_id) 对话历史，JSON文件持久化。

持久化策略（对齐 DeepSeek Harness 的"热路径同步、持久化异步"原则）：
- add_* 方法只更新内存并标记脏（O(1)，绝不阻塞事件循环）；
- 0.5s 去抖后台任务把整段历史落盘（asyncio.to_thread，写盘不阻塞循环）；
- loop 在每次模型请求前调用 session.flush() 做持久化检查点（turn 边界）；
- 文件写入保持原子性（tmp + replace），进程退出前未 flush 的窗口 ≤0.5s。
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# 异步落盘去抖间隔：同一轮内连续 append 合并为一次写盘。
SAVE_DEBOUNCE_S = 0.5

SYSTEM_PROMPT = """你是 KnowledgeDiver 知识助手。你有一个个人知识库（本会话中已收集的卡片，通过工具访问）。

# 理解质量分数
工具返回中的 quality 字段包含卡牌质量评估：
- gap_score（0~1）：综合薄弱分，越高表示卡牌越需要补充。>0.5 需关注，>0.7 严重不足。
- quality_score（0~1）：综合质量分，越高越好。
- structure_score（0~1）：结构完整度（内容充实度、来源丰富度、链接紧密度）。<0.4 说明内容单薄或缺少来源。
- graph_score（0~1）：知识图谱密度分（degree / (depth+1) / 2）。<0.2 说明是深层孤立叶子，>0.7 说明是知识枢纽。
- semantic_score（0~1）：语义融入度（与最相似邻居卡片的嵌入距离，越近越高）。缺少向量或没有可比邻居时为 0.5（无法评估）。
- confidence_score（0~1）：LLM 生成卡片时的自评置信度。
- dimensions：各子维度明细（content/sources/links）。
- tree_dimensions：树结构子维度（depth/degree/density）。

注意：各维度默认值 0.5 表示"无法评估"而非"质量中等"，请结合具体内容判断。
- gap_percentile（0~1）：卡片在本会话全部卡片中的相对薄弱位置，1.0 表示本会话最弱、0.0 表示最好；用于挑选优先改进对象，不等同于绝对质量。

search_similar_cards 返回的 score 是语义相似度（0~1，越高越匹配），与质量分无关。

# 工作流（严格遵循）
1. 总是先用 search_similar_cards 检查本地知识库中是否有相关信息
2. search_similar_cards 的分数只是向量召回，不要只看分数下结论；即使 score 不高（≥0.35），只要主题相关，就用 get_card_info 读取卡片内容确认
3. **在决定联网搜索或刷新卡片之前**，先用 assess_exploration_need 评估必要性：
   - 传入 card_id：评估某张卡片是否需要 refresh 或 expand
   - 传入 keyword：评估本地是否已有足够内容覆盖该关键词
   - 根据返回的 decision（local_sufficient / needs_refresh / needs_expand / optional / no_local）决定下一步
   - 如果 decision 为 local_sufficient 或 optional，优先用 get_card_info 读本地已有卡片，宁可多读本地内容，也不要轻易联网搜索
   - refresh_card 是最后手段，触发条件非常严格，一般情况不要使用；需要补内容时优先 expand_from_card
4. 需要了解知识库整体质量时，用 assess_card_quality 获取薄弱卡排行；评估知识库覆盖度与簇健康度时，可主动调用 assess_knowledge_base（纯本地计算）读取簇级诊断（avg_gap/compactness/薄弱簇识别），发现薄弱簇后可再用 plan_knowledge_gaps 盘点缺失子主题，**并优先用 expand_from_card 对薄弱簇代表卡补强一轮后复检——补强有界：连续两轮无改善、或本轮未生成新卡、或已无薄弱簇时停止，避免无限循环**。需要判断挂载位置、层级缺失或选择扩展源卡时，可调用 get_card_tree 查看完整树形结构
5. 基于卡片原文内容回答，引用卡片标题
6. 只有当本地确实没有任何相关信息时，才使用 search_by_keyword 联网搜索。如果搜索主题是某张已知卡片的子方向或同领域主题（例如已知"自动化就业"卡片，搜索"嵌入式就业"），必须先用 search_similar_cards 找到该卡，把其 ID 传入 source_card_id，新卡片会自动挂载到该卡下（自动建立无向链接 + 设为父卡）；若搜索主题是全新独立领域，可不传 source_card_id，但生成新卡后若发现与已有卡相关，应立即用 link_card 挂载（见规则 8）
7. 联网搜索（search_by_keyword / expand_from_card / refresh_card）会自动生成并保存新卡片到知识库
8. 发现两张卡片内容高度相关但尚未链接时，用 link_card 建立链接。**链接是无向的（对称）**：card_id_a / card_id_b 顺序无关，只建立引用关系；要把某张卡挂到另一张卡下面（树形挂载），**必须传 parent 参数**指明父卡：parent='a' 表示 card_id_a 是父卡（link_card(card_id_a='父卡标题', card_id_b='子卡标题', parent='a')），parent='b' 反之。省略 parent 不会改变树结构
9. 知识库必须保持树形结构：禁止留下平铺的孤立根卡；新卡与已有卡相关时，必须用 link_card + parent 挂载到最相关的节点下

# 知识库构建规范（用户要求构建/完善知识库时严格执行）
- 卡牌必须是**独立的领域概念**（名词性短语，如"提示词工程""卷积神经网络"），禁止用完整句子或动词短语作为卡牌主题
- search_by_keyword 的关键词同样必须是独立概念，不要传句子；**歧义短词/跨领域多义词必须自带领域限定**（如「Zero（合金装备）」「毒蛇（合金装备V）」，禁止搜索无领域限定的裸短词如「Zero」「毒蛇」——联网会返回区块链/其他游戏等同名异义内容，产生无关卡片）
- **禁止产生孤立卡片**：第一次搜索建立的卡是根卡；之后的每一次搜索，若主题与已有卡相关，必须传入 source_card_id（挂到根卡或最相关的已有卡），形成树状结构；全新领域可生成新根卡，但需评估是否应与已有卡建立链接
- 优先用 expand_from_card 在已有卡下扩展子主题（新卡会自动链接到源卡），而不是反复 search_by_keyword 创建并列根卡
- 每搜索一个新主题前，先 search_similar_cards 确认本地确实没有该概念
- **搜索节奏（硬性）**：每两次 search_by_keyword 之间必须至少调用一次读层工具（list_cards / search_similar_cards / get_card_info / get_card_tree / assess_knowledge_base）检查知识库现状，再决定下一步；当 search_by_keyword 返回 blocked 或 local_hit（本地已有相近卡片）时，必须停下来用 get_card_info 读取本地卡片并评估，而不是换一个词继续搜索
- **挂载反馈必须执行**：search_by_keyword 返回"新卡片暂未挂载"时，按提示评估候选父卡，如相关必须立即用 link_card 挂载（必须传 parent 指明父卡，如 link_card(card_id_a='父卡标题', card_id_b='新卡标题', parent='a')）；不要留新卡平铺成根

# 规则
- 用中文回答
- 回答必须基于卡片原文内容，不要编造或猜测
- **工具调用必须如实报告**：工具返回 success=false 时，必须向用户明确说明失败原因（如"搜索失败：缺少关键词"），禁止声称操作成功或编造卡片已创建；卡片的真实创建情况以工具返回的 titles 为准
- **禁止在回复文本中输出工具调用格式**：不要输出 `<search_by_keyword>`、`🔧 调用工具`、XML 或任何模拟工具调用的文本。需要调用工具时，必须使用真正的工具调用机制（tool_calls）；文本只允许描述**已经实际完成**的动作
- **工具调用失败时先纠正再重试**：工具返回 success=false 时，先按失败原因修正（用 list_cards 获取真实卡片 ID、核对参数名如 card_id vs cluster_id），修正后重试；不要盲目换词反复重试同一工具
- 如果本地和网络都没有相关信息，诚实告知用户
- 需要了解全貌时，用 list_cards 和 get_linked_cards 扩展上下文
- 回答时引用相关卡片标题，方便用户追溯
- 当收到 /loop 前缀消息时进入自主迭代模式，遵循 Loop 行为约定
"""


def _session_dir(username: str) -> Path:
    return Path("data/agent") / username


def _session_path(username: str, session_id: str) -> Path:
    return _session_dir(username) / f"{session_id}.json"


def _load_messages(username: str, session_id: str) -> Optional[list[dict]]:
    path = _session_path(username, session_id)
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _save_messages(username: str, session_id: str, messages: list[dict]) -> None:
    path = _session_path(username, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(messages, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


def _delete_session_file(username: str, session_id: str) -> None:
    try:
        _session_path(username, session_id).unlink(missing_ok=True)
    except Exception:
        pass


def _sanitize(messages: list[dict]) -> list[dict]:
    """修复孤悬的 tool_calls / tool 消息配对，确保 DeepSeek API 不报 400。

    处理三种畸变：
    1. 孤悬 tool（无前导 assistant(tool_calls)）→ 跳过
    2. 不完整的 tool_calls（assistant(tool_calls) 后跟不足量的 tool 结果）→ 移除该 assistant
    3. 末尾孤悬 assistant(tool_calls)（无任何 tool 结果）→ 移除
    """
    if not messages:
        return messages
    result: list[dict] = []
    pending_tool_count = 0
    tc_index: int | None = None  # 最近一次 assistant(tool_calls) 在 result 中的位置
    for msg in messages:
        role = msg.get("role", "")
        if role == "assistant" and msg.get("tool_calls"):
            # 如果之前还有未完成的 tool_calls（新的 tool_calls 打断了旧的），先移除旧的
            if pending_tool_count > 0 and tc_index is not None:
                del result[tc_index]
                tc_index = None
            pending_tool_count = len(msg["tool_calls"])
            tc_index = len(result)
            result.append(msg)
        elif role == "tool" and pending_tool_count > 0:
            pending_tool_count -= 1
            result.append(msg)
        elif role == "tool":
            # 孤悬的 tool 消息 — 跳过，因为没有前导 tool_calls
            continue
        else:
            # 非 tool 消息打断了 tool 结果序列 — assistant(tool_calls) 不完整，移除
            if pending_tool_count > 0 and tc_index is not None:
                del result[tc_index]
            pending_tool_count = 0
            tc_index = None
            result.append(msg)
    # 末尾仍有未完成的 tool_calls — 移除
    if pending_tool_count > 0 and tc_index is not None:
        del result[tc_index]
    return result


class AgentSession:
    def __init__(self, username: str, session_id: str):
        self._username = username
        self._session_id = session_id
        self.created_at = datetime.utcnow()
        self.last_active = datetime.utcnow()

        loaded = _load_messages(username, session_id)
        if loaded and len(loaded) > 0 and loaded[0].get("role") == "system":
            loaded[0]["content"] = SYSTEM_PROMPT
            self._messages: list[dict] = _sanitize(loaded)
        else:
            self._messages = [{"role": "system", "content": SYSTEM_PROMPT}]

        # 异步持久化状态：内存为准，磁盘为异步投影。
        self._dirty = False
        self._save_task: Optional[asyncio.Task] = None
        self._io_lock = asyncio.Lock()

    # ── 持久化：内存即时 + 异步落盘 ──────────────────────────────

    def _save_sync(self) -> None:
        """同步写盘（原 _save）：原子 tmp+replace，仅在 worker 线程中调用。"""
        _save_messages(self._username, self._session_id, self._messages)

    def _mark_dirty(self) -> None:
        """标记脏并调度去抖落盘。热路径只做内存操作 + 任务调度，不碰磁盘。"""
        self._dirty = True
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # 无事件循环的同步调用路径 → 直接同步保存，保持旧行为。
            self._dirty = False
            self._save_sync()
            return
        if self._save_task is None or self._save_task.done():
            self._save_task = loop.create_task(self._flush_later())

    async def _flush_later(self) -> None:
        """去抖落盘：等 SAVE_DEBOUNCE_S 合并同轮 append，再到线程中写盘。"""
        try:
            await asyncio.sleep(SAVE_DEBOUNCE_S)
            await self._persist()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("[AgentSession] 异步保存失败，将在下次 flush 重试")
            self._dirty = True
        finally:
            if self._save_task is asyncio.current_task():
                self._save_task = None

    async def _persist(self) -> None:
        """把当前历史写到磁盘（worker 线程），成功即清除脏标记。"""
        if not self._dirty:
            return
        self._dirty = False
        async with self._io_lock:
            await asyncio.to_thread(self._save_sync)

    async def flush(self) -> None:
        """持久化检查点（turn 边界）：取消去抖等待，立即落盘并等待完成。

        loop 在每次模型请求前调用，保证"模型看到的"都已持久化；
        正常退出与异常路径也依赖它做最终落盘。失败只记日志，不打断对话。
        """
        try:
            task = self._save_task
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            await self._persist()
        except Exception:
            logger.exception("[AgentSession] flush 失败")

    # ── 消息追加（内存即时，磁盘异步） ───────────────────────────

    def add_user_message(self, text: str) -> None:
        self._messages.append({"role": "user", "content": text})
        self._mark_dirty()

    def add_assistant_message(self, text: str, reasoning_content: str = "") -> None:
        if not text.strip():
            return
        msg: dict = {"role": "assistant", "content": text}
        if reasoning_content:
            msg["reasoning_content"] = reasoning_content
        self._messages.append(msg)
        self._mark_dirty()

    def add_system_message(self, text: str) -> None:
        if not text.strip():
            return
        self._messages.append({"role": "system", "content": text})
        self._mark_dirty()

    def add_tool_result(self, call_id: str, name: str, result: str) -> None:
        self._messages.append({
            "role": "tool",
            "tool_call_id": call_id,
            "content": result,
        })
        self._mark_dirty()

    def add_tool_calls(self, tool_calls: list[dict], reasoning_content: str = "") -> None:
        msg: dict = {
            "role": "assistant",
            "content": None,
            "tool_calls": tool_calls,
        }
        if reasoning_content:
            msg["reasoning_content"] = reasoning_content
        self._messages.append(msg)
        self._mark_dirty()

    def to_llm_messages(self) -> list[dict]:
        return list(self._messages)

    def prune(self, max_messages: int) -> None:
        system = [m for m in self._messages if m["role"] == "system"]
        rest = [m for m in self._messages if m["role"] != "system"]
        if len(rest) <= max_messages:
            return
        start = len(rest) - max_messages
        # 如果裁切点落在 tool 消息上，向前回溯到它的 assistant(tool_calls)，
        # 避免产生孤悬的 tool 消息（DeepSeek 要求 tool 必须有前导 tool_calls）
        while start > 0 and rest[start].get("role") == "tool":
            start -= 1
        self._messages = system + rest[start:]
        self._mark_dirty()

    def clear(self) -> None:
        # 清空是低频管理操作：取消未完成的异步保存，直接同步落盘，
        # 保证"清空"与"落盘"的先后顺序确定。
        task = self._save_task
        if task is not None and not task.done():
            task.cancel()
        self._save_task = None
        self._messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        self._dirty = False
        _delete_session_file(self._username, self._session_id)
        self._save_sync()

    @property
    def message_count(self) -> int:
        return len(self._messages)

    @property
    def user_message_count(self) -> int:
        return sum(1 for m in self._messages if m["role"] == "user")


class AgentContext:
    _instance: Optional["AgentContext"] = None
    _MAX_SESSIONS = 50

    def __init__(self):
        self._sessions: dict[str, AgentSession] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    @classmethod
    def get(cls) -> "AgentContext":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _key(self, username: str, session_id: str) -> str:
        return f"{username}:{session_id}"

    def get_or_create(self, username: str, session_id: str) -> AgentSession:
        k = self._key(username, session_id)
        if k not in self._sessions:
            if len(self._sessions) >= self._MAX_SESSIONS:
                oldest = min(self._sessions, key=lambda k: self._sessions[k].last_active)
                del self._sessions[oldest]
                self._locks.pop(oldest, None)
            self._sessions[k] = AgentSession(username, session_id)
        session = self._sessions[k]
        session.last_active = datetime.utcnow()
        return session

    def clear(self, username: str, session_id: str) -> None:
        k = self._key(username, session_id)
        session = self._sessions.pop(k, None)
        if session:
            session.clear()
        self._locks.pop(k, None)

    def lock(self, username: str, session_id: str) -> asyncio.Lock:
        k = self._key(username, session_id)
        if k not in self._locks:
            self._locks[k] = asyncio.Lock()
        return self._locks[k]
