import streamlit as st
import os
import json
import random
import re
import requests  # Built by Kenneth Reitz
import whisper  # Built by Alec Radford / OpenAI
from pydub import AudioSegment, silence  # Built by James Robert
from moviepy import VideoFileClip, AudioFileClip, TextClip, CompositeVideoClip, CompositeAudioClip, ImageClip  # Built by Zulko
from moviepy.video.fx import Loop
from google import genai  # Built by Google DeepMind -- unified SDK
from google.genai import types
from elevenlabs.client import ElevenLabs  # Built by Mati Staniszewski & Piotr Dabkowski
from elevenlabs import save
from duckduckgo_search import DDGS  # Built by rany2

# --- Fonts. Pick one in the UI -- all of them self-heal the same way (auto-download
# if missing), so you don't need to hand-commit any font files to your repo anymore. ---
FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
FONT_OPTIONS = {
    "Anton (classic meme Impact-style)": "https://raw.githubusercontent.com/google/fonts/main/ofl/anton/Anton-Regular.ttf",
    "Bebas Neue (tall condensed)": "https://raw.githubusercontent.com/google/fonts/main/ofl/bebasneue/BebasNeue-Regular.ttf",
    "Archivo Black (chunky sans)": "https://raw.githubusercontent.com/google/fonts/main/ofl/archivoblack/ArchivoBlack-Regular.ttf",
}


def ensure_font(font_name):
    url = FONT_OPTIONS[font_name]
    path = os.path.join(FONT_DIR, url.split("/")[-1])
    if os.path.exists(path) and os.path.getsize(path) > 10_000:
        return path
    os.makedirs(FONT_DIR, exist_ok=True)
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    with open(path, "wb") as f:
        f.write(r.content)
    return path


# --- Gemini model fallback chain -- see earlier notes, model IDs keep dying ---
GEMINI_MODEL_CANDIDATES = ["gemini-flash-latest", "gemini-3.5-flash", "gemini-2.5-flash"]

# Backup meme keywords, used only if Gemini's own suggested words (see generate_script)
# come back empty. Gemini's list is now the primary source since it's guaranteed to
# actually appear in the script -- this is just a safety net.
STATIC_MEME_KEYWORDS = ["scam", "clown", "toxic", "hacker", "karma", "crying", "mom", "brother"]

# Words that trigger a Vine Boom -- overlap with words we told Gemini to actually say.
BOOM_KEYWORDS = ["clown", "toxic", "karma", "scam", "busted", "cooked", "cap"]

MIN_CAPTION_SECONDS = 0.25  # below this, a caption reads as "flickering," not "fast"

st.set_page_config(page_title="RoRants Factory", page_icon="🔥")
st.title("🚀 The iPad Rant Factory")
st.write("Print high-retention TikToks and Shorts from the cloud.")

with st.sidebar:
    st.header("🔑 Engine Keys")
    gemini_key = st.text_input("Gemini API Key", type="password").strip()
    eleven_key = st.text_input("ElevenLabs API Key", type="password").strip()


def scrape_meme(keyword, index):
    """Pulls one meme-flavored image for a keyword. DDG is free but unmoderated, and
    some image hosts silently 403 requests with no real User-Agent -- this header
    cuts down on that specific silent-failure mode."""
    filename = f"meme_{index}.jpg"
    headers = {"User-Agent": "Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko)"}
    try:
        with DDGS() as ddg:
            results = list(ddg.images(f"{keyword} meme png", max_results=1))
            if results:
                img_data = requests.get(results[0]['image'], headers=headers, timeout=6).content
                with open(filename, 'wb') as f:
                    f.write(img_data)
                return filename
    except Exception:
        pass
    return None


def resolve_voice_id(client, name="Adam"):
    """The old SDK let you pass voice='Adam' as a plain string. The new SDK wants the
    real voice_id, so we look it up by name once instead of hardcoding an ID."""
    results = client.voices.search(search=name)
    if not results.voices:
        raise ValueError(f"No ElevenLabs voice matching '{name}'. Check the spelling.")
    return results.voices[0].voice_id


