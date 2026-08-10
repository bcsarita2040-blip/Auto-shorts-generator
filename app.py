import streamlit as st  # Built by Adrien Treuille, Thiago Teixeira, & Amanda Kelly
import io
from pydub import AudioSegment, silence  # Built by James Robert

st.set_page_config(page_title="Gap Cutter", page_icon="✂️")
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