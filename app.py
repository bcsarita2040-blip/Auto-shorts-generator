import streamlit as st  # Built by Adrien Treuille, Thiago Teixeira, & Amanda Kelly
import os
import io
import json
import re
from typing import Any

from duckduckgo_search import DDGS  # Built by deedy5
from google import genai  # Built by Google DeepMind
from google.genai import types
from elevenlabs.client import ElevenLabs  # Built by Mati Staniszewski & Piotr Dabkowski
from pydub import AudioSegment, silence, effects  # Built by James Robert

st.set_page_config(page_title="RoRants Studio", page_icon="🎙️", layout="wide")

ELEVENLABS_ADAM_VOICE_ID_FALLBACK = "pNInz6obpgDQGcFmaJgB"
STOCK_PHOTO_EXCLUSION = ['freepik', 'alamy', 'shutterstock', 'gettyimages', 'stock', 'dreamstime', 'pinterest']
GEMINI_MODEL_CANDIDATES = ["gemini-flash-latest", "gemini-3.5-flash", "gemini-2.5-flash"]

# Session State Setup
DEFAULT_STATE = {
    "user_script": "",
    "meme_moments": [],
    "meme_images": {},
    "audio_bytes": None,
    "audio_duration_ms": None,
}
for _key, _default in DEFAULT_STATE.items():
    if _key not in st.session_state:
        st.session_state[_key] = _default

st.title("🎙️ RoRants Studio (Direct Script Mode)")
st.caption("Paste your raw script → auto-find word-anchored memes → generate exact-length chipmunk voiceover.")


# ---------- Gemini Meme Anchor Helpers ----------

def _generate_with_fallback(client, prompt, json_mode=False, schema=None):
    config = types.GenerateContentConfig(response_mime_type="application/json", response_schema=schema) if json_mode else None
    last_error = None
    for model_name in GEMINI_MODEL_CANDIDATES:
        try:
            if config:
                response = client.models.generate_content(model=model_name, contents=prompt, config=config)
            else:
                response = client.models.generate_content(model=model_name, contents=prompt)
            return response.text
        except Exception as e:
            last_error = e
            continue
    raise last_error


def find_meme_moments(client, script_text):
    """Numbers every word of YOUR script so Gemini references real index placements."""
    words = script_text.split()
    if not words:
        return []
    numbered = " ".join(f"[{i}]{w}" for i, w in enumerate(words))
    prompt = f"""
    Here is a script with each word numbered by its index, format [index]word:
    {numbered}

    Identify 3 to 5 key dramatic or meme-able moments in this exact script. For each, return:
    - search_term: a clean 2-8 word image search query (e.g. "crying cat meme png")
    - start_anchor: the exact word text where the meme should start showing
    - end_anchor: the exact word text where it should stop showing
    - start_word: the [index] number of start_anchor
    - end_word: the [index] number of end_anchor
    """
    schema = {
        "type": "object",
        "properties": {
            "moments": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "search_term": {"type": "string"},
                        "start_anchor": {"type": "string"},
                        "end_anchor": {"type": "string"},
                        "start_word": {"type": "integer"},
                        "end_word": {"type": "integer"},
                    },
                    "required": ["search_term", "start_anchor", "end_anchor", "start_word", "end_word"],
                },
            }
        },
        "required": ["moments"],
    }
    try:
        raw = _generate_with_fallback(client, prompt, json_mode=True, schema=schema)
        moments = json.loads(raw).get("moments", [])
    except Exception:
        return []

    cleaned = []
    for m in moments:
        try:
            s, e = int(m["start_word"]), int(m["end_word"])
            s = max(0, min(s, len(words) - 1))
            e = max(s, min(e, len(words) - 1))
            cleaned.append({**m, "start_word": s, "end_word": e})
        except Exception:
            continue
    return cleaned


def find_meme_images(query, max_results=5):
    """Fetches non-stock meme image previews using DuckDuckGo."""
    try:
        with DDGS() as ddg:
            results = list(ddg.images(query, max_results=max_results * 3))
    except Exception:
        return []
    filtered = []
    for r in results:
        url = (r.get("image") or "").lower()
        if any(bad in url for bad in STOCK_PHOTO_EXCLUSION):
            continue
        filtered.append(r)
        if len(filtered) >= max_results:
            break
    return filtered


# ---------- ElevenLabs & Audio Helpers ----------

def resolve_voice_id(client, name="Adam"):
    try:
        results = client.voices.search(search=name)
        if results.voices:
            return results.voices[0].voice_id
    except Exception:
        pass
    return ELEVENLABS_ADAM_VOICE_ID_FALLBACK


def chipmunk_speed(audio_segment, speed=1.15):
    if speed == 1.0:
        return audio_segment
    new_frame_rate = int(audio_segment.frame_rate * speed)
    sped_up = audio_segment._spawn(audio_segment.raw_data, overrides={'frame_rate': new_frame_rate})
    return sped_up.set_frame_rate(audio_segment.frame_rate)


def strip_gaps(sound):
    """Strips silences safely without cutting off word endings."""
    chunks = silence.split_on_silence(sound, min_silence_len=200, silence_thresh=-40, keep_silence=50)
    combined = AudioSegment.empty()
    for chunk in chunks:
        combined += chunk
    return combined


def _consume_audio_stream(audio_stream):
    if isinstance(audio_stream, (bytes, bytearray)):
        return bytes(audio_stream)
    return b"".join(audio_stream)


