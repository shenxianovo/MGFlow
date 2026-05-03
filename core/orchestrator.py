from __future__ import annotations

import asyncio
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
3. 如果有多个节点可以同时执行，调用 launch_workers 并行启动它们
4. 如果只有一个节点可执行，调用 launch_worker 启动
5. Worker 完成后，继续检查黑板，启动下一批可执行节点
6. 所有节点完成后（render 节点 done），告诉用户动画已生成

## 重要规则

- 优先使用 launch_workers 并行启动多个可执行节点，提高效率
- launch_worker / launch_workers 时，第一个节点（creative_planning）需要传入用户的原始需求作为 extra_input
- 后续节点不需要 extra_input，Worker 会自动从黑板读取上游依赖
- 如果某个节点 failed，你可以决定重试（再次 launch_worker）或告诉用户
- 如果 launch_worker 返回 waiting_for_input，说明 Worker 在向用户提问。问题已经直接展示给用户了，你不需要重复转述。只需简短告知用户"某某节点有个问题需要回答"即可，等用户回答后调用 answer_worker 传递答案
- 用中文和用户交流
- 重要：每轮必须调用工具（read_blackboard / launch_worker / launch_workers 等），不要只输出文字而不调用工具。如果还有未完成的节点，必须继续调度，不要停下来

## 用户上传文件处理

