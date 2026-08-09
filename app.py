import streamlit as st
import os
import json
from ddgs import DDGS
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
tab1, tab2 = st.tabs(["🎙️ Script + Voice Factory", "🕵️ RoRants Topic & Meme Hunter"])

with tab1:
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

with tab2:
    st.header("🕵️ Topic & Meme Hunter")
    st.write("Turn a Roblox topic into a research-backed RoRants story concept and ready-to-grab overlay links.")
    topic_seed = st.text_input("Topic seed", placeholder="e.g. Roblox scam, toxic admin, fake giveaway")
    duration_minutes = st.slider("Duration (minutes)", min_value=1, max_value=5, value=1, step=1)

    def parse_hunter_response(raw_text):
        """Extract and validate Gemini's small JSON response."""
        cleaned = (raw_text or "").strip()
        if cleaned.startswith("```"):
            lines = cleaned.splitlines()
            if lines and lines[0].strip().startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            cleaned = "\n".join(lines).strip()

        json_start = cleaned.find("{")
        json_end = cleaned.rfind("}")
        if json_start == -1 or json_end == -1:
            raise ValueError("Gemini did not return the requested JSON object.")

        payload = json.loads(cleaned[json_start:json_end + 1])
        story_idea = str(payload.get("story_idea") or payload.get("story_concept") or "").strip()
        raw_terms = payload.get("meme_search_terms") or payload.get("search_terms") or []

        meme_search_terms = []
        for term in raw_terms:
            if isinstance(term, str):
                term = term.strip()
                if term and term not in meme_search_terms:
                    meme_search_terms.append(term)

        if not story_idea:
            raise ValueError("Gemini returned no story idea.")
        if len(meme_search_terms) < 3:
            raise ValueError("Gemini returned fewer than three meme search terms.")

        return story_idea, meme_search_terms[:5]

    if st.button("Hunt for Topics & Memes"):
        if not gemini_key:
            st.error("Add your Gemini API key in the sidebar before starting the hunt.")
        elif not topic_seed.strip():
            st.error("Enter a topic seed first.")
        else:
            # Step A: collect a small amount of current Roblox drama/news context.
            with st.spinner("Searching the web for Roblox drama..."):
                try:
                    raw_search_results = list(
                        DDGS().text(
                            f"Roblox {topic_seed.strip()} drama controversy",
                            max_results=5,
                        )
                    )
                except Exception as e:
                    raw_search_results = []
                    st.warning(f"DuckDuckGo's text search was unavailable, so Gemini will use the topic seed alone. Reason: {e}")

            web_context = []
            for result in raw_search_results:
                if not isinstance(result, dict):
                    continue
                web_context.append({
                    "title": str(result.get("title") or "").strip(),
                    "snippet": str(result.get("body") or result.get("snippet") or "").strip(),
                    "url": str(result.get("href") or result.get("url") or "").strip(),
                })

            # Step B: turn those snippets into one structured RoRants concept.
            context_text = json.dumps(web_context, ensure_ascii=False, indent=2) if web_context else "No web snippets were returned."
            hunter_prompt = f"""
You are a topic researcher for a fast-paced RoRants-style Roblox video.

Topic seed: {topic_seed.strip()}
Target video duration: about {duration_minutes} minute(s)
DuckDuckGo context:
{context_text}

Use the supplied context as inspiration, but do not present an unverified allegation as a confirmed fact.
Create one specific, high-retention story concept with these clearly labeled parts:
- HOOK: an immediate first-person opening
- ESCALATION: the conflict gets worse in concrete beats
- TWIST/PAYOFF: a satisfying, funny, or karma-driven ending

Also produce 3 to 5 specific visual search queries for reaction memes, Roblox screenshots,
or other overlays that fit exact moments in the story. Make each query concrete enough for an
image search, not a vague one-word topic.

Return ONLY valid JSON in this exact shape:
{{
  "story_idea": "HOOK: ...\\n\\nESCALATION: ...\\n\\nTWIST/PAYOFF: ...",
  "meme_search_terms": ["query 1", "query 2", "query 3"]
}}
"""

            hunter_models = ["gemini-2.5-flash", "gemini-flash-latest", "gemini-3.5-flash"]
            story_idea = None
            meme_search_terms = []
            used_hunter_model = None
            last_hunter_error = None
            gemini_client = genai.Client(api_key=gemini_key)

            with st.spinner("Gemini is shaping the hook, escalation, and payoff..."):
                for model_name in hunter_models:
                    try:
                        response = gemini_client.models.generate_content(
                            model=model_name,
                            contents=hunter_prompt,
                            config={"response_mime_type": "application/json"},
                        )
                        story_idea, meme_search_terms = parse_hunter_response(response.text)
                        used_hunter_model = model_name
                        break
                    except Exception as e:
                        last_hunter_error = e

            if not story_idea:
                st.error(f"Gemini could not formulate the story. Last error: {last_hunter_error}")
            else:
                # Step C: fetch up to two direct image links for every Gemini search term.
                meme_links = {term: [] for term in meme_search_terms}
                image_search_errors = {}
                seen_urls = set()

                with st.spinner("Hunting down meme and screenshot links..."):
                    for query in meme_search_terms:
                        try:
                            image_results = list(DDGS().images(query, max_results=2))
                            for image_result in image_results:
                                if not isinstance(image_result, dict):
                                    continue
                                image_url = str(
                                    image_result.get("image") or image_result.get("thumbnail") or ""
                                ).strip()
                                if image_url.startswith(("http://", "https://")) and image_url not in seen_urls:
                                    meme_links[query].append(image_url)
                                    seen_urls.add(image_url)
                        except Exception as e:
                            image_search_errors[query] = str(e)

                # Step D: present a copy-ready concept and direct, previewable image links.
                st.success(f"Hunt complete — story built with {used_hunter_model}.")
                st.text_area(
                    "📖 Finalized RoRants Story Idea (copy-ready)",
                    value=story_idea,
                    height=300,
                )

                with st.expander("🖼️ Downloadable Meme & Screenshot Links for Kdenlive", expanded=True):
                    if not any(meme_links.values()):
                        st.info("No direct image links were returned this time. Try a more specific topic seed.")

                    for query, urls in meme_links.items():
                        st.markdown(f"**Search term:** `{query}`")
                        if query in image_search_errors:
                            st.caption(f"Image search unavailable for this term: {image_search_errors[query]}")
                        elif not urls:
                            st.caption("No usable direct image URL was returned for this term.")

                        for index, image_url in enumerate(urls, start=1):
                            st.markdown(f"{index}. 🔗 <{image_url}>")
                            try:
                                st.image(image_url, caption=f"{query} — result {index}", width=320)
                            except Exception:
                                st.caption("Thumbnail preview unavailable; the direct link may still work.")