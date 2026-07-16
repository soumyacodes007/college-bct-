"""LangGraph orchestration of the full pipeline: script -> audio -> per-scene codegen/compile/fix
-> assemble.

This replaces topic2manim's hand-rolled `for` loop + global in-memory jobs dict. The graph is
compiled with a checkpointer keyed by job_id as thread_id, so job status is read straight from
checkpointed state (server.py) instead of a separate dict that a restart would wipe out.

Conditional-edge router functions are kept pure (they only read state and return a routing key) --
all state mutation, including "give up on this scene after too many failed fixes," happens inside the
node functions themselves.
"""

import os
from pathlib import Path
from typing import Optional, TypedDict

from langgraph.graph import END, START, StateGraph

from code_synth import fix_manim_code, generate_manim_code
from languages import get_language
from llm_client import get_client
from render_pipeline import (
    compile_video,
    concatenate_videos,
    correct_scene_duration,
    merge_video_and_audio,
    sanitize_filename,
)
from voice_synth import concatenate_audio_fragments, generate_complete_audio

MAX_REPL_ITERATIONS = int(os.getenv("MAX_REPL_ITERATIONS", "3"))
MAX_FIX_CALLS_PER_JOB = int(os.getenv("MAX_FIX_CALLS_PER_JOB", "12"))


class PipelineState(TypedDict):
    job_id: str
    topic: str
    language_name: str
    target_duration: int
    speaker: str

    video_data: list[dict]
    audio_path: Optional[str]
    audio_durations: dict[int, float]

    scene_index: int  # 1-based; index of the scene currently being worked on
    generated_videos: list[str]
    succeeded_scene_indices: list[int]  # parallel to generated_videos; which scenes actually made it in
    current_code: Optional[str]
    current_class_name: Optional[str]
    current_error: Optional[str]
    repl_iteration: int
    total_fix_calls: int
    previous_context: Optional[dict]
    next_action: str

    final_video_path: Optional[str]
    status: str
    progress: int
    current_step: str
    message: str
    error: Optional[str]


def create_initial_state(job_id: str, topic: str, language_name: str, target_duration: int, speaker: str) -> PipelineState:
    return PipelineState(
        job_id=job_id,
        topic=topic,
        language_name=language_name,
        target_duration=target_duration,
        speaker=speaker,
        video_data=[],
        audio_path=None,
        audio_durations={},
        scene_index=1,
        generated_videos=[],
        succeeded_scene_indices=[],
        current_code=None,
        current_class_name=None,
        current_error=None,
        repl_iteration=0,
        total_fix_calls=0,
        previous_context=None,
        next_action="codegen",
        final_video_path=None,
        status="running",
        progress=0,
        current_step="script",
        message="Job queued",
        error=None,
    )


def node_script(state: PipelineState) -> dict:
    from script_writer import generate_script

    client = get_client()
    language = get_language(state["language_name"])

    video_data = generate_script(client, state["topic"], language, state["target_duration"])
    if not video_data:
        raise Exception("Script generation failed")

    return {
        "video_data": video_data,
        "progress": 25,
        "current_step": "script",
        "message": f"Script generated with {len(video_data)} scenes",
    }


def node_audio(state: PipelineState) -> dict:
    language = get_language(state["language_name"])
    media_dir = str(Path(__file__).parent.parent / "media" / state["job_id"])

    audio_path, audio_durations = generate_complete_audio(
        video_data=state["video_data"],
        language_code=language.sarvam_code,
        speaker=state["speaker"],
        media_dir=media_dir,
    )

    return {
        "audio_path": audio_path,
        "audio_durations": audio_durations,
        "progress": 40,
        "current_step": "tts",
        "message": "Audio generated" if audio_path else "TTS failed, continuing without audio",
    }


def node_scene_codegen(state: PipelineState) -> dict:
    client = get_client()
    language = get_language(state["language_name"])
    scene_index = state["scene_index"]
    total_scenes = len(state["video_data"])
    scene_data = state["video_data"][scene_index - 1]
    audio_duration = state["audio_durations"].get(scene_index)

    progress = 45 + (scene_index / total_scenes) * 30

    if state["current_error"] is None:
        result = generate_manim_code(
            client,
            text=scene_data.get("text", ""),
            animation=scene_data.get("animation", ""),
            index=scene_index,
            previous_context=state["previous_context"],
            audio_duration=audio_duration,
            font=language.font,
        )
        message = f"Generating code for scene {scene_index}/{total_scenes}"
    else:
        result = fix_manim_code(
            client,
            original_code=state["current_code"],
            error_message=state["current_error"],
            class_name=state["current_class_name"],
            font=language.font,
        )
        message = f"Fixing scene {scene_index} (attempt {state['repl_iteration'] + 1}/{MAX_REPL_ITERATIONS})"

    if not result:
        # Total LLM failure (not a compile error) -- give up on this scene and move on.
        print(f"[graph_pipeline] Scene {scene_index}: code generation itself failed, skipping scene")
        next_index = scene_index + 1
        return {
            "current_code": None,
            "current_error": None,
            "repl_iteration": 0,
            "scene_index": next_index,
            "next_action": "assemble" if next_index > total_scenes else "codegen",
            "progress": progress,
            "current_step": "code",
            "message": message + " -- failed, skipped",
        }

    return {
        "current_code": result.get("content", ""),
        "current_class_name": result.get("class_name", f"Scene{scene_index}"),
        "progress": progress,
        "current_step": "code",
        "message": message,
    }


