import streamlit as st
import os
import io
import json
import re
from datetime import datetime, timezone
from typing import Any

from duckduckgo_search import DDGS  # Built by deedy5
from google import genai  # Built by Google DeepMind
from google.genai import types
from elevenlabs.client import ElevenLabs  # Built by Mati Staniszewski & Piotr Dabkowski
from pydub import AudioSegment, silence, effects  # Built by James Robert

st.set_page_config(page_title="RoRants Studio", page_icon="🎙️", layout="wide")

WORDS_PER_SECOND = 2.5
ELEVENLABS_ADAM_VOICE_ID_FALLBACK = "pNInz6obpgDQGcFmaJgB"
STOCK_PHOTO_EXCLUSION = ['freepik', 'alamy', 'shutterstock', 'gettyimages', 'stock', 'dreamstime', 'pinterest']

GEMINI_MODEL_CANDIDATES = ["gemini-flash-latest", "gemini-3.5-flash", "gemini-2.5-flash"]

DEFAULT_STATE = {
    "final_script": "",
    "script_editor": "",
    "research_draft": "",
    "source_results": [],
    "meme_moments": [],
    "meme_images": {},
    "audio_bytes": None,
    "audio_duration_ms": None,
    "voice_stale": False,
    "last_voiced_script": "",
}
for _key, _default in DEFAULT_STATE.items():
    if _key not in st.session_state:
        st.session_state[_key] = _default

st.title("🎙️ RoRants Studio")
st.caption("Research → exact-word-count script → word-anchored meme moments → on-demand voiceover.")


# ---------- Gemini helpers ----------

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


def clean_script_text(text):
    return (text or "").replace('*', '').replace('"', '').strip()


def trim_to_exact_words(text, target_words):
    """Deterministic fallback: clip on a real word boundary, never mid-word."""
    words = text.split()
    if len(words) <= target_words:
        return text
    return " ".join(words[:target_words])


def build_research_draft(client, category, current_year):
    """Path A only. Grounds the script in real search results."""
    query = f"{category} Roblox controversy drama {current_year}"
    try:
        with DDGS() as ddg:
            news_results = list(ddg.news(query, max_results=6))
    except Exception:
        news_results = []
    try:
        with DDGS() as ddg:
            text_results = list(ddg.text(query, max_results=6))
    except Exception:
        text_results = []

    combined = news_results + text_results
    snippets = "\n".join(
        f"- {r.get('title', '')}: {(r.get('body') or r.get('excerpt') or '')[:300]}" for r in combined
    ) or "No search results were found for this topic."

    prompt = f"""
    Using ONLY the real information in these search snippets about "{category}" Roblox
    drama/controversy, write a factual, grounded research draft (700-800 words)
    summarizing what's actually happening or happened -- specific names, events, and
    details where the snippets provide them. Do not invent facts the snippets don't
    support.

    Search snippets:
    {snippets}
    """
    draft = clean_script_text(_generate_with_fallback(client, prompt))
    return draft, combined


def create_final_script(client, source_material, target_words):
    """Generates exact word count script with deterministic trimming guarantee."""
    candidates = []
    for _ in range(6):
        prev_note = ""
        if candidates:
            prev_count = len(candidates[-1].split())
            direction = "too long" if prev_count > target_words else "too short"
            prev_note = f" Your previous attempt was {prev_count} words ({direction} by {abs(prev_count - target_words)}) -- correct that."
        prompt = f"""
        Adapt the following material into a first-person "Roblox rant" story script,
        RoRants-style: hook in the first line, escalating story, punchy twist or payoff
        at the end, casual Gen-Alpha slang. No emojis, stage directions, character
        names, or brackets.

        Material:
        {source_material}

        The script MUST be EXACTLY {target_words} words. Count carefully before
        answering.{prev_note}
        Return ONLY the script text, nothing else.
        """
        try:
            text = clean_script_text(_generate_with_fallback(client, prompt))
            if text:
                candidates.append(text)
                if len(text.split()) == target_words:
                    return text
        except Exception:
            continue

    if not candidates:
        raise RuntimeError("Gemini never returned a usable script after 6 attempts.")

    over_or_equal = [c for c in candidates if len(c.split()) >= target_words]
    best = min(over_or_equal, key=lambda c: len(c.split())) if over_or_equal else max(candidates, key=lambda c: len(c.split()))
    return trim_to_exact_words(best, target_words)


