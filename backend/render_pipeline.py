"""Manim compilation + ffmpeg concat/merge + duration correction. Replaces concat_video.py.

New vs. the original: correct_scene_duration() closes the gap the original left open -- the LLM was
asked to hit an exact duration via prompt instructions alone, with no verification. This measures the
actual rendered duration and rescales the clip to match the audio if it drifted past tolerance.
"""

import os
import re
import subprocess


def sanitize_filename(filename: str) -> str:
    sanitized = re.sub(r"['\"\?!:;,\(\)\[\]\{\}]", "", filename)
    sanitized = re.sub(r"_+", "_", sanitized)
    return sanitized.strip("_")


def compile_video(file_path: str, class_name: str, topic_slug: str, index: int) -> tuple[str | None, str | None]:
    try:
        cmd = ["manim", "-ql", file_path, class_name]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

        if result.returncode == 0:
            filename_without_ext = os.path.splitext(os.path.basename(file_path))[0]
            video_path = f"media/videos/{filename_without_ext}/480p15/{class_name}.mp4"
            return video_path, None

        return None, result.stderr

    except subprocess.TimeoutExpired:
        return None, "Timeout: Compilation took more than 5 minutes"
    except Exception as e:
        return None, str(e)


def get_video_duration(video_path: str) -> float | None:
    try:
        cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            video_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            return float(result.stdout.strip())
        return None
    except Exception:
        return None


def correct_scene_duration(video_path: str, target_duration: float, tolerance: float = 0.3) -> str:
    """Rescales video_path in place if its actual duration drifts from target_duration past tolerance.

    Returns the (possibly unchanged) video_path.
    """
    actual_duration = get_video_duration(video_path)
    if actual_duration is None or target_duration is None:
        return video_path

    drift = abs(actual_duration - target_duration)
    if drift <= tolerance:
        return video_path

    factor = actual_duration / target_duration
    corrected_path = video_path.replace(".mp4", "_synced.mp4")

    print(
        f"  [render_pipeline] Duration drift {drift:.2f}s (actual={actual_duration:.2f}s, "
        f"target={target_duration:.2f}s) -- rescaling by factor {factor:.4f}"
    )

    cmd = [
        "ffmpeg",
        "-i", video_path,
        "-filter:v", f"setpts={factor}*PTS",
        "-an",
        corrected_path,
        "-y",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode == 0:
        return corrected_path

    print(f"  [render_pipeline] Duration correction failed, keeping original: {result.stderr}")
    return video_path


def concatenate_videos(video_paths: list[str], output_path: str) -> bool:
    if not video_paths:
        return False

    os.makedirs(os.path.dirname(output_path) or "media", exist_ok=True)
    list_file = os.path.join(os.path.dirname(output_path) or "media", "video_list.txt")

    with open(list_file, "w") as f:
        for p in video_paths:
            if os.path.exists(p):
                f.write(f"file '{os.path.abspath(p)}'\n")

    try:
        cmd = ["ffmpeg", "-f", "concat", "-safe", "0", "-i", list_file, "-c", "copy", output_path, "-y"]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            os.remove(list_file)
            return True
        print(f"[ERROR] Error concatenating videos: {result.stderr}")
        return False
    except Exception as e:
        print(f"[ERROR] {e}")
        return False


def merge_video_and_audio(video_path: str, audio_path: str, output_path: str) -> bool:
    if not (os.path.exists(video_path) and os.path.exists(audio_path)):
        return False

    try:
        cmd = [
            "ffmpeg",
            "-i", video_path,
            "-i", audio_path,
            "-c:v", "copy",
            "-c:a", "aac",
            "-map", "0:v:0",
            "-map", "1:a:0",
            output_path,
            "-y",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            return True
        print(f"[ERROR] Error merging video and audio: {result.stderr}")
        return False
    except Exception as e:
        print(f"[ERROR] {e}")
        return False
