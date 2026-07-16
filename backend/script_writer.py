"""Generates the scene script (narration + animation description per scene).

Replaces topic2manim's animations.py. Same prompt shape (it's tuned and works), but language is an
explicit parameter instead of being inferred from the topic text, and target duration drives scene
count instead of a hardcoded 60s cap.
"""

import json
import re

from openai import OpenAI

from languages import Language
from llm_client import call_llm


def _estimate_scene_count(target_duration_sec: int) -> tuple[int, int]:
    # ~6-8s per scene, same pacing topic2manim used, just derived from target duration now
    low = max(2, round(target_duration_sec / 8))
    high = max(low, round(target_duration_sec / 6))
    return low, high


def generate_script(client: OpenAI, topic: str, language: Language, target_duration_sec: int = 30) -> list[dict]:
    """Generates the scene list. Returns [{"text": ..., "animation": ...}, ...] or raises."""

    scenes_low, scenes_high = _estimate_scene_count(target_duration_sec)

    prompt = f"""Develop an educational script for this topic: {topic}

INSTRUCTIONS:
- Create an engaging and educational script about the topic
- Divide the script into logical scenes/fragments (between {scenes_low}-{scenes_high} scenes)
- For each scene, provide:
  1. The script text (narration) - BRIEF and CONCISE
  2. A detailed description of the Manim animation that should accompany that text
- Avoid using commercial logos (like ChatGPT, OpenAI, etc.)
- I DON'T want Python Manim code, just the description of what you want to visualize
- Animations should be specific and detailed so they can be implemented in Manim

LANGUAGE REQUIREMENT:
- Write the ENTIRE script (all "text" fields) natively and fluently in {language.display_name}.
- Do not write in English and translate literally -- write as a native {language.display_name} speaker would.
- The "animation" field descriptions may stay in English (they are internal instructions, never shown to viewers).

CRITICAL TIME RESTRICTION:
- The COMPLETE video must last MAXIMUM {target_duration_sec} seconds
- Each scene should last approximately 6-8 seconds
- The text of each scene must be SHORT (maximum 2-3 sentences)
- Animations must be SIMPLE and FAST

OUTPUT FORMAT (JSON):
Respond ONLY with a valid JSON array, where each element has this structure:
{{
  "text": "script text for this scene in {language.display_name} (BRIEF, 2-3 sentences maximum)",
  "animation": "detailed description of the specific animation for this fragment"
}}

IMPORTANT: Respond ONLY with the JSON array, without any additional text before or after."""

    system = (
        "You are an expert in creating educational video scripts. You always respond in valid JSON "
        f"format without additional text. Write all narration text natively in {language.display_name}."
    )

    response_text = call_llm(client, system, prompt)

    if "```json" in response_text:
        response_text = re.search(r"```json\s*(.*?)\s*```", response_text, re.DOTALL).group(1)
    elif "```" in response_text:
        response_text = re.search(r"```\s*(.*?)\s*```", response_text, re.DOTALL).group(1)

    return json.loads(response_text)
