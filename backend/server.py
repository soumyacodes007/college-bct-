"""FastAPI app -- routes only. Orchestration lives in graph_pipeline.py.

Job state is never stored in a Python dict: it's read straight from the LangGraph checkpointer via
graph.aget_state(config), keyed by job_id as thread_id. That's what makes a job survive a server
restart, unlike topic2manim's global `jobs = {}` + daemon thread.
"""

import asyncio
import os
import sys
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).parent))

load_dotenv()

from graph_pipeline import build_graph, create_initial_state
from languages import DEFAULT_LANGUAGE, DEFAULT_SPEAKER, LANGUAGES

PROJECT_ROOT = Path(__file__).parent.parent
MEDIA_DIR = PROJECT_ROOT / "media"
MEDIA_DIR.mkdir(exist_ok=True)

DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)
CHECKPOINT_DB = str(DATA_DIR / "checkpoints.sqlite3")


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with AsyncSqliteSaver.from_conn_string(CHECKPOINT_DB) as checkpointer:
        app.state.checkpointer = checkpointer
        app.state.graph = build_graph(checkpointer)
        yield


app = FastAPI(title="Conceptreel API", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class GenerateRequest(BaseModel):
    topic: str
    language: str = DEFAULT_LANGUAGE
    target_duration: int = 30
    speaker: str = DEFAULT_SPEAKER


async def _run_job(graph, job_id: str, config: dict):
    try:
        await graph.ainvoke(None, config)
    except Exception as e:
        print(f"[server] Job {job_id} failed: {e}")
        await graph.aupdate_state(config, {"status": "failed", "error": str(e), "message": f"Error: {e}"})


@app.post("/api/generate")
async def generate_video(req: GenerateRequest):
    if req.language not in LANGUAGES:
        raise HTTPException(400, f"Unsupported language: {req.language}. Options: {list(LANGUAGES)}")
    if not req.topic.strip():
        raise HTTPException(400, "Topic is required")

    job_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": job_id}}
    initial_state = create_initial_state(job_id, req.topic, req.language, req.target_duration, req.speaker)

    graph = app.state.graph
    await graph.aupdate_state(config, initial_state)
    asyncio.create_task(_run_job(graph, job_id, config))

    return {"job_id": job_id, "status": "queued"}


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str):
    graph = app.state.graph
    config = {"configurable": {"thread_id": job_id}}
    snapshot = await graph.aget_state(config)

    if not snapshot or not snapshot.values:
        raise HTTPException(404, "Job not found")

    values = snapshot.values
    video_url = None
    if values.get("final_video_path"):
        video_url = f"/api/media/{os.path.basename(values['final_video_path'])}"

    return {
        "job_id": job_id,
        "status": values.get("status"),
        "progress": values.get("progress"),
        "current_step": values.get("current_step"),
        "message": values.get("message"),
        "error": values.get("error"),
        "video_url": video_url,
    }


@app.get("/api/media/{filename}")
async def get_media(filename: str):
    path = MEDIA_DIR / filename
    if not path.exists():
        raise HTTPException(404, "File not found")
    return FileResponse(path)


@app.get("/api/languages")
async def get_languages():
    return {"languages": list(LANGUAGES.keys()), "default": DEFAULT_LANGUAGE}


@app.get("/api/health")
async def health_check():
    return {"status": "healthy", "service": "Conceptreel API"}
