import requests
import random
import datetime
import os
import re
import csv
import PyPDF2
import io
import logging
import threading
import smtplib
import html
from email.message import EmailMessage
from email.utils import formataddr

from bson import ObjectId
from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    session,
    send_file,
)
from dotenv import load_dotenv
from pymongo import MongoClient
from pymongo.errors import PyMongoError
from reportlab.pdfgen import canvas
from database.mongo import db_manager

try:
    from sentence_transformers import SentenceTransformer, util
except ImportError:
    SentenceTransformer = None
    util = None

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-pro")

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret-key-change-me")
app.logger.setLevel(logging.INFO)

MONGODB_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
MONGODB_DB_NAME = os.getenv("MONGODB_DB_NAME", "ai_interview_system")
MAX_WARNINGS = 3
REQUEST_TIMEOUT = 10
SENTENCE_TRANSFORMER_MODEL = os.getenv(
    "SENTENCE_TRANSFORMER_MODEL",
    "sentence-transformers/all-MiniLM-L6-v2",
)
RESULT_EMAIL_DELAY_SECONDS = int(os.getenv("RESULT_EMAIL_DELAY_SECONDS", "300"))
SMTP_HOST = os.getenv("SMTP_HOST", os.getenv("SMTP_SERVER", ""))
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME = os.getenv("SMTP_USERNAME", os.getenv("SMTP_EMAIL", ""))
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_USE_TLS = os.getenv("SMTP_USE_TLS", "true").lower() == "true"
EMAIL_SENDER = os.getenv("EMAIL_SENDER", SMTP_USERNAME)
FASTAPI_BASE_URL = os.getenv("FASTAPI_BASE_URL", "http://127.0.0.1:8000/api")
SPEECH_SERVICE_URL = os.getenv("SPEECH_SERVICE_URL", "http://127.0.0.1:9000")

mongo_client = None
mongo_db = None
sentence_model = None
TECHNICAL_TERM_CORRECTIONS = {
    "encapsulation": [
        "in capsule ation",
        "encapsulasion",
        "encapsolation",
        "encapsulations",
        "incapsulation",
        "encapsuation",
        "encapsulaton",
    ],
    "inheritance": [
        "in heritance",
        "inheritence",
        "inneritance",
        "inheritanc",
        "ineritance",
        "inheretance",
    ],
    "polymorphism": [
        "poly morphism",
        "polly morphism",
        "polymorfism",
        "polimorphism",
        "polymorphysm",
        "polymorphim",
    ],
    "REST API": [
        "rest ap i",
        "rest a p i",
        "restapi",
        "rest a p eye",
    ],
    "microservices": [
        "micro services",
        "microservice",
        "micro service",
        "microservice is",
        "microservis",
    ],
    "API": [
        "a p i",
        "ap i",
        "a p eye",
    ],
    "database": [
        "data base",
        "data bases",
        "databace",
    ],
    "algorithm": [
        "algo rhythm",
        "algo rithm",
        "algoritm",
        "algorythm",
    ],
    "framework": [
        "frame work",
        "frame werk",
        "framwork",
    ],
}


# =========================
# DATABASE CONNECTION
# =========================
def get_db():
    global mongo_client, mongo_db

    if mongo_db is not None:
        return mongo_db
    
    # Check if we should skip MongoDB
    if os.getenv("USE_IN_MEMORY_DB", "false").lower() == "true":
        return None

    try:
        mongo_client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=3000)
        mongo_client.admin.command("ping")
        mongo_db = mongo_client[MONGODB_DB_NAME]
        return mongo_db
    except Exception as e:
        app.logger.warning(f"MongoDB not available: {e}")
        return None


# =========================
# DATABASE INITIALIZATION
# =========================
def init_db():
    db = get_db()
    if db is None:
        app.logger.info("Running without MongoDB - using in-memory mode")
        return
    
    try:
        db.questions.create_index("domain")
        db.candidates.create_index([("created_at", -1)])
        app.logger.info("MongoDB initialized successfully")
    except Exception as e:
        app.logger.warning(f"MongoDB initialization failed: {e}")


def serialize_mongo_doc(document):

    if not document:
        return document

    doc = dict(document)
    if "_id" in doc:
        doc["id"] = str(doc.pop("_id"))
    return doc


def sync_fastapi_session(candidate_name, candidate_email, mode, metadata, questions, greeting=""):

    normalized_questions = []
    for item in questions[:5]:
        normalized_questions.append(
            {
                "question": item.get("question", ""),
                "expected_keywords": item.get("expected_keywords", item.get("keywords", [])),
                "source": item.get("source", "legacy-flask"),
            }
        )

    session_doc = db_manager.create_session(
        candidate_name=candidate_name,
        candidate_email=(candidate_email or "").strip(),
        mode=mode,
        metadata=metadata or {},
        questions=normalized_questions,
    )

    if greeting:
        db_manager.update_session_greeting(session_doc["session_id"], greeting)

    return session_doc["session_id"]


def parse_object_id(value):

    try:
        return ObjectId(value)
    except Exception as exc:
        raise ValueError("Invalid document id") from exc


def get_sentence_model():

    global sentence_model

    if sentence_model is None and SentenceTransformer is not None:
        sentence_model = SentenceTransformer(SENTENCE_TRANSFORMER_MODEL)

    return sentence_model


def apply_technical_corrections(text):

    corrected = text or ""
    for canonical, variants in TECHNICAL_TERM_CORRECTIONS.items():
        for variant in variants:
            corrected = re.sub(
                rf"\b{re.escape(variant)}\b",
                canonical,
                corrected,
                flags=re.IGNORECASE,
            )
    corrected = re.sub(r"\s+", " ", corrected).strip()
    return corrected


