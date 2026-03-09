import os
import json
import base64
import tempfile
import streamlit as st
from openai import OpenAI

from prompts import PROMPT_B2
from report_generator import create_report


st.set_page_config(page_title="B2 Writing Correction", layout="centered")

# ---------------------------
# Logo centrado
# ---------------------------
c1, c2, c3 = st.columns([1, 2, 1])
with c2:
    if os.path.exists("logo.jpg"):
        st.image("logo.jpg", width=320)

st.title("B2 Writing Correction")
st.write("Paste the task and your answer to receive feedback.")


# ---------------------------
# API key: local o cloud
# ---------------------------
api_key = ""

try:
    api_key = st.secrets.get("OPENAI_API_KEY", "")
except Exception:
    api_key = os.environ.get("OPENAI_API_KEY", "")

if not api_key:
    st.error("Missing OPENAI_API_KEY.")
    st.stop()

client = OpenAI(api_key=api_key)


# ---------------------------
# Inputs
# ---------------------------
task = st.text_area("Task", height=150)

task_image = st.file_uploader(
    "Optional: upload the task image",
    type=["png", "jpg", "jpeg", "webp"]
)

answer = st.text_area("Your Answer", height=250)


# ---------------------------
# Helpers
# ---------------------------
def build_prompt(task_text: str, student_text: str, has_image: bool) -> str:
    image_note = ""
    if has_image:
        image_note = "\n\nNOTE: A task image is provided. Use the image content as part of the task instructions."
    return PROMPT_B2.replace("{{TASK}}", (task_text or "") + image_note).replace("{{STUDENT_TEXT}}", student_text or "")


def clean_json(text: str) -> str:
    return text.strip().replace("```json", "").replace("```", "").strip()