def find_meme_moments(client, script_text):
    """Numbers every word so Gemini references real index placements."""
    words = script_text.split()
    if not words:
        return []
    numbered = " ".join(f"[{i}]{w}" for i, w in enumerate(words))
    prompt = f"""
    Here is a script with each word numbered by its index, format [index]word:
    {numbered}

    Identify 3 to 5 key dramatic/meme-able moments in this script. For each, return:
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
    """Filters out stock photo domains from search results."""
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


# ---------- ElevenLabs / audio helpers ----------

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
    """Prevents word endings from getting chopped off during speedup."""
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
    """Pads short audio with trailing silence or trims long audio to hit target_ms EXACTLY."""
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


# ---------- Sidebar ----------

with st.sidebar:
    st.header("🔑 Engine Keys")
    gemini_key = st.text_input("Gemini API Key", type="password").strip()
    eleven_key = st.text_input("ElevenLabs API Key", type="password").strip()

# ---------- Main controls + pipeline trigger ----------

with st.form("pipeline_form"):
    col_a, col_b = st.columns(2)
    with col_a:
        duration_seconds = st.slider("⏱️ Duration (seconds)", min_value=5, max_value=300, value=45, step=1)
    with col_b:
        voice_speed = st.slider("🐿️ Voice Speed", min_value=1.0, max_value=1.5, value=1.15, step=0.05)

    target_words = int(duration_seconds * WORDS_PER_SECOND)
    st.caption(f"Target word count for this duration: **{target_words} words**")

    event_category = st.text_input("Path A — Category / Vibe (optional)", placeholder="e.g. Roblox hacker, UGC scammer, toxic kid")
    custom_material = st.text_area("Path B — Custom Brief / Script (optional)", placeholder="Paste your own brief or full script here. If this has text, it overrides Path A entirely.")

    run_button = st.form_submit_button("🚀 RUN RORANTS STUDIO PIPELINE", use_container_width=True)

if run_button:
    if not gemini_key:
        st.error("Add your Gemini key in the sidebar first.")
    else:
        gemini_client = genai.Client(api_key=gemini_key)
        using_path_b = bool(custom_material.strip())

        with st.spinner("Running the pipeline..."):
            try:
                if using_path_b:
                    st.session_state.research_draft = ""
                    st.session_state.source_results = []
                    source_material = custom_material.strip()
                else:
                    if not event_category.strip():
                        st.error("Path A needs a Category / Vibe -- or fill in Path B with your own material instead.")
                        st.stop()
                    current_year = datetime.now(timezone.utc).year
                    draft, sources = build_research_draft(gemini_client, event_category.strip(), current_year)
                    st.session_state.research_draft = draft
                    st.session_state.source_results = sources
                    source_material = draft

                script = create_final_script(gemini_client, source_material, target_words)
                st.session_state.final_script = script
                st.session_state.script_editor = script
                st.session_state.voice_stale = bool(st.session_state.audio_bytes)

                moments = find_meme_moments(gemini_client, script)
                st.session_state.meme_moments = moments
                images = {}
                for m in moments:
                    images[m["search_term"]] = find_meme_images(m["search_term"])
                st.session_state.meme_images = images

                st.success(f"Pipeline done ({'Path B — custom material' if using_path_b else 'Path A — ' + event_category}). {len(script.split())} words, {len(moments)} meme moments found.")
            except Exception as e:
                st.error(f"❌ Pipeline failed: {e}")

# ---------- Research draft (Path A only) ----------

if st.session_state.research_draft:
    with st.expander("📰 Research Draft & Sources"):
        st.write(st.session_state.research_draft)
        if st.session_state.source_results:
            st.caption("Sources pulled for this draft:")
            for r in st.session_state.source_results[:10]:
                st.markdown(f"- [{r.get('title', 'source')}]({r.get('url') or r.get('href', '#')})")

# ---------- Script editor ----------

if st.session_state.final_script:
    st.subheader("📝 Script")

    def _mark_stale():
        st.session_state.voice_stale = True

    st.text_area("Editable script -- editing this marks any existing voiceover as stale.",
                  key="script_editor", height=220, on_change=_mark_stale)
    st.caption(f"Current word count: {len(st.session_state.script_editor.split())}")

    if st.session_state.voice_stale and st.session_state.audio_bytes:
        st.warning("⚠️ Script has changed since the last voiceover. Regenerate to hear the update.")

# ---------- Meme moments ----------

if st.session_state.meme_moments:
    st.subheader("🖼️ Word-Anchored Meme Moments")
    for m in st.session_state.meme_moments:
        with st.expander(f"\"{m['start_anchor']}\" → \"{m['end_anchor']}\" (words {m['start_word']}-{m['end_word']}) — {m['search_term']}"):
            images = st.session_state.meme_images.get(m["search_term"], [])
            if not images:
                st.caption("No clean (non-stock-photo) results found for this query.")
            else:
                cols = st.columns(len(images))
                for col, img in zip(cols, images):
                    with col:
                        st.image(img.get("thumbnail") or img.get("image"), use_container_width=True)
                        st.markdown(f"[Full image]({img.get('image')})")

# ---------- Decoupled voice generation ----------

if st.session_state.final_script:
    st.subheader("🎙️ Voiceover")
    voice_label = "🔄 Regenerate Voice" if st.session_state.audio_bytes else "🎙️ Generate Voiceover"
    if st.button(voice_label):
        if not eleven_key:
            st.error("Add your ElevenLabs key in the sidebar first.")
        else:
            with st.spinner("Rendering exact-length voiceover..."):
                try:
                    eleven_client = ElevenLabs(api_key=eleven_key)
                    voice_id = resolve_voice_id(eleven_client, "Adam")
                    audio_bytes, achieved_ms = generate_voice(
                        eleven_client, st.session_state.script_editor, voice_id, voice_speed, duration_seconds,
                    )
                    st.session_state.audio_bytes = audio_bytes
                    st.session_state.audio_duration_ms = achieved_ms
                    st.session_state.voice_stale = False
                    st.session_state.last_voiced_script = st.session_state.script_editor
                    st.success(f"Voice ready -- {achieved_ms / 1000:.2f}s (target was {duration_seconds}s).")
                except Exception as e:
                    st.error(f"❌ ElevenLabs error: {e}")

    if st.session_state.audio_bytes:
        st.audio(st.session_state.audio_bytes, format="audio/mp3")
        st.download_button("📥 DOWNLOAD VOICE MP3", data=st.session_state.audio_bytes,
                            file_name="RoRants_Voice.mp3", mime="audio/mpeg")