def build_recruiter_summary(questions, answers, feedback_items, percentage, result_status):

    try:
        prompt = f"""
You are an AI recruiter assistant.
Based on this interview performance, create a concise recruiter summary.

Overall Score: {percentage}%
Final Result: {result_status}

Questions and answers:
{chr(10).join([f"Q: {q['question']}{chr(10)}A: {answers[i] if i < len(answers) else 'No answer'}{chr(10)}Feedback: {feedback_items[i] if i < len(feedback_items) else ''}" for i, q in enumerate(questions)])}

Return exactly in this format:
Communication Level: <Low/Moderate/High>
Technical Confidence: <Low/Moderate/High>
Strengths: <short sentence>
Weaknesses: <short sentence>
Hiring Recommendation: <Hire/Consider/Needs Improvement>
"""
        summary_text = call_gemini(prompt, timeout=10)
        lines = [line.strip() for line in summary_text.splitlines() if ":" in line]
        parsed = {}
        for line in lines:
            key, value = line.split(":", 1)
            parsed[key.strip().lower()] = value.strip()
        if parsed:
            return {
                "communication_level": parsed.get("communication level", "Moderate"),
                "technical_confidence": parsed.get("technical confidence", "Moderate"),
                "strengths": parsed.get("strengths", "Shows reasonable understanding of core concepts."),
                "weaknesses": parsed.get("weaknesses", "Needs more depth and clearer examples in some answers."),
                "hiring_recommendation": parsed.get("hiring recommendation", "Consider"),
            }
    except Exception as exc:
        app.logger.warning("Recruiter summary fallback used: %s", exc)

    answered_count = sum(1 for answer in answers if (answer or "").strip() not in {"", "NO_ANSWER", "SKIPPED"})
    avg_answer_words = 0
    if answers:
        avg_answer_words = sum(len((answer or "").split()) for answer in answers) / len(answers)

    if avg_answer_words >= 20:
        communication_level = "High"
    elif avg_answer_words >= 8:
        communication_level = "Moderate"
    else:
        communication_level = "Low"

    if percentage >= 75:
        technical_confidence = "High"
        recommendation = "Hire"
    elif percentage >= 55:
        technical_confidence = "Moderate"
        recommendation = "Consider"
    else:
        technical_confidence = "Low"
        recommendation = "Needs Improvement"

    return {
        "communication_level": communication_level,
        "technical_confidence": technical_confidence,
        "strengths": f"Answered {answered_count} questions with an overall score of {percentage}%.",
        "weaknesses": "Needs sharper technical depth and more structured examples in weaker responses.",
        "hiring_recommendation": recommendation,
    }


def wrap_pdf_text(text, max_chars=95):

    words = (text or "").split()
    if not words:
        return [""]

    lines = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if len(candidate) <= max_chars:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def ensure_pdf_space(pdf, y, required_height):

    if y > required_height:
        return y

    pdf.showPage()
    return 780


def generate_detailed_report_pdf(candidate_name, interview_domain, percentage, result_status, feedback, recruiter_summary):

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer)
    y = 780
    page_width = 595

    status_color = (0.10, 0.64, 0.33) if result_status == "Pass" else (0.86, 0.18, 0.18)

    pdf.setFillColorRGB(0.06, 0.12, 0.24)
    pdf.roundRect(36, 730, 523, 70, 14, fill=1, stroke=0)
    pdf.setFillColorRGB(1, 1, 1)
    pdf.setFont("Helvetica-Bold", 18)
    pdf.drawString(54, 772, "AgnoHire AI Interview Report")
    pdf.setFont("Helvetica", 10)
    pdf.drawString(54, 752, f"Generated on {datetime.date.today()} for recruiter review")

    pdf.setFillColorRGB(*status_color)
    pdf.roundRect(445, 746, 92, 28, 10, fill=1, stroke=0)
    pdf.setFillColorRGB(1, 1, 1)
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawCentredString(491, 756, result_status.upper())
    y = 706

    pdf.setFillColorRGB(0.07, 0.10, 0.16)
    pdf.roundRect(36, y - 68, 523, 58, 12, fill=0, stroke=1)
    pdf.setFont("Helvetica", 11)
    pdf.drawString(54, y - 24, f"Candidate Name: {candidate_name}")
    pdf.drawString(54, y - 44, f"Interview Type: {interview_domain}")
    pdf.drawString(300, y - 24, f"Overall Score: {percentage}%")
    pdf.drawString(300, y - 44, f"Hiring Recommendation: {recruiter_summary.get('hiring_recommendation', 'Consider')}")
    y -= 92

    pdf.setFillColorRGB(0.95, 0.97, 1.0)
    pdf.roundRect(36, y - 112, 523, 102, 12, fill=1, stroke=0)
    pdf.setFillColorRGB(0.07, 0.10, 0.16)
    pdf.setFont("Helvetica-Bold", 12)
    pdf.drawString(50, y - 8, "AI Recruiter Summary")
    y -= 22

    pdf.setFont("Helvetica", 10)
    summary_lines = [
        f"Communication Level: {recruiter_summary.get('communication_level', 'Moderate')}",
        f"Technical Confidence: {recruiter_summary.get('technical_confidence', 'Moderate')}",
        f"Strengths: {recruiter_summary.get('strengths', '')}",
        f"Weaknesses: {recruiter_summary.get('weaknesses', '')}",
        f"Hiring Recommendation: {recruiter_summary.get('hiring_recommendation', 'Consider')}",
    ]
    for line in summary_lines:
        for wrapped in wrap_pdf_text(line, max_chars=85):
            pdf.drawString(50, y - 6, wrapped)
            y -= 15

    y -= 14
    y = ensure_pdf_space(pdf, y, 130)

    pdf.setFont("Helvetica-Bold", 12)
    pdf.drawString(50, y, "Detailed Feedback")
    y -= 22

    for index, item in enumerate(feedback, start=1):
        lines = wrap_pdf_text(item, max_chars=88)
        block_height = 32 + (len(lines) * 14)
        y = ensure_pdf_space(pdf, y, block_height + 70)

        pdf.setFillColorRGB(0.98, 0.98, 0.99)
        pdf.roundRect(42, y - block_height + 6, 510, block_height, 10, fill=1, stroke=0)
        pdf.setFillColorRGB(0.07, 0.10, 0.16)
        pdf.setFont("Helvetica-Bold", 11)
        pdf.drawString(54, y - 14, f"Question {index}")
        pdf.setFont("Helvetica", 10)
        line_y = y - 32
        for line in lines:
            pdf.drawString(54, line_y, line)
            line_y -= 14
        y -= block_height + 10

    y = ensure_pdf_space(pdf, y, 90)
    pdf.setFillColorRGB(0.06, 0.12, 0.24)
    pdf.line(36, 48, page_width - 36, 48)
    pdf.setFillColorRGB(0.35, 0.40, 0.48)
    pdf.setFont("Helvetica", 9)
    pdf.drawString(36, 34, "AI-generated interview summary for recruiter reference")

    pdf.save()
    buffer.seek(0)
    return buffer.getvalue()


