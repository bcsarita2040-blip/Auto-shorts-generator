import streamlit as st
import os
import random
import re
import requests  # Built by Kenneth Reitz
import whisper  # Built by Alec Radford / OpenAI
from pydub import AudioSegment, silence  # Built by James Robert
from moviepy import VideoFileClip, AudioFileClip, TextClip, CompositeVideoClip, CompositeAudioClip, ImageClip  # Built by Zulko (Kirill Lykov's fork lives on, but Zulko started it)
from moviepy.video.fx import Loop
from google import genai  # Built by Google DeepMind -- the NEW unified SDK. google.generativeai is dead as of Nov 30, 2025.
from elevenlabs.client import ElevenLabs  # Built by Mati Staniszewski & Piotr Dabkowski
from elevenlabs import save
from duckduckgo_search import DDGS  # Built by rany2

# --- Caption font, with a self-heal ---
# Your crash on 7/11 happened because fonts/Anton-Regular.ttf never made it onto the
# deployed server (Pillow's real error was "cannot open resource" -- the file just
# wasn't there). Instead of just telling you to re-check your GitHub commit, this now
# fixes itself: if the bundled file is missing for ANY reason (forgot to commit it,
# .gitignore ate it, a future you deletes the folder by accident), it re-downloads the
# exact same font straight from Google Fonts' own repo before it's ever needed.
FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
FONT_PATH = os.path.join(FONT_DIR, "Anton-Regular.ttf")
FONT_URL = "https://raw.githubusercontent.com/google/fonts/main/ofl/anton/Anton-Regular.ttf"


def ensure_font():
    if os.path.exists(FONT_PATH) and os.path.getsize(FONT_PATH) > 10_000:
        return  # already there and not a truncated/empty file
    os.makedirs(FONT_DIR, exist_ok=True)
    r = requests.get(FONT_URL, timeout=15)
    r.raise_for_status()
    with open(FONT_PATH, "wb") as f:
        f.write(r.content)


# --- Gemini model fallback chain ---
# Google has been retiring "flash" model IDs every 1-3 months through 2026 -- including,
# as of this month, throwing early/unannounced 404s on models that officially aren't
# supposed to shut down until October. This list means one dead model ID can't
# take the whole app down. We try them in order and use whichever answers first.
GEMINI_MODEL_CANDIDATES = ["gemini-flash-latest", "gemini-3.5-flash", "gemini-2.5-flash"]

# Words that trigger a Vine Boom. These deliberately overlap with the words we tell
# Gemini to use in the script below -- so the booms land on words we KNOW get spoken,
# instead of hoping Whisper's transcript happens to contain an emoji (it won't --
# ElevenLabs doesn't voice emoji, and Whisper only transcribes what was actually said).
BOOM_KEYWORDS = ["clown", "toxic", "karma", "scam", "busted", "cooked", "cap"]

st.set_page_config(page_title="RoRants Factory", page_icon="🔥")
st.title("🚀 The iPad Rant Factory")
st.write("Print high-retention TikToks and Shorts from the cloud.")

with st.sidebar:
    st.header("🔑 Engine Keys")
    gemini_key = st.text_input("Gemini API Key", type="password")
    eleven_key = st.text_input("ElevenLabs API Key", type="password")


def scrape_meme(keyword, index):
    filename = f"meme_{index}.jpg"
    try:
        with DDGS() as ddg:
            results = list(ddg.images(f"{keyword} meme png", max_results=1))
            if results:
                img_data = requests.get(results[0]['image'], timeout=5).content
                with open(filename, 'wb') as f:
                    f.write(img_data)
                return filename
    except Exception:
        pass
    return None


def resolve_voice_id(client, name="Adam"):
    """The old SDK let you pass voice='Adam' as a plain string. The new SDK wants the
    real voice_id, so we look it up by name once instead of hardcoding an ID that could
    change or that I could get wrong typing it from memory."""
    results = client.voices.search(search=name)
    if not results.voices:
        raise ValueError(
            f"No ElevenLabs voice matching '{name}'. Check the spelling or pick a voice "
            f"from your Voice Library and swap the name here."
        )
    return results.voices[0].voice_id


def generate_script(client, prompt):
    """Try each candidate model in order until one actually answers."""
    last_error = None
    for model_name in GEMINI_MODEL_CANDIDATES:
        try:
            response = client.models.generate_content(model=model_name, contents=prompt)
            return response.text, model_name
        except Exception as e:
            last_error = e
            continue
    raise last_error


