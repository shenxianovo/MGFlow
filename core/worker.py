from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .blackboard import Blackboard
from .events import (
    EventBus,
    WORKER_STARTED,
    WORKER_COMPLETED,
    WORKER_FAILED,
    WORKER_PROGRESS,
    WORKER_TOKEN,
    WORKER_NEED_INPUT,
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
        self._input_queue: asyncio.Queue[str] = asyncio.Queue()
        self._waiting_for_input = False

    _ASK_TOOL_SCHEMA = {
        "type": "function",
        "function": {
            "name": "ask_for_clarification",
            "description": "向用户提问以获取更多信息。当你不确定某个细节时使用此工具。",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "要问用户的问题",
                    },
                },
                "required": ["question"],
            },
        },
    }

    def _build_tool_schemas(self) -> list[dict]:
        schemas = [self._ASK_TOOL_SCHEMA]
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
        if fn_name == "ask_for_clarification":
            question = fn_args.get("question", "")
            self._waiting_for_input = True
            await self.event_bus.emit(
                WORKER_NEED_INPUT,
                {"node": self.node_name, "question": question},
            )
            answer = await self._input_queue.get()
            self._waiting_for_input = False
            return {"answer": answer}

        t = get_tool(fn_name)
        try:
            fn_args["project_dir"] = str(self.project_dir)
            return await t.execute(**fn_args)
        except Exception as e:
            return {"error": f"工具执行失败: {e}"}

    def provide_input(self, answer: str) -> None:
        self._input_queue.put_nowait(answer)

    async def run(self, user_input: str = "") -> dict:
        await self.event_bus.emit(
            WORKER_STARTED, {"node": self.node_name}
        )
        await self.blackboard.set_running(self.node_name)
        self._started_at = datetime.now(timezone.utc).isoformat()
        self._log_messages: list[dict] = []

        try:
            if self.node_def.deterministic:
                result = await self._run_deterministic()
            else:
                result = await self._run_loop(user_input)
            self._save_log("done")
            await self.blackboard.set_done(self.node_name, result)
            await self.event_bus.emit(
                WORKER_COMPLETED,
                {"node": self.node_name, "output": result},
            )
            return result
        except Exception as e:
            error_msg = str(e)
            self._save_log("failed", error=error_msg)
            await self.blackboard.set_failed(self.node_name, error_msg)
            await self.event_bus.emit(
                WORKER_FAILED,
                {"node": self.node_name, "error": error_msg},
            )
            raise

    def _save_log(self, status: str, error: str | None = None) -> None:
        logs_dir = Path(self.project_dir) / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        log_data = {
            "node_name": self.node_name,
            "started_at": self._started_at,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "status": status,
            "messages": self._log_messages,
        }
        if error:
            log_data["error"] = error
        log_path = logs_dir / f"{self.node_name}.json"
        log_path.write_text(
            json.dumps(log_data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    async def _run_deterministic(self) -> dict:
        from renderer.compiler import compile_html

        ir_data = self.blackboard.get_output("motion_design")
        sound = self.blackboard.get_output("sound_design")
        if sound and sound.get("audio_path"):
            ir_data["audio_path"] = sound["audio_path"]

        output_dir = self.project_dir / "output"
        output_dir.mkdir(parents=True, exist_ok=True)

        import uuid
        output_path = output_dir / f"mg_{uuid.uuid4().hex[:8]}.html"
        compile_html(ir_data, str(output_path))

        await self.event_bus.emit(
            WORKER_PROGRESS,
            {"node": self.node_name, "message": f"渲染完成: {output_path.name}"},
        )
        return {"html_path": str(output_path), "title": ir_data.get("title", "")}

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
        self._log_messages = messages
        tool_schemas = self._build_tool_schemas()

        for iteration in range(self.node_def.max_iterations):
            await self.event_bus.emit(
                WORKER_PROGRESS,
                {
                    "node": self.node_name,
                    "message": f"第 {iteration + 1} 轮推理...",
                },
            )

            kwargs: dict[str, Any] = {
                "messages": messages,
                "max_tokens": self.node_def.max_tokens,
            }
            if tool_schemas:
                kwargs["tools"] = tool_schemas

            content, tool_calls = await self._stream_response(**kwargs)

            if content:
                await self.event_bus.emit(
                    WORKER_PROGRESS,
                    {"node": self.node_name, "message": content[:200]},
                )

            if not tool_calls:
                messages.append({"role": "assistant", "content": content})
                return self._parse_output(content)

            assistant_msg: dict[str, Any] = {"role": "assistant", "content": content or None}
            assistant_msg["tool_calls"] = [
                {
                    "id": tc["id"],
                    "type": "function",
                    "function": {"name": tc["name"], "arguments": tc["arguments"]},
                }
                for tc in tool_calls
            ]
            messages.append(assistant_msg)

            for tc in tool_calls:
                fn_name = tc["name"]
                try:
                    fn_args = json.loads(tc["arguments"])
                except json.JSONDecodeError:
                    fn_args = {}

                result = await self._execute_tool(fn_name, fn_args)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": json.dumps(result, ensure_ascii=False),
                })

        last = messages[-1]
        return self._parse_output(last.get("content", "") if isinstance(last, dict) else "")

    async def _stream_response(self, **kwargs: Any) -> tuple[str, list[dict]]:
        content = ""
        tool_calls_by_index: dict[int, dict] = {}

        async for chunk in self.llm.chat_stream(**kwargs):
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta is None:
                continue

            if delta.content:
                content += delta.content
                await self.event_bus.emit(
                    WORKER_TOKEN,
                    {"node": self.node_name, "token": delta.content},
                )

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

        tool_calls = [tool_calls_by_index[i] for i in sorted(tool_calls_by_index)]
        return content, tool_calls

    def _parse_output(self, content: str) -> dict:
        content = content.strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            pass
        start = content.find("{")
        end = content.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(content[start : end + 1])
            except json.JSONDecodeError:
                pass
        return {"raw_output": content}