def send_result_email(candidate_email, candidate_name, percentage, result_status, interview_domain, feedback, recruiter_summary):

    if not candidate_email:
        return

    if not SMTP_HOST or not SMTP_USERNAME or not SMTP_PASSWORD or not EMAIL_SENDER:
        app.logger.warning("Result email skipped because SMTP settings are incomplete.")
        return

    subject = "Your AI Interview Report"
    status_bg = "#16a34a" if result_status == "Pass" else "#dc2626"
    body = (
        f"Hello {candidate_name},\n\n"
        f"Your {interview_domain} interview result is now available.\n\n"
        f"Status: {result_status}\n"
        f"Score: {percentage}%\n\n"
        "Detailed Feedback:\n"
        + "\n".join(f"- {item}" for item in (feedback or []))
        + "\n\n"
        "Thank you for completing the interview.\n"
        "Please contact the administrator if you need further details.\n"
    )
    html_feedback = "".join(
        f"<li style='margin-bottom:10px;'>{html.escape(item)}</li>"
        for item in (feedback or [])
    )
    html_body = f"""
    <html>
      <body style="margin:0;padding:24px;background:#eef4ff;font-family:Segoe UI,Arial,sans-serif;color:#0f172a;">
        <div style="max-width:720px;margin:0 auto;background:#ffffff;border-radius:18px;overflow:hidden;border:1px solid #dbe3ef;">
          <div style="background:#0f172a;padding:24px 28px;color:#ffffff;">
            <div style="font-size:22px;font-weight:700;">AgnoHire AI Interview Result</div>
            <div style="margin-top:8px;font-size:13px;opacity:0.85;">Professional interview summary and recruiter review</div>
          </div>

          <div style="padding:28px;">
            <p style="margin-top:0;font-size:16px;">Hello <strong>{html.escape(candidate_name)}</strong>,</p>
            <p style="line-height:1.7;">Your <strong>{html.escape(interview_domain)}</strong> interview result is now available.</p>

            <div style="display:flex;gap:12px;flex-wrap:wrap;margin:22px 0;">
              <div style="padding:14px 18px;border-radius:14px;background:#f8fafc;border:1px solid #dbe3ef;">
                <div style="font-size:12px;text-transform:uppercase;color:#64748b;font-weight:700;">Score</div>
                <div style="margin-top:6px;font-size:28px;font-weight:800;">{percentage}%</div>
              </div>
              <div style="padding:14px 18px;border-radius:14px;background:{status_bg};color:#ffffff;min-width:120px;">
                <div style="font-size:12px;text-transform:uppercase;font-weight:700;opacity:0.9;">Status</div>
                <div style="margin-top:6px;font-size:24px;font-weight:800;">{html.escape(result_status)}</div>
              </div>
              <div style="padding:14px 18px;border-radius:14px;background:#f8fafc;border:1px solid #dbe3ef;flex:1;min-width:220px;">
                <div style="font-size:12px;text-transform:uppercase;color:#64748b;font-weight:700;">Hiring Recommendation</div>
                <div style="margin-top:6px;font-size:20px;font-weight:800;">{html.escape(recruiter_summary.get('hiring_recommendation', 'Consider'))}</div>
              </div>
            </div>

            <div style="padding:18px;border-radius:16px;background:#f8fbff;border:1px solid #dbe3ef;">
              <div style="font-size:18px;font-weight:700;margin-bottom:10px;">AI Recruiter Summary</div>
              <p style="margin:8px 0;"><strong>Communication Level:</strong> {html.escape(recruiter_summary.get('communication_level', 'Moderate'))}</p>
              <p style="margin:8px 0;"><strong>Technical Confidence:</strong> {html.escape(recruiter_summary.get('technical_confidence', 'Moderate'))}</p>
              <p style="margin:8px 0;"><strong>Strengths:</strong> {html.escape(recruiter_summary.get('strengths', ''))}</p>
              <p style="margin:8px 0;"><strong>Weaknesses:</strong> {html.escape(recruiter_summary.get('weaknesses', ''))}</p>
            </div>

            <div style="margin-top:22px;">
              <div style="font-size:18px;font-weight:700;margin-bottom:10px;">Detailed Feedback</div>
              <ul style="padding-left:20px;line-height:1.7;margin:0;">
                {html_feedback}
              </ul>
            </div>

            <p style="margin-top:24px;line-height:1.7;color:#475569;">
              The detailed PDF report is attached to this email for recruiter and candidate review.
            </p>
          </div>
        </div>
      </body>
    </html>
    """

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = formataddr(("AI Interview System", EMAIL_SENDER))
    message["To"] = candidate_email
    message.set_content(body)
    message.add_alternative(html_body, subtype="html")

    pdf_bytes = generate_detailed_report_pdf(
        candidate_name=candidate_name,
        interview_domain=interview_domain,
        percentage=percentage,
        result_status=result_status,
        feedback=feedback,
        recruiter_summary=recruiter_summary,
    )
    message.add_attachment(
        pdf_bytes,
        maintype="application",
        subtype="pdf",
        filename="Interview_Report.pdf",
    )

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
            server.ehlo()
            if SMTP_USE_TLS:
                server.starttls()
                server.ehlo()
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.send_message(message)
        app.logger.info("Interview result email sent to %s", candidate_email)
    except Exception as exc:
        app.logger.warning("Failed to send interview result email to %s: %s", candidate_email, exc)


