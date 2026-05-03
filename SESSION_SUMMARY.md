# MGFlow 项目状态总结

## 项目概述

MGFlow 是一个 AI 多 Agent 系统，用于自动化 MG（Motion Graphics）动画生产。用户通过聊天框输入创意简报，系统自动驱动 8 个节点的 DAG 工作流完成整个动画制作。

## 架构

- **Blackboard 模式**：文件系统黑板（`project_dir/blackboard.json`），5 种状态：pending/running/done/stale/failed
- **Orchestrator**：顶层 LLM agent，通过 function calling 调度，拥有 6 个元工具（read_blackboard, update_blackboard, launch_worker, launch_workers, invalidate_node, answer_worker）
- **Worker**：每个节点独立的 agent loop，有自己的 system prompt + 工具集
- **事件总线**：异步 pub-sub，连接 Orchestrator/Worker/Web 层
- **IR 编译器**：LLM 生成 IR JSON → 确定性编译为 HTML+JS 动画

## DAG 节点

```
creative_planning (root, 无依赖)
├── script_writing (依赖 creative_planning)
├── style_setting (依赖 creative_planning, 工具: style_extract)
│
storyboard (依赖 script_writing + style_setting)
├── visual_design (依赖 storyboard, 工具: image_generate, image_search)
├── sound_design (依赖 storyboard, 工具: tts_generate)
│
motion_design (依赖 storyboard + visual_design)
│
render (依赖 motion_design + sound_design, 确定性，无 LLM)
```

## 目录结构

```
MGFlow/
├── app.py                  # FastAPI 入口，SSE 推送，项目管理 API
├── core/
│   ├── orchestrator.py     # Orchestrator agent loop + 6 元工具
│   ├── worker.py           # Worker 通用运行时（流式 LLM + 工具调用 + 日志持久化 + ask_for_clarification）
│   ├── blackboard.py       # 文件系统黑板 + 状态机
│   ├── events.py           # 异步事件总线
│   ├── llm.py              # LLM 客户端（chat + chat_stream）
│   ├── node.py             # @node 装饰器 + 注册表 + DAG
│   └── tool.py             # @tool 装饰器 + 注册表
├── nodes/                  # 8 个节点定义（声明式）
├── tools/                  # 4 个工具实现（image_gen, image_search, tts, style_extract）
├── renderer/
│   ├── compiler.py         # IR → HTML 编译器
│   ├── player.js           # JS 播放器引擎
│   ├── schema.py           # IR schema 定义
│   └── ir_validator.py     # IR 质检
├── static/index.html       # 前端 SPA（DAG 面板 + 聊天 + 预览）
└── projects/               # 运行时数据（gitignore）
```

## 已完成功能（本次会话）

1. **EventBus 双实例 Bug 修复** — Blackboard 接受 event_bus 参数，不再用全局单例
2. **LLM 流式输出（后端）** — `LLMClient.chat_stream()` + Worker `_stream_response()` 逐 token 推送
3. **LLM 流式输出（前端）** — Orchestrator 逐字显示 AI 回复，Worker 实时输出显示在 DAG 节点下方
4. **Worker 对话日志持久化** — 完整 messages 保存到 `project_dir/logs/{node_name}.json`，API 可查询
5. **节点详情面板** — 点击 DAG 节点弹出模态框，显示完整对话日志，自动刷新
6. **预览资产路径修复** — compiler 生成相对路径 + app.py 路径替换 + 资产服务路由
7. **历史项目列表** — `GET /api/projects` + 前端下拉列表，可加载恢复
8. **并行 Worker** — `launch_workers` 元工具，asyncio.gather 并行执行
9. **真实 TTS（MiniMax）** — hex 编码音频解码，无 API key 时降级为 mock
10. **ask_for_clarification** — Worker 暂停请求用户输入，Orchestrator 中转

## 待实现

- **任意节点切入** — 用户上传产出物（脚本、图片等），AI 识别对应节点并标记为 done

## 技术栈

- Python + FastAPI（async）
- LLM: Claude Sonnet（通过 Bilibili llmapi OpenAI 兼容网关）
- TTS: MiniMax API（speech-02-hd, female-tianmei）
- 图片生成: gpt-image-2
- 图片搜索: Tavily API
- 前端: Vanilla JS SPA + SSE

## 关键设计决策

- 全异步，Worker 之间通过事件总线通信
- 工具定义只在节点内部，不污染其他 Agent 上下文
- render 节点跳过 LLM，直接调用编译器
- 惰性回滚：上游变更标记下游为 stale，Orchestrator 决定是否重做
- Worker 的 `ask_for_clarification` 通过 asyncio.Queue 实现暂停/恢复

## .env 配置

```
LLM_GATEWAY_API_KEY=...
LLM_GATEWAY_BASE_URL=http://llmapi.bilibili.co/v1
TAVILY_API_KEY=...
MINIMAX_API_KEY=...
MINIMAX_API_BASE_URL=https://api.minimaxi.com/v1
```

## 注意事项

- `renderer/compiler.py` 的 `_to_web_path` 用户已手动修改为 `".." + normalized[idx:]`（相对路径从 output/ 目录回退到 artifacts/）
- app.py 的 preview 端点仍做路径替换兼容旧文件
- Python 环境在 `.venv/Scripts/python.exe`
- 开发方式：DevOps 迭代，每步可运行可测试，commit 用 conventional 中文格式
