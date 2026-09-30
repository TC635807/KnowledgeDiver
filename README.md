# KnowledgeDiver

AI 驱动的本地优先知识收集与组织系统：输入关键词或文档，系统自动搜索、抓取、生成 Wiki 风格知识卡片，组织为树形结构并建立无向链接；同时提供语义搜索、质量评估与可自主改进知识库的 Agent。

> 本仓库是从内部完整系统剥离出的**核心代码版**：开箱即可免费自托管运行，不含任何商业化模块与内置密钥。详见[「本开源版不包含什么」](#本开源版不包含什么)。

## 核心能力

### 1. 搜索 → 抓取 → 卡片流水线
- **免费多引擎搜索（默认，无需任何 API key）**：Bing / AnySearch / Exa-MCP / DuckDuckGo / SearXNG 按优先级依次尝试，任一命中即止；取链机制与 DeepSeek Harness 的 `dsh-free-search` 插件同源（见 `backend/search/free.py`）。也可按需切换到 `bocha` / `baidu` / `exa` 等第三方搜索源。相关配置项：`DEFAULT_SEARCH_PROVIDER`、`FREE_SEARCH_ENGINES`、`ANYSEARCH_API_KEY`。
- 候选池全量返回后由 URL 优先级器筛选。
- 三级抓取：crawl4ai / Playwright（主路径）→ trafilatura → light HTML/BS4（兜底）。
- 卡片由抓取**原文**直接生成（单来源截断 30,000 字符），避免“摘要的摘要”二次压缩。
- 可组合 Pipeline：Source → Fetcher/Processor → CardBuilder → CardPersister → Embedder → Explorer，各阶段通过抽象接口可替换。
- 文档上传：txt / md / pdf / docx → 结构分析 → 根卡 / 章节卡 / 细节卡三层卡片树。

### 2. 卡片与知识组织
- 每张卡片包含标题、Markdown 正文、元数据、来源 URL、标签、LLM 置信度。
- 树方向由显式 `parent_id` 承载；卡片间另有 Obsidian 风格的**无向链接**（`links` / `backlinks` 对称维护，幂等）。
- 前端支持树形 / 最新 / 标题三种排序，以及 vis-network 交互式知识图谱。
- 卡片详情可回看抓取时保存的网页原文全文。

### 3. 语义搜索
- 本地 bge-small-zh-v1.5 嵌入模型 + sqlite-vec 向量索引。
- 新卡片自动向量化；启动时自动补齐缺失向量索引。
- 前端搜索自动优先语义搜索，失败时回退标题匹配。

### 4. 质量评估与缺口分析
- 四维评分：结构完整度 + 图论信号 + 语义融入度 + LLM 自评置信度，合成 `quality_score` / `gap_score`。
- 提供全库质量报告、最薄弱卡片排行、gap 分布直方图与维度热图。
- HDBSCAN 语义聚类做簇级健康度诊断，识别 weak / fragmented / undercovered 主题域。

### 5. Agent 助手
- ReAct 式 Agent，13 个工具分为读层 / 处方层 / 写层。
- 支持 `/loop` 自主迭代模式：持续评估薄弱卡片与薄弱簇，按需搜索、扩展、刷新和挂载。
- Agent 循环后台化：页面刷新或 SSE 断开不取消正在执行的任务；重连后回放进度。
- 写层工具连续失败自动熔断；工具名、卡片 ID、参数 JSON 均有多级容错。
- 卡片搜索采用标题预检、query 领域锚定、树结构注入等机制抑制主题漂移和扁平化。

### 6. 任务与断线恢复
- 每次收集 / 扩展 / 刷新 / 文档分析都对应一个 Task。
- SSE 实时推送 progress / card / complete / error 事件；刷新页面后通过任务流回放已生成卡片和当前进度。

## 技术栈

| 层 | 技术 |
| --- | --- |
| 后端 | FastAPI + Python 3.10+ |
| 前端 | React 18 + TypeScript + Vite + react-router-dom |
| 可视化 | vis-network |
| 存储 | 全局 SQLite（用户/会话元数据/域名质量等）+ 每会话独立 SQLite（cards / raw_pages / 向量索引） |
| 向量 | sqlite-vec，512 维，cosine 距离 |
| 嵌入 | bge-small-zh-v1.5，本地 CPU 推理 |
| 搜索 | 免费多引擎（Bing/AnySearch/Exa-MCP/DDG/SearXNG）+ Bocha/Baidu/Exa 适配 |
| 抓取 | crawl4ai / Playwright + trafilatura + httpx/BeautifulSoup |
| 测试 | pytest + Vitest |

## 快速开始

```bash
# 1) 先建 .env 并填好 JWT_SECRET（必填：为空时后端启动直接抛错）
cp .env.example .env && vim .env      # JWT_SECRET 必填；搜索默认无需任何 key

# 2) 下载嵌入模型权重（约 92MB，仓库不再内置）
pip install -U huggingface_hub
huggingface-cli download BAAI/bge-small-zh-v1.5 --local-dir models/bge-small-zh-v1.5
# 国内网络可先 export HF_ENDPOINT=https://hf-mirror.com
# 若 models/bge-small-zh-v1.5/ 缺失，程序会回退到在线 HF 名称 BAAI/bge-small-zh-v1.5（首次使用需联网下载）

# 3) 开发环境（后端 :8000 + 前端 :3000）
./start.sh            # Linux / macOS
# Windows 使用 start.bat

# 生产部署
./server_start.sh     # 安装依赖 + 后端重启 + 前端构建 + Nginx reload
```

后端依赖统一由根目录 `requirements.txt` 管理；前端依赖由 `frontend/package.json` 管理。

> **必填项**：`JWT_SECRET` 为空时 `backend/config.py` 会直接 `raise RuntimeError`，后端无法启动，因此这是唯一强制项；要生成卡片或使用 Agent，还需填写 `AI_API_KEY`。搜索默认免费，无需任何 key。

## 配置

环境变量集中读取于 `backend/config.py`，实际密钥放在 `.env`（已被 `.gitignore` 排除）。模板见 `.env.example`。

常用配置：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `AI_API_URL` | `https://api.deepseek.com` | OpenAI 兼容 API 地址；可填完整 `/v1/responses` 端点，代码自动规范化 |
| `AI_API_KEY` | 空 | API 密钥 |
| `AI_MODEL` | `deepseek-v4-flash` | 摘要 / 卡片生成模型 |
| `AGENT_MODEL` | 同 `AI_MODEL` | Agent 专用模型 |
| `AI_CONCURRENCY` | `2` | LLM 并发上限 |
| `DEFAULT_SEARCH_PROVIDER` | `free` | 搜索源：`free` / `bocha` / `baidu` / `exa` |
| `FREE_SEARCH_ENGINES` | `bing,anysearch,exa-mcp,ddg,searxng` | 免费引擎优先级（左→右，任一成功即止） |
| `ANYSEARCH_API_KEY` | 空 | 可选，仅用于提高 AnySearch 匿名额度 |
| `BOCHA_API_KEY` | 空 | 第三方搜索密钥（仅 `provider=bocha` 时需要） |
| `MAX_CONCURRENT_TASKS` | `5` | 并发任务上限 |
| 嵌入模型目录 | `models/bge-small-zh-v1.5/` | 该目录存在则离线加载本地权重；缺失时回退在线 HF 名称 `BAAI/bge-small-zh-v1.5` |

## 项目结构

```
backend/
├── main.py                 # FastAPI 入口与安全中间件
├── config.py               # 全局配置唯一来源
├── routes/                 # API 路由（薄层）
├── pipeline/               # 可组合搜索流水线 + PipelineAPI
├── agent/                  # Agent 工具、循环、后台管理器
├── quality/                # 质量评分、簇级评估、改进策略
├── ai/                     # LLM 调用与本地嵌入模型
├── search/                 # 搜索源适配（free 免费多引擎 / 博查 / 百度 / Exa）
├── scraper/                # 三级抓取、域名质量、URL 优先级
├── storage/                # 存储抽象与 SQLite 实现
└── models/                 # Pydantic 模型与任务模型

frontend/src/
├── components/             # 卡片树、图谱、收集器、Agent 抽屉、质量面板等
├── hooks/                  # useSSE / useTaskManager / useAgent 等领域逻辑
├── api/                    # API 调用层
└── utils/                  # 树构建、图谱转换、Markdown 安全处理

cards/{username}/{session_id}/session.db   # 每会话独立知识库（运行时生成，已忽略）
data/knowledgediver.db                     # 全局数据库（运行时生成，已忽略）
models/bge-small-zh-v1.5/                  # 本地嵌入模型权重（需自行下载，已忽略）
```

## 测试

```bash
# 后端
cd tests/backend && pytest -v

# 前端
cd frontend && npm test
```

## 本开源版不包含什么

本仓库是从内部完整系统中剥离出的**核心代码版**，目标是开箱即可自托管运行。以下内容不在开源范围内：

- **论文、专利与竞赛材料**：期刊/会议投稿、研究手稿、专利申报文件，以及各类学科竞赛材料。
- **研究语料与实验记录**：内部评测数据集、实验日志、端到端审计产物与中间结果。
- **嵌入模型权重**：不再内置 `bge-small-zh-v1.5` 权重（约 92MB），请按「快速开始」第 2 步自行下载到 `models/bge-small-zh-v1.5/`。
- **演示数据**：不含真实卡片库、示例数据库、用户数据或抓取缓存，`data/` 与 `models/` 仅保留占位文件。
- **支付、订单、会员与积分等商业化模块**：在线收款、订单与订阅/权益相关的后端模块、前端页面及配置项（如 `PAY_*`）均已整体移除。
- **内置密钥**：仓库中不含任何真实密钥，全部配置项统一通过 `.env` 提供，仓库内只有 `.env.example` 模板。

## License

MIT（见 [LICENSE](LICENSE)）。