def schedule_result_email(candidate_email, candidate_name, percentage, result_status, interview_domain, feedback, recruiter_summary):

    if not candidate_email:
        return

    timer = threading.Timer(
        RESULT_EMAIL_DELAY_SECONDS,
        send_result_email,
        args=(candidate_email, candidate_name, percentage, result_status, interview_domain, feedback, recruiter_summary),
    )
    timer.daemon = True
    timer.start()


def call_gemini(prompt, timeout=REQUEST_TIMEOUT):
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY is not set")

    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
    )
    headers = {"Content-Type": "application/json"}
    data = {"contents": [{"parts": [{"text": prompt}]}]}

    response = requests.post(url, headers=headers, json=data, timeout=timeout)
    response.raise_for_status()
    result = response.json()

    return result["candidates"][0]["content"]["parts"][0]["text"].strip()


# =========================
# AI QUESTION GENERATOR
# =========================
def generate_ai_question(domain, difficulty):

    fresher_fallback = [
        f"Tell me one {domain} project you did in college.",
        f"If your {domain} code is not working, what is the first thing you check?",
        f"If your {domain} program is slow, what simple fix would you try first?",
        f"Before final submission of a {domain} project, what two checks do you do?",
        f"Name one real use of {domain} in a student project.",
    ]

    tough_patterns = [
        "system design",
        "microservices",
        "distributed",
        "consistency",
        "kubernetes",
        "scalability",
        "eventual consistency",
        "high availability",
        "load balancer",
        "sharding",
        "fault tolerance",
    ]

    basic_patterns = ["what is", "where is", "advantages", "define ", "explain the basics"]

    try:

        prompt = f"""
You are interviewing a fresher (entry-level candidate).
Create exactly ONE {difficulty} level interview question for {domain}.

Rules:
- Ask a SIMPLE fresher-level question only.
- Keep language easy and direct (one sentence).
- Ask about very basic coding tasks, simple debugging, and student mini-project experience.
- Strictly avoid advanced topics like system design, distributed systems, scalability design, architecture patterns.
- Do NOT ask textbook-style "what is", "where is it used", or "advantages/disadvantages" questions.
- Keep it under 16 words.
- Return only one question line, no numbering.
"""

        question = call_gemini(prompt, timeout=8)
        lowered = question.lower()

        if any(p in lowered for p in basic_patterns):
            return random.choice(fresher_fallback)

        if any(p in lowered for p in tough_patterns):
            return random.choice(fresher_fallback)

        if len(question.split()) > 16:
            return random.choice(fresher_fallback)

        return question

    except (requests.RequestException, KeyError, IndexError, RuntimeError, ValueError) as exc:
        app.logger.warning("Falling back to local question for domain '%s': %s", domain, exc)
        return random.choice(fresher_fallback)


