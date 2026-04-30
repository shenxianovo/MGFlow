from __future__ import annotations

import json
from pathlib import Path
from typing import Any, AsyncGenerator

from .blackboard import Blackboard
from .events import EventBus, ORCHESTRATOR_MESSAGE, WORKER_PROGRESS
from .llm import LLMClient
from .node import discover_nodes, get_all_nodes
from .tool import discover_tools
from .worker import Worker


def _build_node_capability_table() -> str:
    nodes = get_all_nodes()
    lines = []
    for name, nd in nodes.items():
        deps = ", ".join(nd.depends_on) if nd.depends_on else "无"
        tools = ", ".join(nd.tools) if nd.tools else "无"
        det = "（确定性，无需LLM）" if nd.deterministic else ""
        lines.append(f"- {name}: 依赖=[{deps}], 工具=[{tools}]{det}")
    return "\n".join(lines)


ORCHESTRATOR_SYSTEM_PROMPT = """你是 MGFlow 的编排调度器（Orchestrator）。你的职责是：
1. 理解用户需求
2. 读取黑板（Blackboard）了解当前各节点状态
3. 决定下一步启动哪个 Worker 节点
4. 驱动整个 MG 动画生产流程直到完成

## MG 动画生产 DAG

{node_table}

## 工作流程

每轮你应该：
1. 先调用 read_blackboard 了解当前状态
2. 查看哪些节点可以执行（状态为 pending 且依赖已满足）
3. 调用 launch_worker 启动下一个节点
4. Worker 完成后，继续检查黑板，启动下一个可执行节点
5. 所有节点完成后（render 节点 done），告诉用户动画已生成

## 重要规则

- 每次只启动一个 Worker（MVP 串行执行）
- launch_worker 时，第一个节点（creative_planning）需要传入用户的原始需求作为 extra_input
- 后续节点不需要 extra_input，Worker 会自动从黑板读取上游依赖
- 如果某个节点 failed，你可以决定重试（再次 launch_worker）或告诉用户
- 用中文和用户交流
"""

META_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_blackboard",
            "description": "读取当前黑板状态，了解各节点的完成情况和产出物",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_blackboard",
            "description": "手动更新某个节点的产出物（用于用户直接提供了某个阶段的成果）",
            "parameters": {
                "type": "object",
                "properties": {
                    "node_name": {"type": "string", "description": "节点名称"},
                    "output": {"type": "object", "description": "节点产出数据"},
                },
                "required": ["node_name", "output"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "launch_worker",
            "description": "启动一个 Worker 执行指定节点。节点必须处于可执行状态（pending/stale 且依赖已满足）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "node_name": {"type": "string", "description": "要执行的节点名称"},
                    "extra_input": {
                        "type": "string",
                        "description": "额外输入信息（如用户原始需求），传给 Worker 的 user_input",
                    },
                },
                "required": ["node_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "invalidate_node",
            "description": "使某个节点及其所有下游节点失效（标记为 stale），用于用户修改了上游需求时",
            "parameters": {
                "type": "object",
                "properties": {
                    "node_name": {"type": "string", "description": "要失效的节点名称"},
                },
                "required": ["node_name"],
            },
        },
    },
]


class Orchestrator:
    def __init__(
        self,
        project_dir: Path,
        event_bus: EventBus,
        llm: LLMClient | None = None,
    ) -> None:
        self.project_dir = Path(project_dir)
        self.event_bus = event_bus
        self.llm = llm or LLMClient()
        self.blackboard = Blackboard(self.project_dir, event_bus=self.event_bus)
        self.messages: list[dict] = []
        self._initialized = False

    def _ensure_init(self, user_brief: str = "") -> None:
        if not self._initialized:
            discover_nodes("nodes")
            discover_tools("tools")

            if not (self.project_dir / "blackboard.json").exists():
                self.blackboard.init_from_dag(user_brief=user_brief)

            node_table = _build_node_capability_table()
            system_prompt = ORCHESTRATOR_SYSTEM_PROMPT.format(node_table=node_table)
            self.messages = [{"role": "system", "content": system_prompt}]
            self._initialized = True

    async def run(self, user_message: str) -> AsyncGenerator[dict, None]:
        self._ensure_init(user_brief=user_message)
        self.messages.append({"role": "user", "content": user_message})

        max_rounds = 30
        for _ in range(max_rounds):
            resp = await self.llm.chat(
                messages=self.messages,
                tools=META_TOOLS,
                max_tokens=4000,
            )
            choice = resp.choices[0]
            message = choice.message

            if message.content:
                yield {"type": "orchestrator_message", "message": message.content}
                await self.event_bus.emit(
                    ORCHESTRATOR_MESSAGE, {"message": message.content}
                )

            if not message.tool_calls:
                return

            self.messages.append(message)

            for tool_call in message.tool_calls:
                fn_name = tool_call.function.name
                try:
                    fn_args = json.loads(tool_call.function.arguments)
                except json.JSONDecodeError:
                    fn_args = {}

                result = await self._execute_meta_tool(fn_name, fn_args)

                yield {
                    "type": "tool_call",
                    "tool": fn_name,
                    "args": fn_args,
                    "result": result,
                }

                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": json.dumps(result, ensure_ascii=False),
                })

        yield {"type": "error", "message": "达到最大调度轮数，流程未完成"}

    async def _execute_meta_tool(self, fn_name: str, fn_args: dict) -> dict:
        if fn_name == "read_blackboard":
            return {"summary": self.blackboard.to_summary()}

        elif fn_name == "update_blackboard":
            node_name = fn_args["node_name"]
            output = fn_args["output"]
            try:
                await self.blackboard.set_running(node_name)
                await self.blackboard.set_done(node_name, output)
                return {"status": "ok", "node": node_name}
            except Exception as e:
                return {"error": str(e)}

        elif fn_name == "launch_worker":
            node_name = fn_args["node_name"]
            extra_input = fn_args.get("extra_input", "")

            ready = self.blackboard.get_ready_nodes()
            if node_name not in ready:
                status = self.blackboard.get_status(node_name)
                if status == "failed":
                    pass
                else:
                    return {
                        "error": f"节点 {node_name} 当前不可执行（状态: {status}，可执行节点: {ready}）"
                    }

            try:
                worker = Worker(
                    node_name=node_name,
                    blackboard=self.blackboard,
                    event_bus=self.event_bus,
                    llm=self.llm,
                    project_dir=self.project_dir,
                )
                output = await worker.run(user_input=extra_input)
                summary = json.dumps(output, ensure_ascii=False)
                if len(summary) > 500:
                    summary = summary[:500] + "..."
                return {
                    "status": "completed",
                    "node": node_name,
                    "output_summary": summary,
                    "ready_nodes": self.blackboard.get_ready_nodes(),
                }
            except Exception as e:
                return {
                    "status": "failed",
                    "node": node_name,
                    "error": str(e),
                    "ready_nodes": self.blackboard.get_ready_nodes(),
                }

        elif fn_name == "invalidate_node":
            node_name = fn_args["node_name"]
            invalidated = await self.blackboard.invalidate_downstream(node_name)
            return {
                "invalidated": invalidated,
                "ready_nodes": self.blackboard.get_ready_nodes(),
            }

        return {"error": f"未知工具: {fn_name}"}