def route_after_codegen(state: PipelineState) -> str:
    return "compile" if state.get("current_code") else state["next_action"]


def node_scene_compile(state: PipelineState) -> dict:
    scene_index = state["scene_index"]
    total_scenes = len(state["video_data"])
    scene_data = state["video_data"][scene_index - 1]
    topic_slug = sanitize_filename(state["topic"].lower().replace(" ", "_"))

    content_dir = Path(__file__).parent.parent / "content"
    content_dir.mkdir(exist_ok=True)
    filepath = str(content_dir / f"{topic_slug}-{state['job_id']}-{scene_index}.py")

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(state["current_code"])

    video_path, compile_error = compile_video(filepath, state["current_class_name"], topic_slug, scene_index)

    if video_path and os.path.exists(video_path):
        target_duration = state["audio_durations"].get(scene_index)
        if target_duration:
            video_path = correct_scene_duration(video_path, target_duration)

        next_index = scene_index + 1
        return {
            "generated_videos": state["generated_videos"] + [video_path],
            "succeeded_scene_indices": state["succeeded_scene_indices"] + [scene_index],
            "previous_context": {
                "text": scene_data.get("text", ""),
                "animation": scene_data.get("animation", ""),
                "code": state["current_code"],
            },
            "scene_index": next_index,
            "current_error": None,
            "current_code": None,
            "repl_iteration": 0,
            "next_action": "assemble" if next_index > total_scenes else "codegen",
            "message": f"Scene {scene_index}/{total_scenes} compiled",
        }

    # Compile failed.
    repl_iteration = state["repl_iteration"] + 1
    total_fix_calls = state["total_fix_calls"]

    if repl_iteration < MAX_REPL_ITERATIONS and total_fix_calls < MAX_FIX_CALLS_PER_JOB:
        return {
            "current_error": compile_error,
            "repl_iteration": repl_iteration,
            "total_fix_calls": total_fix_calls + 1,
            "next_action": "codegen",
            "message": f"Scene {scene_index} failed to compile, retrying fix ({repl_iteration}/{MAX_REPL_ITERATIONS})",
        }

    # Give up on this scene -- fix budget or iteration budget exhausted.
    print(f"[graph_pipeline] Scene {scene_index}: giving up after {repl_iteration} attempts")
    next_index = scene_index + 1
    return {
        "current_error": None,
        "current_code": None,
        "repl_iteration": 0,
        "scene_index": next_index,
        "next_action": "assemble" if next_index > total_scenes else "codegen",
        "message": f"Scene {scene_index} skipped (unfixable)",
    }


def route_after_compile(state: PipelineState) -> str:
    return state["next_action"]


def node_assemble(state: PipelineState) -> dict:
    if not state["generated_videos"]:
        return {"status": "failed", "error": "No scenes were generated", "message": "Failed: no scenes generated"}

    media_dir = Path(__file__).parent.parent / "media"
    media_dir.mkdir(exist_ok=True)

    silent_path = str(media_dir / f"output_silent_{state['job_id']}.mp4")
    if not concatenate_videos(state["generated_videos"], silent_path):
        return {"status": "failed", "error": "Failed to concatenate videos", "message": "Failed: concat error"}

    final_path = str(media_dir / f"output_{state['job_id']}.mp4")

    # Rebuild the audio track from only the scenes that actually made it into generated_videos.
    # Using the original full audio_path (every scene's narration) here would desync video and
    # audio the moment any scene gets skipped -- the video would be shorter than the narration.
    job_media_dir = media_dir / state["job_id"]
    surviving_fragments = [
        str(job_media_dir / "audio_fragments" / f"fragment_{i}.wav")
        for i in state["succeeded_scene_indices"]
        if (job_media_dir / "audio_fragments" / f"fragment_{i}.wav").exists()
    ]

    synced_audio_path = None
    if surviving_fragments:
        synced_audio_path = str(job_media_dir / "audio_synced.wav")
        if not concatenate_audio_fragments(surviving_fragments, synced_audio_path):
            synced_audio_path = None

    if synced_audio_path and os.path.exists(synced_audio_path):
        if not merge_video_and_audio(silent_path, synced_audio_path, final_path):
            final_path = silent_path
    else:
        os.rename(silent_path, final_path)

    return {
        "final_video_path": final_path,
        "status": "completed",
        "progress": 100,
        "current_step": "video",
        "message": "Video generation completed!",
    }


def build_graph(checkpointer):
    builder = StateGraph(PipelineState)

    builder.add_node("script", node_script)
    builder.add_node("audio", node_audio)
    builder.add_node("codegen", node_scene_codegen)
    builder.add_node("compile", node_scene_compile)
    builder.add_node("assemble", node_assemble)

    builder.add_edge(START, "script")
    builder.add_edge("script", "audio")
    builder.add_edge("audio", "codegen")
    builder.add_conditional_edges("codegen", route_after_codegen, {"compile": "compile", "codegen": "codegen", "assemble": "assemble"})
    builder.add_conditional_edges("compile", route_after_compile, {"codegen": "codegen", "assemble": "assemble"})
    builder.add_edge("assemble", END)

    return builder.compile(checkpointer=checkpointer)