def get_easy_domain_questions(domain, total=5):

    domain_question_bank = {
        "Java": [
            "Explain encapsulation with a simple class example.",
            "What is the difference between method overloading and overriding?",
            "What is the difference between abstract class and interface?",
            "Explain inheritance and polymorphism with one real example.",
            "What is exception handling in Java and why is it needed?",
            "Difference between checked and unchecked exceptions in Java.",
            "What is the difference between final, finally, and finalize?",
            "What is the use of try-catch-finally block in Java?",
            "Difference between ArrayList and LinkedList in Java.",
            "Difference between String, StringBuilder, and StringBuffer.",
        ],
        "Python": [
            "Difference between list and tuple in Python.",
            "What is the use of dictionary in Python?",
            "Difference between *args and **kwargs.",
            "What is exception handling in Python with example?",
            "Difference between break, continue, and pass.",
            "What is the use of __init__ in Python classes?",
            "Difference between shallow copy and deep copy.",
            "What is list comprehension in Python?",
        ],
        "C++": [
            "Difference between class and struct in C++.",
            "What is constructor and destructor in C++?",
            "Difference between function overloading and overriding.",
            "What is the use of virtual function in C++?",
            "Difference between pointer and reference in C++.",
            "What is exception handling in C++?",
            "What is STL and name any two STL containers.",
            "Difference between stack and heap memory in C++.",
        ],
        "C Programming": [
            "Difference between array and pointer in C.",
            "What is structure in C and why do we use it?",
            "Difference between call by value and call by reference.",
            "What is dynamic memory allocation in C?",
            "Difference between malloc and calloc.",
            "What is the use of pointers in C?",
            "What is file handling in C?",
            "Difference between while loop and do-while loop.",
        ],
        "DBMS": [
            "What is normalization and why is it important?",
            "Difference between primary key and foreign key.",
            "What is the difference between DBMS and RDBMS?",
            "Explain ACID properties in simple terms.",
            "What is transaction in DBMS?",
            "Difference between DELETE, TRUNCATE, and DROP.",
            "What is indexing and why is it used?",
            "Difference between one-to-one and one-to-many relationship.",
        ],
        "SQL": [
            "Difference between WHERE and HAVING clause.",
            "Difference between INNER JOIN and LEFT JOIN.",
            "What is GROUP BY in SQL?",
            "What is a subquery in SQL?",
            "Difference between UNION and UNION ALL.",
            "What is the use of ORDER BY in SQL?",
            "Difference between CHAR and VARCHAR.",
            "What is primary key and unique key in SQL?",
        ],
    }

    easy_pool = domain_question_bank.get(
        domain,
        [
            f"Explain one important concept in {domain} with example.",
            f"What is the difference between two basic concepts in {domain} you learned?",
            f"Why is {domain} used in software development?",
            f"Name one common error in {domain} and how to avoid it.",
            f"Explain one beginner-level topic in {domain} in simple words.",
            f"Difference between theory and practical use of {domain}.",
        ],
    )

    if total >= len(easy_pool):
        random.shuffle(easy_pool)
        return easy_pool

    return random.sample(easy_pool, total)

# =========================
# AI ANSWER EVALUATION
# =========================
def extract_score(evaluation_text):

    match = re.search(r"Score\s*:\s*(\d+(?:\.\d+)?)", evaluation_text, re.IGNORECASE)
    if not match:
        return 0

    value = float(match.group(1))
    value = max(0.0, min(10.0, value))
    return int(round(value))


def evaluate_keyword_answer(question, answer, keywords):

    clean_answer = (answer or "").strip().lower()
    normalized_keywords = [k.strip() for k in (keywords or []) if k.strip()]

    if not normalized_keywords:
        return "Score: 0\nFeedback: No keywords were configured for this company question."

    matched = [k for k in normalized_keywords if k.lower() in clean_answer]
    missing = [k for k in normalized_keywords if k not in matched]
    score = int(round((len(matched) / len(normalized_keywords)) * 10))

    feedback = f"Matched {len(matched)} of {len(normalized_keywords)} keywords."
    if missing:
        feedback += f" Missing keywords: {', '.join(missing[:5])}."

    return f"Score: {score}\nFeedback: {feedback}"


def split_keywords(raw):

    return [item.strip(" -\t\r\n") for item in re.split(r"[\n,;]+", raw or "") if item.strip()]


def parse_company_questions_from_text(text, evaluation_mode):

    if evaluation_mode == "keyword":
        blocks = re.split(r"\n\s*\n", text)
        parsed = []
        for block in blocks:
            question_match = re.search(r"Question\s*:\s*(.+)", block, re.IGNORECASE)
            keywords_match = re.search(r"Keywords?\s*:\s*(.+)", block, re.IGNORECASE | re.DOTALL)
            if not question_match:
                continue
            parsed.append(
                {
                    "question": question_match.group(1).strip(),
                    "keywords": split_keywords(keywords_match.group(1) if keywords_match else ""),
                }
            )

        if parsed:
            return parsed[:5]

    questions = []
    for line in text.splitlines():
        cleaned = re.sub(r"^[\-\d.)\s]+", "", line.strip())
        lowered = cleaned.lower()
        looks_like_question = (
            len(cleaned) >= 10
            and (
                cleaned.endswith("?")
                or lowered.startswith(("what ", "how ", "why ", "explain ", "describe ", "tell ", "define "))
            )
        )
        if looks_like_question:
            questions.append({"question": cleaned, "keywords": []})

    return questions[:5]


def parse_company_upload(file_storage, evaluation_mode):

    filename = (file_storage.filename or "").lower()
    ext = filename.rsplit(".", 1)[-1] if "." in filename else ""

    if ext == "pdf":
        reader = PyPDF2.PdfReader(file_storage)
        text = ""
        for page in reader.pages:
            text += (page.extract_text() or "") + "\n"
        return parse_company_questions_from_text(text, evaluation_mode)

    if ext == "txt":
        text = file_storage.read().decode("utf-8", errors="ignore")
        return parse_company_questions_from_text(text, evaluation_mode)

    if ext == "csv":
        text = file_storage.read().decode("utf-8", errors="ignore")
        rows = list(csv.DictReader(io.StringIO(text)))
        parsed = []
        for row in rows:
            question = (row.get("question") or row.get("Question") or "").strip()
            keywords = row.get("keywords") or row.get("Keywords") or ""
            if question:
                parsed.append(
                    {
                        "question": question,
                        "keywords": split_keywords(keywords) if evaluation_mode == "keyword" else [],
                    }
                )
        return parsed[:5]

    if ext in {"xlsx", "xls"}:
        try:
            import pandas as pd
        except ImportError as exc:
            raise RuntimeError("Excel upload requires pandas and openpyxl.") from exc

        dataframe = pd.read_excel(file_storage)
        parsed = []
        for _, row in dataframe.fillna("").iterrows():
            question = str(row.get("question") or row.get("Question") or "").strip()
            keywords = str(row.get("keywords") or row.get("Keywords") or "")
            if question:
                parsed.append(
                    {
                        "question": question,
                        "keywords": split_keywords(keywords) if evaluation_mode == "keyword" else [],
                    }
                )
        return parsed[:5]

    raise RuntimeError("Unsupported file format. Use PDF, TXT, CSV, or Excel.")

