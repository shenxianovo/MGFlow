from __future__ import annotations

import json
from typing import Any

from .blackboard import Blackboard
from .events import (
    EventBus,
    WORKER_STARTED,
    WORKER_COMPLETED,
    WORKER_FAILED,
    WORKER_PROGRESS,
)
from .llm import LLMClient
from .node import get_node, get_all_nodes
from .tool import get_tool


class Worker:
    def __init__(
        self,
        node_name: str,
        blackboard: Blackboard,
        event_bus: EventBus,
        llm: LLMClient | None = None,
        project_dir: Any = None,
    ) -> None:
        self.node_name = node_name
        self.node_def = get_node(node_name)
        self.blackboard = blackboard
        self.event_bus = event_bus
        self.llm = llm or LLMClient()
        self.project_dir = project_dir or blackboard.project_dir

    def _build_tool_schemas(self) -> list[dict]:
        schemas = []
        for tool_name in self.node_def.tools:
            t = get_tool(tool_name)
            schemas.append(t.to_function_schema())
        return schemas

    def _build_dependency_context(self) -> str:
        parts = []
        for dep_name in self.node_def.depends_on:
            output = self.blackboard.get_output(dep_name)
            if output is None:
                continue
            all_nodes = get_all_nodes()
            dep_label = dep_name
            if dep_name in all_nodes:
                dep_label = dep_name
            if isinstance(output, dict):
                output_str = json.dumps(output, ensure_ascii=False, indent=2)
            else:
                output_str = str(output)
            parts.append(f"【{dep_label} 的产出】\n{output_str}")
        return "\n\n".join(parts)

    async def _execute_tool(self, fn_name: str, fn_args: dict) -> dict:
        t = get_tool(fn_name)
        try:
            return await t.execute(**fn_args)
        except Exception as e:
            return {"error": f"工具执行失败: {e}"}

    async def run(self, user_input: str = "") -> dict:
        await self.event_bus.emit(
            WORKER_STARTED, {"node": self.node_name}
        )
        await self.blackboard.set_running(self.node_name)

        try:
            result = await self._run_loop(user_input)
            await self.blackboard.set_done(self.node_name, result)
            await self.event_bus.emit(
                WORKER_COMPLETED,
                {"node": self.node_name, "output": result},
            )
            return result
        except Exception as e:
            error_msg = str(e)
            await self.blackboard.set_failed(self.node_name, error_msg)
            await self.event_bus.emit(
                WORKER_FAILED,
                {"node": self.node_name, "error": error_msg},
            )
            raise

    async def _run_loop(self, user_input: str) -> dict:
        dep_context = self._build_dependency_context()
        user_message = ""
        if dep_context:
            user_message += dep_context + "\n\n"
        if user_input:
            user_message += f"【用户需求】\n{user_input}"
        if not user_message:
            user_message = "请开始工作。"

        messages: list[dict] = [
            {"role": "system", "content": self.node_def.system_prompt},
            {"role": "user", "content": user_message},
        ]
        tool_schemas = self._build_tool_schemas()

        for iteration in range(self.node_def.max_iterations):
            await self.event_bus.emit(
                WORKER_PROGRESS,
                {
                    "node": self.node_name,
                    "message": f"第 {iteration + 1} 轮推理...",
                },
            )

            kwargs: dict[str, Any] = {"messages": messages}
            if tool_schemas:
                kwargs["tools"] = tool_schemas

            resp = await self.llm.chat(**kwargs)
            choice = resp.choices[0]
            message = choice.message

            if message.content:
                await self.event_bus.emit(
                    WORKER_PROGRESS,
                    {
                        "node": self.node_name,
                        "message": message.content[:200],
                    },
                )

            if not message.tool_calls:
                return self._parse_output(message.content or "")

            messages.append(message)

            for tool_call in message.tool_calls:
                fn_name = tool_call.function.name
                try:
                    fn_args = json.loads(tool_call.function.arguments)
                except json.JSONDecodeError:
                    fn_args = {}

                result = await self._execute_tool(fn_name, fn_args)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": json.dumps(result, ensure_ascii=False),
                })

        return self._parse_output(messages[-1].get("content", "") if isinstance(messages[-1], dict) else "")

    def _parse_output(self, content: str) -> dict:
        content = content.strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return {"raw_output": content}