当用户消息中包含"用户引用的暂存文件"清单时，说明用户上传了已有的产出物。清单只包含文件名、类型和路径，不包含文件内容。你应该：
1. 先调用 read_blackboard 了解当前状态
2. 根据文件名和类型推断它们对应 DAG 中的哪些节点
3. 对于图片素材：将它们登记为 visual_design 的产出物（assets 列表），路径使用 artifacts/<文件名>
4. 对于音频文件：登记为 sound_design 的产出物，路径使用 artifacts/<文件名>
5. 对于文本文件：这些文件的内容会在 Worker 启动时作为上游依赖自动传入，你只需要根据文件名推断对应节点，用 update_blackboard 标记为 done，output 中注明文件路径即可
6. 调用 update_blackboard 标记对应节点为 done
7. 然后继续正常的工作流调度
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
    {
        "type": "function",
        "function": {
            "name": "launch_workers",
            "description": "同时并行启动多个 Worker。所有指定节点必须处于可执行状态。比 launch_worker 更高效，适合多个节点依赖都已满足时使用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "node_names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "要并行执行的节点名称列表",
                    },
                    "extra_inputs": {
                        "type": "object",
                        "description": "可选，key 为节点名称，value 为该节点的额外输入",
                    },
                },
                "required": ["node_names"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "answer_worker",
            "description": "回答一个正在等待用户输入的 Worker 的问题。Worker 收到答案后会继续执行直到完成。",
            "parameters": {
                "type": "object",
                "properties": {
                    "node_name": {"type": "string", "description": "等待输入的节点名称"},
                    "answer": {"type": "string", "description": "回答内容"},
                },
                "required": ["node_name", "answer"],
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
        self._active_workers: dict[str, tuple[Worker, asyncio.Task]] = {}

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
            content = ""
            tool_calls_by_index: dict[int, dict] = {}

            async for chunk in self.llm.chat_stream(
                messages=self.messages,
                tools=META_TOOLS,
                max_tokens=4000,
            ):
                delta = chunk.choices[0].delta if chunk.choices else None
                if delta is None:
                    continue

                if delta.content:
                    content += delta.content
                    yield {"type": "orchestrator_token", "token": delta.content}

                if delta.tool_calls:
                    for tc_delta in delta.tool_calls:
                        idx = tc_delta.index
                        if idx not in tool_calls_by_index:
                            tool_calls_by_index[idx] = {
                                "id": tc_delta.id or "",
                                "name": "",
                                "arguments": "",
                            }
                        entry = tool_calls_by_index[idx]
                        if tc_delta.id:
                            entry["id"] = tc_delta.id
                        if tc_delta.function:
                            if tc_delta.function.name:
                                entry["name"] += tc_delta.function.name
                            if tc_delta.function.arguments:
                                entry["arguments"] += tc_delta.function.arguments

            if content:
                yield {"type": "orchestrator_message", "message": content}
                await self.event_bus.emit(
                    ORCHESTRATOR_MESSAGE, {"message": content}
                )

            tool_calls = [tool_calls_by_index[i] for i in sorted(tool_calls_by_index)]

            if not tool_calls:
                return

            assistant_msg: dict[str, Any] = {"role": "assistant", "content": content or None}
            assistant_msg["tool_calls"] = [
                {
                    "id": tc["id"],
                    "type": "function",
                    "function": {"name": tc["name"], "arguments": tc["arguments"]},
                }
                for tc in tool_calls
            ]
            self.messages.append(assistant_msg)

            for tc in tool_calls:
                fn_name = tc["name"]
                try:
                    fn_args = json.loads(tc["arguments"])
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
                    "tool_call_id": tc["id"],
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
                status = self.blackboard.get_status(node_name)
                if status == "done":
                    await self.blackboard.invalidate_downstream(node_name)
                if status not in ("running",):
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
                task = asyncio.create_task(worker.run(user_input=extra_input))
                self._active_workers[node_name] = (worker, task)

                result = await self._await_worker_or_input(node_name)
                return result
            except Exception as e:
                self._active_workers.pop(node_name, None)
                return {
                    "status": "failed",
                    "node": node_name,
                    "error": str(e),
                    "ready_nodes": self.blackboard.get_ready_nodes(),
                }

        elif fn_name == "launch_workers":
            node_names = fn_args.get("node_names", [])
            extra_inputs = fn_args.get("extra_inputs", {})

            ready = self.blackboard.get_ready_nodes()
            not_ready = [n for n in node_names if n not in ready and self.blackboard.get_status(n) != "failed"]
            if not_ready:
                return {"error": f"以下节点当前不可执行: {not_ready}，可执行节点: {ready}"}

            async def run_one(name: str) -> dict:
                try:
                    worker = Worker(
                        node_name=name,
                        blackboard=self.blackboard,
                        event_bus=self.event_bus,
                        llm=self.llm,
                        project_dir=self.project_dir,
                    )
                    output = await worker.run(user_input=extra_inputs.get(name, ""))
                    summary = json.dumps(output, ensure_ascii=False)
                    if len(summary) > 300:
                        summary = summary[:300] + "..."
                    return {"node": name, "status": "completed", "output_summary": summary}
                except Exception as e:
                    return {"node": name, "status": "failed", "error": str(e)}

            results = await asyncio.gather(*[run_one(n) for n in node_names])
            return {
                "results": list(results),
                "ready_nodes": self.blackboard.get_ready_nodes(),
            }

        elif fn_name == "invalidate_node":
            node_name = fn_args["node_name"]
            invalidated = await self.blackboard.invalidate_downstream(node_name)
            return {
                "invalidated": invalidated,
                "ready_nodes": self.blackboard.get_ready_nodes(),
            }

        elif fn_name == "answer_worker":
            node_name = fn_args["node_name"]
            answer = fn_args["answer"]

            if node_name not in self._active_workers:
                return {"error": f"节点 {node_name} 没有正在等待输入的 Worker"}

            worker, task = self._active_workers[node_name]
            if not worker._waiting_for_input:
                return {"error": f"节点 {node_name} 的 Worker 当前没有在等待输入"}

            worker.provide_input(answer)
            result = await self._await_worker_or_input(node_name)
            return result

        return {"error": f"未知工具: {fn_name}"}

    async def _await_worker_or_input(self, node_name: str) -> dict:
        worker, task = self._active_workers[node_name]
        while True:
            if task.done():
                self._active_workers.pop(node_name, None)
                try:
                    output = task.result()
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

            if worker._waiting_for_input:
                return {
                    "status": "waiting_for_input",
                    "node": node_name,
                    "message": "Worker 正在等待用户输入，请使用 answer_worker 回答",
                }

            await asyncio.sleep(0.1)
