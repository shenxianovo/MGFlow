from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from core.events import EventBus
from core.orchestrator import Orchestrator

load_dotenv()

app = FastAPI(title="MGFlow")

PROJECTS_DIR = Path("projects")
PROJECTS_DIR.mkdir(exist_ok=True)

_sessions: dict[str, dict] = {}


def _get_session(project_id: str) -> dict:
    if project_id not in _sessions:
        project_dir = PROJECTS_DIR / project_id
        if not project_dir.exists():
            raise HTTPException(404, f"项目 {project_id} 不存在")
        event_bus = EventBus()
        orch = Orchestrator(project_dir, event_bus)
        _sessions[project_id] = {
            "orchestrator": orch,
            "event_bus": event_bus,
            "project_dir": project_dir,
        }
    return _sessions[project_id]


# --- Models ---

class CreateProjectRequest(BaseModel):
    brief: str


class CreateProjectResponse(BaseModel):
    project_id: str
    brief: str


class ChatRequest(BaseModel):
    message: str


# --- Routes ---

@app.post("/api/projects", response_model=CreateProjectResponse)
async def create_project(req: CreateProjectRequest):
    project_id = uuid.uuid4().hex[:8]
    project_dir = PROJECTS_DIR / project_id
    project_dir.mkdir(parents=True)
    (project_dir / "brief.txt").write_text(req.brief, encoding="utf-8")
    return CreateProjectResponse(project_id=project_id, brief=req.brief)


@app.get("/api/projects/{project_id}/status")
async def get_status(project_id: str):
    session = _get_session(project_id)
    orch: Orchestrator = session["orchestrator"]
    orch._ensure_init()
    bb_data = orch.blackboard.load()
    return {
        "project_id": project_id,
        "brief": bb_data.get("user_brief", ""),
        "nodes": bb_data.get("nodes", {}),
    }


@app.get("/api/projects/{project_id}/logs/{node_name}")
async def get_node_log(project_id: str, node_name: str):
    project_dir = PROJECTS_DIR / project_id
    if not project_dir.exists():
        raise HTTPException(404, f"项目 {project_id} 不存在")
    log_path = project_dir / "logs" / f"{node_name}.json"
    if not log_path.exists():
        raise HTTPException(404, f"节点 {node_name} 尚无执行日志")
    return json.loads(log_path.read_text(encoding="utf-8"))


@app.post("/api/projects/{project_id}/chat")
async def chat(project_id: str, req: ChatRequest):
    session = _get_session(project_id)
    orch: Orchestrator = session["orchestrator"]
    event_bus: EventBus = session["event_bus"]

    queue: asyncio.Queue[dict | None] = asyncio.Queue()

    async def on_node_state(data: dict):
        await queue.put({"event": "node_state", "data": data})

    async def on_worker_progress(data: dict):
        await queue.put({"event": "worker_progress", "data": data})

    async def on_worker_token(data: dict):
        await queue.put({"event": "worker_token", "data": data})

    from core.events import NODE_STATE_CHANGED, WORKER_PROGRESS, WORKER_TOKEN
    event_bus.subscribe(NODE_STATE_CHANGED, on_node_state)
    event_bus.subscribe(WORKER_PROGRESS, on_worker_progress)
    event_bus.subscribe(WORKER_TOKEN, on_worker_token)

    async def generate():
        try:
            async for event in orch.run(req.message):
                etype = event.get("type", "")
                if etype == "orchestrator_token":
                    yield {
                        "event": "orchestrator_token",
                        "data": json.dumps(
                            {"token": event["token"]}, ensure_ascii=False
                        ),
                    }
                elif etype == "orchestrator_message":
                    yield {
                        "event": "message",
                        "data": json.dumps(
                            {"message": event["message"]}, ensure_ascii=False
                        ),
                    }
                elif etype == "tool_call":
                    yield {
                        "event": "tool_call",
                        "data": json.dumps(
                            {
                                "tool": event["tool"],
                                "args": event["args"],
                                "result": event["result"],
                            },
                            ensure_ascii=False,
                        ),
                    }
                elif etype == "error":
                    yield {
                        "event": "error",
                        "data": json.dumps(
                            {"message": event["message"]}, ensure_ascii=False
                        ),
                    }

                while not queue.empty():
                    side_event = await queue.get()
                    yield {
                        "event": side_event["event"],
                        "data": json.dumps(side_event["data"], ensure_ascii=False),
                    }

            yield {"event": "done", "data": "{}"}
        finally:
            event_bus.unsubscribe(NODE_STATE_CHANGED, on_node_state)
            event_bus.unsubscribe(WORKER_PROGRESS, on_worker_progress)
            event_bus.unsubscribe(WORKER_TOKEN, on_worker_token)

    return EventSourceResponse(generate())


@app.get("/api/projects/{project_id}/preview")
async def preview(project_id: str):
    session = _get_session(project_id)
    orch: Orchestrator = session["orchestrator"]
    orch._ensure_init()

    render_output = orch.blackboard.get_output("render")
    if not render_output or not render_output.get("html_path"):
        raise HTTPException(404, "动画尚未渲染完成")

    html_path = Path(render_output["html_path"])
    if not html_path.exists():
        raise HTTPException(404, f"HTML 文件不存在: {html_path}")

    return HTMLResponse(html_path.read_text(encoding="utf-8"))


STATIC_DIR = Path("static")
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def index():
    index_path = STATIC_DIR / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    return HTMLResponse("<h1>MGFlow</h1><p>static/index.html not found</p>")

