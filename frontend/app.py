"""Streamlit UI for Conceptreel. Talks to the FastAPI backend over HTTP only -- no direct pipeline
imports, matching the two-process split (uvicorn + streamlit) from docker-compose.yml.
"""

import os
import time

import requests
import streamlit as st

# BACKEND_URL is used for server-side calls Streamlit itself makes (job polling, etc.) -- inside
# Docker this is the internal service hostname (http://backend:8000), reachable only within the
# compose network. PUBLIC_BACKEND_URL is what gets embedded in the <video> tag's src, which the
# user's actual browser has to resolve -- it needs the host-reachable address instead.
BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")
PUBLIC_BACKEND_URL = os.getenv("PUBLIC_BACKEND_URL", "http://localhost:8000")

st.set_page_config(page_title="Conceptreel", page_icon="🎬")
st.title("🎬 Conceptreel")
st.caption("Topic → narrated Manim video, in any Sarvam-supported language.")


@st.cache_data(ttl=60)
def get_languages():
    try:
        r = requests.get(f"{BACKEND_URL}/api/languages", timeout=5)
        r.raise_for_status()
        return r.json()
    except Exception:
        return {"languages": ["Bengali", "English", "Hindi"], "default": "Bengali"}


languages = get_languages()

if "job_id" not in st.session_state:
    st.session_state.job_id = None

generate_tab, old_videos_tab = st.tabs(["Generate", "Old videos"])

with generate_tab:
    with st.form("generate_form"):
        topic = st.text_input("Topic", placeholder="How does photosynthesis work?")
        language = st.selectbox(
            "Language", languages["languages"],
            index=languages["languages"].index(languages["default"]) if languages["default"] in languages["languages"] else 0,
        )
        duration = st.slider("Target duration (seconds)", min_value=20, max_value=60, value=30, step=5)
        speaker = st.selectbox(
            "Voice", ["shubh", "aditya", "ritu", "priya", "neha", "rahul", "pooja", "rohan"],
        )
        submitted = st.form_submit_button("Generate video")

    if submitted:
        if not topic.strip():
            st.error("Please enter a topic.")
        else:
            try:
                r = requests.post(
                    f"{BACKEND_URL}/api/generate",
                    json={"topic": topic, "language": language, "target_duration": duration, "speaker": speaker},
                    timeout=10,
                )
                r.raise_for_status()
                st.session_state.job_id = r.json()["job_id"]
            except Exception as e:
                st.error(f"Failed to start generation: {e}")

    if st.session_state.job_id:
        job_id = st.session_state.job_id
        progress_bar = st.progress(0)
        status_box = st.empty()

        while True:
            try:
                r = requests.get(f"{BACKEND_URL}/api/jobs/{job_id}", timeout=10)
                r.raise_for_status()
                job = r.json()
            except Exception as e:
                status_box.error(f"Lost connection to backend: {e}")
                break

            progress_bar.progress(min(int(job.get("progress") or 0), 100))
            status_box.info(f"[{job.get('current_step')}] {job.get('message')}")

            if job.get("status") == "completed":
                status_box.success("Done!")
                st.video(f"{PUBLIC_BACKEND_URL}{job['video_url']}")
                st.session_state.job_id = None
                break

            if job.get("status") == "failed":
                status_box.error(f"Generation failed: {job.get('error')}")
                st.session_state.job_id = None
                break

            time.sleep(2)

with old_videos_tab:
    if st.button("Refresh"):
        st.cache_data.clear()

    @st.cache_data(ttl=15)
    def get_videos():
        r = requests.get(f"{BACKEND_URL}/api/videos", timeout=10)
        r.raise_for_status()
        return r.json()["videos"]

    try:
        videos = get_videos()
    except Exception as e:
        videos = []
        st.error(f"Could not load videos: {e}")

    if not videos:
        st.info("No videos generated yet.")

    for v in videos:
        label = v.get("topic") or v["job_id"]
        with st.expander(f"{label} — {v.get('language') or ''} — {v['created_at'][:19].replace('T', ' ')}"):
            st.video(f"{PUBLIC_BACKEND_URL}{v['video_url']}")
