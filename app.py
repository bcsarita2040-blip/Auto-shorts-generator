import streamlit as st
import os
from pydub import AudioSegment, silence  # Built by James Robert
from google import genai  # Built by Google DeepMind
from elevenlabs.client import ElevenLabs  # Built by Mati Staniszewski & Piotr Dabkowski
from elevenlabs import save

# --- Everything video/MoviePy/Whisper/meme-related is gone. This is script + voice only. ---

GEMINI_MODEL_CANDIDATES = ["gemini-flash-latest", "gemini-3.5-flash", "gemini-2.5-flash"]
WORDS_PER_SECOND = 2.5  # rough natural-speech estimate -- only used to size Gemini's FIRST draft.
                        # The exact final length is guaranteed later by measuring + trimming
                        # the real audio, not by trusting this number.

# How much extra raw material to ask for, accounting for: the sped-up voice shrinking
# duration, ElevenLabs' actual pace varying from our word/sec guess, and gap-stripping
# removing dead air. Two attempts: a modest buffer, then a bigger one if that's still short.
SAFETY_MARGINS = [1.15, 1.4]

st.set_page_config(page_title="RoRants Voice Factory", page_icon="🎙️")
st.title("🎙️ Script + Voice Factory")
st.write("Just the script and the sped-up Adam voice. No video, no editing, no memes.")

with st.sidebar:
    st.header("🔑 Engine Keys")
    gemini_key = st.text_input("Gemini API Key", type="password").strip()
    eleven_key = st.text_input("ElevenLabs API Key", type="password").strip()


def resolve_voice_id(client, name="Adam"):
    """The SDK wants a real voice_id, not a name -- look it up once instead of
    hardcoding an ID that could be wrong or change."""
    results = client.voices.search(search=name)
    if not results.voices:
        raise ValueError(f"No ElevenLabs voice matching '{name}'. Check the spelling.")
    return results.voices[0].voice_id


def chipmunk_speed(audio_segment, speed=1.15):
    """The famous sped-up, high-pitched Adam voice: override the frame rate to play
    back faster (pitch rises with it), then resample to a standard rate so it's still
    a normal, playable MP3. speed=1.0 is a no-op."""
    if speed == 1.0:
        return audio_segment
    new_frame_rate = int(audio_segment.frame_rate * speed)
    sped_up = audio_segment._spawn(audio_segment.raw_data, overrides={'frame_rate': new_frame_rate})
    return sped_up.set_frame_rate(audio_segment.frame_rate)


def strip_gaps(sound):
    """Cuts dead air between words/sentences down to almost nothing. This runs BEFORE
    chipmunk_speed, so gap-stripping and speed-up don't fight each other."""
    chunks = silence.split_on_silence(sound, min_silence_len=150, silence_thresh=-40, keep_silence=15)
    combined = AudioSegment.empty()
    for chunk in chunks:
        combined += chunk
    return combined


def write_script(client, topic, seconds_to_write):
    """Plain script generation sized to a word-count estimate. No JSON, no meme words --
    this app doesn't touch video anymore, we just want clean spoken text."""
    prompt = f"""
    Write a first-person YouTube Shorts/TikTok "Roblox rant" story about: '{topic}'.
    This genre (like RoRants) reads like someone telling their friends what ACTUALLY
    happened to them -- storytelling with an edge, not a generic angry speech.
    Structure: hook in the first line, escalating story, punchy payoff or twist at the end.
    The spoken text MUST be approximately {round(seconds_to_write)} seconds long when read
    aloud at a natural pace (roughly {round(seconds_to_write * WORDS_PER_SECOND)} words).
    Use casual Gen-Alpha slang. Naturally include the words 'clown', 'toxic', and 'karma'
    as actual spoken words somewhere in the story.
    Do NOT include emojis, stage directions, character names, or brackets -- this gets
    read aloud by a voice engine, so emojis never get spoken.
    """
    last_error = None
    for model_name in GEMINI_MODEL_CANDIDATES:
        try:
            response = client.models.generate_content(model=model_name, contents=prompt)
            text = (response.text or "").replace('*', '').replace('"', '').strip()
            if not text:
                raise ValueError("Gemini returned an empty script.")
            return text, model_name
        except Exception as e:
            last_error = e
            continue
    raise last_error


