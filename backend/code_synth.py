"""Generates and fixes Manim scene code. Replaces topic2manim's manim_generator.py.

Prompt rules (colors, overlap avoidance, text width) are kept as-is from the original -- they're
tuned against real compile failures and proven by the README's working examples. What's new: an
explicit font instruction for non-Latin scripts, threaded from languages.py instead of hoping the
LLM picks a sane default (it won't -- Manim's default font has no Bengali/Devanagari glyphs).
"""

import json
import re
import time

from openai import OpenAI

from llm_client import call_llm

# call_llm only retries on transport failures or a fully-empty response; a whitespace-only or
# garbage (non-JSON) response passes that check and only fails later at parse time here, so this
# retry wraps the whole generate-then-parse cycle instead of relying on call_llm's retry alone.
MAX_PARSE_RETRIES = 3

_FONT_RULES_TEMPLATE = """
FONT REQUIREMENT (CRITICAL):
- This scene's text is in a non-Latin script. EVERY Text() and Paragraph() object MUST include font="{font}".
- Example: Text("Some text", font="{font}", font_size=40)
- Without this font, the glyphs will not render correctly.
"""

_TECHNICAL_RULES = """
IMPORTANT TECHNICAL RESTRICTIONS:
1. The class MUST inherit from Scene (not MovingCameraScene, not ThreeDScene)
2. DO NOT use self.camera.frame (doesn't exist in Scene)
3. For zoom, use: object.animate.scale(factor) instead of camera.frame
4. Keep animations SIMPLE and FUNCTIONAL
5. Use only basic animations: Write, Create, FadeIn, FadeOut, Transform, ReplacementTransform
6. Avoid complex 3D animations
7. If you need camera movement, use self.play(self.camera.animate.move_to(...)) but WITHOUT .frame
8. NEVER create empty Text or Paragraph objects (Text('') or Paragraph(''))
9. NEVER use positioning methods (.move_to(), .align_to(), .next_to()) on empty Text/Paragraph objects
10. If you need placeholder text, use actual text like Text("Placeholder") instead of Text('')

CRITICAL COLOR USAGE RULES:
1. ONLY use these basic colors that are always available: WHITE, BLACK, RED, GREEN, BLUE, YELLOW, PURPLE, ORANGE, PINK, GRAY
2. DO NOT use color variants like RED_A, RED_B, ORANGE_D, BLUE_E, etc. (they may not be imported)
3. If you need custom colors, use hex codes: color="#FF5733" or RGB: rgb_to_color([1, 0.5, 0.2])
4. For gradients or multiple colors, stick to the basic colors listed above

CRITICAL RULES TO AVOID TEXT OVERLAP:
1. ALWAYS use FadeOut() to remove old elements BEFORE showing new ones
2. If showing multiple texts/objects, position them in DIFFERENT places (UP, DOWN, LEFT, RIGHT)
3. Use self.clear() if you need to clear the entire scene
4. DO NOT write new text over existing text without removing it first
5. Keep a maximum of 2-3 text elements on screen simultaneously
6. Use .to_edge(UP/DOWN) or .shift(UP/DOWN) to separate elements vertically

RULES TO CONTROL TEXT WIDTH:
1. For LONG texts (>80 characters), use Paragraph() instead of Text()
2. Use the width parameter to limit width: Text("...", width=10) or Paragraph("...", width=11)
3. Appropriate font size: font_size=24-36 for long texts, 40-48 for short titles
4. If the text is VERY long, divide it into multiple Text/Paragraph objects
5. Maximum recommended width is width=12 (to leave margins)

RESPONSE FORMAT (JSON):
{
  "content": "complete Python code here (use single quotes inside the code)",
  "class_name": "ClassName"
}

IMPORTANT:
- The code must be executable without errors
- Escape quotes correctly in the JSON
- ALWAYS clean old elements before showing new ones
"""


