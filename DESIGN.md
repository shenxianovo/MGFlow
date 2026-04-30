# MGFlow 架构设计文档

## 核心理念

用户面对一个聊天框，输入任意内容（brief、文件路径、点子等）。AI 推断用户已完成 MG 动画流程中的哪些节点，自主规划并执行剩余节点。

## 架构模式：Blackboard + Worker 隔离

```
用户聊天 → Orchestrator 读黑板，推断状态，规划下一步
         → 启动 Worker(s)（可并行）
         → Worker 完成，产出物写入黑板，事件通知 Orchestrator
         → Orchestrator 检查黑板，继续或结束
         → 回复用户
```

### Blackboard（黑板）

- 存储位置：文件系统（`project_dir/blackboard.json`）
- 天然支持断点续做和任意节点切入
- 节点状态五种：`pending` → `running` → `done` / `failed` → `stale`
- 每个节点有自定义的输出 schema（强类型，下游 Worker 启动前校验依赖）

### Orchestrator

- 通过 LLM function calling 调度，不硬编码逻辑
- 拥有"元工具"：`read_blackboard`、`update_blackboard`、`launch_worker`、`invalidate_node`
- 不加载任何 Worker 的工具定义，只知道每个节点的能力摘要 + 输入输出 schema
- context 极小：黑板状态 + 用户对话 + 节点能力表

### Worker

- 每个 Worker 是独立的 agent loop（统一模型，不区分单次/多轮）
- 有自己的 system prompt + 工具集 + 独立 context
- 工具定义只在节点内部，不污染其他 Agent 的上下文
- 简单节点第一轮就不调工具，loop 自然结束，等价于单次调用

### Worker 请求用户输入

- Worker 有一个特殊工具 `ask_for_clarification`
- 调用后 Worker loop 暂停
- 事件上抛给 Orchestrator
- Orchestrator 能答就答（从黑板/对话历史推断），不能就转发用户
- 用户回答后，Orchestrator 路由答案回 Worker，loop 继续
- 聊天界面中带来源标签，如 `[美术设计] 需要确认：...`

### 并行与异步

- Worker 通过异步任务队列执行，Orchestrator 不阻塞
- Worker 完成后发事件通知，Orchestrator 收到后重新读黑板决策
- 用户可以在 Worker 运行期间继续对话

### 回滚机制

- 惰性回滚：修改上游节点时，代码根据 DAG 依赖关系确定性标记下游为 `stale`
- Orchestrator（LLM）决定 stale 节点是重做还是增量修补
- 不激进清除产出物，避免不必要的重复工作

## DAG 节点定义

| 节点 | 依赖 | 产出物 | 工具 |
|------|------|--------|------|
| creative_planning | 无 | 创意方向文档（文本） | 无 |
| script_writing | creative_planning | 解说词/对白脚本（文本） | 无 |
| style_setting | creative_planning | 风格定义 JSON（色板、字体、mood 等） | 无（或 style_extract） |
| storyboard | script_writing + style_setting | 分镜列表 JSON（镜头描述、时长、节奏） | 无 |
| visual_design | storyboard | 分层美术资产（图片文件路径列表） | image_generate, image_search |
| motion_design | storyboard + visual_design | 动画 IR JSON（复用老项目 IR schema） | 无（纯 LLM 结构化输出） |
| sound_design | storyboard | 音频文件路径 + 时间轴标记 | tts_generate (MiniMax) |
| render | motion_design + sound_design | 最终 HTML 文件路径 | 无（确定性 IR 编译器，纯代码） |

## 输出格式

- LLM 只生成 IR JSON，不碰 HTML
- 确定性 IR → HTML 编译器：JS 播放器读取 IR JSON，创建 DOM，按时间轴驱动 CSS animation
- IR schema 复用老项目的定义（元素类型、动画预设、keyframes、转场、镜头运动）

## 软件架构：声明式注册 + 事件驱动

### 设计原则
- 加一个节点或工具只需写一个文件，不改任何已有代码
- 节点声明式定义，自动注册，DAG 从 `depends_on` 自动构建
- 引擎（core/）和业务（nodes/、tools/）完全解耦
- 所有组件通过事件总线通信，互不直接引用

### 节点定义方式

```python
# nodes/visual_design.py
@node(
    name="visual_design",
    depends_on=["storyboard"],
    tools=["image_generate", "image_search"],
    max_iterations=15,
)
class VisualDesign(Node):
    system_prompt = "你是MG动画的美术设计师..."
    class Input:
        storyboard: StoryboardOutput
        style: StyleOutput
    class Output:
        assets: list[Asset]
```