def chipmunk_speed(audio_segment, speed=1.15):
    """The famous sped-up, high-pitched Adam voice: override the frame rate to play
    back faster (which drags pitch up with it), then resample to a standard rate so
    it's still a normal, playable MP3. speed=1.0 is a no-op."""
    if speed == 1.0:
        return audio_segment
    new_frame_rate = int(audio_segment.frame_rate * speed)
    sped_up = audio_segment._spawn(audio_segment.raw_data, overrides={'frame_rate': new_frame_rate})
    return sped_up.set_frame_rate(audio_segment.frame_rate)


def crop_to_vertical(clip, target_w=1080, target_h=1920):
    """Scales the source so it fully COVERS a 1080x1920 frame, then center-crops the
    overflow. This replaces the old .resize(newsize=(1080,1920)), which forced your
    footage into that box by stretching it -- that's the squish you saw."""
    target_ratio = target_w / target_h
    clip_ratio = clip.w / clip.h
    if clip_ratio > target_ratio:
        resized = clip.resized(height=target_h)
    else:
        resized = clip.resized(width=target_w)
    return resized.cropped(x_center=resized.w / 2, y_center=resized.h / 2, width=target_w, height=target_h)


def generate_script(client, prompt):
    """Asks Gemini for the script AND a handful of meme-worthy words pulled FROM that
    exact script, as structured JSON -- so meme matching is tied to words we KNOW are
    in the text, instead of hoping a fixed guess-list happens to show up."""
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema={
            "type": "object",
            "properties": {
                "script": {"type": "string"},
                "meme_words": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["script", "meme_words"],
        },
    )
    last_error = None
    for model_name in GEMINI_MODEL_CANDIDATES:
        try:
            response = client.models.generate_content(model=model_name, contents=prompt, config=config)
            data = json.loads(response.text)
            script = (data.get("script") or "").strip()
            words = [str(w).strip().lower() for w in data.get("meme_words", []) if str(w).strip()]
            if not script:
                raise ValueError("Gemini returned an empty script.")
            return script, words, model_name
        except Exception as e:
            last_error = e
            continue
    raise last_error


# --- Everything the render needs lives inside ONE form: topic, script override,
# length, voice, boom volume, font, and all 3 uploads. Nothing runs until you hit
# the button -- that's what fixed the iPad Safari "ghost drop" file issue. ---
with st.form("masterpiece_form"):
    topic = st.text_area("🔥 What is the drama about?", "A toxic 12-year-old tried to hack my Roblox account, so I got him banned.")
    custom_script = st.text_area("✍️ Your Own Script (optional)", placeholder="Leave blank and I'll write one from the description above instead.")
    target_seconds = st.slider("⏱️ Target Length (seconds)", min_value=15, max_value=180, value=45, step=5,
                                help="Longer renders composite way more caption clips -- stay under ~90s until you've confirmed shorter ones run clean on your Streamlit Cloud RAM limit.")
    voice_speed = st.slider("🐿️ Sped-Up 'Rant Channel' Voice", min_value=1.0, max_value=1.3, value=1.15, step=0.05,
                             help="The classic high-pitched, fast Adam voice from RoRants and every other rant channel. 1.0 = normal Adam, no chipmunk.")
    boom_volume = st.slider("💥 Vine Boom Volume", min_value=0.0, max_value=1.5, value=0.7, step=0.1)
    font_choice = st.selectbox("🔤 Caption Font", list(FONT_OPTIONS.keys()))

    st.write("📁 Drop Your Raw Assets Here:")
    bg_file = st.file_uploader("Gameplay Background (MP4)", type=["mp4"])
    boom_file = st.file_uploader("Vine Boom Effect (MP3)", type=["mp3"])
    lofi_file = st.file_uploader("Background Lofi (MP3)", type=["mp3"])

    submit_button = st.form_submit_button("⚡ GENERATE MASTERPIECE")

