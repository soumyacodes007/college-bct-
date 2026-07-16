"""Sarvam TTS provider. Replaces topic2manim's OpenAI-only tts_generator.py.

Schema confirmed against live docs.sarvam.ai (2026-07):
  POST https://api.sarvam.ai/text-to-speech
  header: api-subscription-key
  body: {text, target_language_code, speaker, model, pace, speech_sample_rate, output_audio_codec}
  response: {request_id, audios: [base64-encoded audio]}
"""

import base64
import os
import subprocess

import requests
from langsmith import traceable

SARVAM_TTS_URL = "https://api.sarvam.ai/text-to-speech"


def get_audio_duration(audio_path: str) -> float | None:
    try:
        cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            audio_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            return float(result.stdout.strip())
        print(f"  [voice_synth] Could not get duration for {audio_path}")
        return None
    except Exception as e:
        print(f"  [voice_synth] Error getting audio duration: {e}")
        return None


@traceable(name="sarvam_tts_call", run_type="tool")
def generate_audio_fragment(
    text: str,
    index: int,
    language_code: str,
    speaker: str,
    output_dir: str = "media/audio_fragments",
    pace: float = 1.0,
) -> tuple[str | None, float | None]:
    api_key = os.getenv("SARVAM_API_KEY")
    if not api_key:
        raise ValueError("SARVAM_API_KEY is not set in the environment")

    os.makedirs(output_dir, exist_ok=True)
    audio_path = os.path.join(output_dir, f"fragment_{index}.wav")

    print(f"  Generating audio fragment {index} ({language_code})...")

    try:
        response = requests.post(
            SARVAM_TTS_URL,
            headers={"api-subscription-key": api_key, "Content-Type": "application/json"},
            json={
                "text": text,
                "target_language_code": language_code,
                "speaker": speaker,
                "model": "bulbul:v3",
                "pace": pace,
                "speech_sample_rate": "24000",
                "output_audio_codec": "wav",
            },
            timeout=60,
        )
        response.raise_for_status()
        data = response.json()

        audio_b64 = data["audios"][0]
        with open(audio_path, "wb") as f:
            f.write(base64.b64decode(audio_b64))

        duration = get_audio_duration(audio_path)
        print(f"  [OK] Audio fragment saved: {audio_path} (duration: {duration})")
        return audio_path, duration

    except Exception as e:
        print(f"  [ERROR] Sarvam TTS failed for fragment {index}: {e}")
        return None, None


def concatenate_audio_fragments(audio_paths: list[str], output_path: str = "media/audio.wav") -> bool:
    if not audio_paths:
        print("[ERROR] No audio fragments to concatenate")
        return False

    os.makedirs(os.path.dirname(output_path) or "media", exist_ok=True)
    list_file = os.path.join(os.path.dirname(output_path) or "media", "audio_list.txt")

    with open(list_file, "w") as f:
        for p in audio_paths:
            if os.path.exists(p):
                f.write(f"file '{os.path.abspath(p)}'\n")

    try:
        cmd = ["ffmpeg", "-f", "concat", "-safe", "0", "-i", list_file, "-c", "copy", output_path, "-y"]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            os.remove(list_file)
            return True
        print(f"[ERROR] Error concatenating audio: {result.stderr}")
        return False
    except Exception as e:
        print(f"[ERROR] {e}")
        return False


def generate_complete_audio(
    video_data: list[dict], language_code: str, speaker: str, media_dir: str = "media"
) -> tuple[str | None, dict[int, float]]:
    audio_fragments = []
    audio_durations: dict[int, float] = {}

    for index, scene_data in enumerate(video_data, 1):
        text = scene_data.get("text", "")
        if not text:
            continue

        audio_path, duration = generate_audio_fragment(
            text=text,
            index=index,
            language_code=language_code,
            speaker=speaker,
            output_dir=os.path.join(media_dir, "audio_fragments"),
        )

        if audio_path and os.path.exists(audio_path):
            audio_fragments.append(audio_path)
            if duration:
                audio_durations[index] = duration

    if not audio_fragments:
        return None, {}

    output_path = os.path.join(media_dir, "audio.wav")
    if concatenate_audio_fragments(audio_fragments, output_path):
        return output_path, audio_durations
    return None, {}
