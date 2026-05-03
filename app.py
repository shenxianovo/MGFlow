from __future__ import annotations

import asyncio
import json
import re
import shutil
import uuid
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, UploadFile, File
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

@app.get("/api/projects")
async def list_projects():
    projects = []
    for d in sorted(PROJECTS_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if not d.is_dir():
            continue
        brief_path = d / "brief.txt"
        brief = brief_path.read_text(encoding="utf-8").strip() if brief_path.exists() else ""
        bb_path = d / "blackboard.json"
        done_count = 0
        total_count = 0
        if bb_path.exists():
            bb = json.loads(bb_path.read_text(encoding="utf-8"))
            nodes = bb.get("nodes", {})
            total_count = len(nodes)
            done_count = sum(1 for n in nodes.values() if n.get("status") == "done")
        projects.append({
            "project_id": d.name,
            "brief": brief[:80],
            "progress": f"{done_count}/{total_count}",
        })
    return projects

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


TEXT_EXTS = {".txt", ".md", ".json", ".csv", ".srt"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}
AUDIO_EXTS = {".mp3", ".wav", ".ogg", ".flac", ".aac"}


def _file_category(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext in TEXT_EXTS:
        return "text"
    if ext in IMAGE_EXTS:
        return "image"
    if ext in AUDIO_EXTS:
        return "audio"
    return "other"


@app.post("/api/projects/{project_id}/upload")
async def upload_files(project_id: str, files: list[UploadFile] = File(...)):
    project_dir = PROJECTS_DIR / project_id
    if not project_dir.exists():
        raise HTTPException(404, f"项目 {project_id} 不存在")

    staging_dir = project_dir / "staging"
    staging_dir.mkdir(exist_ok=True)

    saved = []
    for f in files:
        content = await f.read()
        dest = staging_dir / f.filename
        dest.write_bytes(content)

        category = _file_category(f.filename)
        if category in ("image", "audio"):
            artifacts_dir = project_dir / "artifacts"
            artifacts_dir.mkdir(exist_ok=True)
            shutil.copy2(dest, artifacts_dir / f.filename)

        saved.append({
            "filename": f.filename,
            "category": category,
            "size": len(content),
        })

    return {"files": saved}


@app.get("/api/projects/{project_id}/staging")
async def list_staging(project_id: str):
    project_dir = PROJECTS_DIR / project_id
    if not project_dir.exists():
        raise HTTPException(404, f"项目 {project_id} 不存在")

    staging_dir = project_dir / "staging"
    if not staging_dir.exists():
        return {"files": []}

    files = []
    for f in sorted(staging_dir.iterdir()):
        if f.is_file():
            files.append({
                "filename": f.name,
                "category": _file_category(f.name),
                "size": f.stat().st_size,
            })
    return {"files": files}


def _resolve_file_refs(message: str, staging_dir: Path) -> str:
    """Parse @filename references and inject file content."""
    pattern = re.compile(r"@([\w\-\.]+\.\w+)")
    matches = pattern.findall(message)
    if not matches:
        return message

    appendix_parts = []
    for filename in matches:
        filepath = staging_dir / filename
        if not filepath.exists():
            continue
        category = _file_category(filename)
        if category == "text":
            try:
                text = filepath.read_text(encoding="utf-8")
                if len(text) > 8000:
                    text = text[:8000] + "\n...(内容已截断)"
                appendix_parts.append(
                    f"--- 附件: {filename} ---\n{text}\n--- 附件结束 ---"
                )
            except Exception:
                appendix_parts.append(
                    f"--- 附件: {filename} ---\n[无法读取文本内容]\n--- 附件结束 ---"
                )
        elif category == "image":
            appendix_parts.append(
                f"--- 附件: {filename} (图片文件, 已保存到 artifacts/{filename}) ---"
            )
        elif category == "audio":
            appendix_parts.append(
                f"--- 附件: {filename} (音频文件, 已保存到 artifacts/{filename}) ---"
            )
        else:
            appendix_parts.append(
                f"--- 附件: {filename} (文件类型: {Path(filename).suffix}) ---"
            )

    if appendix_parts:
        return message + "\n\n" + "\n\n".join(appendix_parts)
    return message


@app.post("/api/projects/{project_id}/chat")
async def chat(project_id: str, req: ChatRequest):
    session = _get_session(project_id)
    orch: Orchestrator = session["orchestrator"]
    event_bus: EventBus = session["event_bus"]

    staging_dir = session["project_dir"] / "staging"
    resolved_message = _resolve_file_refs(req.message, staging_dir)

    queue: asyncio.Queue[dict | None] = asyncio.Queue()

    async def on_node_state(data: dict):
        await queue.put({"event": "node_state", "data": data})

    async def on_worker_progress(data: dict):
        await queue.put({"event": "worker_progress", "data": data})

    async def on_worker_token(data: dict):
        await queue.put({"event": "worker_token", "data": data})

    async def on_worker_need_input(data: dict):
        await queue.put({"event": "worker_need_input", "data": data})

    from core.events import NODE_STATE_CHANGED, WORKER_PROGRESS, WORKER_TOKEN, WORKER_NEED_INPUT
    event_bus.subscribe(NODE_STATE_CHANGED, on_node_state)
    event_bus.subscribe(WORKER_PROGRESS, on_worker_progress)
    event_bus.subscribe(WORKER_TOKEN, on_worker_token)
    event_bus.subscribe(WORKER_NEED_INPUT, on_worker_need_input)

    async def generate():
        try:
            async for event in orch.run(resolved_message):
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
            event_bus.unsubscribe(WORKER_NEED_INPUT, on_worker_need_input)

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

    html = html_path.read_text(encoding="utf-8")
    api_prefix = f"/api/projects/{project_id}/artifacts/"
    html = html.replace("../artifacts/", api_prefix)
    html = html.replace("../assets/", api_prefix)
    return HTMLResponse(html)


@app.get("/api/projects/{project_id}/artifacts/{file_path:path}")
async def serve_artifact(project_id: str, file_path: str):
    project_dir = PROJECTS_DIR / project_id
    if not project_dir.exists():
        raise HTTPException(404, f"项目 {project_id} 不存在")
    artifact = (project_dir / "artifacts" / file_path).resolve()
    if not str(artifact).startswith(str((project_dir / "artifacts").resolve())):
        raise HTTPException(403, "路径越界")
    if not artifact.exists():
        raise HTTPException(404, f"文件不存在: {file_path}")
    return FileResponse(artifact)


STATIC_DIR = Path("static")
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def index():
    index_path = STATIC_DIR / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    return HTMLResponse("<h1>MGFlow</h1><p>static/index.html not found</p>")