def evaluate_answer_with_sentence_transformer(question, answer):

    model = get_sentence_model()
    if model is None or util is None:
        return None

    question_embedding = model.encode(question, convert_to_tensor=True)
    answer_embedding = model.encode(answer, convert_to_tensor=True)
    similarity = float(util.cos_sim(question_embedding, answer_embedding)[0][0])
    similarity = max(0.0, min(1.0, (similarity + 1.0) / 2.0))

    word_count = len(answer.split())
    detail_bonus = 0.1 if word_count > 20 else 0.0
    score = int(round(min(1.0, similarity + detail_bonus) * 10))

    if score >= 8:
        feedback = "Strong semantic alignment with the question and good answer detail."
        suggestion = "Add one concrete example to make the answer more interview-ready."
    elif score >= 5:
        feedback = "The answer is relevant, but it needs stronger technical depth and structure."
        suggestion = "Explain the concept more clearly and connect it to a practical example."
    else:
        feedback = "The answer has weak semantic alignment or is too brief."
        suggestion = "Answer directly, define the concept, and include one short real-world use case."

    return f"Score: {score}\nFeedback: {feedback}\nImprovement: {suggestion}"


def evaluate_answer(question, answer):

    clean_answer = (answer or "").strip()

    if clean_answer == "" or clean_answer == "NO_ANSWER":
        return "Score: 0\nFeedback: No answer provided."

    if clean_answer == "SKIPPED":
        return "Score: 1\nFeedback: Question was skipped."

    try:

        prompt = f"""
You are evaluating a fresher interview response.

Question:
{question}

Candidate Answer:
{clean_answer}

Scoring rubric (0 to 10):
- Relevance to question: 0-4
- Technical correctness: 0-4
- Clarity and completeness: 0-2

Important rules:
- Give fair, question-aligned score only.
- Avoid random extreme scoring.
- Use 0 or 10 only when clearly deserved.
- If answer is partially correct, use mid-range score.

Return exactly this format:
Score: <0-10>
Feedback: <1-2 concise sentences>
"""

        evaluation = call_gemini(prompt, timeout=10)

        score = extract_score(evaluation)
        if "Feedback:" not in evaluation:
            evaluation = f"Score: {score}\nFeedback: Answer evaluated based on relevance, correctness, and clarity."

        return evaluation

    except (requests.RequestException, KeyError, IndexError, RuntimeError, ValueError) as exc:
        app.logger.warning("Using fallback evaluation for question '%s': %s", question, exc)
        st_evaluation = evaluate_answer_with_sentence_transformer(question, clean_answer)
        if st_evaluation:
            return st_evaluation

        answer_words = len(clean_answer.split())
        if answer_words <= 3:
            score = 2
        elif answer_words <= 10:
            score = 4
        elif answer_words <= 25:
            score = 6
        else:
            score = 7

        return (
            f"Score: {score}\n"
            "Feedback: Partial automatic evaluation used because hosted AI and local semantic model were unavailable."
        )

# =========================
# HOME PAGE
# =========================
@app.route("/")
def index():

    domains = [
        "C Programming",
        "C++",
        "Java",
        "Python",
        "SQL",
        "DBMS",
        "Operating Systems",
        "Computer Networks",
        "Data Structures",
        "Algorithms",
        "Machine Learning",
        "Artificial Intelligence",
        "Cloud Computing",
        "Cyber Security",
        "React",
        "Spring Boot",
    ]

    return render_template("index.html", domains=domains)


# =========================
# START DOMAIN INTERVIEW
# =========================
@app.route("/start", methods=["POST"])
def start():

    session["name"] = request.form["name"]
    session["email"] = request.form.get("email", "").strip()
    session["domain"] = request.form["domain"]
    session["current"] = 0
    session["answers"] = []
    session["warnings"] = 0
    session["result_saved"] = False
    session["result_email_scheduled"] = False
    session["welcome"] = f"Welcome {session['name']}. Your AI interview starts now."
    # Instant load: avoid multiple external API calls on interview start.
    quick_questions = get_easy_domain_questions(session["domain"], total=5)
    session["questions"] = [{"question": q} for q in quick_questions]
    session["api_session_id"] = sync_fastapi_session(
        candidate_name=session["name"],
        candidate_email=session.get("email", ""),
        mode="domain",
        metadata={"domain": session["domain"], "source": "legacy-flask"},
        questions=session["questions"],
        greeting=session["welcome"],
    )

    return redirect(url_for("interview", session_id=session["api_session_id"]))