def voice_it(eleven_client, script_text, voice_id):
    """ElevenLabs -> gap-stripped AudioSegment, ready for the speed effect."""
    audio_stream = eleven_client.text_to_speech.convert(
        voice_id=voice_id, text=script_text, model_id="eleven_multilingual_v2", output_format="mp3_44100_128",
    )
    save(audio_stream, "raw_voice.mp3")
    raw = AudioSegment.from_mp3("raw_voice.mp3")
    return strip_gaps(raw)


with st.form("voice_factory_form"):
    topic = st.text_area("🔥 What is the drama about?", "A toxic 12-year-old tried to hack my Roblox account, so I got him banned.")
    custom_script = st.text_area("✍️ Your Own Script (optional)",
                                  placeholder="Drop your own script here. If you do, the length slider below is ignored completely -- your script decides the length.")
    target_seconds = st.slider("⏱️ Exact Target Length (seconds)", min_value=5, max_value=300, value=45, step=1,
                                help="The final MP3 is trimmed to land on this number exactly. Ignored if you dropped in your own script above.")
    voice_speed = st.slider("🐿️ Sped-Up 'Rant Channel' Voice", min_value=1.0, max_value=1.3, value=1.15, step=0.05,
                             help="1.0 = normal Adam, no chipmunk. This is accounted for when sizing the script now, so a fast voice setting won't cut your video short anymore.")
    submit_button = st.form_submit_button("⚡ GENERATE SCRIPT + VOICE")

if submit_button:
    using_custom = bool(custom_script.strip())
    missing_keys = (not eleven_key) or (not using_custom and not gemini_key)
    if missing_keys:
        st.error("Bro, you're missing an API key. Load it up first!")
    else:
        with st.spinner("Cooking..."):
            eleven_client = ElevenLabs(api_key=eleven_key)
            try:
                voice_id = resolve_voice_id(eleven_client, "Adam")
            except Exception as e:
                st.error(f"❌ ElevenLabs rejected this call. Real reason: {e}")
                st.stop()

            script_text = None
            used_model = None
            final_sound = None

            if using_custom:
                script_text = custom_script.strip()
                used_model = "your own script"
                st.info("Using your custom script -- no length target applied.")
                try:
                    final_sound = chipmunk_speed(voice_it(eleven_client, script_text, voice_id), voice_speed)
                except Exception as e:
                    st.error(f"❌ ElevenLabs rejected this call. Real reason: {e}")
                    st.stop()
            else:
                gemini_client = genai.Client(api_key=gemini_key)
                target_ms = int(target_seconds * 1000)

                for margin in SAFETY_MARGINS:
                    # Pre-speedup material needed so that AFTER chipmunk_speed shrinks
                    # it, we still land at or past the target -- this is the exact bug
                    # from last time: the old version never accounted for the speed-up
                    # shrinking the final duration.
                    seconds_to_write = target_seconds * voice_speed * margin
                    try:
                        script_text, used_model = write_script(gemini_client, topic, seconds_to_write)
                        candidate = chipmunk_speed(voice_it(eleven_client, script_text, voice_id), voice_speed)
                    except Exception as e:
                        st.error(f"❌ Generation call rejected. Real reason: {e}")
                        st.stop()
                    final_sound = candidate
                    if len(candidate) >= target_ms:
                        break

                if len(final_sound) >= target_ms:
                    final_sound = final_sound[:target_ms].fade_out(80)
                    st.success(f"Script + voice done (engine: {used_model}) -- locked to exactly {target_seconds}s.")
                else:
                    achieved = len(final_sound) / 1000
                    st.warning(f"Even on the bigger retry, this only reached {achieved:.1f}s instead of {target_seconds}s. "
                               f"Padding it with silence to hit the number would break your 'no gaps' rule, so here's the "
                               f"honest result instead -- try a shorter target or a punchier topic.")

            output_file = "script_voice.mp3"
            final_sound.export(output_file, format="mp3")

            st.text_area("📜 Script", script_text, height=200)
            st.caption(f"Final audio length: {len(final_sound)/1000:.2f}s")
            with open(output_file, "rb") as f:
                st.download_button("📥 DOWNLOAD VOICE MP3", data=f, file_name="RoRant_Voice.mp3", mime="audio/mpeg")

            for cleanup_file in ["raw_voice.mp3", output_file]:
                if os.path.exists(cleanup_file):
                    os.remove(cleanup_file)