def generate_manim_code(
    client: OpenAI,
    text: str,
    animation: str,
    index: int,
    previous_context: dict | None,
    audio_duration: float | None,
    font: str | None,
) -> dict | None:
    context_section = ""
    if previous_context:
        context_section = f"""
PREVIOUS SCENE CONTEXT (to maintain continuity):
- Previous text: {previous_context.get('text', 'N/A')}
- Previous animation: {previous_context.get('animation', 'N/A')}
- Previous generated code:
```python
{previous_context.get('code', 'N/A')}
```
IMPORTANT: Maintain visual and narrative coherence with the previous scene.
"""
    else:
        context_section = "\nCONTEXT: This is the FIRST scene of the video.\n"

    if audio_duration:
        duration_section = f"""
CRITICAL AUDIO SYNCHRONIZATION:
- This scene has an audio narration that lasts EXACTLY {audio_duration:.2f} seconds
- Your animation should target approximately {audio_duration:.2f} seconds
  (exact sync is corrected automatically after rendering, so prioritize looking good over
  hitting the number precisely)
"""
    else:
        duration_section = """
TIMING GUIDANCE:
- This scene should last approximately 6-8 seconds
- Use short run_time in animations (0.5-1.5 seconds)
"""

    font_section = _FONT_RULES_TEMPLATE.format(font=font) if font else ""

    prompt = f"""{context_section}
Generate Python code for Manim that implements this educational animation.

CURRENT CONTENT:
- Narrative text: {text}
- Animation description: {animation}

{duration_section}
{font_section}
{_TECHNICAL_RULES}"""

    system = (
        "You are an expert in Manim Community Edition (v0.19.1). You generate simple, functional "
        "Python code without errors. NEVER use self.camera.frame in Scene. Always respond in valid JSON format."
    )

    for attempt in range(MAX_PARSE_RETRIES):
        try:
            response_text = call_llm(client, system, prompt)
        except Exception as e:
            print(f"[code_synth] Scene {index} generation failed (attempt {attempt + 1}/{MAX_PARSE_RETRIES}): {e}")
            response_text = None

        result = _parse_code_response(response_text, index) if response_text else None
        if result:
            return result

        if attempt < MAX_PARSE_RETRIES - 1:
            print(f"[code_synth] Scene {index}: retrying generation ({attempt + 2}/{MAX_PARSE_RETRIES})")
            time.sleep(2 ** attempt)

    return None


def fix_manim_code(client: OpenAI, original_code: str, error_message: str, class_name: str, font: str | None) -> dict | None:
    font_section = _FONT_RULES_TEMPLATE.format(font=font) if font else ""

    fix_prompt = f"""The following Manim code failed to compile with an error. Please fix the code.

CURRENT CODE:
```python
{original_code}
```

ERROR MESSAGE:
```
{error_message}
```

IMPORTANT RULES:
1. Fix ONLY the error mentioned - don't change working parts
2. The class MUST inherit from Scene (not MovingCameraScene, not ThreeDScene)
3. DO NOT use self.camera.frame (doesn't exist in Scene)
4. ONLY use basic colors: WHITE, BLACK, RED, GREEN, BLUE, YELLOW, PURPLE, ORANGE, PINK, GRAY
5. NEVER create empty Text or Paragraph objects
6. Use only basic animations: Write, Create, FadeIn, FadeOut, Transform, ReplacementTransform
{font_section}
RESPONSE FORMAT (JSON):
{{
  "content": "complete fixed Python code here",
  "class_name": "{class_name}",
  "fix_explanation": "brief explanation of what was fixed"
}}

Respond ONLY with valid JSON."""

    system = (
        "You are an expert debugger for Manim Community Edition (v0.19.1). You fix Python code "
        "errors. Always respond in valid JSON format."
    )

    for attempt in range(MAX_PARSE_RETRIES):
        try:
            response_text = call_llm(client, system, fix_prompt)
        except Exception as e:
            print(f"[code_synth] Fix for {class_name} failed (attempt {attempt + 1}/{MAX_PARSE_RETRIES}): {e}")
            response_text = None

        result = _parse_code_response(response_text, class_name) if response_text else None
        if result:
            print(f"[code_synth] Fix applied: {result.get('fix_explanation', 'no explanation')}")
            return result

        if attempt < MAX_PARSE_RETRIES - 1:
            print(f"[code_synth] Fix for {class_name}: retrying ({attempt + 2}/{MAX_PARSE_RETRIES})")
            time.sleep(2 ** attempt)

    return None


def _parse_code_response(response_text: str, label) -> dict | None:
    if "```json" in response_text:
        response_text = re.search(r"```json\s*(.*?)\s*```", response_text, re.DOTALL).group(1)
    elif "```" in response_text:
        response_text = re.search(r"```\s*(.*?)\s*```", response_text, re.DOTALL).group(1)

    try:
        return json.loads(response_text)
    except json.JSONDecodeError as e:
        print(f"[code_synth] Failed to parse JSON for {label}: {e}")
        return None
