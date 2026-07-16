"""Explicit language configuration shared by script-gen, Sarvam TTS, and Manim codegen.

Language is passed as a parameter end-to-end instead of being inferred from the topic text --
inference breaks for TTS (needs an explicit target_language_code) and for Manim's Text()/Paragraph()
rendering of non-Latin scripts (needs a font with the right glyph coverage, or you get tofu boxes).
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Language:
    display_name: str
    sarvam_code: str
    font: str | None  # None means default Manim font (Latin scripts render fine without one)


LANGUAGES: dict[str, Language] = {
    "English": Language("English", "en-IN", None),
    "Bengali": Language("Bengali", "bn-IN", "Noto Sans Bengali"),
    "Hindi": Language("Hindi", "hi-IN", "Noto Sans Devanagari"),
    "Gujarati": Language("Gujarati", "gu-IN", "Noto Sans Gujarati"),
    "Kannada": Language("Kannada", "kn-IN", "Noto Sans Kannada"),
    "Malayalam": Language("Malayalam", "ml-IN", "Noto Sans Malayalam"),
    "Marathi": Language("Marathi", "mr-IN", "Noto Sans Devanagari"),
    "Odia": Language("Odia", "od-IN", "Noto Sans Oriya"),
    "Punjabi": Language("Punjabi", "pa-IN", "Noto Sans Gurmukhi"),
    "Tamil": Language("Tamil", "ta-IN", "Noto Sans Tamil"),
    "Telugu": Language("Telugu", "te-IN", "Noto Sans Telugu"),
}

DEFAULT_LANGUAGE = "Bengali"

SARVAM_SPEAKERS_V3 = [
    "shubh", "aditya", "ritu", "priya", "neha", "rahul", "pooja", "rohan",
    "simran", "kavya", "amit", "dev", "ishita", "shreya", "ratan", "varun",
]

DEFAULT_SPEAKER = "shubh"


def get_language(name: str) -> Language:
    if name not in LANGUAGES:
        raise ValueError(f"Unsupported language: {name}. Supported: {list(LANGUAGES)}")
    return LANGUAGES[name]