if submit_button:
    if not (gemini_key and eleven_key and bg_file and boom_file and lofi_file):
        st.error("Bro, you're missing keys or files. Load them up first!")
    else:
        with st.spinner("Cooking... Give it 2-3 minutes. Do not close Safari."):
            # 0. Font check first -- fail fast before spending any API quota.
            try:
                font_path = ensure_font(font_choice)
            except Exception as font_error:
                st.error(f"❌ Couldn't get the caption font ready. Real reason: {font_error}")
                st.stop()

            # 1. Save uploaded files
            with open("background.mp4", "wb") as f: f.write(bg_file.getbuffer())
            with open("boom.mp3", "wb") as f: f.write(boom_file.getbuffer())
            with open("lofi.mp3", "wb") as f: f.write(lofi_file.getbuffer())

            gemini_client = genai.Client(api_key=gemini_key)
            gemini_meme_words = []

            # 2. Script: yours if you gave one, otherwise Gemini writes it
            if custom_script.strip():
                script_text = custom_script.strip()
                used_model = "your own script"
                st.success("Using your custom script.")
            else:
                st.info("Drafting the script...")
                prompt = f"""
                Write a first-person YouTube Shorts/TikTok "Roblox rant" story about: '{topic}'.
                This genre (like RoRants) reads like someone telling their friends what
                ACTUALLY happened to them -- storytelling with an edge, not a generic angry
                speech. Structure: hook in the first line, escalating story, punchy payoff
                or twist at the end.
                The spoken text MUST be approximately {target_seconds} seconds long when read
                aloud at a natural pace (roughly {round(target_seconds * 2.5)} words).
                Use casual Gen-Alpha slang. Naturally include the words 'clown', 'toxic', and
                'karma' as actual spoken words somewhere in the story.
                Also return 4-6 short, punchy words or two-word phrases taken FROM the script
                itself that would make good reaction meme images.
                Do NOT include emojis, stage directions, character names, or brackets in the
                script -- it gets read aloud by a voice engine, so emojis never get spoken.
                """
                try:
                    script_text, gemini_meme_words, used_model = generate_script(gemini_client, prompt)
                    script_text = script_text.replace('*', '').replace('"', '').strip()
                    st.success(f"Script written successfully! (engine: {used_model})")
                except Exception as gemini_error:
                    st.error(f"❌ Gemini API completely rejected this call. Real reason: {gemini_error}")
                    st.warning("Double check your API key, confirm billing/free-tier access in AI Studio, and note the daily free quota resets at midnight Pacific time.")
                    st.stop()

            # 3. Voice & Silence Killer (The Breathless Effect)
            st.info("Rendering Adam voice and stripping dead air...")
            eleven_client = ElevenLabs(api_key=eleven_key)

            try:
                adam_voice_id = resolve_voice_id(eleven_client, "Adam")
                audio_stream = eleven_client.text_to_speech.convert(
                    voice_id=adam_voice_id,
                    text=script_text,
                    model_id="eleven_multilingual_v2",
                    output_format="mp3_44100_128",
                )
                save(audio_stream, "raw_voice.mp3")
            except Exception as eleven_error:
                st.error(f"❌ ElevenLabs rejected this call. Real reason: {eleven_error}")
                st.warning("Check your ElevenLabs key and confirm you still have characters left this billing cycle.")
                st.stop()

            sound = AudioSegment.from_mp3("raw_voice.mp3")
            audio_chunks = silence.split_on_silence(sound, min_silence_len=150, silence_thresh=-40, keep_silence=25)

            combined_sound = AudioSegment.empty()
            for chunk in audio_chunks:
                combined_sound += chunk

            # The famous sped-up voice, applied BEFORE export and BEFORE Whisper ever
            # sees the file -- so captions, boom placement, and video length all get
            # computed on the sped-up timeline and stay in sync automatically.
            combined_sound = chipmunk_speed(combined_sound, voice_speed)
            combined_sound.export("voice.mp3", format="mp3")

            # 4. Transcription
            st.info("Mapping word timestamps...")
            whisper_model = whisper.load_model("tiny")
            transcription = whisper_model.transcribe("voice.mp3", word_timestamps=True)

            # 5. Video Compilation
            st.info("Stitching visuals, fetching memes, and dropping booms...")
            vocal_track = AudioFileClip("voice.mp3")
            ambient_music = AudioFileClip("lofi.mp3").with_volume_scaled(0.1).with_duration(vocal_track.duration)

            source_video = VideoFileClip("background.mp4")
            if source_video.duration < vocal_track.duration:
                source_video = source_video.with_effects([Loop(duration=vocal_track.duration + 2)])

            max_start = max(0, source_video.duration - vocal_track.duration - 1)
            start_marker = random.uniform(0, max_start)
            video_slice = crop_to_vertical(source_video.subclipped(start_marker, start_marker + vocal_track.duration))

            audio_layers = [vocal_track, ambient_music]
            visual_layers = [video_slice]

            meme_keywords = list(dict.fromkeys(gemini_meme_words + STATIC_MEME_KEYWORDS))  # Gemini's words first, deduped
            img_tracker = []
            img_count = 0
            meme_status = []

            for segment in transcription['segments']:
                for w in segment['words']:
                    raw_word = w['word']
                    clean_word = re.sub(r'[^\w\s]', '', raw_word).strip().lower()
                    start, end = w['start'], w['end']
                    dur = max(MIN_CAPTION_SECONDS, end - start)

                    txt = TextClip(text=raw_word.upper(), font=font_path, font_size=100, color='yellow',
                                   stroke_color='black', stroke_width=4, size=(900, None), method='caption')
                    visual_layers.append(txt.with_position('center').with_start(start).with_duration(dur))

                    if "!" in raw_word or clean_word in BOOM_KEYWORDS:
                        try:
                            audio_layers.append(AudioFileClip("boom.mp3").with_start(start).with_volume_scaled(boom_volume))
                        except Exception:
                            pass

                    if clean_word in meme_keywords and img_count < 5:
                        img_path = scrape_meme(clean_word, img_count)
                        if img_path:
                            try:
                                meme_clip = ImageClip(img_path).resized(width=800).with_position(('center', 300)).with_start(start).with_duration(1.5)
                                visual_layers.append(meme_clip)
                                img_tracker.append(img_path)
                                img_count += 1
                                meme_status.append(f"✅ {clean_word}")
                            except Exception:
                                meme_status.append(f"⚠️ {clean_word} (downloaded but failed to composite)")
                        else:
                            meme_status.append(f"❌ {clean_word} (DDG returned nothing)")

            if meme_status:
                st.caption("Meme fetch results: " + ", ".join(meme_status))
            else:
                st.caption("No meme-matching words landed in this script, so no meme overlays this render.")

            final_audio = CompositeAudioClip(audio_layers)
            master_render = CompositeVideoClip(visual_layers, size=(1080, 1920)).with_audio(final_audio)

            st.info("Exporting final MP4...")
            output_file = "VIRAL_RANT.mp4"
            master_render.write_videofile(output_file, fps=24, codec="libx264", audio_codec="aac", bitrate="2500k", preset="ultrafast", threads=2)

            with open(output_file, "rb") as file:
                st.download_button(label="📥 DOWNLOAD TO IPAD CAMERA ROLL", data=file, file_name="Viral_RoRant.mp4", mime="video/mp4")

            for f in ["voice.mp3", "raw_voice.mp3", output_file] + img_tracker:
                if os.path.exists(f): os.remove(f)

            st.success("Boom. File is ready.")
