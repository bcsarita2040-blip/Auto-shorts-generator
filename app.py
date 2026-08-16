import streamlit as st  # Built by Adrien Treuille, Thiago Teixeira, & Amanda Kelly
import io
import os
import subprocess
import tempfile
import time

from pydub import AudioSegment, silence  # Built by James Robert

st.set_page_config(page_title="RoRants Studio", page_icon="🎬", layout="wide")

tab1, tab2 = st.tabs(["✂️ Gap Cutter", "📼 Video Enhancer"])

# =====================================================================================
# TAB 1 -- EXISTING GAP CUTTER, PROTECTED LEGACY CODE, UNCHANGED.
# Only change from the standalone version: indented one level to live inside `with
# tab1:` instead of at module level. No function, variable, UI element, processing
# step, or dependency was altered.
# =====================================================================================
with tab1:
    st.title("✂️ MP3 Gap Cutter")
    st.caption("Upload an MP3, strip the dead air between words, download the result. Nothing else.")


    def strip_gaps(sound):
        """Same gap-stripping settings verified earlier in this project: min_silence_len=200,
        keep_silence=50 -- wide enough to not clip word endings into gibberish, tight
        enough to actually kill the dead air between them."""
        chunks = silence.split_on_silence(sound, min_silence_len=200, silence_thresh=-40, keep_silence=50)
        combined = AudioSegment.empty()
        for chunk in chunks:
            combined += chunk
        return combined


    uploaded_file = st.file_uploader("Drop your MP3 here", type=["mp3"])

    if uploaded_file:
        if st.button("✂️ CUT THE GAPS", use_container_width=True):
            with st.spinner("Cutting gaps..."):
                try:
                    raw_sound = AudioSegment.from_file(io.BytesIO(uploaded_file.getvalue()), format="mp3")
                    original_ms = len(raw_sound)
                    cleaned = strip_gaps(raw_sound)
                    cleaned_ms = len(cleaned)

                    buffer = io.BytesIO()
                    cleaned.export(buffer, format="mp3", bitrate="128k")
                    audio_bytes = buffer.getvalue()

                    cut_seconds = (original_ms - cleaned_ms) / 1000
                    st.success(f"Done -- {original_ms/1000:.2f}s → {cleaned_ms/1000:.2f}s (cut {cut_seconds:.2f}s of dead air)")
                    st.audio(audio_bytes, format="audio/mp3")
                    st.download_button("📥 DOWNLOAD CLEANED MP3", data=audio_bytes,
                                        file_name=f"cleaned_{uploaded_file.name}", mime="audio/mpeg")
                except Exception as e:
                    st.error(f"❌ Couldn't process this file. Real reason: {e}")

# =====================================================================================
# TAB 2 -- NEW: VIDEO ENHANCER
# Real talk on what this actually is: this is FFmpeg-based cleanup (denoise, deband,
# sharpen) plus a high-quality upscale -- NOT true AI super-resolution. It genuinely
# reduces compression artifacts and produces a cleaner, crisper result that holds up
# better when you zoom in, but it cannot invent detail that the source never captured.
# True AI super-resolution (Real-ESRGAN etc.) was deliberately NOT used here: it pulls
# in GPU-oriented PyTorch/CUDA dependencies that don't install cleanly without a GPU
# (confirmed by literally trying it), and would be far too slow on Streamlit Cloud's
# free CPU-only tier regardless.
# =====================================================================================
with tab2:
    st.title("📼 Video Enhancer")
    st.caption("Cleans up compression artifacts, sharpens, and upscales -- so your gameplay "
               "footage holds up when you zoom in for a rant video. Not AI upscaling, real FFmpeg "
               "processing: denoise → deband → upscale → sharpen.")

    target_res = st.selectbox("Target resolution", ["1080p (1920x1080)", "1440p (2560x1440)", "Keep original size, just clean it up"])
    strength = st.select_slider("Cleanup strength", options=["Light", "Medium", "Strong"], value="Medium",
                                 help="Stronger removes more compression noise but can look slightly softer before the sharpen pass corrects it.")
    sharpen_amount = st.slider("Sharpen amount", min_value=0.0, max_value=2.0, value=0.8, step=0.1)

    STRENGTH_PARAMS = {
        "Light": "hqdn3d=2:1.5:3:2",
        "Medium": "hqdn3d=4:3:6:4",
        "Strong": "hqdn3d=6:4.5:9:6",
    }

    video_file = st.file_uploader("Drop your video here", type=["mp4", "mov", "mkv"])

    if video_file:
        if st.button("📼 ENHANCE VIDEO", use_container_width=True):
            with st.spinner("Enhancing... this genuinely takes a few minutes, it's really processing every frame."):
                in_path = None
                out_path = None
                try:
                    with tempfile.NamedTemporaryFile(delete=False, suffix=f"_{video_file.name}") as tmp_in:
                        tmp_in.write(video_file.getvalue())
                        in_path = tmp_in.name
                    out_path = in_path.rsplit(".", 1)[0] + "_enhanced.mp4"

                    probe = subprocess.run(
                        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", in_path],
                        capture_output=True, text=True,
                    )
                    import json as _json
                    duration = float(_json.loads(probe.stdout)["format"]["duration"]) if probe.returncode == 0 else None

                    filters = [STRENGTH_PARAMS[strength], "deblock=filter=strong:block=4"]
                    if target_res == "1080p (1920x1080)":
                        filters.append("scale=1920:1080:flags=lanczos")
                    elif target_res == "1440p (2560x1440)":
                        filters.append("scale=2560:1440:flags=lanczos")
                    if sharpen_amount > 0:
                        filters.append(f"unsharp=5:5:{sharpen_amount}:5:5:0.0")
                    filter_chain = ",".join(filters)

                    start_time = time.time()
                    result = subprocess.run(
                        ["ffmpeg", "-y", "-i", in_path, "-vf", filter_chain,
                         "-c:v", "libx264", "-crf", "18", "-preset", "medium",
                         "-c:a", "copy", out_path],
                        capture_output=True, text=True,
                    )
                    elapsed = time.time() - start_time

                    if result.returncode != 0:
                        st.error(f"❌ FFmpeg failed. Real reason: {result.stderr.strip()[-800:]}")
                    else:
                        with open(out_path, "rb") as f:
                            enhanced_bytes = f.read()
                        speed_note = f" ({elapsed/duration:.1f}x real-time)" if duration else ""
                        st.success(f"Done in {elapsed:.0f}s{speed_note}")
                        st.video(enhanced_bytes)
                        st.download_button("📥 DOWNLOAD ENHANCED VIDEO", data=enhanced_bytes,
                                            file_name=f"enhanced_{video_file.name}", mime="video/mp4")
                except Exception as e:
                    st.error(f"❌ Couldn't process this file. Real reason: {e}")
                finally:
                    for p in (in_path, out_path):
                        if p and os.path.exists(p):
                            os.remove(p)