# =========================
# RESUME INTERVIEW
# =========================
@app.route("/resume_interview", methods=["POST"])
def resume_interview():

    session["name"] = request.form["name"]
    session["email"] = request.form.get("email", "").strip()
    session["domain"] = "Resume Interview"
    session["current"] = 0
    session["answers"] = []
    session["warnings"] = 0
    session["result_saved"] = False
    session["result_email_scheduled"] = False

    file = request.files["resume"]

    try:
        reader = PyPDF2.PdfReader(file)
        text = ""
        for page in reader.pages:
            text += page.extract_text() or ""
    except Exception as exc:
        app.logger.warning("Resume parsing failed: %s", exc)
        text = ""

    prompt = f"""
You are interviewing a candidate based on the resume.
Generate exactly 5 interview questions to assess whether the candidate is technically strong.

Rules:
- Questions must be based on the candidate's actual projects, tools, and technologies in resume.
- Include tool-focused questions (for example: Git, Docker, Postman, Jira, CI/CD, cloud tools, DB tools) only if tools appear in resume.
- Include depth-check questions: ownership, debugging approach, trade-offs, and production readiness.
- Keep questions practical and clear; avoid generic textbook questions like "what is X".
- Return only 5 questions, one per line, no numbering.

Resume:
{text}
"""

    try:
        questions_text = call_gemini(prompt, timeout=10)
        questions = [q for q in questions_text.split("\n") if q.strip() != ""]
    except (requests.RequestException, KeyError, IndexError, RuntimeError, ValueError) as exc:
        app.logger.warning("Using fallback resume questions: %s", exc)

        questions = [
            "Pick one project from your resume and explain your exact technical contribution.",
            "Which tool from your resume did you use most, and how did it improve your workflow?",
            "Describe one tough bug you solved and the steps you followed to debug it.",
            "How did you validate your project quality before demo or deployment?",
            "What tells you that your strongest technology skill is production-ready?",
        ]

    session["questions"] = [{"question": q} for q in questions[:5]]
    session["welcome"] = f"Welcome {session['name']}. Your AI interview starts now."
    session["api_session_id"] = sync_fastapi_session(
        candidate_name=session["name"],
        candidate_email=session.get("email", ""),
        mode="resume",
        metadata={"domain": session["domain"], "source": "legacy-flask"},
        questions=session["questions"],
        greeting=session["welcome"],
    )

    return redirect(url_for("interview", session_id=session["api_session_id"]))


@app.route("/company_interview", methods=["POST"])
def company_interview():

    session["name"] = request.form["name"]
    session["email"] = request.form.get("email", "").strip()
    session["domain"] = "Company Based Interview"
    session["current"] = 0
    session["answers"] = []
    session["warnings"] = 0
    session["result_saved"] = False
    session["result_email_scheduled"] = False
    session["evaluation_mode"] = request.form.get("evaluation_mode", "ai")

    company_file = request.files["company_file"]

    try:
        parsed_questions = parse_company_upload(company_file, session["evaluation_mode"])
    except Exception as exc:
        app.logger.warning("Company question parsing failed: %s", exc)
        parsed_questions = []

    if not parsed_questions:
        if session["evaluation_mode"] == "keyword":
            parsed_questions = [
                {
                    "question": "What is REST API?",
                    "keywords": ["HTTP", "Stateless", "GET", "POST", "PUT", "DELETE", "Client Server"],
                },
                {
                    "question": "What is normalization in DBMS?",
                    "keywords": ["redundancy", "dependency", "tables", "consistency"],
                },
                {
                    "question": "Explain object-oriented programming.",
                    "keywords": ["class", "object", "inheritance", "encapsulation", "polymorphism"],
                },
                {
                    "question": "What is exception handling?",
                    "keywords": ["error", "try", "catch", "finally"],
                },
                {
                    "question": "What is an API?",
                    "keywords": ["interface", "communication", "request", "response"],
                },
            ]
        else:
            parsed_questions = [
                {"question": "Explain one challenge from a company project and how you solved it.", "keywords": []},
                {"question": "How do you debug a production issue in a web application?", "keywords": []},
                {"question": "What makes an API scalable and maintainable?", "keywords": []},
                {"question": "How do you improve code quality in a team project?", "keywords": []},
                {"question": "Describe your approach to testing a backend service.", "keywords": []},
            ]

    session["questions"] = parsed_questions[:5]
    session["welcome"] = f"Welcome {session['name']}. Your AI interview starts now."
    session["api_session_id"] = sync_fastapi_session(
        candidate_name=session["name"],
        candidate_email=session.get("email", ""),
        mode="company-keyword" if session.get("evaluation_mode") == "keyword" else "company-ai",
        metadata={
            "domain": session["domain"],
            "evaluation_mode": session.get("evaluation_mode", "ai"),
            "source": "legacy-flask",
        },
        questions=session["questions"],
        greeting=session["welcome"],
    )

    return redirect(url_for("interview", session_id=session["api_session_id"]))


# =========================
# INTERVIEW PAGE
# =========================
@app.route("/interview")
def interview():

    current = session.get("current", 0)
    questions = session.get("questions", [])

    if not questions:
        return redirect(url_for("index"))

    if current >= len(questions):
        return redirect(url_for("result"))

    question = questions[current]

    return render_template(
        "interview.html",
        question=question,
        index=current + 1,
        total=5,
        api_session_id=session.get("api_session_id", ""),
        api_base_url=FASTAPI_BASE_URL,
        speech_service_url=SPEECH_SERVICE_URL,
    )


# =========================
# SAVE ANSWER
# =========================
@app.route("/save_answer", methods=["POST"])
def save_answer():

    if "questions" not in session:
        return redirect(url_for("index"))

    answer = request.form.get("answer", "").strip()

    if not answer:
        answer = "NO_ANSWER"

    answers = session.get("answers", [])
    answers.append(answer)

    session["answers"] = answers
    session["current"] = len(answers)

    return redirect(url_for("interview"))