### 工具定义方式

```python
# tools/image_gen.py
@tool(name="image_generate")
class ImageGenerate(Tool):
    description = "生成AI图片"
    parameters = { ... }
    async def execute(self, prompt: str, size: str = "1536x1024") -> dict: ...
```

### 事件总线

```python
# 事件类型
WorkerStarted / WorkerCompleted / WorkerNeedInput / NodeStateChanged / ...
```

Orchestrator、Worker、Web 层都通过事件总线通信。Web 层只需订阅事件推 SSE。

### 目录结构

```
MGFlow/
├── app.py                        # FastAPI 入口，薄壳
├── .env
├── requirements.txt
├── DESIGN.md
│
├── core/                         # 引擎（不含业务逻辑）
│   ├── __init__.py
│   ├── node.py                   # Node 基类 + @node 装饰器 + 注册表
│   ├── tool.py                   # Tool 基类 + @tool 装饰器 + 注册表
│   ├── blackboard.py             # Blackboard 读写 + 状态机
│   ├── orchestrator.py           # Orchestrator agent loop
│   ├── worker.py                 # Worker 通用运行时
│   ├── events.py                 # 事件总线
│   └── llm.py                    # LLM 客户端封装
│
├── nodes/                        # 节点定义（声明式，自动注册）
│   ├── __init__.py               # 自动扫描注册
│   ├── creative_planning.py
│   ├── script_writing.py
│   ├── style_setting.py
│   ├── storyboard.py
│   ├── visual_design.py
│   ├── motion_design.py
│   ├── sound_design.py
│   └── render.py
│
├── tools/                        # 工具实现（自动注册）
│   ├── __init__.py               # 自动扫描注册
│   ├── image_gen.py
│   ├── image_search.py
│   ├── tts.py
│   └── style_extract.py
│
├── renderer/                     # IR → HTML 确定性编译器
│   ├── compiler.py               # 组装最终 HTML（嵌入 player.js + IR）
│   └── player.js                 # JS 播放器引擎
│
├── static/                       # 前端
│   └── index.html
│
└── projects/                     # 运行时数据（gitignore）
    └── {project_id}/
        ├── blackboard.json
        ├── artifacts/
        └── output/
```

## 技术栈

- 后端：Python + FastAPI（async）
- 前端：Web 单页应用（SSE 实时推送）
- LLM：统一 Claude Sonnet（通过 Bilibili llmapi OpenAI 兼容网关）
- TTS：MiniMax API（真实服务）
- 图片生成：gpt-image-2
- 图片搜索：Tavily API

## 前端布局

```
┌─────────────────────────────────────────────────┐
│  [DAG 状态面板] 各节点状态一目了然               │
│  creative ✓ → script ✓ → storyboard ⚙ running   │
│              → style ✓ ↗                         │
├─────────────────────────────────────────────────┤
│  [聊天区]                                        │
│  用户: 做一个小龙虾科普视频                       │
│  系统: 已启动创意策划和风格设定...                │
│  [美术设计]: 小龙虾用卡通还是写实？               │
│  用户: 卡通                                      │
├─────────────────────────────────────────────────┤
│  [预览区] iframe 播放当前动画                     │
└─────────────────────────────────────────────────┘
```

### 错误处理

- Worker 内部自行重试 + 降级（如 image_generate 限流自动切 image_search）
- 完全无法继续时标记节点为 `failed`
- Orchestrator 看到 `failed` 节点，决定重试、换策略、或问用户
- 节点状态五种：`pending` / `running` / `done` / `stale` / `failed`

## 第一版范围（MVP）

### 必须实现
- Orchestrator + Blackboard + Worker 运行时
- 全部 8 个节点（sound_design 先 mock，按字数估时长）
- IR 确定性编译器（JS 播放器）
- Web UI（状态面板 + 聊天 + 预览）
- 核心链路：用户 brief → 全流程 → 动画预览

### 第一版简化（后续补全）
| 机制 | 完整版 | 第一版简化 |
|------|--------|-----------|
| 并行 fork | 异步任务队列 | 先串行跑，架构上预留并行接口 |
| 回滚 | 惰性标记 + 增量更新 | 先做全量重跑，stale 直接清除 |
| Worker 问用户 | Orchestrator 中转 | 先实现，但简单场景可能用不到 |
| 任意节点切入 | 用户上传产出物自动识别 | 先支持从头跑，手动编辑 blackboard.json 模拟切入 |
| TTS | MiniMax 真实服务 | 先 mock，后续接入 |