def read_bytes(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def ensure_list(x):
    if isinstance(x, list):
        return x
    if isinstance(x, str):
        x = x.strip()
        return [x] if x else []
    return []


def ensure_dict(x):
    return x if isinstance(x, dict) else {}


def normalize_data(data: dict) -> dict:
    data = ensure_dict(data)

    data["strengths"] = ensure_dict(data.get("strengths"))
    data["weaknesses"] = ensure_dict(data.get("weaknesses"))

    for k in ["content", "communicative_achievement", "organisation", "language"]:
        data["strengths"][k] = ensure_list(data["strengths"].get(k))
        data["weaknesses"][k] = ensure_list(data["weaknesses"].get(k))

    data["improvement_summary"] = ensure_list(data.get("improvement_summary"))

    corr_list = ensure_list(data.get("corrections", []))
    fixed_corr = []
    for c in corr_list:
        if isinstance(c, dict):
            fixed_corr.append(c)
        elif isinstance(c, str) and c.strip():
            fixed_corr.append({"error_fragment": c.strip(), "suggestion": ""})
    data["corrections"] = fixed_corr

    data["criterion_feedback"] = ensure_dict(data.get("criterion_feedback"))

    data["criterion_evidence"] = ensure_dict(data.get("criterion_evidence"))
    for k in ["content", "communicative_achievement", "organisation", "language"]:
        ev = ensure_list(data["criterion_evidence"].get(k))
        ev = [str(x) for x in ev[:2]]
        while len(ev) < 2:
            ev.append("")
        data["criterion_evidence"][k] = ev

    data["score"] = ensure_dict(data.get("score"))
    for k in ["content", "communicative_achievement", "organisation", "language", "total"]:
        if k not in data["score"]:
            data["score"][k] = 0
    if "overall_band" not in data["score"]:
        data["score"]["overall_band"] = "B2"

    data.setdefault("model_answer", "")
    data.setdefault("overall_comment", "")
    data.setdefault("task_type", "")
    data.setdefault("level", "B2")
    data.setdefault("task_extracted", "")

    return data


def image_to_data_url(uploaded_file) -> str:
    b = uploaded_file.getvalue()
    mime = uploaded_file.type or "image/jpeg"
    b64 = base64.b64encode(b).decode("utf-8")
    return f"data:{mime};base64,{b64}"


def save_task_image(uploaded_file) -> str:
    ext = "jpg"
    if uploaded_file.type and "png" in uploaded_file.type:
        ext = "png"
    elif uploaded_file.type and "webp" in uploaded_file.type:
        ext = "webp"
    elif uploaded_file.name and "." in uploaded_file.name:
        ext = uploaded_file.name.split(".")[-1].lower()

    path = f"task_image_upload.{ext}"
    with open(path, "wb") as f:
        f.write(uploaded_file.getvalue())
    return path


def word_count_check(text: str):
    words = text.split()
    count = len(words)

    min_words = 140
    max_words = 190

    if count < min_words:
        status = "Below recommended length"
    elif count > max_words:
        status = "Above recommended length"
    else:
        status = "Within recommended range"

    return count, status, min_words, max_words


def estimate_cefr(score: int) -> str:
    if score >= 18:
        return "Strong B2 / approaching C1"
    elif score >= 15:
        return "Solid B2"
    elif score >= 12:
        return "Borderline B2 (B1+)"
    elif score >= 9:
        return "B1"
    else:
        return "Below B1"


def build_inline_corrections_html(text: str, corrections: list) -> str:
    if not text:
        return ""

    html = text

    for c in corrections:
        if not isinstance(c, dict):
            continue

        error = str(c.get("error_fragment", "")).strip()
        suggestion = str(c.get("suggestion", "")).strip()

        if not error or error not in html:
            continue

        if suggestion:
            replacement = (
                f'<span style="text-decoration: underline; text-decoration-color: #d88; '
                f'text-decoration-thickness: 2px;">{error}</span>'
                f' <span style="color:#1f4e79;"><b>→ {suggestion}</b></span>'
            )
        else:
            replacement = (
                f'<span style="text-decoration: underline; text-decoration-color: #d88; '
                f'text-decoration-thickness: 2px;">{error}</span>'
            )

        html = html.replace(error, replacement, 1)

    html = html.replace("\n", "<br>")
    return html


# ---------------------------
# Generate
# ---------------------------
if st.button("Generate Feedback"):

    if not answer:
        st.warning("Please paste your answer.")
        st.stop()

    if not task and not task_image:
        st.warning("Please paste the task or upload an image.")
        st.stop()

    with st.spinner("Evaluating writing..."):
        try:
            prompt = build_prompt(task, answer, has_image=task_image is not None)

            task_image_path = ""
            if task_image:
                task_image_path = save_task_image(task_image)

                data_url = image_to_data_url(task_image)

                messages = [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": data_url}}
                        ]
                    }
                ]
            else:
                messages = [
                    {
                        "role": "user",
                        "content": prompt
                    }
                ]

            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                temperature=0.2
            )

            raw = response.choices[0].message.content
            raw = clean_json(raw)
            data = json.loads(raw)
            data = normalize_data(data)

            # ---------------------------
            # Extra improvements
            # ---------------------------
            word_count, word_status, min_words, max_words = word_count_check(answer)
            estimated_cefr = estimate_cefr(int(data["score"].get("total", 0)))

            data["word_count"] = word_count
            data["word_count_status"] = word_status
            data["recommended_range"] = f"{min_words}-{max_words}"
            data["estimated_cefr"] = estimated_cefr

            if task_image_path:
                data["task_image_path"] = task_image_path

            docx_path, pdf_path = create_report(data, task, answer)

        except Exception as e:
            st.error(f"OpenAI / report error: {e}")
            st.stop()

    st.success("Report generated!")

    # ---------------------------
    # On-screen improvements
    # ---------------------------
    st.markdown("## Quick Overview")

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("### Word Count")
        st.write(f"**Word count:** {word_count}")
        st.write(f"**Recommended range:** {min_words}-{max_words} words")
        st.write(f"**Status:** {word_status}")

    with c2:
        st.markdown("### Estimated CEFR Level")
        st.write(f"**{estimated_cefr}**")
        st.write(f"**Total score:** {data['score'].get('total', 0)} / 20")
        st.write(f"**Overall band:** {data['score'].get('overall_band', 'B2')}")

    if data.get("corrections"):
        st.markdown("### Student Text with Inline Corrections")
        corrected_html = build_inline_corrections_html(answer, data["corrections"])
        st.markdown(corrected_html, unsafe_allow_html=True)

    with open(docx_path, "rb") as f:
        st.download_button(
            "Download DOCX",
            f,
            file_name="writing_feedback.docx"
        )

    if pdf_path and os.path.exists(pdf_path):
        with open(pdf_path, "rb") as f:
            st.download_button(
                "Download PDF",
                f,
                file_name="writing_feedback.pdf"
            )