import streamlit as st
import os
import random
import re
import requests
import whisper # Built by Alec Radford / OpenAI
from pydub import AudioSegment, silence # Built by James Robert
from moviepy.editor import VideoFileClip, AudioFileClip, TextClip, CompositeVideoClip, CompositeAudioClip, ImageClip
from moviepy.config import change_settings
import google.generativeai as genai # Built by Google DeepMind
from elevenlabs.client import ElevenLabs # Built by Mati Staniszewski / Piotr Dabkowski
from elevenlabs import save
from duckduckgo_search import DDGS # Built by rany2

# Tell MoviePy exactly where Linux keeps ImageMagick
change_settings({"IMAGEMAGICK_BINARY": "/usr/bin/convert"})

st.set_page_config(page_title="RoRants Factory", page_icon="🔥")
st.title("🚀 The iPad Rant Factory")
st.write("Print high-retention TikToks and Shorts from the cloud.")

with st.sidebar:
    st.header("🔑 Engine Keys")
    gemini_key = st.text_input("Gemini API Key", type="password")
    eleven_key = st.text_input("ElevenLabs API Key", type="password")

topic = st.text_area("🔥 What is the drama about?", "A toxic 12-year-old tried to hack my Roblox account, so I got him banned.")
video_format = st.radio("⏱️ Target Platform Length", ["Shorts (Under 60s)", "TikTok (Over 60s)"])

st.write("📁 Drop Your Raw Assets Here:")
bg_file = st.file_uploader("Gameplay Background (MP4)", type=["mp4"])
boom_file = st.file_uploader("Vine Boom Effect (MP3)", type=["mp3"])
lofi_file = st.file_uploader("Background Lofi (MP3)", type=["mp3"])

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
    except:
        pass
    return None

if st.button("⚡ GENERATE MASTERPIECE"):
    if not (gemini_key and eleven_key and bg_file and boom_file and lofi_file):
        st.error("Bro, you're missing keys or files. Load them up first!")
    else:
        with st.spinner("Cooking... Give it 2-3 minutes. Do not close Safari."):
            # 1. Save uploaded files to the temporary cloud disk
            with open("background.mp4", "wb") as f: f.write(bg_file.getbuffer())
            with open("boom.mp3", "wb") as f: f.write(boom_file.getbuffer())
            with open("lofi.mp3", "wb") as f: f.write(lofi_file.getbuffer())
            
            genai.configure(api_key=gemini_key)
            
            # 2. AI Script Writer
            st.info("Drafting the script...")
            model = genai.GenerativeModel('gemini-1.5-flash')
            target_len = "over 70 seconds long" if "Over 60s" in video_format else "strictly around 40 seconds long"
            
            prompt = f"""
            Write a hyper-fast, aggressive YouTube Shorts/TikTok rant about: '{topic}'.
            The spoken text MUST be {target_len}. 
            Use words like 'clown', 'toxic', 'karma'. Add emojis. 
            Do NOT include stage directions, character names, or brackets. Just the raw spoken text.
            """
            script_text = model.generate_content(prompt).text.replace('*', '').replace('"', '').strip()
            st.success("Script written!")
            
            # 3. Voice & Silence Killer (The Breathless Effect)
            st.info("Rendering Adam voice and stripping dead air...")
            client = ElevenLabs(api_key=eleven_key)
            raw_audio = client.generate(text=script_text, voice="Adam", model="eleven_multilingual_v2")
            save(raw_audio, "raw_voice.mp3")
            
            sound = AudioSegment.from_mp3("raw_voice.mp3")
            audio_chunks = silence.split_on_silence(sound, min_silence_len=150, silence_thresh=-40, keep_silence=25)
            
            combined_sound = AudioSegment.empty()
            for chunk in audio_chunks: 
                combined_sound += chunk
            combined_sound.export("voice.mp3", format="mp3")
            
            # 4. Transcription
            st.info("Mapping word timestamps...")
            whisper_model = whisper.load_model("tiny") # Tiny prevents cloud memory crashes
            transcription = whisper_model.transcribe("voice.mp3", word_timestamps=True)
            
            # 5. Video Compilation
            st.info("Stitching visuals, fetching memes, and dropping booms...")
            vocal_track = AudioFileClip("voice.mp3")
            ambient_music = AudioFileClip("lofi.mp3").volumex(0.1).set_duration(vocal_track.duration)
            
            source_video = VideoFileClip("background.mp4")
            # Loop video if it's too short for the 60s+ TikToks
            if source_video.duration < vocal_track.duration:
                from moviepy.video.fx.all import loop
                source_video = source_video.fx(loop, duration=vocal_track.duration + 2)
                
            max_start = max(0, source_video.duration - vocal_track.duration - 1)
            start_marker = random.uniform(0, max_start)
            video_slice = source_video.subclip(start_marker, start_marker + vocal_track.duration).resize(newsize=(1080, 1920))
            
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
                    
                    # Bounce Text
                    txt = TextClip(raw_word.upper(), fontsize=100, color='yellow', stroke_color='black', stroke_width=4, font='Impact', size=(900, None), method='caption')
                    visual_layers.append(txt.set_position('center').set_start(start).set_duration(dur))
                    
                    # Vine Booms
                    if any(marker in raw_word for marker in ["!", "😭", "💀", "😡"]):
                        try: audio_layers.append(AudioFileClip("boom.mp3").set_start(start).volumex(0.5))
                        except: pass
                        
                    # Meme Overlays
                    if clean_word in meme_keywords and img_count < 5:
                        img_path = scrape_meme(clean_word, img_count)
                        if img_path:
                            try:
                                meme_clip = ImageClip(img_path).resize(width=800).set_position(('center', 300)).set_start(start).set_duration(1.5)
                                visual_layers.append(meme_clip)
                                img_tracker.append(img_path)
                                img_count += 1
                            except: pass

            final_audio = CompositeAudioClip(audio_layers)
            master_render = CompositeVideoClip(visual_layers, size=(1080, 1920)).set_audio(final_audio)
            
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