# =========================
# RESULT PAGE
# =========================
@app.route("/result")
def result():

    if "name" not in session or "domain" not in session or "questions" not in session:
        return redirect(url_for("index"))

    questions = session.get("questions", [])[:5]
    answers = session.get("answers", [])[:5]

    if not questions:
        return redirect(url_for("index"))

    cached_result = session.get("result_data")

    if cached_result:
        percentage = cached_result["percentage"]
        result_status = cached_result["result"]
        feedback = cached_result["feedback"]
        recruiter_summary = cached_result.get("recruiter_summary", {})
    else:
        total_score = 0
        feedback = []

        for i, q in enumerate(questions):

            ans = answers[i] if i < len(answers) else ""

            if session.get("domain") == "Company Based Interview" and session.get("evaluation_mode") == "keyword":
                evaluation = evaluate_keyword_answer(q["question"], ans, q.get("keywords", []))
            else:
                evaluation = evaluate_answer(q["question"], ans)

            feedback.append(evaluation)

            score = extract_score(evaluation)

            total_score += score

        percentage = int((total_score / 50) * 100)
        result_status = "Pass" if percentage >= 60 else "Fail"
        recruiter_summary = build_recruiter_summary(
            questions=questions,
            answers=answers,
            feedback_items=feedback,
            percentage=percentage,
            result_status=result_status,
        )
        session["result_data"] = {
            "percentage": percentage,
            "result": result_status,
            "feedback": feedback,
            "recruiter_summary": recruiter_summary,
        }

    if not session.get("result_saved"):
        db = get_db()
        db.candidates.insert_one(
            {
                "name": session["name"],
                "email": session.get("email", ""),
                "domain": session["domain"],
                "score": percentage,
                "result": result_status,
                "date": str(datetime.date.today()),
                "created_at": datetime.datetime.utcnow(),
            }
        )
        session["result_saved"] = True

    if not session.get("result_email_scheduled") and session.get("email"):
        schedule_result_email(
            candidate_email=session.get("email"),
            candidate_name=session["name"],
            percentage=percentage,
            result_status=result_status,
            interview_domain=session["domain"],
            feedback=feedback,
            recruiter_summary=recruiter_summary,
        )
        session["result_email_scheduled"] = True

    return render_template(
        "result.html",
        total=5,
        percentage=percentage,
        result=result_status,
        feedback=feedback,
        email=session.get("email", ""),
        recruiter_summary=recruiter_summary,
    )


# =========================
# DASHBOARD
# =========================
@app.route("/dashboard")
def dashboard():

    db = get_db()
    candidates = [
        serialize_mongo_doc(candidate)
        for candidate in db.candidates.find().sort([("created_at", -1), ("score", -1)])
    ]

    total_candidates = len(candidates)
    avg_score = (
        round(sum(c["score"] for c in candidates) / total_candidates, 2)
        if total_candidates
        else 0
    )

    return render_template(
        "dashboard.html",
        candidates=candidates,
        total_candidates=total_candidates,
        avg_score=avg_score,
    )


@app.route("/delete_candidate/<id>", methods=["POST"])
def delete_candidate(id):

    db = get_db()
    try:
        db.candidates.delete_one({"_id": parse_object_id(id)})
    except ValueError:
        pass

    return redirect(url_for("dashboard"))


# =========================
# ADMIN PANEL
# =========================
@app.route("/admin")
def admin():

    db = get_db()
    questions = [serialize_mongo_doc(question) for question in db.questions.find().sort("_id", -1)]

    return render_template("admin.html", questions=questions)


@app.route("/add_question", methods=["POST"])
def add_question():

    domain = request.form["domain"]
    question = request.form["question"]
    correct = request.form["correct"]
    keywords = request.form["keywords"]

    db = get_db()
    db.questions.insert_one(
        {
            "domain": domain,
            "question": question,
            "correct_answer": correct,
            "keywords": keywords,
            "created_at": datetime.datetime.utcnow(),
        }
    )

    return redirect(url_for("admin"))


@app.route("/delete/<id>", methods=["POST"])
def delete(id):

    db = get_db()
    try:
        db.questions.delete_one({"_id": parse_object_id(id)})
    except ValueError:
        pass

    return redirect(url_for("admin"))


# =========================
# PDF REPORT DOWNLOAD
# =========================
@app.route("/download_report/<name>/<int:score>")
def download_report(name, score):
    pdf_bytes = generate_detailed_report_pdf(
        candidate_name=name,
        interview_domain="Interview",
        percentage=score,
        result_status="Pass" if score >= 60 else "Fail",
        feedback=["Downloaded from dashboard summary."],
        recruiter_summary={
            "communication_level": "Moderate",
            "technical_confidence": "Moderate" if score >= 60 else "Low",
            "strengths": "Candidate completed the interview workflow.",
            "weaknesses": "Detailed answer-level data is not stored in this dashboard export.",
            "hiring_recommendation": "Hire" if score >= 75 else ("Consider" if score >= 60 else "Needs Improvement"),
        },
    )

    return send_file(
        io.BytesIO(pdf_bytes),
        as_attachment=True,
        download_name="Interview_Report.pdf",
        mimetype="application/pdf",
    )


# =========================
# CHEATING DETECTION
# =========================
@app.route("/cheating_alert", methods=["POST"])
def cheating_alert():

    if "warnings" not in session:
        session["warnings"] = 0

    session["warnings"] += 1

    if session["warnings"] >= MAX_WARNINGS:
        return {"status": "terminated"}

    return {"status": "warning", "count": session["warnings"]}


# =========================
# ANALYTICS
# =========================
@app.route("/analytics")
def analytics():

    db = get_db()
    data = list(db.candidates.find({}, {"name": 1, "score": 1}))

    names = [d.get("name", "Unknown") for d in data]
    scores = [d.get("score", 0) for d in data]

    return render_template("analytics.html", names=names, scores=scores)


# =========================
# MAIN
# =========================
init_db()

if __name__ == "__main__":

    app.run(debug=os.getenv("FLASK_DEBUG", "false").lower() == "true")