def export_exact_duration_mp3(sound, target_ms, path, max_iterations=4):
    """Pads short audio with trailing silence or trims long audio to match target timeline exactly."""
    def fit_segment(seg, length):
        if len(seg) >= length:
            return seg[:length]
        pad = AudioSegment.silent(
            duration=length - len(seg),
            frame_rate=seg.frame_rate
        ).set_channels(seg.channels).set_sample_width(seg.sample_width)
        return (seg + pad)[:length]

    working = fit_segment(sound, target_ms)

    for _ in range(max_iterations):
        working.export(path, format="mp3", bitrate="128k")
        reloaded = AudioSegment.from_mp3(path)
        drift = target_ms - len(reloaded)

        if abs(drift) <= 20:
            return reloaded, path

        new_length = max(1, len(working) + drift)
        working = fit_segment(working, new_length)

    working.export(path, format="mp3", bitrate="128k")
    reloaded = AudioSegment.from_mp3(path)
    return reloaded, path


def generate_voice(eleven_client, script_text, voice_id, voice_speed, duration_seconds):
    """Sends YOUR exact script directly to ElevenLabs without modifying a single word."""
    audio_stream = eleven_client.text_to_speech.convert(
        voice_id=voice_id, text=script_text, model_id="eleven_multilingual_v2", output_format="mp3_44100_128",
    )
    raw_bytes = _consume_audio_stream(audio_stream)
    raw_sound = AudioSegment.from_file(io.BytesIO(raw_bytes), format="mp3")

    gapped = strip_gaps(raw_sound)
    normalized = effects.normalize(gapped)
    sped = chipmunk_speed(normalized, voice_speed)

    target_ms = int(duration_seconds * 1000)
    tmp_path = "temp_voice_export.mp3"
    final_sound, exported_path = export_exact_duration_mp3(sped, target_ms, tmp_path)
    with open(exported_path, "rb") as f:
        final_bytes = f.read()
    if os.path.exists(exported_path):
        os.remove(exported_path)
    return final_bytes, len(final_sound)


# ---------- Sidebar Controls ----------

with st.sidebar:
    st.header("🔑 Engine Keys")
    gemini_key = st.text_input("Gemini API Key", type="password").strip()
    eleven_key = st.text_input("ElevenLabs API Key", type="password").strip()

# ---------- Main Workspace ----------

raw_script = st.text_area(
    "✍️ Paste Your Script Here (Will NOT be altered or rewritten)",
    placeholder="Throwback to when this guy...",
    height=200
)

col_a, col_b = st.columns(2)
with col_a:
    duration_seconds = st.slider("⏱️ Target Audio Duration (seconds)", min_value=5, max_value=300, value=50, step=1)
with col_b:
    voice_speed = st.slider("🐿️ Chipmunk Voice Speed", min_value=1.0, max_value=1.5, value=1.20, step=0.05)

word_count = len(raw_script.split())
st.caption(f"Current word count: **{word_count} words**. (Recommended for ~{duration_seconds}s at {voice_speed}x speed: ~170–190 words).")

col_btn1, col_btn2 = st.columns(2)
with col_btn1:
    fetch_memes = st.button("🖼️ Extract Meme Moments", use_container_width=True)
with col_btn2:
    run_voice = st.button("🎙️ Generate Voiceover", use_container_width=True)


# ---------- Meme Extraction Trigger ----------

if fetch_memes:
    if not raw_script.strip():
        st.error("Paste your script into the text area first!")
    elif not gemini_key:
        st.error("Enter your Gemini API key in the sidebar.")
    else:
        with st.spinner("Analyzing script for meme moments..."):
            try:
                gemini_client = genai.Client(api_key=gemini_key)
                moments = find_meme_moments(gemini_client, raw_script)
                st.session_state.meme_moments = moments
                images = {}
                for m in moments:
                    images[m["search_term"]] = find_meme_images(m["search_term"])
                st.session_state.meme_images = images
                st.success(f"Found {len(moments)} meme moments.")
            except Exception as e:
                st.error(f"❌ Meme extraction failed: {e}")

# Display Memes
if st.session_state.meme_moments:
    st.subheader("🖼️ Word-Anchored Meme Moments")
    for m in st.session_state.meme_moments:
        with st.expander(f"\"{m['start_anchor']}\" → \"{m['end_anchor']}\" (words {m['start_word']}-{m['end_word']}) — {m['search_term']}"):
            images = st.session_state.meme_images.get(m["search_term"], [])
            if not images:
                st.caption("No clean results found for this query.")
            else:
                cols = st.columns(len(images))
                for col, img in zip(cols, images):
                    with col:
                        st.image(img.get("thumbnail") or img.get("image"), use_container_width=True)
                        st.markdown(f"[Full image]({img.get('image')})")


# ---------- Voice Generation Trigger ----------

if run_voice:
    if not raw_script.strip():
        st.error("Paste your script into the text area first!")
    elif not eleven_key:
        st.error("Enter your ElevenLabs API key in the sidebar.")
    else:
        with st.spinner("Generating pitch-shifted voiceover..."):
            try:
                eleven_client = ElevenLabs(api_key=eleven_key)
                voice_id = resolve_voice_id(eleven_client, "Adam")
                audio_bytes, achieved_ms = generate_voice(
                    eleven_client, raw_script, voice_id, voice_speed, duration_seconds
                )
                st.session_state.audio_bytes = audio_bytes
                st.session_state.audio_duration_ms = achieved_ms
                st.success(f"Voice ready — {achieved_ms / 1000:.2f}s audio generated.")
            except Exception as e:
                st.error(f"❌ Voice generation failed: {e}")

# Display Audio Player
if st.session_state.audio_bytes:
    st.subheader("🎧 Voiceover Preview")
    st.audio(st.session_state.audio_bytes, format="audio/mp3")
    st.download_button("📥 DOWNLOAD VOICE MP3", data=st.session_state.audio_bytes, file_name="RoRants_Voice.mp3", mime="audio/mpeg")