# --- Everything the render needs lives inside ONE form now: topic, format, and all
# 3 uploads. On the old version only the button was in the form, so every file you
# picked fired an immediate rerun of the whole script -- that's your "ghost drop" on
# iPad Safari. Batching it all here means nothing runs until you hit the button. ---
with st.form("masterpiece_form"):
    topic = st.text_area("🔥 What is the drama about?", "A toxic 12-year-old tried to hack my Roblox account, so I got him banned.")
    video_format = st.radio("⏱️ Target Platform Length", ["Shorts (Under 60s)", "TikTok (Over 60s)"])

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
            # 0. Font check FIRST, before we spend a single Gemini or ElevenLabs call --
            # no point burning API quota on a render that would only die later at the
            # caption step anyway.
            try:
                ensure_font()
            except Exception as font_error:
                st.error(f"❌ Couldn't get the caption font ready. Real reason: {font_error}")
                st.warning("This self-heals on its own -- if it keeps failing, your Streamlit Cloud container likely can't reach raw.githubusercontent.com, which is a network/outbound-access issue, not a code bug.")
                st.stop()

            # 1. Save uploaded files to the temporary cloud disk
            with open("background.mp4", "wb") as f: f.write(bg_file.getbuffer())
            with open("boom.mp3", "wb") as f: f.write(boom_file.getbuffer())
            with open("lofi.mp3", "wb") as f: f.write(lofi_file.getbuffer())

            gemini_client = genai.Client(api_key=gemini_key)

            # 2. AI Script Writer with Crash Armor
            st.info("Drafting the script...")
            target_len = "over 70 seconds long" if "TikTok" in video_format else "strictly around 40 seconds long"

            prompt = f"""
            Write a first-person YouTube Shorts/TikTok "Roblox rant" story about: '{topic}'.
            This genre (like RoRants) reads like someone telling their friends what
            ACTUALLY happened to them -- it's storytelling with an edge, not a generic
            angry speech. Structure: a hook in the first line, an escalating story,
            a punchy payoff or twist at the end.
            The spoken text MUST be {target_len}.
            Use casual Gen-Alpha slang. Naturally include the words 'clown', 'toxic',
            and 'karma' as actual spoken words somewhere in the story -- they trigger
            sound effects downstream, so they need to be real words in the text.
            Do NOT include emojis, stage directions, character names, or brackets --
            this gets read aloud by a voice engine, so emojis never get spoken and
            would only clutter the audio. Just the raw spoken text.
            """

            try:
                raw_text, used_model = generate_script(gemini_client, prompt)
                script_text = raw_text.replace('*', '').replace('"', '').strip()
                st.success(f"Script written successfully! (engine: {used_model})")
            except Exception as gemini_error:
                st.error(f"❌ Gemini API completely rejected this call. Real reason: {gemini_error}")
                st.warning("Double check your API key for accidental spaces, confirm the key has billing/free-tier access in AI Studio, and note the daily free quota resets at midnight Pacific time.")
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
            combined_sound.export("voice.mp3", format="mp3")

            # 4. Transcription
            st.info("Mapping word timestamps...")
            whisper_model = whisper.load_model("tiny")  # Tiny prevents cloud memory crashes
            transcription = whisper_model.transcribe("voice.mp3", word_timestamps=True)

            # 5. Video Compilation
            st.info("Stitching visuals, fetching memes, and dropping booms...")
            vocal_track = AudioFileClip("voice.mp3")
            ambient_music = AudioFileClip("lofi.mp3").with_volume_scaled(0.1).with_duration(vocal_track.duration)

            source_video = VideoFileClip("background.mp4")
            # Loop video if it's too short for the 60s+ TikToks
            if source_video.duration < vocal_track.duration:
                source_video = source_video.with_effects([Loop(duration=vocal_track.duration + 2)])

            max_start = max(0, source_video.duration - vocal_track.duration - 1)
            start_marker = random.uniform(0, max_start)
            video_slice = source_video.subclipped(start_marker, start_marker + vocal_track.duration).resized(new_size=(1080, 1920))

            audio_layers = [vocal_track, ambient_music]
            visual_layers = [video_slice]

            meme_keywords = ["scam", "clown", "toxic", "hacker", "karma", "crying", "mom", "brother"]
            img_tracker = []
            img_count = 0

            for segment in transcription['segments']:
                for w in segment['words']:
                    raw_word = w['word']
                    clean_word = re.sub(r'[^\w\s]', '', raw_word).strip().lower()
                    start, end = w['start'], w['end']
                    dur = max(0.1, end - start)

                    # Bounce Text -- rendered with Pillow now, not ImageMagick, so it needs
                    # a real font FILE, not a font name. That's why FONT_PATH points at the
                    # bundled Anton-Regular.ttf instead of the string 'Impact'.
                    txt = TextClip(text=raw_word.upper(), font=FONT_PATH, font_size=100, color='yellow',
                                   stroke_color='black', stroke_width=4, size=(900, None), method='caption')
                    visual_layers.append(txt.with_position('center').with_start(start).with_duration(dur))

                    # Vine Booms -- fires on real spoken hype-words or an exclamation
                    # mark, not on emoji (Whisper transcribes audio; it can't produce
                    # a character nobody said out loud).
                    if "!" in raw_word or clean_word in BOOM_KEYWORDS:
                        try:
                            audio_layers.append(AudioFileClip("boom.mp3").with_start(start).with_volume_scaled(0.5))
                        except Exception:
                            pass

                    # Meme Overlays
                    if clean_word in meme_keywords and img_count < 5:
                        img_path = scrape_meme(clean_word, img_count)
                        if img_path:
                            try:
                                meme_clip = ImageClip(img_path).resized(width=800).with_position(('center', 300)).with_start(start).with_duration(1.5)
                                visual_layers.append(meme_clip)
                                img_tracker.append(img_path)
                                img_count += 1
                            except Exception:
                                pass

            final_audio = CompositeAudioClip(audio_layers)
            master_render = CompositeVideoClip(visual_layers, size=(1080, 1920)).with_audio(final_audio)

            st.info("Exporting final MP4...")
            output_file = "VIRAL_RANT.mp4"
            # Lower bitrate and 24fps strictly to avoid hitting Streamlit's 1GB RAM limit
            master_render.write_videofile(output_file, fps=24, codec="libx264", audio_codec="aac", bitrate="2500k", preset="ultrafast", threads=2)

            # 6. Expose Download Button
            with open(output_file, "rb") as file:
                st.download_button(label="📥 DOWNLOAD TO IPAD CAMERA ROLL", data=file, file_name="Viral_RoRant.mp4", mime="video/mp4")

            # Clean up cloud disk
            for f in ["voice.mp3", "raw_voice.mp3", output_file] + img_tracker:
                if os.path.exists(f): os.remove(f)

            st.success("Boom. File is ready.")
