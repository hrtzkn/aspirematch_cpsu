from flask import Blueprint, render_template, request, redirect, url_for, session, flash, jsonify, make_response, send_file, current_app
from ..db import get_db_connection
from psycopg2.extras import RealDictCursor
import psycopg2
import psycopg2.extras
import os
import re
from werkzeug.utils import secure_filename
from io import BytesIO
import base64
import smtplib
from email.message import EmailMessage
from collections import Counter
from ..description import letter_descriptions, preferred_program_map, ai_responses, short_letter_descriptions
from math import ceil
from calendar import monthrange
import datetime
import random
import time
from datetime import datetime, timezone, timedelta

student_bp = Blueprint('student', __name__, template_folder='../../frontend/templates/student')

UPLOAD_FOLDER = "frontend/static/uploads/students"
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg"}

def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

def image_to_base64(filename):
    path = os.path.join(
        current_app.static_folder,
        "images",
        filename
    )
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()
    
def student_photo_to_base64(filename):
    if not filename:
        return None

    path = os.path.join(
        current_app.static_folder,
        "uploads",
        "students",
        filename
    )

    if not os.path.exists(path):
        return None

    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()

def ask_ai(messages, temperature=0.3, max_tokens=700):
    from groq import Groq
    import os

    client = Groq(api_key=os.getenv("GROQ_API_KEY"))

    response = client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens
    )

    return response.choices[0].message.content

def is_ask_about_aspirematch(question):
    question_lower = question.lower()
    
    # simple keywords
    keywords = [
        "career", "survey", "recommendation", "result",
        "dashboard", "guidance", "report", "aspirematch",
        "strength", "weakness", "advice", "letter",
        "program", "course", "hi", "hello", "hey", "aspire"
    ]
    
    # remove punctuation
    clean_question = re.sub(r"[^\w\s]", "", question_lower)
    words = clean_question.split()
    
    matched_keywords = [k for k in keywords if k in words or k in clean_question]
    print("Matched keywords:", matched_keywords)
    return bool(matched_keywords)

SYSTEM_PROMPT = (
    "You are Aspire, the friendly AI assistant for AspireMatch. "
    "AspireMatch is a student career interest recommendation application. "
    "You can answer questions about the app, including career surveys, survey results, career recommendations, "
    "student dashboards, guidance counselor reports, and system usage. "
    "When a student's survey data is provided, summarize it in simple, concise, supportive language, "
    "highlighting the most important points: Career Letter Explanation, Strengths, Weaknesses, and Personalized Career Advice. "
    "Keep it short and easy to read for a student. "
    "If a user asks anything outside of AspireMatch, politely respond: "
    "'I'm sorry, I can only answer questions related to the AspireMatch.'"
)

def fetch_and_rewrite_section(ai_text, section_name):
    """
    Extracts a section from ai_explanation and rewrites it in concise, student-friendly form
    """
    import re

    # Extract section
    pattern = rf"{section_name}\s*(.*?)(?=\n[A-Z][a-zA-Z ]+\n|$)"
    match = re.search(pattern, ai_text, re.DOTALL | re.IGNORECASE)
    section_text = match.group(1).strip() if match else "Not available yet."

    # Prepare AI prompt for rewriting
    prompt = [
        {"role": "system", "content": f"Rewrite the following {section_name} section in 1-2 concise, student-friendly sentences. Keep it supportive and easy to understand."},
        {"role": "user", "content": section_text}
    ]

    try:
        rewritten = ask_ai(prompt)
        return rewritten
    except Exception as e:
        print(f"Error rewriting section {section_name}:", e)
        return section_text  # fallback to original if AI fails

def generate_ai_insights(top_letters, preferred_program, fullname):
    letters_str = ", ".join(top_letters)

    letter_meanings = ", ".join(
        [f"{l} ({short_letter_descriptions.get(l, 'Unknown')})" for l in top_letters]
    )

    prompt = f"""
    You are an educational guidance AI.
    The student's name is {fullname}.
    Their top career letters are: {letters_str}.

    The short meaning of each letter:
    {letter_meanings}

    Their preferred program is: {preferred_program}.

    Create a easy-to-read explanation with the following sections ONLY:

    Career Letter Explanation 
    - Explain each letter using the short meanings only.

    Strengths
    - List strengths based on the {letters_str}.
    - Use bullet points with the symbol "•"

    Weaknesses
    - List possible areas for improvement based on traits that are less dominant compared to the top letters.
    - Do NOT repeat strengths.
    - Keep weaknesses constructive and supportive.
    - Use bullet points with the symbol "•"

    Personalized Career Advice
    - Provide friendly guidance.
    - two sentence only
    """

    messages = [
        {
            "role": "system",
            "content": (
                "You are an educational guidance AI. "
                "You MUST return ONLY plain text. "
                "Do NOT use asterisks (*), hashtags (#), or markdown formatting. "
                "Use only • for bullet points and plain headings without extra symbols."
            )
        },
        {"role": "user", "content": prompt}
    ]

    return ask_ai(messages, temperature=0.3, max_tokens=700)

def format_ai_explanation_for_pdf(text):
    if not text:
        return ""

    sections = [
        "Career Letter Explanation",
        "Strengths",
        "Weaknesses",
        "Personalized Career Advice"
    ]

    formatted = text.strip()

    for title in sections:
        formatted = formatted.replace(
            title,
            f'<div class="font-semibold text-black uppercase mt-4 ai-subtitle">{title}</div>'
        )

    lines = formatted.split("\n")
    html_lines = []
    in_list = False

    for line in lines:
        if line.strip().startswith("•"):
            if not in_list:
                html_lines.append('<ul class="list-disc ml-6">')
                in_list = True
            html_lines.append(f"<li>{line.replace('•', '').strip()}</li>")
        else:
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append(f"<p>{line}</p>")

    if in_list:
        html_lines.append("</ul>")

    return f'<div class="ai-content">{"".join(html_lines)}</div>'

def generate_otp():
    return str(random.randint(100000, 999999))

def send_otp_email(email, otp):
    import os
    import requests
    from flask import current_app

    SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY")

    if not SENDGRID_API_KEY:
        current_app.logger.error("❌ SENDGRID_API_KEY not set.")
        return False

    data = {
        "personalizations": [
            {
                "to": [{"email": email}],
                "subject": "Your AspireMatch Login OTP"
            }
        ],
        "from": {"email": "aspirematch2@gmail.com"},
        "content": [
            {
                "type": "text/plain",
                "value": f"""Your One-Time Password (OTP) is:

{otp}

This code will expire in 5 minutes.

If you did not request this, please ignore this email."""
            }
        ]
    }

    try:
        response = requests.post(
            "https://api.sendgrid.com/v3/mail/send",
            headers={
                "Authorization": f"Bearer {SENDGRID_API_KEY}",
                "Content-Type": "application/json"
            },
            json=data,
            timeout=15
        )

        if response.status_code == 202:
            current_app.logger.info("✅ OTP email sent via SendGrid.")
            return True
        else:
            current_app.logger.error(f"❌ SendGrid error: {response.text}")
            return False

    except Exception as e:
        current_app.logger.error(f"❌ SendGrid exception: {e}")
        return False
    
def generate_pdf(html):
    from weasyprint import HTML
    from io import BytesIO
    from flask import current_app

    pdf_io = BytesIO()

    HTML(
        string=html,
        base_url=current_app.root_path
    ).write_pdf(pdf_io)

    pdf_io.seek(0)
    return pdf_io
    
def process_image(file):
    from PIL import Image

    image = Image.open(file).convert("RGB")

    width, height = image.size
    size = min(width, height)

    left = (width - size) / 2
    top = (height - size) / 2
    right = left + size
    bottom = top + size

    image = image.crop((left, top, right, bottom))
    image = image.resize((300, 300))

    return image

@student_bp.route("/test-db")
def test_db():
    conn = get_db_connection()
    return "DB CONNECTED"

@student_bp.route("/get_letter_description/<letter>")
def get_letter_description(letter):
    description = letter_descriptions.get(letter, "No description available.")
    return jsonify({ "description": description })

MAX_LOGIN_ATTEMPTS = 5
LOCKOUT_MINUTES = 3

@student_bp.route("/")
def login_page():
    return render_template("student/studentLogin.html")

@student_bp.route("/login", methods=["GET", "POST"])
def studentlogin():
    error = None
    exam_error = False
    email_error = False

    if request.method == "POST":
        exam_id = request.form["exam_id"].strip()
        email = request.form["email"].strip()

        conn = get_db_connection()
        cur = conn.cursor()

        # 🔎 Find student by exam_id OR email
        cur.execute(
            """
            SELECT id, exam_id, email, login_attempts, lockout_until
            FROM student
            WHERE exam_id = %s OR email = %s
            """,
            (exam_id, email)
        )

        student = cur.fetchone()

        if not student:
            error = "Invalid Examination ID or Email"
            exam_error = True
            email_error = True

        else:
            student_id, stored_exam, stored_email, attempts, lockout_until = student
            now = datetime.now(timezone.utc)

            # 🔒 Check lock
            if lockout_until and now < lockout_until:
                remaining = int((lockout_until - now).total_seconds() / 60)
                error = f"Too many failed attempts. Try again in {remaining} minutes."

            else:

                # Reset expired lock
                if lockout_until and now >= lockout_until:
                    cur.execute(
                        "UPDATE student SET login_attempts = 0, lockout_until = NULL WHERE id = %s",
                        (student_id,)
                    )
                    conn.commit()
                    attempts = 0

                # ❌ If exam_id OR email does not match
                if stored_exam != exam_id or stored_email != email:

                    attempts += 1

                    if attempts >= MAX_LOGIN_ATTEMPTS:
                        lock_time = now + timedelta(minutes=LOCKOUT_MINUTES)

                        cur.execute(
                            """
                            UPDATE student
                            SET login_attempts = %s,
                                lockout_until = %s
                            WHERE id = %s
                            """,
                            (attempts, lock_time, student_id)
                        )

                        error = f"Too many failed attempts. Account locked for {LOCKOUT_MINUTES} minutes."

                    else:
                        cur.execute(
                            "UPDATE student SET login_attempts = %s WHERE id = %s",
                            (attempts, student_id)
                        )

                        error = f"Invalid Examination ID or Email. Attempt {attempts}/{MAX_LOGIN_ATTEMPTS}"

                    conn.commit()

                    if stored_exam != exam_id:
                        exam_error = True

                    if stored_email != email:
                        email_error = True

                else:
                    # ✅ Successful login
                    cur.execute(
                        "UPDATE student SET login_attempts = 0, lockout_until = NULL WHERE id = %s",
                        (student_id,)
                    )
                    conn.commit()

                    session["student_id"] = student_id
                    session["exam_id"] = stored_exam
                    session["last_activity"] = datetime.now(timezone.utc)
                    session.permanent = True

                    # Check if survey already answered
                    cur.execute(
                        """
                        SELECT 1 FROM student_survey_answer
                        WHERE exam_id = %s AND student_id = %s
                        """,
                        (stored_exam, student_id)
                    )

                    survey_row = cur.fetchone()

                    cur.close()
                    conn.close()

                    if survey_row:
                        return redirect(url_for("student.home"))
                    else:
                        return redirect(url_for("student.survey"))

        cur.close()
        conn.close()

        return render_template(
            "student/studentLogin.html",
            error=error,
            exam_error=exam_error,
            email_error=email_error,
            exam_id=exam_id,
            email=email
        )

    return render_template("student/studentLogin.html")
"""
@student_bp.route("/login", methods=["GET", "POST"])
def studentlogin():
    error = None
    exam_error = False
    email_error = False

    if request.method == "POST":
        exam_id = request.form["exam_id"]
        email = request.form["email"]

        conn = get_db_connection()
        cur = conn.cursor()

        # Check if student exists
        cur.execute(
            "SELECT id FROM student WHERE exam_id = %s AND email = %s",
            (exam_id, email)
        )
        student = cur.fetchone()

        if not student:
            exam_error = True
            error = "Invalid Examination ID"

            cur.close()
            conn.close()

            return render_template(
                "student/studentLogin.html",
                error=error,
                exam_error=exam_error,
                email_error=email_error,
                exam_id=exam_id,
                email=email
            )

        student_id = student[0]

        # 🔎 CHECK if student already answered survey
        cur.execute(
            "SELECT 1 FROM student_survey_answer WHERE exam_id = %s AND student_id = %s",
            (exam_id, student_id)
        )
        survey_row = cur.fetchone()

        # ✅ IF survey already exists → NO OTP
        if survey_row:
            session["student_id"] = student_id
            session["exam_id"] = exam_id

            cur.close()
            conn.close()

            return redirect(url_for("student.home"))

        # ❗ IF survey does NOT exist → REQUIRE OTP
        otp = generate_otp()

        session["otp"] = otp
        session["otp_exam_id"] = exam_id
        session["otp_email"] = email
        session["otp_time"] = time.time()

        sent = send_otp_email(email, otp)

        if not sent:
            error = "Unable to send OTP. Please try again later."

            cur.close()
            conn.close()

            return render_template(
                "student/studentLogin.html",
                error=error,
                exam_error=False,
                email_error=False,
                exam_id=exam_id,
                email=email
            )

        cur.close()
        conn.close()

        return redirect(url_for("student.verify"))

    return render_template("student/studentLogin.html")
"""
@student_bp.route("/verify", methods=["GET", "POST"])
def verify():
    error = None
    success = None

    if request.method == "POST":

        if "resend" in request.form:
            last_sent = session.get("otp_time", 0)

            if time.time() - last_sent < 60:
                error = "Please wait 1 minute before requesting a new OTP."
            else:
                otp = generate_otp()
                session["otp"] = otp
                session["otp_time"] = time.time()

                send_otp_email(session["otp_email"], otp)
                success = "New OTP sent successfully."

            return render_template(
                "student/verify.html",
                error=error,
                success=success
            )

        user_otp = request.form.get("otp", "")

        if time.time() - session.get("otp_time", 0) > 300:
            error = "OTP expired. Please login again."

        elif user_otp != session.get("otp"):
            error = "Invalid OTP"

        else:
            exam_id = session["otp_exam_id"]

            conn = get_db_connection()
            cur = conn.cursor()

            cur.execute(
                "SELECT id FROM student WHERE exam_id = %s",
                (exam_id,)
            )
            student = cur.fetchone()

            session["student_id"] = student[0]
            session["exam_id"] = exam_id

            # Clear OTP session
            session.pop("otp", None)
            session.pop("otp_email", None)
            session.pop("otp_exam_id", None)
            session.pop("otp_time", None)

            cur.close()
            conn.close()

            return redirect(url_for("student.survey"))

    return render_template("student/verify.html", error=error, success=success)

interestPairs = [
  [
    { "number": 1, "key": "A", "text": "Operate a printing press" },
    { "number": 1, "key": "B", "text": "Study the causes of earthquakes" }
  ],
  [
    { "number": 2, "key": "C", "text": "Plant and harvest crops" },
    { "number": 2, "key": "R", "text": "Replaces a car window and fender" }
  ],
  [
    { "number": 3, "key": "E", "text": "Analyze reports and records" },
    { "number": 3, "key": "F", "text": "Operate a machine" }
  ],
  [
    { "number": 4, "key": "G", "text": "Work in an office" },
    { "number": 4, "key": "H", "text": "Answer costumer questions" }
  ],
  [
    { "number": 5, "key": "D", "text": "Write reports" },
    { "number": 5, "key": "J", "text": "Helps former prison inmates find work" }
  ],
  [
    { "number": 6, "key": "L", "text": "Design a freeway" },
    { "number": 6, "key": "M", "text": "Plan educational lessons" }
  ],
  [
    { "number": 7, "key": "N", "text": "balance a checkbook" },
    { "number": 7, "key": "O", "text": "Take an x -ray" }
  ],
  [
    { "number": 8, "key": "P", "text": "Write a computer program" },
    { "number": 8, "key": "Q", "text": "Train animals" }
  ],
  [
    { "number": 9, "key": "C", "text": "Be in charge of replanting forest" },
    { "number": 9, "key": "A", "text": "Acts in TV show or movies" }
  ],
  [
    { "number": 10, "key": "D", "text": "Solve a burglary" },
    { "number": 10, "key": "F", "text": "Checks product quality" }
  ],
  [
    { "number": 11, "key": "E", "text": "Build an airport" },
    { "number": 11, "key": "G", "text": "keep company business records" }
  ],
  [
    { "number": 12, "key": "F", "text": "Put together small tools" },
    { "number": 12, "key": "P", "text": "Design a website" }
  ],
  [
    { "number": 13, "key": "M", "text": "Tutor students" },
    { "number": 13, "key": "Q", "text": "Works at zoo" }
  ],
  [
    { "number": 14, "key": "J", "text": "Take care of children" },
    { "number": 14, "key": "O", "text": "Plan special diets" }
  ],
  [
    { "number": 15, "key": "A", "text": "Choreograph a dance" },
    { "number": 15, "key": "K", "text": "Lobby or show support for a cause" }
  ],
  [
    { "number": 16, "key": "H", "text": "Sell cloths" },
    { "number": 16, "key": "E", "text": "Work with your hands" }
  ],
  [
    { "number": 17, "key": "I", "text": "Work at an amusement park" },
    { "number": 17, "key": "N", "text": "Sell insurance" }
  ],
  [
    { "number": 18, "key": "I", "text": "Learn about ethnic groups" },
    { "number": 18, "key": "P", "text": "Manage an information system" }
  ],
  [
    { "number": 19, "key": "N", "text": "Appraise the value of a house" },
    { "number": 19, "key": "M", "text": "File books at the library" }
  ],
  [
    { "number": 20, "key": "M", "text": "Grade papers" },
    { "number": 20, "key": "R", "text": "Operate a train" }
  ],
  [
    { "number": 21, "key": "L", "text": "Order building supplies" },
    { "number": 21, "key": "E", "text": "Paint motors" }
  ],
  [
    { "number": 22, "key": "P", "text": "Develop new computer games" },
    { "number": 22, "key": "H", "text": "Buy merchandise for a store" }
  ],
  [
    { "number": 23, "key": "K", "text": "Work to get someone elected" },
    { "number": 23, "key": "C", "text": "Identify plants in a forest" }
  ],
  [
    { "number": 24, "key": "D", "text": "Guard inmates in a prison" },
    { "number": 24, "key": "L", "text": "Read blueprints" }
  ],
  [
    { "number": 25, "key": "H", "text": "Line up concerts for a band" },
    { "number": 25, "key": "K", "text": "Ask people survey questions" }
  ],
  [
    { "number": 26, "key": "E", "text": "Manage a factory" },
    { "number": 26, "key": "O", "text": "Work as a nurse in a hospital" }
  ],
  [
    { "number": 27, "key": "A", "text": "Paint a portrait" },
    { "number": 27, "key": "K", "text": "Testify before Congress" }
  ],
  [
    { "number": 28, "key": "B", "text": "Work with a microscope" },
    { "number": 28, "key": "I", "text": "Schedule tee times at a golf course" }
  ],
  [
    { "number": 29, "key": "C", "text": "Classify plants" },
    { "number": 29, "key": "O", "text": "Transcribe medical records" }
  ],
  [
    { "number": 30, "key": "E", "text": "Make three-dimensional items" },
    { "number": 30, "key": "D", "text": "Analyze handwriting" }
  ],
  [
    { "number": 31, "key": "B", "text": "Design indoor sprinkler systems" },
    { "number": 31, "key": "F", "text": "Run a factory sewing machine" }
  ],
  [
    { "number": 32, "key": "G", "text": "Develop personnel policies" },
    { "number": 32, "key": "Q", "text": "Train racehorses" }
  ],
  [
    { "number": 33, "key": "D", "text": "Guard an office building" },
    { "number": 33, "key": "H", "text": "Run a department store" }
  ],
  [
    { "number": 34, "key": "A", "text": "Write for a newspaper" },
    { "number": 34, "key": "G", "text": "Use a calculator" }
  ],
  [
    { "number": 35, "key": "O", "text": "Help people at a mental health clinic" },
    { "number": 35, "key": "L", "text": "Remodel old houses" }
  ],
  [
    { "number": 36, "key": "M", "text": "Care for young children" },
    { "number": 36, "key": "D", "text": "Locate a missing person" }
  ],
  [
    { "number": 37, "key": "N", "text": "Plan estate disbursements/payments" },
    { "number": 37, "key": "P", "text": "Enter data" }
  ],
  [
    { "number": 38, "key": "A", "text": "Design a book cover" },
    { "number": 38, "key": "E", "text": "Build toys with written instructions" }
  ],
  [
    { "number": 39, "key": "B", "text": "Figure out why someone is sick" },
    { "number": 39, "key": "R", "text": "Fly an airplane" }
  ],
  [
    { "number": 40, "key": "C", "text": "Learn how things grow and stay alive" },
    { "number": 40, "key": "H", "text": "Sell cars" }
  ],
  [
    { "number": 41, "key": "I", "text": "Work as a restaurant host or hostess" },
    { "number": 41, "key": "D", "text": "Fight fires" }
  ],
  [
    { "number": 42, "key": "G", "text": "Keep payroll records for a company" },
    { "number": 42, "key": "J", "text": "Work in a nursing home" }
  ],
  [
    { "number": 43, "key": "G", "text": "Hire new staff" },
    { "number": 43, "key": "O", "text": "Run ventilators/breathing machines" }
  ],
  [
    { "number": 44, "key": "R", "text": "Drive a taxi" },
    { "number": 44, "key": "A", "text": "Broadcast the news" }
  ],
  [
    { "number": 45, "key": "K", "text": "Audit taxes for the government" },
    { "number": 45, "key": "B", "text": "Sort and date dinosaur bones" }
  ],
  [
    { "number": 46, "key": "O", "text": "Give shots" },
    { "number": 46, "key": "C", "text": "Design landscaping" }
  ],
  [
    { "number": 47, "key": "P", "text": "Give tech support to computer users" },
    { "number": 47, "key": "D", "text": "Work in a courtroom" }
  ],
  [
    { "number": 48, "key": "Q", "text": "Care for injured animals" },
    { "number": 48, "key": "I", "text": "Serve meals to customers" }
  ],
  [
    { "number": 49, "key": "F", "text": "Install rivets" },
    { "number": 49, "key": "Q", "text": "Raise worms" }
  ],
  [
    { "number": 50, "key": "N", "text": "Balance accounts" },
    { "number": 50, "key": "M", "text": "Develop learning games" }
  ],
  [
    { "number": 51, "key": "J", "text": "Read to sick people" },
    { "number": 51, "key": "P", "text": "Repair computers" }
  ],
  [
    { "number": 52, "key": "F", "text": "Compare sizes and shapes of objects" },
    { "number": 52, "key": "Q", "text": "Fish" }
  ],
  [
    { "number": 53, "key": "R", "text": "Repair bicycles" },
    { "number": 53, "key": "K", "text": "Deliver mail" }
  ],
  [
    { "number": 54, "key": "M", "text": "Teach Special Education" },
    { "number": 54, "key": "P", "text": "Set up a tracking system" }
  ],
  [
    { "number": 55, "key": "G", "text": "Manage a store" },
    { "number": 55, "key": "H", "text": "Advertise goods and services" }
  ],
  [
    { "number": 56, "key": "R", "text": "Distribute supplies to dentists" },
    { "number": 56, "key": "I", "text": "Compete in a sports event" }
  ],
  [
    { "number": 57, "key": "I", "text": "Check guests into a hotel" },
    { "number": 57, "key": "M", "text": "Teach adults to read" }
  ],
  [
    { "number": 58, "key": "L", "text": "Follow step-by-step instructions" },
    { "number": 58, "key": "N", "text": "Collect past due bills" }
  ],
  [
    { "number": 59, "key": "L", "text": "Build kitchen cabinets" },
    { "number": 59, "key": "N", "text": "Refinance a mortgage" }
  ],
  [
    { "number": 60, "key": "A", "text": "Sing in a concert" },
    { "number": 60, "key": "R", "text": "Direct the takeoff/landing of planes" }
  ],
  [
    { "number": 61, "key": "G", "text": "Operate a cash register" },
    { "number": 61, "key": "B", "text": "Collect rocks" }
  ],
  [
    { "number": 62, "key": "G", "text": "Start a business" },
    { "number": 62, "key": "L", "text": "Draft a blueprint" }
  ],
  [
    { "number": 63, "key": "M", "text": "Assess student progress" },
    { "number": 63, "key": "L", "text": "Design an airplane" }
  ],
  [
    { "number": 64, "key": "O", "text": "Wrap a sprained ankle" },
    { "number": 64, "key": "I", "text": "Guide an international tour group" }
  ],
  [
    { "number": 65, "key": "P", "text": "Solve technical problems" },
    { "number": 65, "key": "J", "text": "Provide spiritual guidance to others" }
  ],
  [
    { "number": 66, "key": "Q", "text": "Manage a veterinary clinic" },
    { "number": 66, "key": "K", "text": "Lead others" }
  ],
  [
    { "number": 67, "key": "E", "text": "Operate heavy equipment" },
    { "number": 67, "key": "Q", "text": "Manage a fish hatchery" }
  ],
  [
    { "number": 68, "key": "F", "text": "Assemble cars" },
    { "number": 68, "key": "K", "text": "Protect our borders" }
  ],
  [
    { "number": 69, "key": "A", "text": "Play an instrument" },
    { "number": 69, "key": "J", "text": "Plan activities for adult day care" }
  ],
  [
    { "number": 70, "key": "C", "text": "Research soybean use in paint" },
    { "number": 70, "key": "J", "text": "Provide consumer information" }
  ],
  [
    { "number": 71, "key": "D", "text": "Guard money in an armored car" },
    { "number": 71, "key": "B", "text": "Study human behavior" }
  ],
  [
    { "number": 72, "key": "E", "text": "Fix a television set" },
    { "number": 72, "key": "M", "text": "Run a school" }
  ],
  [
    { "number": 73, "key": "F", "text": "Fix a control panel" },
    { "number": 73, "key": "J", "text": "Help friends with personal problems" }
  ],
  [
    { "number": 74, "key": "C", "text": "Oversee a logging crew" },
    { "number": 74, "key": "B", "text": "Study weather conditions" }
  ],
  [
    { "number": 75, "key": "R", "text": "Pack boxes at a warehouse" },
    { "number": 75, "key": "A", "text": "Teach dancing" }
  ],
  [
    { "number": 76, "key": "O", "text": "Sterilize surgical instruments" },
    { "number": 76, "key": "B", "text": "Study soil conditions" }
  ],
  [
    { "number": 77, "key": "N", "text": "Play the stock market" },
    { "number": 77, "key": "C", "text": "Protect the environment" }
  ],
  [
    { "number": 78, "key": "R", "text": "Inspect cargo containers" },
    { "number": 78, "key": "F", "text": "Work in a cannery" }
  ],
  [
    { "number": 79, "key": "I", "text": "Coach a school sports team" },
    { "number": 79, "key": "P", "text": "Update a website" }
  ],
  [
    { "number": 80, "key": "Q", "text": "Hunt" },
    { "number": 80, "key": "K", "text": "Enlist in a branch of the military" }
  ],
  [
    { "number": 81, "key": "H", "text": "Sell sporting goods" },
    { "number": 81, "key": "J", "text": "Cut and style hair" }
  ],
  [
    { "number": 82, "key": "B", "text": "Experiment to find new metals" },
    { "number": 82, "key": "N", "text": "Work in a bank" }
  ],
  [
    { "number": 83, "key": "G", "text": "Work with computer programs" },
    { "number": 83, "key": "N", "text": "Loan money" }
  ],
  [
    { "number": 84, "key": "L", "text": "Hang wallpaper" },
    { "number": 84, "key": "D", "text": "Make an arrest" }
  ],
  [
    { "number": 85, "key": "O", "text": "Deliver babies" },
    { "number": 85, "key": "H", "text": "Persuade people to buy something" }
  ],
  [
    { "number": 86, "key": "H", "text": "Stock shelves" },
    { "number": 86, "key": "I", "text": "Serve concession stand drinks" }
  ]
]

@student_bp.route("/chatbot", methods=["POST"])
def chatbot():
    user_msg = request.json.get("message", "").strip()
    student_id = request.json.get("student_id")
    print("Student ID received:", student_id)
    print("User message received:", user_msg)

    if not user_msg:
        return jsonify({"reply": "Please type a message to get a response."})

    if not student_id:
        return jsonify({"reply": "Student ID is missing. Please login again."})

    if not is_ask_about_aspirematch(user_msg):
        return jsonify({
            "reply": "I can only answer questions related to AspireMatch such as survey results or program recommendations."
        })

    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)

        pair_columns = ", ".join([f"ss.pair{i}" for i in range(1, 87)])
        cur.execute(f"""
            SELECT s.fullname, ss.ai_explanation, {pair_columns}
            FROM student s
            JOIN student_survey_answer ss
            ON s.id = ss.student_id
            WHERE s.id = %s
            ORDER BY ss.id DESC
            LIMIT 1
        """, (student_id,))
        result = cur.fetchone()

        ai_text = result.get("ai_explanation", "")

        pair_letters = [result.get(f"pair{i}") for i in range(1, 87) if result.get(f"pair{i}")]
        letter_count = Counter(pair_letters)
        top3 = [l for l, _ in letter_count.most_common(3)]

        msg = user_msg.lower()

        if not result:
            if any(k in msg for k in ["career interest survey result", "career interest result",
                                        "interest survey result", "career survey result",
                                        "career result", "result"]):
                return jsonify({
                    "reply": "Your survey results are not available yet. Please complete it first."
                })

            if any(k in msg for k in ["recommended program", "recommended course", "course and program"]):
                return jsonify({
                    "reply": "Please complete a survey first."
                })

            return jsonify({
                "reply": "Your survey results are not available yet."
            })
        
        if not ai_text or ai_text.strip() == "":
            if any(k in msg for k in [
                "strength", "strengths",
                "weakness", "weaknesses",
                "career letter explanation",
                "career explanation",
                "advice", "career advice",
                "personalized career advice"
            ]):
                return jsonify({
                    "reply": "Generate an AI explanation first, go to RESULT then select CAREER INTEREST and click the button GENERATE AI EXPLANATION."
                })

        if any(k in msg for k in ["career letter explanation", "letter explanation", "career explanation"]):
            rewritten = fetch_and_rewrite_section(ai_text, "Career Letter Explanation")
            return jsonify({"reply": rewritten})

        if any(k in msg for k in ["strengths", "strength"]):
            rewritten = fetch_and_rewrite_section(ai_text, "Strengths")
            return jsonify({"reply": rewritten})

        if any(k in msg for k in ["weaknesses", "weakness"]):
            rewritten = fetch_and_rewrite_section(ai_text, "Weaknesses")
            return jsonify({"reply": rewritten})

        if any(k in msg for k in ["personalized career advice", "career advice", "advice"]):
            rewritten = fetch_and_rewrite_section(ai_text, "Personalized Career Advice")
            return jsonify({"reply": rewritten})

        if any(k in msg for k in ["recommended program", "recommended course", "course and program"]):
            if not top3:
                return jsonify({"reply": "Your survey results are not available yet."})

            cur.execute("SELECT program_name, category_letter FROM program")
            all_programs = cur.fetchall()
            matched_programs = []
            for prog in all_programs:
                prog_letters = [l.strip() for l in prog["category_letter"].split(",")]
                score = sum(1 for l in top3 if l in prog_letters)
                if score > 0:
                    matched_programs.append((prog, score))
            matched_programs = sorted(matched_programs, key=lambda x: x[1], reverse=True)[:3]

            if not matched_programs:
                return jsonify({"reply": "No program recommendations found yet."})

            reply_lines = []
            reply_lines.append("Top Recommended Programs:\n")

            for prog, score in matched_programs:
                reply_lines.append(f"• {prog['program_name']}")

            reply_lines.append("\n These programs are recommended since your top letter are : {', '.join(top3)}\n")

            for letter in top3:
                desc = letter_descriptions.get(letter, "No description available.")
                reply_lines.append(f"• {letter} - {desc}")

            # Join with proper line breaks
            final_reply = "\n".join(reply_lines)

            return jsonify({"reply": final_reply})

        # Survey Result
        if any(k in msg for k in ["career interest survey result", "career interest result",
                                   "interest survey result", "career survey result",
                                   "career result", "result"]):
            if not ai_text:
                return jsonify({
                    "reply": "Your survey results are not available yet. Please complete a survey first."
                }) 
        
            prompt = [
                {"role": "system", "content": "Summarize this career result briefly in 3-5 sentences for a student."},
                {"role": "user", "content": ai_text}
            ]
            try:
                short_summary = ask_ai(prompt)
                return jsonify({"reply": f"Your Career Result:\n\n{short_summary}"})
            except Exception as e:
                print("Error in Career Result:", e)
                return jsonify({"reply": "Sorry, I couldn't answer your career result right now."})

        # Survey Info
        if any(k in msg for k in ["career interest survey", "career survey", "interest survey"]):
            return jsonify({
                "reply": "The AspireMatch survey identifies which CPSU programs best match your interests and strengths based on your answers."
            })
        
        if any(k in msg for k in ["hi", "hello", "hey", "aspire"]):
            return jsonify({
                "reply": "Hello! I'm Aspire, your AspireMatch virtual assistant. How can I assist you today?"
            })

        # Program List
        if "program" in msg or "course" in msg:
            cur.execute("SELECT program_name FROM program")
            programs = cur.fetchall()
            program_list = "\n".join([f"• {p['program_name']}" for p in programs])
            return jsonify({"reply": f"Available Programs:\n\n{program_list}"})

        # Default: fallback AI
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg}
        ]
        try:
            reply = ask_ai(messages)
            return jsonify({"reply": reply})
        except Exception as e:
            print("Error in fallback AI:", e)
            return jsonify({"reply": "Sorry, I couldn't process your question right now."})

    except Exception as e:
        print("Chatbot error:", e)
        return jsonify({
            "reply": "Something went wrong while processing your request."
        }), 500

@student_bp.route("/chatbot_receive_interest", methods=["POST"])
def chatbot_receive_interest():
    letter = request.json.get("letter")

    if letter in ai_responses:
        reply = random.choice(ai_responses[letter])
    else:
        reply = "Interesting choice!"

    return {"reply": reply}

@student_bp.route("/home")
def home():
    if "student_id" not in session:
        return redirect(url_for("student.login_page"))

    student_id = session["student_id"]
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT result_unlocked, inventory_result_unlocked
        FROM notifications
        WHERE student_id = %s
        ORDER BY id DESC LIMIT 1
    """, (session["student_id"],))
    row = cur.fetchone()

    cur.execute("""
        SELECT
            MAX(CASE WHEN result_unlocked = TRUE THEN 1 ELSE 0 END),
            MAX(CASE WHEN inventory_result_unlocked = TRUE THEN 1 ELSE 0 END)
        FROM notifications
        WHERE student_id = %s
    """, (session["student_id"],))

    survey_result_unlocked, inventory_result_unlocked = cur.fetchone()

    cur.execute("""
        SELECT s.exam_id, s.fullname, s.campus, sa.preferred_program,
               sa.pair1, sa.pair2, sa.pair3, sa.pair4, sa.pair5,
               sa.pair6, sa.pair7, sa.pair8, sa.pair9, sa.pair10,
               sa.pair11, sa.pair12, sa.pair13, sa.pair14, sa.pair15,
               sa.pair16, sa.pair17, sa.pair18, sa.pair19, sa.pair20,
               sa.pair21, sa.pair22, sa.pair23, sa.pair24, sa.pair25,
               sa.pair26, sa.pair27, sa.pair28, sa.pair29, sa.pair30,
               sa.pair31, sa.pair32, sa.pair33, sa.pair34, sa.pair35,
               sa.pair36, sa.pair37, sa.pair38, sa.pair39, sa.pair40,
               sa.pair41, sa.pair42, sa.pair43, sa.pair44, sa.pair45,
               sa.pair46, sa.pair47, sa.pair48, sa.pair49, sa.pair50,
               sa.pair51, sa.pair52, sa.pair53, sa.pair54, sa.pair55,
               sa.pair56, sa.pair57, sa.pair58, sa.pair59, sa.pair60,
               sa.pair61, sa.pair62, sa.pair63, sa.pair64, sa.pair65,
               sa.pair66, sa.pair67, sa.pair68, sa.pair69, sa.pair70,
               sa.pair71, sa.pair72, sa.pair73, sa.pair74, sa.pair75,
               sa.pair76, sa.pair77, sa.pair78, sa.pair79, sa.pair80,
               sa.pair81, sa.pair82, sa.pair83, sa.pair84, sa.pair85,
               sa.pair86
        FROM student s
        LEFT JOIN student_survey_answer sa 
            ON s.exam_id = sa.exam_id
        WHERE s.id = %s;
    """, (student_id,))
    
    row = cur.fetchone()

    student_survey_answer_completed = "❌ Not Completed"
    match_status = "❌ Not Match"
    interview_status = "❌ Not Available"

    if row:
        student_results = {
            "exam_id": row[0],
            "fullname": row[1],
            "campus": row[2],
            "preferred_program": row[3],
            "answers": [row[i] for i in range(4, 90)]
        }

        answers_clean = [ans for ans in student_results["answers"] if ans]

        if answers_clean:
            student_survey_answer_completed = "✅ Completed"

            from collections import Counter

            letter_counts = Counter(answers_clean)
            top_letters = [letter for letter, _ in letter_counts.most_common(3)]

            # ✅ CLEAN top letters
            top_letters = [l.strip().upper() for l in top_letters]

            preferred = student_results["preferred_program"]
            student_campus = student_results["campus"]

            program_letters = []

            if preferred:
                cur.execute("""
                    SELECT category_letter
                    FROM program
                    WHERE LOWER(TRIM(program_name)) = LOWER(TRIM(%s))
                    AND LOWER(TRIM(campus)) = LOWER(TRIM(%s))
                    LIMIT 1
                """, (preferred, student_campus))

                program_row = cur.fetchone()

                if program_row and program_row[0]:
                    program_letters = [l.strip().upper() for l in program_row[0].split(",")]

            # ✅ BETTER MATCH LOGIC
            common_letters = set(top_letters) & set(program_letters)

            if common_letters:
                match_status = "✅ Match"
                interview_status = "Don't need for interview"
            else:
                match_status = "❌ Not Match"

            if match_status == "❌ Not Match":
                cur.execute("""
                    SELECT sc.schedule_date, sc.start_time, sc.end_time
                    FROM student_schedules ss
                    JOIN schedules sc ON ss.schedule_id = sc.id
                    WHERE ss.student_id = %s;
                """, (student_id,))
                picked = cur.fetchone()

                if picked:
                    schedule_date, start_time, end_time = picked
                    from datetime import datetime
                    start_12 = datetime.strptime(str(start_time), "%H:%M:%S").strftime("%I:%M %p")
                    end_12 = datetime.strptime(str(end_time), "%H:%M:%S").strftime("%I:%M %p")
                    interview_status = f"{schedule_date} ({start_12} - {end_12})"
                else:
                    interview_status = "add_date"

    cur.execute("""
        SELECT COUNT(*) 
        FROM personal_descriptions 
        WHERE student_id = %s;
    """, (student_id,))
    inventory_count = cur.fetchone()[0]

    if inventory_count > 0:
        inventory_status = "completed"
    else:
        inventory_status = "not_completed"

    conn.close()

    return render_template(
        "student/home.html",
        student_results=student_results,
        student_campus=student_results["campus"],
        student_survey_answer_completed=student_survey_answer_completed,
        match_status=match_status,
        interview_status=interview_status,
        inventory_status=inventory_status,
        survey_result_unlocked=survey_result_unlocked,
        inventory_result_unlocked=inventory_result_unlocked
    )

@student_bp.route("/choose_schedule")
def choose_schedule():
    if "student_id" not in session:
        return redirect(url_for("student.login_page"))

    student_id = session["student_id"]
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT result_unlocked
        FROM notifications
        WHERE student_id = %s
        ORDER BY id DESC LIMIT 1
    """, (session["student_id"],))
    row = cur.fetchone()

    cur.execute("""
        SELECT
            MAX(CASE WHEN result_unlocked = TRUE THEN 1 ELSE 0 END),
            MAX(CASE WHEN inventory_result_unlocked = TRUE THEN 1 ELSE 0 END)
        FROM notifications
        WHERE student_id = %s
    """, (session["student_id"],))

    survey_result_unlocked, inventory_result_unlocked = cur.fetchone()

    cur.close()
    conn.close()

    return render_template("student/choose_schedule.html",
        survey_result_unlocked=survey_result_unlocked)


@student_bp.route("/get_schedules")
def get_schedules():
    if "student_id" not in session:
        return jsonify([])

    student_id = session["student_id"]

    conn = get_db_connection()
    cur = conn.cursor()

    # Get student's campus
    cur.execute("""
        SELECT campus FROM student WHERE id = %s
    """, (student_id,))
    result = cur.fetchone()

    if not result:
        cur.close()
        conn.close()
        return jsonify([])

    student_campus = result[0]

    # Only get schedules from same campus
    cur.execute("""
        SELECT id, schedule_date, start_time, end_time, slot_count
        FROM schedules
        WHERE slot_count > 0
        AND campus = %s
        ORDER BY schedule_date ASC
    """, (student_campus,))

    rows = cur.fetchall()

    schedules = []
    for r in rows:
        schedules.append({
            "id": r[0],
            "date": r[1].strftime("%Y-%m-%d"),
            "start_time": str(r[2]),
            "end_time": str(r[3]),
            "slots": r[4]
        })

    cur.close()
    conn.close()
    return jsonify(schedules)

@student_bp.route("/save_student_schedule", methods=["POST"])
def save_student_schedule():
    if "student_id" not in session:
        return jsonify({"success": False, "message": "Unauthorized"})

    data = request.json
    schedule_id = data.get("schedule_id")
    student_id = session["student_id"]

    if not schedule_id:
        return jsonify({"success": False, "message": "Missing schedule"})

    conn = get_db_connection()
    cur = conn.cursor()

    # Get student campus
    cur.execute("""
        SELECT campus FROM student WHERE id = %s
    """, (student_id,))
    student_row = cur.fetchone()

    if not student_row:
        cur.close()
        conn.close()
        return jsonify({"success": False, "message": "Student not found"})

    student_campus = student_row[0]

    # Check if schedule belongs to same campus AND has slot
    cur.execute("""
        SELECT campus, slot_count
        FROM schedules
        WHERE id = %s
    """, (schedule_id,))
    schedule_row = cur.fetchone()

    if not schedule_row:
        cur.close()
        conn.close()
        return jsonify({"success": False, "message": "Schedule not found"})

    schedule_campus, slot_count = schedule_row

    if schedule_campus != student_campus:
        cur.close()
        conn.close()
        return jsonify({"success": False, "message": "Invalid campus schedule"})

    if slot_count <= 0:
        cur.close()
        conn.close()
        return jsonify({"success": False, "message": "No available slots"})

    # Check duplicate selection
    cur.execute("""
        SELECT 1 FROM student_schedules 
        WHERE student_id = %s AND schedule_id = %s
    """, (student_id, schedule_id))

    if cur.fetchone():
        cur.close()
        conn.close()
        return jsonify({"success": False, "message": "Already selected"})

    # Insert schedule
    cur.execute("""
        INSERT INTO student_schedules (student_id, schedule_id, created_at)
        VALUES (%s, %s, NOW())
    """, (student_id, schedule_id))

    # Decrease slot safely
    cur.execute("""
        UPDATE schedules
        SET slot_count = slot_count - 1
        WHERE id = %s
    """, (schedule_id,))

    conn.commit()
    cur.close()
    conn.close()

    return jsonify({"success": True, "message": "Schedule saved successfully"})

@student_bp.route("/survey")
def survey():
    if "student_id" not in session:
        return redirect(url_for("student.login_page"))
    
    student_id = session["student_id"]
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT 
            s.fullname, s.gender, s.email
        FROM student s
        WHERE s.id = %s
    """, (student_id,))

    info = cur.fetchone()
                
    return render_template("student/survey.html", info=info)

@student_bp.route("/surveyForm")
def surveyForm():
    if "student_id" not in session:
        return redirect(url_for("student.login_page"))

    session["survey_start"] = time.time()

    student_id = session["student_id"]
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT campus
        FROM student
        WHERE id = %s
    """, (student_id,))
    student_campus = cur.fetchone()[0]
    cur.execute("""
        SELECT program_name
        FROM program
        WHERE campus = %s AND is_active = TRUE
        ORDER BY program_name
    """, (student_campus,))
    programs = cur.fetchall()

    cur.close()
    conn.close()

    return render_template(
        "student/surveyForm.html",
        programs=programs
    )

@student_bp.route("/submit_survey", methods=["POST"])
def submit_survey():
    import time

    # --- 1️⃣ Session validation ---
    if "student_id" not in session or "exam_id" not in session:
        return jsonify({"status": "error", "message": "Not logged in"}), 403

    if "survey_start" not in session or time.time() - session["survey_start"] > 15*60:
        session.clear()
        return jsonify({"status": "error", "message": "Time expired. Survey not saved."}), 403

    # --- 2️⃣ Get data ---
    data = request.json
    preferred_program = data.get("preferred_program")
    answers = data.get("answers")

    TOTAL_PAIRS = 86

    if not preferred_program:
        return jsonify({"status": "error", "message": "Preferred program required"}), 400

    if not answers:
        return jsonify({"status": "error", "message": "No survey answers provided"}), 400

    # Ensure exactly 86 answers
    if len(answers) < TOTAL_PAIRS:
        answers += [None] * (TOTAL_PAIRS - len(answers))
    elif len(answers) > TOTAL_PAIRS:
        answers = answers[:TOTAL_PAIRS]

    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # --- 3️⃣ Prepare SQL ---
        columns = [f"pair{i+1}" for i in range(TOTAL_PAIRS)]
        column_sql = ", ".join(columns)
        placeholder_sql = ", ".join(["%s"] * TOTAL_PAIRS)

        query = f"""
            INSERT INTO student_survey_answer
            (exam_id, student_id, preferred_program, {column_sql})
            VALUES (%s, %s, %s, {placeholder_sql})
            ON CONFLICT (exam_id, student_id) DO UPDATE SET
                preferred_program = EXCLUDED.preferred_program,
                {', '.join([f"{c} = EXCLUDED.{c}" for c in columns])}
        """

        cur.execute(query, (
            session["exam_id"],
            session["student_id"],
            preferred_program,
            *answers
        ))

        # --- 4️⃣ Notification ---
        cur.execute("""
            INSERT INTO notifications (student_id, exam_id, message, is_read)
            VALUES (%s, %s, %s, FALSE)
        """, (
            session["student_id"],
            session["exam_id"],
            "Career Interest Survey Completed!"
        ))

        conn.commit()
        cur.close()
        conn.close()

        return jsonify({"status": "success"})

    except Exception as e:
        print("Survey Save Error:", e)
        return jsonify({"status": "error", "message": "Failed to save survey"}), 500
    
@student_bp.route("/notification")
def notification():
    if "student_id" not in session:
        return redirect(url_for("student.login_page"))

    student_id = session["student_id"]
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT result_unlocked, inventory_result_unlocked
        FROM notifications
        WHERE student_id = %s
        ORDER BY id DESC LIMIT 1
    """, (session["student_id"],))
    row = cur.fetchone()

    cur.execute("""
        SELECT
            MAX(CASE WHEN result_unlocked = TRUE THEN 1 ELSE 0 END),
            MAX(CASE WHEN inventory_result_unlocked = TRUE THEN 1 ELSE 0 END)
        FROM notifications
        WHERE student_id = %s
    """, (session["student_id"],))

    survey_result_unlocked, inventory_result_unlocked = cur.fetchone()

    cur.execute("""
        SELECT message, created_at
        FROM notifications
        WHERE student_id = %s
        ORDER BY created_at DESC
    """, (session["student_id"],))
    notifications = cur.fetchall()

    cur.execute("""
        SELECT s.exam_id, s.fullname, s.campus, sa.preferred_program,
               sa.pair1, sa.pair2, sa.pair3, sa.pair4, sa.pair5,
               sa.pair6, sa.pair7, sa.pair8, sa.pair9, sa.pair10,
               sa.pair11, sa.pair12, sa.pair13, sa.pair14, sa.pair15,
               sa.pair16, sa.pair17, sa.pair18, sa.pair19, sa.pair20,
               sa.pair21, sa.pair22, sa.pair23, sa.pair24, sa.pair25,
               sa.pair26, sa.pair27, sa.pair28, sa.pair29, sa.pair30,
               sa.pair31, sa.pair32, sa.pair33, sa.pair34, sa.pair35,
               sa.pair36, sa.pair37, sa.pair38, sa.pair39, sa.pair40,
               sa.pair41, sa.pair42, sa.pair43, sa.pair44, sa.pair45,
               sa.pair46, sa.pair47, sa.pair48, sa.pair49, sa.pair50,
               sa.pair51, sa.pair52, sa.pair53, sa.pair54, sa.pair55,
               sa.pair56, sa.pair57, sa.pair58, sa.pair59, sa.pair60,
               sa.pair61, sa.pair62, sa.pair63, sa.pair64, sa.pair65,
               sa.pair66, sa.pair67, sa.pair68, sa.pair69, sa.pair70,
               sa.pair71, sa.pair72, sa.pair73, sa.pair74, sa.pair75,
               sa.pair76, sa.pair77, sa.pair78, sa.pair79, sa.pair80,
               sa.pair81, sa.pair82, sa.pair83, sa.pair84, sa.pair85,
               sa.pair86
        FROM student s
        LEFT JOIN student_survey_answer sa 
            ON s.exam_id = sa.exam_id
        WHERE s.id = %s;
    """, (student_id,))
    
    row = cur.fetchone()

    if row:
        student_results = {
            "exam_id": row[0],
            "fullname": row[1],
            "campus": row[2],
            "preferred_program": row[3],
            "answers": [row[i] for i in range(4, 90)]
        }

    cur.close()
    conn.close()

    session["survey_result_unlocked"] = survey_result_unlocked
    session["inventory_result_unlocked"] = inventory_result_unlocked

    return render_template(
        "student/notification.html",
        notifications=notifications,
        student_campus=student_results["campus"],
        survey_result_unlocked=survey_result_unlocked,
        inventory_result_unlocked=inventory_result_unlocked
    )

@student_bp.route("/notification_read/<int:notification_id>", methods=["POST"])
def notification_read(notification_id):
    if "student_id" not in session:
        return jsonify({"status": "error"}), 403

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        UPDATE notifications
        SET is_read = TRUE
        WHERE id = %s AND student_id = %s
    """, (notification_id, session["student_id"]))
    conn.commit()
    cur.close()
    conn.close()

    return jsonify({"status": "success"})

@student_bp.route("/notification_count")
def notification_count():
    if "student_id" not in session:
        return jsonify({"count": 0})

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT COUNT(*) FROM notifications
        WHERE student_id = %s AND is_read = FALSE
    """, (session["student_id"],))
    count = cur.fetchone()[0]
    cur.close()
    conn.close()

    return jsonify({"count": count})

@student_bp.route("/notification_mark_all_read", methods=["POST"])
def notification_mark_all_read():
    if "student_id" not in session:
        return jsonify({"status": "error"}), 403

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        UPDATE notifications
        SET is_read = TRUE
        WHERE student_id = %s AND is_read = FALSE
    """, (session["student_id"],))
    conn.commit()
    cur.close()
    conn.close()

    return jsonify({"status": "success"})

@student_bp.route("/surveyResult_link_clicked")
def surveyResult_link_clicked():
    if "student_id" not in session:
        return redirect(url_for("student.login_page"))

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        UPDATE notifications
        SET result_unlocked = TRUE
        WHERE student_id = %s
    """, (session["student_id"],))
    conn.commit()
    cur.close()
    conn.close()

    session["survey_result_unlocked"] = True

    return redirect(url_for("student.surveyResult"))

@student_bp.route("/studentInventoryResult_link_clicked")
def studentInventoryResult_link_clicked():
    if "student_id" not in session:
        return redirect(url_for("student.login_page"))

    student_id = session["student_id"]
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        UPDATE notifications
        SET inventory_result_unlocked = TRUE
        WHERE student_id = %s
    """, (student_id,))
    conn.commit()
    cur.close()
    conn.close()

    session["inventory_result_unlocked"] = True

    return redirect(url_for("student.studentInventoryResult"))

@student_bp.route("/surveyResult")
def surveyResult():
    if "student_id" not in session:
        return redirect(url_for("student.login_page"))

    student_id = session["student_id"]

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT result_unlocked, inventory_result_unlocked
        FROM notifications
        WHERE student_id = %s
        ORDER BY id DESC LIMIT 1
    """, (session["student_id"],))
    row = cur.fetchone()
    
    cur.execute("""
        SELECT
            MAX(CASE WHEN result_unlocked = TRUE THEN 1 ELSE 0 END),
            MAX(CASE WHEN inventory_result_unlocked = TRUE THEN 1 ELSE 0 END)
        FROM notifications
        WHERE student_id = %s
    """, (session["student_id"],))

    survey_result_unlocked, inventory_result_unlocked = cur.fetchone()

    cur.execute("""
        SELECT s.exam_id, s.fullname, s.school_year, s.campus, s.photo,
               c.campus_name, c.campus_address, c.guidance_counselor,
               sa.preferred_program, sa.ai_explanation,
               sa.pair1, sa.pair2, sa.pair3, sa.pair4, sa.pair5,
               sa.pair6, sa.pair7, sa.pair8, sa.pair9, sa.pair10,
               sa.pair11, sa.pair12, sa.pair13, sa.pair14, sa.pair15,
               sa.pair16, sa.pair17, sa.pair18, sa.pair19, sa.pair20,
               sa.pair21, sa.pair22, sa.pair23, sa.pair24, sa.pair25,
               sa.pair26, sa.pair27, sa.pair28, sa.pair29, sa.pair30,
               sa.pair31, sa.pair32, sa.pair33, sa.pair34, sa.pair35,
               sa.pair36, sa.pair37, sa.pair38, sa.pair39, sa.pair40,
               sa.pair41, sa.pair42, sa.pair43, sa.pair44, sa.pair45,
               sa.pair46, sa.pair47, sa.pair48, sa.pair49, sa.pair50,
               sa.pair51, sa.pair52, sa.pair53, sa.pair54, sa.pair55,
               sa.pair56, sa.pair57, sa.pair58, sa.pair59, sa.pair60,
               sa.pair61, sa.pair62, sa.pair63, sa.pair64, sa.pair65,
               sa.pair66, sa.pair67, sa.pair68, sa.pair69, sa.pair70,
               sa.pair71, sa.pair72, sa.pair73, sa.pair74, sa.pair75,
               sa.pair76, sa.pair77, sa.pair78, sa.pair79, sa.pair80,
               sa.pair81, sa.pair82, sa.pair83, sa.pair84, sa.pair85,
               sa.pair86
        FROM student s
        LEFT JOIN student_survey_answer sa 
            ON s.exam_id = sa.exam_id
        LEFT JOIN campus c
            ON s.campus = c.campus_name
        WHERE s.id = %s;
    """, (student_id,))
    
    row = cur.fetchone()

    year = row[2] if row[2] else "N/A"

    if not row:
        return "No survey results found."

    student_results = {
        "exam_id": row[0],
        "fullname": row[1],
        "school_year": row[2],
        "campus": row[3],
        "photo": row[4],
        "campus_name": row[5],
        "campus_address": row[6],
        "guidance_counselor": row[7],
        "preferred_program": row[8],
        "ai_explanation": format_ai_explanation_for_pdf(row[9]) if row[9] else None,
        "answers": [row[i] for i in range(10, 96)]
    }

    answers_clean = student_results["answers"]
    preferred = student_results["preferred_program"]

    top_letters = []
    program_letters = []

    if answers_clean:
        letter_counts = Counter(answers_clean)
        top_letters = [letter for letter, _ in letter_counts.most_common(3)]
        top_letters = [letter.strip().upper() for letter in top_letters]

    if preferred:
        cur.execute("""
            SELECT category_letter 
            FROM program 
            WHERE LOWER(TRIM(program_name)) = LOWER(TRIM(%s))
            AND LOWER(TRIM(campus)) = LOWER(TRIM(%s))
            LIMIT 1
        """, (preferred, student_results["campus"]))

        result = cur.fetchone()

        if result and result[0]:
            program_letters = [letter.strip().upper() for letter in result[0].split(",")]
        else:
            program_letters = []

    if not preferred and not answers_clean:
        match_status = "Not Yet Answer"
    elif any(letter in program_letters for letter in top_letters):
        match_status = "Match"
    else:
        match_status = "Not Match"

    predicted_programs = []

    if top_letters:
        conditions = " OR ".join(["category_letter ILIKE %s"] * len(top_letters))
        values = [f"%{letter}%" for letter in top_letters]

        query = f"""
            SELECT DISTINCT ON (program_name) program_name, category_letter
            FROM program
            WHERE ({conditions})
            AND TRIM(LOWER(campus)) = TRIM(LOWER(%s))
            ORDER BY program_name
            LIMIT 5
        """

        values.append(student_results["campus"])
        cur.execute(query, values)
        predicted_programs = cur.fetchall()
    print("Top Letters:", top_letters)
    print("Program Letters:", program_letters)

    conn.close()

    return render_template(
        "student/surveyResult.html",
        year=year,
        student_results=student_results,
        guidance_counselor=student_results["guidance_counselor"],
        campus_name=student_results["campus_name"],
        campus_address=student_results["campus_address"],
        top_letters=top_letters,
        letter_descriptions=letter_descriptions,
        match_status=match_status,
        predicted_programs=predicted_programs,
        ai_explanation=student_results["ai_explanation"],
        survey_result_unlocked=survey_result_unlocked,
        inventory_result_unlocked=inventory_result_unlocked
    )

@student_bp.route("/generate-ai-explanation", methods=["POST"])
def generate_ai_explanation():
    if "student_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401

    conn = get_db_connection()
    cur = conn.cursor()

    # get exam_id
    cur.execute("SELECT exam_id FROM student WHERE id=%s", (session["student_id"],))
    exam_id = cur.fetchone()[0]

    # check if explanation already exists
    cur.execute("""
        SELECT ai_explanation
        FROM student_survey_answer
        WHERE exam_id = %s
    """, (exam_id,))
    
    existing = cur.fetchone()

    if existing and existing[0]:
        conn.close()
        return jsonify({"explanation": existing[0]})

    data = request.json
    top_letters = data.get("top_letters", [])
    preferred_program = data.get("preferred_program", "")
    fullname = data.get("fullname", "")

    explanation = generate_ai_insights(
        top_letters,
        preferred_program,
        fullname
    )

    # save explanation
    cur.execute("""
        UPDATE student_survey_answer
        SET ai_explanation = %s
        WHERE exam_id = %s
    """, (explanation, exam_id))

    conn.commit()
    conn.close()

    return jsonify({"explanation": explanation})

@student_bp.route('/download_pdf/<int:student_id>')
def download_pdf(student_id):
    if "student_id" not in session or session["student_id"] != student_id:
        return redirect(url_for("login_page"))

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT s.exam_id, s.fullname, s.school_year, s.campus, s.photo,
               c.campus_name, c.campus_address, c.guidance_counselor,
               sa.preferred_program, sa.ai_explanation,
               sa.pair1, sa.pair2, sa.pair3, sa.pair4, sa.pair5,
               sa.pair6, sa.pair7, sa.pair8, sa.pair9, sa.pair10,
               sa.pair11, sa.pair12, sa.pair13, sa.pair14, sa.pair15,
               sa.pair16, sa.pair17, sa.pair18, sa.pair19, sa.pair20,
               sa.pair21, sa.pair22, sa.pair23, sa.pair24, sa.pair25,
               sa.pair26, sa.pair27, sa.pair28, sa.pair29, sa.pair30,
               sa.pair31, sa.pair32, sa.pair33, sa.pair34, sa.pair35,
               sa.pair36, sa.pair37, sa.pair38, sa.pair39, sa.pair40,
               sa.pair41, sa.pair42, sa.pair43, sa.pair44, sa.pair45,
               sa.pair46, sa.pair47, sa.pair48, sa.pair49, sa.pair50,
               sa.pair51, sa.pair52, sa.pair53, sa.pair54, sa.pair55,
               sa.pair56, sa.pair57, sa.pair58, sa.pair59, sa.pair60,
               sa.pair61, sa.pair62, sa.pair63, sa.pair64, sa.pair65,
               sa.pair66, sa.pair67, sa.pair68, sa.pair69, sa.pair70,
               sa.pair71, sa.pair72, sa.pair73, sa.pair74, sa.pair75,
               sa.pair76, sa.pair77, sa.pair78, sa.pair79, sa.pair80,
               sa.pair81, sa.pair82, sa.pair83, sa.pair84, sa.pair85,
               sa.pair86
        FROM student s
        LEFT JOIN student_survey_answer sa 
            ON s.exam_id = sa.exam_id
        LEFT JOIN campus c
            ON s.campus = c.campus_name
        WHERE s.id = %s;
    """, (student_id,))

    row = cur.fetchone()

    if not row:
        cur.close()
        conn.close()
        return "Survey results not found", 404

    year = row[2] if row[2] else "N/A"

    student_data = {
        "exam_id": row[0],
        "fullname": row[1],
        "school_year": row[2],
        "campus": row[3],
        "photo": row[4],
        "campus_name": row[5],
        "campus_address": row[6],
        "guidance_counselor": row[7],
        "preferred_program": row[8],
        "ai_explanation": format_ai_explanation_for_pdf(row[9]),
        "answers": [row[i] for i in range(10, 96)]
    }

    answers_clean = student_data["answers"]
    preferred = student_data["preferred_program"]

    top_letters = []
    program_letters = []

    if answers_clean:
        letter_counts = Counter(answers_clean)
        top_letters = [letter for letter, _ in letter_counts.most_common(3)]

    if preferred:
        cur.execute("SELECT category_letter FROM program WHERE program_name = %s", (preferred,))
        result = cur.fetchone()
        program_letters = result[0].split(",") if result else []

    if not preferred and not answers_clean:
        match_status = "Not Yet Answer"
    elif any(letter in program_letters for letter in top_letters):
        match_status = "Match"
    else:
        match_status = "Not Match"

    predicted_programs = []

    if top_letters:
        conditions = " OR ".join(["category_letter ILIKE %s"] * len(top_letters))
        values = [f"%{letter}%" for letter in top_letters]

        query = f"""
            SELECT program_name, category_letter
            FROM program
            WHERE {conditions}
            ORDER BY program_name
            LIMIT 5
        """
        cur.execute(query, values)
        predicted_programs = cur.fetchall()

    student_photo_base64 = None

    student_photo_base64 = student_photo_to_base64(student_data.get("photo"))

    cpsu_logo = image_to_base64("cpsulogo.png")
    bagong_logo = image_to_base64("bagong-pilipinas-logo.png")
    safe_logo = image_to_base64("logo.png")

    html = render_template(
        "student/surveyResultPDF.html",
        year=year,
        student_data=student_data,
        student_campus=student_data["campus"],
        guidance_counselor=student_data["guidance_counselor"],
        campus_name=student_data["campus_name"],
        campus_address=student_data["campus_address"],
        top_letters=top_letters,
        match_status=match_status,
        predicted_programs=predicted_programs,
        letter_descriptions=letter_descriptions,
        cpsu_logo_base64=cpsu_logo,
        bagong_logo_base64=bagong_logo,
        safe_logo_base64=safe_logo,
        student_photo_base64=student_photo_base64
    )

    pdf_file = generate_pdf(html)

    filename = f"Career_Survey_Result_{student_data['exam_id']}_{student_data['fullname']}.pdf"

    print("PHOTO FILE:", student_data["photo"])
    print("PHOTO BASE64:", bool(student_photo_base64))

    return send_file(
        pdf_file,
        mimetype="application/pdf",
        download_name=filename,
        as_attachment=True
    )

@student_bp.route("/studentInventory")
def studentInventory():
    if "exam_id" not in session or "student_id" not in session:
        return redirect(url_for("student.login_page"))

    student_id = session["student_id"]

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT s.exam_id, s.fullname, s.created_at, s.campus, s.photo, 
               sa.preferred_program, sa.ai_explanation,
               sa.pair1, sa.pair2, sa.pair3, sa.pair4, sa.pair5,
               sa.pair6, sa.pair7, sa.pair8, sa.pair9, sa.pair10,
               sa.pair11, sa.pair12, sa.pair13, sa.pair14, sa.pair15,
               sa.pair16, sa.pair17, sa.pair18, sa.pair19, sa.pair20,
               sa.pair21, sa.pair22, sa.pair23, sa.pair24, sa.pair25,
               sa.pair26, sa.pair27, sa.pair28, sa.pair29, sa.pair30,
               sa.pair31, sa.pair32, sa.pair33, sa.pair34, sa.pair35,
               sa.pair36, sa.pair37, sa.pair38, sa.pair39, sa.pair40,
               sa.pair41, sa.pair42, sa.pair43, sa.pair44, sa.pair45,
               sa.pair46, sa.pair47, sa.pair48, sa.pair49, sa.pair50,
               sa.pair51, sa.pair52, sa.pair53, sa.pair54, sa.pair55,
               sa.pair56, sa.pair57, sa.pair58, sa.pair59, sa.pair60,
               sa.pair61, sa.pair62, sa.pair63, sa.pair64, sa.pair65,
               sa.pair66, sa.pair67, sa.pair68, sa.pair69, sa.pair70,
               sa.pair71, sa.pair72, sa.pair73, sa.pair74, sa.pair75,
               sa.pair76, sa.pair77, sa.pair78, sa.pair79, sa.pair80,
               sa.pair81, sa.pair82, sa.pair83, sa.pair84, sa.pair85,
               sa.pair86
        FROM student s
        LEFT JOIN student_survey_answer sa 
            ON s.exam_id = sa.exam_id
        WHERE s.id = %s;
    """, (student_id,))
    
    row = cur.fetchone()

    created_at = row[2]

    start_year = created_at.year
    end_year = start_year + 1
    year = f"{start_year}-{end_year}"

    if not row:
        return "No survey results found."

    student_results = {
        "exam_id": row[0],
        "fullname": row[1],
        "created_at": row[2],
        "campus": row[3],
    }

    cur.execute("""
        SELECT program_name 
        FROM program 
        WHERE campus = %s
        ORDER BY program_name
    """, (student_results["campus"],))
    
    programs = [row[0] for row in cur.fetchall()]

    cur.close()
    conn.close()

    return render_template("student/studentInventory.html", programs=programs, student_campus=student_results["campus"])


@student_bp.route("/student/save_course", methods=["POST"])
def save_course():
    if "exam_id" not in session or "student_id" not in session:
        return jsonify({"status": "error", "message": "Not logged in"}), 401

    course_name = request.form.get("course_name")
    exam_id = session["exam_id"]
    student_id = session["student_id"]

    if not course_name:
        return jsonify({"status": "error", "message": "Course is required"}), 400

    conn = get_db_connection()
    cur = conn.cursor()

    # Check if course already exists for this exam_id
    cur.execute("SELECT id FROM course WHERE exam_id = %s AND student_id = %s", (exam_id, student_id))
    existing = cur.fetchone()

    if existing:
        # Update existing course
        cur.execute("""
            UPDATE course
            SET course_name = %s, created_at = %s
            WHERE exam_id = %s AND student_id = %s
        """, (course_name, datetime.now(), exam_id, student_id))
    else:
        # Insert new course
        cur.execute("""
            INSERT INTO course (exam_id, student_id, course_name, created_at)
            VALUES (%s, %s, %s, %s)
        """, (exam_id, student_id, course_name, datetime.now()))

    conn.commit()
    cur.close()
    conn.close()

    return jsonify({"status": "success", "message": "Course saved successfully"})

@student_bp.route("/studentInventoryForm", methods=["GET", "POST"])
def studentInventoryForm():
    if "student_id" not in session:
        return redirect(url_for("student.login_page"))

    student_id = session["student_id"]
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    cur.execute("""
        SELECT *
        FROM student s
        LEFT JOIN personal_information sa ON sa.student_id = s.id
        LEFT JOIN family_background sb ON sb.student_id = s.id
        LEFT JOIN status_of_parent sc ON sc.student_id = s.id
        LEFT JOIN academic_information sd ON sd.student_id = s.id
        LEFT JOIN behavior_information se ON se.student_id = s.id
        LEFT JOIN psychological_consultations sf ON sf.student_id = s.id
        LEFT JOIN personal_descriptions sg ON sg.student_id = s.id
        LEFT JOIN course sh ON sh.student_id = s.id
        LEFT JOIN cpsu_enrollment_reason si ON si.student_id = s.id
        LEFT JOIN other_schools_considered sj ON sj.student_id = s.id
        WHERE s.id = %s
    """, (student_id,))
    info = cur.fetchone()

    selected_reasons = []

    if info and info["reasons"]:
        selected_reasons = [r.strip() for r in info["reasons"].split(",")]

    other_schools_selected = []

    if info and info["school_choices"]:
        other_schools_selected = [s.strip() for s in info["school_choices"].split(",")]

    cur.execute("SELECT 1 FROM personal_information WHERE student_id = %s", (student_id,))
    existing_inventory = cur.fetchone()
    is_update = True if existing_inventory else False

    if request.method == "POST":
        
        nickname = request.form.get("nickname")
        present_address = request.form.get("present_address")
        provincial_address = request.form.get("provincial_address")
        date_of_birth = request.form.get("date_of_birth")
        place_of_birth = request.form.get("place_of_birth")
        age = request.form.get("age")
        birth_order = request.form.get("birth_order")
        siblings_count = request.form.get("siblings_count")
        civil_status = request.form.get("civil_status")
        religion = request.form.get("religion")
        nationality = request.form.get("nationality")
        home_phone = request.form.get("home_phone")
        mobile_no = request.form.get("mobile_no")
        email = request.form.get("email")
        weight = request.form.get("weight")
        height = request.form.get("height")
        blood_type = request.form.get("blood_type")
        hobbies = request.form.get("hobbies")
        talents = request.form.get("talents")
        emergency_name = request.form.get("emergency_name")
        emergency_relationship = request.form.get("emergency_relationship")
        emergency_address = request.form.get("emergency_address")
        emergency_contact = request.form.get("emergency_contact")

        father_name = request.form.get("father_name") or None
        father_age = request.form.get("father_age") or None
        father_education = request.form.get("father_education") or None
        father_occupation = request.form.get("father_occupation") or None
        father_income = request.form.get("father_income") or None
        father_contact = request.form.get("father_contact") or None

        mother_name = request.form.get("mother_name") or None
        mother_age = request.form.get("mother_age") or None
        mother_education = request.form.get("mother_education") or None
        mother_occupation = request.form.get("mother_occupation") or None
        mother_income = request.form.get("mother_income") or None
        mother_contact = request.form.get("mother_contact") or None

        parent_status = request.form.get("parent_status")  # radio button

        another_family_list = request.form.getlist("another_family")
        father_another_family = "father" in another_family_list
        mother_another_family = "mother" in another_family_list

        elementary_school_name = request.form.get("elementary_school_name")
        elementary_year_graduated = request.form.get("elementary_year_graduated")
        elementary_awards = request.form.get("elementary_awards")

        junior_high_school_name = request.form.get("junior_high_school_name")
        junior_high_year_graduated = request.form.get("junior_high_year_graduated")
        junior_high_awards = request.form.get("junior_high_awards")

        senior_high_school_name = request.form.get("senior_high_school_name")
        senior_high_year_graduated = request.form.get("senior_high_year_graduated")
        senior_high_awards = request.form.get("senior_high_awards")
        senior_high_track = request.form.get("senior_high_track")
        senior_high_strand = request.form.get("senior_high_strand")

        subject_interested = request.form.get("subject_interested")
        org_membership = request.form.get("org_membership")
        study_finance = request.form.get("study_finance")
        course_personal_choice = request.form.get("course_personal_choice")

        influenced_by = request.form.get("influenced_by")
        feeling_about_course = request.form.get("feeling_about_course")
        personal_choice = request.form.get("personal_choice")

        # Validate Step 5
        if not subject_interested or not org_membership or not study_finance or not course_personal_choice:
            flash("Please complete all required fields before proceeding.", "error")
            return redirect(url_for("student.studentInventoryForm"))

        # If YES → clear Step 6 fields
        if course_personal_choice == "yes":
            influenced_by = None
            feeling_about_course = None
            personal_choice = None

        # If NO → Step 6 required
        if course_personal_choice == "no":
            if not influenced_by or not feeling_about_course or not personal_choice:
                flash("Please complete the additional course questions.", "error")
                return redirect(url_for("student.studentInventoryForm"))

        enroll_reasons = request.form.getlist("enroll_reasons[]")
        other_reason = request.form.get("other_reason") or ""

        if not enroll_reasons and not other_reason.strip():
            flash("Please select at least one reason or specify in Others.", "error")
            return redirect(url_for("student.studentInventoryForm"))

        reasons_str = ", ".join(enroll_reasons) if enroll_reasons else None

        other_schools = request.form.getlist("other_school[]")
        other_school_text = (request.form.get("other_school_other") or "").strip()

        if not other_schools and not other_school_text:
            flash("Please select at least one school or specify in 'Others'.", "error")
            return redirect(url_for("student.studentInventoryForm"))

        other_schools_str = ", ".join(other_schools) if other_schools else None

        def get_behavior(field):
            checked = request.form.get(field)
            when = request.form.get(f"{field}_when") or None
            bother = request.form.get(f"{field}_bother")

            if checked == "yes":
                return True, when, True if bother == "yes" else False
            else:
                return False, None, None
            
        bullying, bullying_when, bullying_bother = get_behavior("bullying")
        suicidal_thoughts, suicidal_thoughts_when, suicidal_thoughts_bother = get_behavior("suicidal_thoughts")
        suicidal_attempts, suicidal_attempts_when, suicidal_attempts_bother = get_behavior("suicidal_attempts")
        panic_attacks, panic_attacks_when, panic_attacks_bother = get_behavior("panic_attacks")
        anxiety, anxiety_when, anxiety_bother = get_behavior("anxiety")
        depression, depression_when, depression_bother = get_behavior("depression")
        self_anger_issues, self_anger_issues_when, self_anger_issues_bother = get_behavior("self_anger_issues")
        recurring_negative_thoughts, recurring_negative_thoughts_when, recurring_negative_thoughts_bother = get_behavior("recurring_negative_thoughts")
        low_self_esteem, low_self_esteem_when, low_self_esteem_bother = get_behavior("low_self_esteem")
        poor_study_habits, poor_study_habits_when, poor_study_habits_bother = get_behavior("poor_study_habits")
        poor_in_decision_making, poor_in_decision_making_when, poor_in_decision_making_bother = get_behavior("poor_in_decision_making")
        impulsivity, impulsivity_when, impulsivity_bother = get_behavior("impulsivity")
        poor_sleeping_habits, poor_sleeping_habits_when, poor_sleeping_habits_bother = get_behavior("poor_sleeping_habits")
        loss_of_appetite, loss_of_appetite_when, loss_of_appetite_bother = get_behavior("loss_of_appetite")
        over_eating, over_eating_when, over_eating_bother = get_behavior("over_eating")
        poor_hygiene, poor_hygiene_when, poor_hygiene_bother = get_behavior("poor_hygiene")
        withdrawal_isolation, withdrawal_isolation_when, withdrawal_isolation_bother = get_behavior("withdrawal_isolation")
        family_problem, family_problem_when, family_problem_bother = get_behavior("family_problem")
        other_relationship_problem, other_relationship_problem_when, other_relationship_problem_bother = get_behavior("other_relationship_problem")
        alcohol_addiction, alcohol_addiction_when, alcohol_addiction_bother = get_behavior("alcohol_addiction")
        gambling_addiction, gambling_addiction_when, gambling_addiction_bother = get_behavior("gambling_addiction")
        drug_addiction, drug_addiction_when, drug_addiction_bother = get_behavior("drug_addiction")
        computer_addiction, computer_addiction_when, computer_addiction_bother = get_behavior("computer_addiction")
        sexual_harassment, sexual_harassment_when, sexual_harassment_bother = get_behavior("sexual_harassment")
        sexual_abuse, sexual_abuse_when, sexual_abuse_bother = get_behavior("sexual_abuse")
        physical_abuse, physical_abuse_when, physical_abuse_bother = get_behavior("physical_abuse")
        verbal_abuse, verbal_abuse_when, verbal_abuse_bother = get_behavior("verbal_abuse")
        pre_marital_sex, pre_marital_sex_when, pre_marital_sex_bother = get_behavior("pre_marital_sex")
        teenage_pregnancy, teenage_pregnancy_when, teenage_pregnancy_bother = get_behavior("teenage_pregnancy")
        abortion, abortion_when, abortion_bother = get_behavior("abortion")
        extra_marital_affairs, extra_marital_affairs_when, extra_marital_affairs_bother = get_behavior("extra_marital_affairs")

        psychiatrist_before = request.form.get("psychiatrist_before")
        psychiatrist_reason = request.form.get("psychiatrist_reason") if psychiatrist_before == "yes" else None
        psychiatrist_when = request.form.get("psychiatrist_when") if psychiatrist_before == "yes" else None

        psychologist_before = request.form.get("psychologist_before")
        psychologist_reason = request.form.get("psychologist_reason") if psychologist_before == "yes" else None
        psychologist_when = request.form.get("psychologist_when") if psychologist_before == "yes" else None

        counselor_before = request.form.get("counselor_before")
        counselor_reason = request.form.get("counselor_reason") if counselor_before == "yes" else None
        counselor_when = request.form.get("counselor_when") if counselor_before == "yes" else None

        personal_description = request.form.get("personal_description")

        if not personal_description or personal_description.strip() == "":
            error = "Please enter something about yourself."
            return render_template("student/studentInventoryForm.html", error=error)

        consent = request.form.get("consent")
        if not consent:
            return "Consent is required", 400
        consent_value = True if consent == "on" else False

        if is_update:
            cur.execute("""
                UPDATE personal_information SET
                    nickname=%s,
                    present_address=%s,
                    provincial_address=%s,
                    date_of_birth=%s,
                    place_of_birth=%s,
                    age=%s,
                    birth_order=%s,
                    siblings_count=%s,
                    civil_status=%s,
                    religion=%s,
                    nationality=%s,
                    home_phone=%s,
                    mobile_no=%s,
                    email=%s,
                    weight=%s,
                    height=%s,
                    blood_type=%s,
                    hobbies=%s,
                    talents=%s,
                    emergency_name=%s,
                    emergency_relationship=%s,
                    emergency_address=%s,
                    emergency_contact=%s
                WHERE student_id=%s
            """, (
                nickname, present_address, provincial_address,
                date_of_birth, place_of_birth, age, birth_order, siblings_count,
                civil_status, religion, nationality, home_phone, mobile_no, email,
                weight, height, blood_type, hobbies, talents,
                emergency_name, emergency_relationship, emergency_address, emergency_contact,
                student_id
            ))
        else:
            cur.execute("""
                INSERT INTO personal_information (
                    student_id, nickname, present_address, provincial_address,
                    date_of_birth, place_of_birth, age, birth_order, siblings_count,
                    civil_status, religion, nationality, home_phone, mobile_no, email,
                    weight, height, blood_type, hobbies, talents,
                    emergency_name, emergency_relationship, emergency_address, emergency_contact
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                student_id, nickname, present_address, provincial_address,
                date_of_birth, place_of_birth, age, birth_order, siblings_count,
                civil_status, religion, nationality, home_phone, mobile_no, email,
                weight, height, blood_type, hobbies, talents,
                emergency_name, emergency_relationship, emergency_address, emergency_contact
            ))

        if is_update:
            cur.execute("""
                UPDATE family_background SET
                    father_name=%s,
                    father_age=%s,
                    father_education=%s,
                    father_occupation=%s,
                    father_income=%s,
                    father_contact=%s,
                    mother_name=%s,
                    mother_age=%s,
                    mother_education=%s,
                    mother_occupation=%s,
                    mother_income=%s,
                    mother_contact=%s
                WHERE student_id=%s
            """, (
                father_name, father_age, father_education, father_occupation,
                father_income, father_contact, mother_name, mother_age, mother_education,
                mother_occupation, mother_income, mother_contact,
                student_id
            ))
        else:
            cur.execute("""
                INSERT INTO family_background (
                    student_id, father_name, father_age, father_education, father_occupation,
                    father_income, father_contact, mother_name, mother_age, mother_education,
                    mother_occupation, mother_income, mother_contact
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                student_id, father_name, father_age, father_education, father_occupation,
                father_income, father_contact, mother_name, mother_age, mother_education,
                mother_occupation, mother_income, mother_contact
            ))

        if is_update:
            cur.execute("""
                UPDATE status_of_parent SET
                    parent_status=%s,
                    father_another_family=%s,
                    mother_another_family=%s
                WHERE student_id=%s
            """, (
                parent_status, father_another_family, mother_another_family,
                student_id
            ))
        else:
            cur.execute("""
                INSERT INTO status_of_parent (
                    student_id, parent_status, father_another_family, mother_another_family
                ) VALUES (%s, %s, %s, %s)
            """, (
                student_id,
                parent_status if parent_status else None,
                father_another_family,
                mother_another_family
            ))

        if is_update:
            cur.execute("""
                UPDATE academic_information SET
                    elementary_school_name=%s,
                    elementary_year_graduated=%s,
                    elementary_awards=%s,
                    junior_high_school_name=%s,
                    junior_high_year_graduated=%s,
                    junior_high_awards=%s,
                    senior_high_school_name=%s,
                    senior_high_year_graduated=%s,
                    senior_high_awards=%s,
                    senior_high_track=%s,
                    senior_high_strand=%s,
                    subject_interested=%s,
                    org_membership=%s,
                    study_finance=%s,
                    course_personal_choice=%s,
                    influenced_by=%s,
                    feeling_about_course=%s,
                    personal_choice=%s
                WHERE student_id=%s
            """, (
                elementary_school_name, elementary_year_graduated, elementary_awards,
                junior_high_school_name, junior_high_year_graduated, junior_high_awards,
                senior_high_school_name, senior_high_year_graduated, senior_high_awards,
                senior_high_track, senior_high_strand, subject_interested, org_membership,
                study_finance, True if course_personal_choice == "yes" else False, 
                influenced_by, feeling_about_course, personal_choice,
                student_id
            ))
        else:
            cur.execute("""
                INSERT INTO academic_information (
                    student_id, elementary_school_name, elementary_year_graduated, elementary_awards,
                    junior_high_school_name, junior_high_year_graduated, junior_high_awards,
                    senior_high_school_name, senior_high_year_graduated, senior_high_awards,
                    senior_high_track, senior_high_strand, subject_interested, org_membership,
                    study_finance, course_personal_choice, influenced_by, feeling_about_course, personal_choice
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                student_id, elementary_school_name, elementary_year_graduated, elementary_awards,
                junior_high_school_name, junior_high_year_graduated, junior_high_awards,
                senior_high_school_name, senior_high_year_graduated, senior_high_awards,
                senior_high_track, senior_high_strand, subject_interested, org_membership,
                study_finance, True if course_personal_choice == "yes" else False, 
                influenced_by, feeling_about_course, personal_choice
            ))

        if is_update:
            cur.execute("""
                UPDATE cpsu_enrollment_reason SET
                    reasons=%s,
                    other_reason=%s
                WHERE student_id=%s
            """, (
                reasons_str, other_reason,
                student_id
            ))
        else:
            cur.execute("""
                INSERT INTO cpsu_enrollment_reason (student_id, reasons, other_reason)
                VALUES (%s, %s, %s)
            """, (student_id, reasons_str, other_reason))

        if is_update:
            cur.execute("""
                UPDATE other_schools_considered SET
                    school_choices=%s,
                    other_school=%s
                WHERE student_id=%s
            """, (
                other_schools_str, other_school_text,
                student_id
            ))
        else:
            cur.execute("""
                INSERT INTO other_schools_considered (student_id, school_choices, other_school)
                VALUES (%s, %s, %s)
            """, (student_id, other_schools_str, other_school_text))

        if is_update:
            cur.execute("""
                UPDATE behavior_information SET
                    bullying=%s, bullying_when=%s, bullying_bother=%s, 
                    suicidal_thoughts=%s, suicidal_thoughts_when=%s, suicidal_thoughts_bother=%s,
                    suicidal_attempts=%s, suicidal_attempts_when=%s, suicidal_attempts_bother=%s,
                    panic_attacks=%s, panic_attacks_when=%s, panic_attacks_bother=%s,
                    anxiety=%s, anxiety_when=%s, anxiety_bother=%s,
                    depression=%s, depression_when=%s, depression_bother=%s,
                    self_anger_issues=%s, self_anger_issues_when=%s, self_anger_issues_bother=%s,
                    recurring_negative_thoughts=%s, recurring_negative_thoughts_when=%s, recurring_negative_thoughts_bother=%s,
                    low_self_esteem=%s, low_self_esteem_when=%s, low_self_esteem_bother=%s,
                    poor_study_habits=%s, poor_study_habits_when=%s, poor_study_habits_bother=%s,
                    poor_in_decision_making=%s, poor_in_decision_making_when=%s, poor_in_decision_making_bother=%s,
                    impulsivity=%s, impulsivity_when=%s, impulsivity_bother=%s,
                    poor_sleeping_habits=%s, poor_sleeping_habits_when=%s, poor_sleeping_habits_bother=%s,
                    loss_of_appetite=%s, loss_of_appetite_when=%s, loss_of_appetite_bother=%s,
                    over_eating=%s, over_eating_when=%s, over_eating_bother=%s,
                    poor_hygiene=%s, poor_hygiene_when=%s, poor_hygiene_bother=%s,
                    withdrawal_isolation=%s, withdrawal_isolation_when=%s, withdrawal_isolation_bother=%s,
                    family_problem=%s, family_problem_when=%s, family_problem_bother=%s,
                    other_relationship_problem=%s, other_relationship_problem_when=%s, other_relationship_problem_bother=%s,
                    alcohol_addiction=%s, alcohol_addiction_when=%s, alcohol_addiction_bother=%s,
                    gambling_addiction=%s, gambling_addiction_when=%s, gambling_addiction_bother=%s,
                    drug_addiction=%s, drug_addiction_when=%s, drug_addiction_bother=%s,
                    computer_addiction=%s, computer_addiction_when=%s, computer_addiction_bother=%s,
                    sexual_harassment=%s, sexual_harassment_when=%s, sexual_harassment_bother=%s,
                    sexual_abuse=%s, sexual_abuse_when=%s, sexual_abuse_bother=%s,
                    physical_abuse=%s, physical_abuse_when=%s, physical_abuse_bother=%s,
                    verbal_abuse=%s, verbal_abuse_when=%s, verbal_abuse_bother=%s,
                    pre_marital_sex=%s, pre_marital_sex_when=%s, pre_marital_sex_bother=%s,
                    teenage_pregnancy=%s, teenage_pregnancy_when=%s, teenage_pregnancy_bother=%s,
                    abortion=%s, abortion_when=%s, abortion_bother=%s,
                    extra_marital_affairs=%s, extra_marital_affairs_when=%s, extra_marital_affairs_bother=%s
                WHERE student_id=%s
            """, (
                bullying, bullying_when, bullying_bother,
                suicidal_thoughts, suicidal_thoughts_when, suicidal_thoughts_bother,
                suicidal_attempts, suicidal_attempts_when, suicidal_attempts_bother,
                panic_attacks, panic_attacks_when, panic_attacks_bother,
                anxiety, anxiety_when, anxiety_bother,
                depression, depression_when, depression_bother,
                self_anger_issues, self_anger_issues_when, self_anger_issues_bother,
                recurring_negative_thoughts, recurring_negative_thoughts_when, recurring_negative_thoughts_bother,
                low_self_esteem, low_self_esteem_when, low_self_esteem_bother,
                poor_study_habits, poor_study_habits_when, poor_study_habits_bother,
                poor_in_decision_making, poor_in_decision_making_when, poor_in_decision_making_bother,
                impulsivity, impulsivity_when, impulsivity_bother,
                poor_sleeping_habits, poor_sleeping_habits_when, poor_sleeping_habits_bother,
                loss_of_appetite, loss_of_appetite_when, loss_of_appetite_bother,
                over_eating, over_eating_when, over_eating_bother,
                poor_hygiene, poor_hygiene_when, poor_hygiene_bother,
                withdrawal_isolation, withdrawal_isolation_when, withdrawal_isolation_bother,
                family_problem, family_problem_when, family_problem_bother,
                other_relationship_problem, other_relationship_problem_when, other_relationship_problem_bother,
                alcohol_addiction, alcohol_addiction_when, alcohol_addiction_bother,
                gambling_addiction, gambling_addiction_when, gambling_addiction_bother,
                drug_addiction, drug_addiction_when, drug_addiction_bother,
                computer_addiction, computer_addiction_when, computer_addiction_bother,
                sexual_harassment, sexual_harassment_when, sexual_harassment_bother,
                sexual_abuse, sexual_abuse_when, sexual_abuse_bother,
                physical_abuse, physical_abuse_when, physical_abuse_bother,
                verbal_abuse, verbal_abuse_when, verbal_abuse_bother,
                pre_marital_sex, pre_marital_sex_when, pre_marital_sex_bother,
                teenage_pregnancy, teenage_pregnancy_when, teenage_pregnancy_bother,
                abortion, abortion_when, abortion_bother,
                extra_marital_affairs, extra_marital_affairs_when, extra_marital_affairs_bother,
                student_id
            ))
        else:
            cur.execute("""
                INSERT INTO behavior_information (
                    student_id, bullying, bullying_when, bullying_bother,
                    suicidal_thoughts, suicidal_thoughts_when, suicidal_thoughts_bother,
                    suicidal_attempts, suicidal_attempts_when, suicidal_attempts_bother,
                    panic_attacks, panic_attacks_when, panic_attacks_bother,
                    anxiety, anxiety_when, anxiety_bother,
                    depression, depression_when, depression_bother,
                    self_anger_issues, self_anger_issues_when, self_anger_issues_bother,
                    recurring_negative_thoughts, recurring_negative_thoughts_when, recurring_negative_thoughts_bother,
                    low_self_esteem, low_self_esteem_when, low_self_esteem_bother,
                    poor_study_habits, poor_study_habits_when, poor_study_habits_bother,
                    poor_in_decision_making, poor_in_decision_making_when, poor_in_decision_making_bother,
                    impulsivity, impulsivity_when, impulsivity_bother,
                    poor_sleeping_habits, poor_sleeping_habits_when, poor_sleeping_habits_bother,
                    loss_of_appetite, loss_of_appetite_when, loss_of_appetite_bother,
                    over_eating, over_eating_when, over_eating_bother,
                    poor_hygiene, poor_hygiene_when, poor_hygiene_bother,
                    withdrawal_isolation, withdrawal_isolation_when, withdrawal_isolation_bother,
                    family_problem, family_problem_when, family_problem_bother,
                    other_relationship_problem, other_relationship_problem_when, other_relationship_problem_bother,
                    alcohol_addiction, alcohol_addiction_when, alcohol_addiction_bother,
                    gambling_addiction, gambling_addiction_when, gambling_addiction_bother,
                    drug_addiction, drug_addiction_when, drug_addiction_bother,
                    computer_addiction, computer_addiction_when, computer_addiction_bother,
                    sexual_harassment, sexual_harassment_when, sexual_harassment_bother,
                    sexual_abuse, sexual_abuse_when, sexual_abuse_bother,
                    physical_abuse, physical_abuse_when, physical_abuse_bother,
                    verbal_abuse, verbal_abuse_when, verbal_abuse_bother,
                    pre_marital_sex, pre_marital_sex_when, pre_marital_sex_bother,
                    teenage_pregnancy, teenage_pregnancy_when, teenage_pregnancy_bother,
                    abortion, abortion_when, abortion_bother,
                    extra_marital_affairs, extra_marital_affairs_when, extra_marital_affairs_bother
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                student_id, bullying, bullying_when, bullying_bother,
                suicidal_thoughts, suicidal_thoughts_when, suicidal_thoughts_bother,
                suicidal_attempts, suicidal_attempts_when, suicidal_attempts_bother,
                panic_attacks, panic_attacks_when, panic_attacks_bother,
                anxiety, anxiety_when, anxiety_bother,
                depression, depression_when, depression_bother,
                self_anger_issues, self_anger_issues_when, self_anger_issues_bother,
                recurring_negative_thoughts, recurring_negative_thoughts_when, recurring_negative_thoughts_bother,
                low_self_esteem, low_self_esteem_when, low_self_esteem_bother,
                poor_study_habits, poor_study_habits_when, poor_study_habits_bother,
                poor_in_decision_making, poor_in_decision_making_when, poor_in_decision_making_bother,
                impulsivity, impulsivity_when, impulsivity_bother,
                poor_sleeping_habits, poor_sleeping_habits_when, poor_sleeping_habits_bother,
                loss_of_appetite, loss_of_appetite_when, loss_of_appetite_bother,
                over_eating, over_eating_when, over_eating_bother,
                poor_hygiene, poor_hygiene_when, poor_hygiene_bother,
                withdrawal_isolation, withdrawal_isolation_when, withdrawal_isolation_bother,
                family_problem, family_problem_when, family_problem_bother,
                other_relationship_problem, other_relationship_problem_when, other_relationship_problem_bother,
                alcohol_addiction, alcohol_addiction_when, alcohol_addiction_bother,
                gambling_addiction, gambling_addiction_when, gambling_addiction_bother,
                drug_addiction, drug_addiction_when, drug_addiction_bother,
                computer_addiction, computer_addiction_when, computer_addiction_bother,
                sexual_harassment, sexual_harassment_when, sexual_harassment_bother,
                sexual_abuse, sexual_abuse_when, sexual_abuse_bother,
                physical_abuse, physical_abuse_when, physical_abuse_bother,
                verbal_abuse, verbal_abuse_when, verbal_abuse_bother,
                pre_marital_sex, pre_marital_sex_when, pre_marital_sex_bother,
                teenage_pregnancy, teenage_pregnancy_when, teenage_pregnancy_bother,
                abortion, abortion_when, abortion_bother,
                extra_marital_affairs, extra_marital_affairs_when, extra_marital_affairs_bother
            ))

        if is_update:
            cur.execute("""
                UPDATE psychological_consultations SET
                    psychiatrist_before=%s,
                    psychiatrist_reason=%s,
                    psychiatrist_when=%s,
                    psychologist_before=%s,
                    psychologist_reason=%s,
                    psychologist_when=%s,
                    counselor_before=%s,
                    counselor_reason=%s,
                    counselor_when=%s
                WHERE student_id=%s
            """, (
                psychiatrist_before, psychiatrist_reason, psychiatrist_when,
                psychologist_before, psychologist_reason, psychologist_when,
                counselor_before, counselor_reason, counselor_when,
                student_id
            ))
        else:
            cur.execute("""
                INSERT INTO psychological_consultations (
                    student_id,
                    psychiatrist_before, psychiatrist_reason, psychiatrist_when,
                    psychologist_before, psychologist_reason, psychologist_when,
                    counselor_before, counselor_reason, counselor_when
                )
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (
                student_id,
                psychiatrist_before, psychiatrist_reason, psychiatrist_when,
                psychologist_before, psychologist_reason, psychologist_when,
                counselor_before, counselor_reason, counselor_when
            ))

        if is_update:
            cur.execute("""
                UPDATE personal_descriptions SET
                    personal_description=%s,
                    consent=%s,
                    consent_date=NOW()
                WHERE student_id=%s
            """, (
                personal_description, consent_value,
                student_id
            ))
        else:
            cur.execute("""
                INSERT INTO personal_descriptions (
                    student_id,personal_description, consent, consent_date
                )
                VALUES (%s,%s,%s, NOW())
            """, (
                student_id,personal_description, consent_value
            ))

        if not is_update:
            cur.execute("""
                INSERT INTO notifications (student_id, exam_id, message)
                VALUES (%s, %s, %s)
            """, (student_id, session["exam_id"], "Student Inventory Form Submitted Successfully!"))
        
        conn.commit()
        cur.close()
        conn.close()

        flash("Inventory form submitted successfully!", "success")
        return redirect(url_for("student.home"))

    cur.execute("SELECT id, fullname, gender, email FROM student WHERE id = %s", (student_id,))
    student = cur.fetchone()

    cur.close()
    conn.close()

    return render_template("student/studentInventoryForm.html", 
        student=student, info=info, has_data=True if info else False, 
        selected_reasons=selected_reasons, other_schools_selected=other_schools_selected)

@student_bp.route("/studentInventoryResult")
def studentInventoryResult():
    if "student_id" not in session:
        return redirect(url_for("student.login_page"))
    
    student_id = session["student_id"]
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    cur.execute("""
        SELECT result_unlocked, inventory_result_unlocked
        FROM notifications
        WHERE student_id = %s
        ORDER BY id DESC LIMIT 1
    """, (session["student_id"],))
    row = cur.fetchone()

    cur.execute("""
        SELECT
            MAX(CASE WHEN result_unlocked = TRUE THEN 1 ELSE 0 END),
            MAX(CASE WHEN inventory_result_unlocked = TRUE THEN 1 ELSE 0 END)
        FROM notifications
        WHERE student_id = %s
    """, (session["student_id"],))

    survey_result_unlocked, inventory_result_unlocked = cur.fetchone()

    cur.execute("""
        SELECT 
            s.id AS id,
            s.fullname, s.gender, s.email, s.campus, s.photo,
            c.campus_name, c.campus_address,
            sa.nickname, sa.present_address, sa.provincial_address,
            sa.date_of_birth, sa.place_of_birth, sa.age, sa.birth_order, sa.siblings_count,
            sa.civil_status, sa.religion, sa.nationality,
            sa.home_phone, sa.mobile_no, sa.email AS personal_email,
            sa.weight, sa.height, sa.blood_type, sa.hobbies, sa.talents,
            sa.emergency_name, sa.emergency_relationship, sa.emergency_address, sa.emergency_contact,
            sb.father_name, sb.father_age, sb.father_education, sb.father_occupation,
            sb.father_income, sb.father_contact, sb.mother_name, sb.mother_age, sb.mother_education,
            sb.mother_occupation, sb.mother_income, sb.mother_contact, 
            sc.parent_status, sc.father_another_family, sc.mother_another_family,
            sd.elementary_school_name, sd.elementary_year_graduated, sd.elementary_awards,
            sd.junior_high_school_name, sd.junior_high_year_graduated, sd.junior_high_awards,
            sd.senior_high_school_name, sd.senior_high_year_graduated, sd.senior_high_awards,
            sd.senior_high_track, sd.senior_high_strand, sd.subject_interested, sd.org_membership,
            sd.study_finance, sd.course_personal_choice, sd.influenced_by, sd.feeling_about_course, sd.personal_choice,
            se.bullying, se.bullying_when, se.bullying_bother,
            se.suicidal_thoughts, se.suicidal_thoughts_when, se.suicidal_thoughts_bother,
            se.suicidal_attempts, se.suicidal_attempts_when, se.suicidal_attempts_bother,
            se.panic_attacks, se.panic_attacks_when, se.panic_attacks_bother,
            se.anxiety, se.anxiety_when, se.anxiety_bother,
            se.depression, se.depression_when, se.depression_bother,
            se.self_anger_issues, se.self_anger_issues_when, se.self_anger_issues_bother,
            se.recurring_negative_thoughts, se.recurring_negative_thoughts_when, se.recurring_negative_thoughts_bother,
            se.low_self_esteem, se.low_self_esteem_when, se.low_self_esteem_bother,
            se.poor_study_habits, se.poor_study_habits_when, se.poor_study_habits_bother,
            se.poor_in_decision_making, se.poor_in_decision_making_when, se.poor_in_decision_making_bother,
            se.impulsivity, se.impulsivity_when, se.impulsivity_bother,
            se.poor_sleeping_habits, se.poor_sleeping_habits_when, se.poor_sleeping_habits_bother,
            se.loss_of_appetite, se.loss_of_appetite_when, se.loss_of_appetite_bother,
            se.over_eating, se.over_eating_when, se.over_eating_bother,
            se.poor_hygiene, se.poor_hygiene_when, se.poor_hygiene_bother,
            se.withdrawal_isolation, se.withdrawal_isolation_when, se.withdrawal_isolation_bother,
            se.family_problem, se.family_problem_when, se.family_problem_bother,
            se.other_relationship_problem, se.other_relationship_problem_when, se.other_relationship_problem_bother,
            se.alcohol_addiction, se.alcohol_addiction_when, se.alcohol_addiction_bother,
            se.gambling_addiction, se.gambling_addiction_when, se.gambling_addiction_bother,
            se.drug_addiction, se.drug_addiction_when, se.drug_addiction_bother,
            se.computer_addiction, se.computer_addiction_when, se.computer_addiction_bother,
            se.sexual_harassment, se.sexual_harassment_when, se.sexual_harassment_bother,
            se.sexual_abuse, se.sexual_abuse_when, se.sexual_abuse_bother,
            se.physical_abuse, se.physical_abuse_when, se.physical_abuse_bother,
            se.verbal_abuse, se.verbal_abuse_when, se.verbal_abuse_bother,
            se.pre_marital_sex, se.pre_marital_sex_when, se.pre_marital_sex_bother,
            se.teenage_pregnancy, se.teenage_pregnancy_when, se.teenage_pregnancy_bother,
            se.abortion, se.abortion_when, se.abortion_bother,
            se.extra_marital_affairs, se.extra_marital_affairs_when, se.extra_marital_affairs_bother,
            sf.psychiatrist_before, sf.psychiatrist_reason, sf.psychiatrist_when,
            sf.psychologist_before, sf.psychologist_reason, sf.psychologist_when,
            sf.counselor_before, sf.counselor_reason, sf.counselor_when,
            sg.personal_description, sg.consent, sg.consent_date, sh.course_name
        FROM student s
        LEFT JOIN personal_information sa ON sa.student_id = s.id
        LEFT JOIN family_background sb ON sb.student_id = s.id
        LEFT JOIN status_of_parent sc ON sc.student_id = s.id
        LEFT JOIN academic_information sd ON sd.student_id = s.id
        LEFT JOIN behavior_information se ON se.student_id = s.id
        LEFT JOIN psychological_consultations sf ON sf.student_id = s.id
        LEFT JOIN personal_descriptions sg ON sg.student_id = s.id
        LEFT JOIN course sh ON sh.student_id = s.id
        LEFT JOIN campus c ON s.campus = c.campus_name
        WHERE s.id = %s
    """, (student_id,))

    info = cur.fetchone()

    student_photo_base64 = None

    if info and info["photo"]:
        student_photo_base64 = student_photo_to_base64(info["photo"])

    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    cur.execute("""
        SELECT reasons, other_reason
        FROM cpsu_enrollment_reason
        WHERE student_id = %s
    """, (student_id,))
    enroll_reason = cur.fetchone()

    cur.execute("""
        SELECT school_choices, other_school
        FROM other_schools_considered
        WHERE student_id = %s
    """, (student_id,))
    other_school_data = cur.fetchone()

    cur.close()
    conn.close()

    selected_reasons = []
    other_reason = ""
    if enroll_reason:
        if enroll_reason[0]:
            selected_reasons = [r.strip() for r in enroll_reason[0].split(",")]
        other_reason = enroll_reason[1] or ""

    other_schools_selected = []
    other_school = ""
    if other_school_data:
        if other_school_data[0]:
            other_schools_selected = [r.strip() for r in other_school_data[0].split(",")]
        other_school = other_school_data[1] or ""

    return render_template(
        "student/studentInventoryResult.html",
    student_id=session["student_id"],
        info=info,
        student_photo_base64=student_photo_base64,
        selected_reasons=selected_reasons,
        other_reason=other_reason,
        other_schools_selected=other_schools_selected,
        other_school=other_school,
        survey_result_unlocked=survey_result_unlocked,
        inventory_result_unlocked=inventory_result_unlocked
    )

@student_bp.route('/download_inventory_pdf/<int:student_id>')
def download_inventory_pdf(student_id):
    if "student_id" not in session or session["student_id"] != student_id:
        return redirect(url_for("student.login_page"))

    conn = get_db_connection()

    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    cur.execute("""
        SELECT 
            s.id AS id,
            s.exam_id, s.fullname, s.gender, s.email, s.campus, s.photo,
            c.campus_name, c.campus_address,
            sa.nickname, sa.present_address, sa.provincial_address,
            sa.date_of_birth, sa.place_of_birth, sa.age, sa.birth_order, sa.siblings_count,
            sa.civil_status, sa.religion, sa.nationality,
            sa.home_phone, sa.mobile_no, sa.email AS personal_email,
            sa.weight, sa.height, sa.blood_type, sa.hobbies, sa.talents,
            sa.emergency_name, sa.emergency_relationship, sa.emergency_address, sa.emergency_contact,
            sb.father_name, sb.father_age, sb.father_education, sb.father_occupation,
            sb.father_income, sb.father_contact, sb.mother_name, sb.mother_age, sb.mother_education,
            sb.mother_occupation, sb.mother_income, sb.mother_contact, 
            sc.parent_status, sc.father_another_family, sc.mother_another_family,
            sd.elementary_school_name, sd.elementary_year_graduated, sd.elementary_awards,
            sd.junior_high_school_name, sd.junior_high_year_graduated, sd.junior_high_awards,
            sd.senior_high_school_name, sd.senior_high_year_graduated, sd.senior_high_awards,
            sd.senior_high_track, sd.senior_high_strand, sd.subject_interested, sd.org_membership,
            sd.study_finance, sd.course_personal_choice, sd.influenced_by, sd.feeling_about_course, sd.personal_choice,
            se.bullying, se.bullying_when, se.bullying_bother,
            se.suicidal_thoughts, se.suicidal_thoughts_when, se.suicidal_thoughts_bother,
            se.suicidal_attempts, se.suicidal_attempts_when, se.suicidal_attempts_bother,
            se.panic_attacks, se.panic_attacks_when, se.panic_attacks_bother,
            se.anxiety, se.anxiety_when, se.anxiety_bother,
            se.depression, se.depression_when, se.depression_bother,
            se.self_anger_issues, se.self_anger_issues_when, se.self_anger_issues_bother,
            se.recurring_negative_thoughts, se.recurring_negative_thoughts_when, se.recurring_negative_thoughts_bother,
            se.low_self_esteem, se.low_self_esteem_when, se.low_self_esteem_bother,
            se.poor_study_habits, se.poor_study_habits_when, se.poor_study_habits_bother,
            se.poor_in_decision_making, se.poor_in_decision_making_when, se.poor_in_decision_making_bother,
            se.impulsivity, se.impulsivity_when, se.impulsivity_bother,
            se.poor_sleeping_habits, se.poor_sleeping_habits_when, se.poor_sleeping_habits_bother,
            se.loss_of_appetite, se.loss_of_appetite_when, se.loss_of_appetite_bother,
            se.over_eating, se.over_eating_when, se.over_eating_bother,
            se.poor_hygiene, se.poor_hygiene_when, se.poor_hygiene_bother,
            se.withdrawal_isolation, se.withdrawal_isolation_when, se.withdrawal_isolation_bother,
            se.family_problem, se.family_problem_when, se.family_problem_bother,
            se.other_relationship_problem, se.other_relationship_problem_when, se.other_relationship_problem_bother,
            se.alcohol_addiction, se.alcohol_addiction_when, se.alcohol_addiction_bother,
            se.gambling_addiction, se.gambling_addiction_when, se.gambling_addiction_bother,
            se.drug_addiction, se.drug_addiction_when, se.drug_addiction_bother,
            se.computer_addiction, se.computer_addiction_when, se.computer_addiction_bother,
            se.sexual_harassment, se.sexual_harassment_when, se.sexual_harassment_bother,
            se.sexual_abuse, se.sexual_abuse_when, se.sexual_abuse_bother,
            se.physical_abuse, se.physical_abuse_when, se.physical_abuse_bother,
            se.verbal_abuse, se.verbal_abuse_when, se.verbal_abuse_bother,
            se.pre_marital_sex, se.pre_marital_sex_when, se.pre_marital_sex_bother,
            se.teenage_pregnancy, se.teenage_pregnancy_when, se.teenage_pregnancy_bother,
            se.abortion, se.abortion_when, se.abortion_bother,
            se.extra_marital_affairs, se.extra_marital_affairs_when, se.extra_marital_affairs_bother,
            sf.psychiatrist_before, sf.psychiatrist_reason, sf.psychiatrist_when,
            sf.psychologist_before, sf.psychologist_reason, sf.psychologist_when,
            sf.counselor_before, sf.counselor_reason, sf.counselor_when,
            sg.personal_description, sg.consent, sg.consent_date, sh.course_name
        FROM student s
        LEFT JOIN personal_information sa ON sa.student_id = s.id
        LEFT JOIN family_background sb ON sb.student_id = s.id
        LEFT JOIN status_of_parent sc ON sc.student_id = s.id
        LEFT JOIN academic_information sd ON sd.student_id = s.id
        LEFT JOIN behavior_information se ON se.student_id = s.id
        LEFT JOIN psychological_consultations sf ON sf.student_id = s.id
        LEFT JOIN personal_descriptions sg ON sg.student_id = s.id
        LEFT JOIN course sh ON sh.student_id = s.id
        LEFT JOIN campus c ON s.campus = c.campus_name
        WHERE s.id = %s
    """, (student_id,))

    info = cur.fetchone()

    student_photo_base64 = None

    if info and info["photo"]:
        student_photo_base64 = student_photo_to_base64(info["photo"])

    if not info:
        return "Student Inventory results not found.", 404

    cur.execute("""
        SELECT reasons, other_reason
        FROM cpsu_enrollment_reason
        WHERE student_id = %s
    """, (student_id,))
    enroll_reason = cur.fetchone()

    cur.execute("""
        SELECT school_choices, other_school
        FROM other_schools_considered
        WHERE student_id = %s
    """, (student_id,))
    other_school_data = cur.fetchone()

    cur.close()
    conn.close()

    student_data = {
        "exam_id": info[1],
        "fullname": info[2]
    }

    selected_reasons = []
    other_reason = ""
    if enroll_reason:
        if enroll_reason[0]:
            selected_reasons = [r.strip() for r in enroll_reason[0].split(",")]
        other_reason = enroll_reason[1] or ""

    other_schools_selected = []
    other_school = ""
    if other_school_data:
        if other_school_data[0]:
            other_schools_selected = [r.strip() for r in other_school_data[0].split(",")]
        other_school = other_school_data[1] or ""

    cpsu_logo_base64 = image_to_base64("cpsulogo.png")

    html = render_template(
        "student/studentInventoryResultPDF.html",
        info=info,
        selected_reasons=selected_reasons,
        other_reason=other_reason,
        other_schools_selected=other_schools_selected,
        other_school=other_school,
        cpsu_logo_base64=cpsu_logo_base64,
        student_photo_base64=student_photo_base64,
    )

    pdf_file = generate_pdf(html)

    filename = f"Inventory_Result_{student_data['exam_id']}_{student_data['fullname'].replace(' ', '_')}.pdf"

    return send_file(
        pdf_file,
        mimetype="application/pdf",
        download_name=filename,
        as_attachment=True
    )

@student_bp.route("/profile", methods=["GET", "POST"])
def profile():
    if "student_id" not in session:
        return redirect(url_for("student.login_page"))

    student_id = session["student_id"]
    conn = get_db_connection()
    cur = conn.cursor()

    if request.method == "POST":
        fullname = request.form.get("fullname")
        gender = request.form.get("gender")
        email = request.form.get("email")

        if fullname and gender and email:
            cur.execute("""
                UPDATE student
                SET fullname = %s,
                    gender = %s,
                    email = %s
                WHERE id = %s
            """, (fullname, gender, email, student_id))
            conn.commit()

    cur.execute("""
        SELECT fullname, gender, email, campus, photo
        FROM student
        WHERE id = %s
    """, (student_id,))
    
    row = cur.fetchone()

    student = {
        "fullname": row[0],
        "gender": row[1],
        "email": row[2],
        "campus": row[3],
        "photo": row[4],
    }

    cur.execute("""
        SELECT result_unlocked, inventory_result_unlocked
        FROM notifications
        WHERE student_id = %s
        ORDER BY id DESC LIMIT 1
    """, (student_id,))
    notif = cur.fetchone()

    cur.execute("""
        SELECT
            MAX(CASE WHEN result_unlocked = TRUE THEN 1 ELSE 0 END),
            MAX(CASE WHEN inventory_result_unlocked = TRUE THEN 1 ELSE 0 END)
        FROM notifications
        WHERE student_id = %s
    """, (session["student_id"],))

    survey_result_unlocked, inventory_result_unlocked = cur.fetchone()

    cur.close()
    conn.close()

    return render_template(
        "student/profile.html",
        student=student,
        student_campus=student["campus"],
        survey_result_unlocked=survey_result_unlocked,
        inventory_result_unlocked=inventory_result_unlocked
    )

@student_bp.route("/upload_student_photo", methods=["POST"])
def upload_student_photo():
    if "exam_id" not in session or "student_id" not in session:
        return redirect(url_for("student.login_page"))

    conn = get_db_connection()
    cur = conn.cursor()

    file = request.files.get("photo")
    if not file or file.filename == "":
        flash("No file selected", "error")
        conn.close()
        return redirect(url_for("student.surveyResult"))

    if not allowed_file(file.filename):
        flash("Invalid file type", "error")
        conn.close()
        return redirect(url_for("student.surveyResult"))

    try:
        image = process_image(file)
    except Exception:
        flash("Invalid image file.", "error")
        conn.close()
        return redirect(url_for("student.surveyResult"))

    upload_folder = os.path.join(
        current_app.static_folder,
        "uploads",
        "students"
    )
    os.makedirs(upload_folder, exist_ok=True)

    new_filename = f"exam_{session['exam_id']}.jpg"
    file_path = os.path.join(upload_folder, new_filename)

    image.save(file_path, "JPEG", quality=90)

    cur.execute(
        "UPDATE student SET photo = %s WHERE id = %s",
        (new_filename, session["student_id"])
    )

    conn.commit()
    cur.close()
    conn.close()

    flash("1×1 photo uploaded successfully", "success")
    return redirect(url_for("student.profile"))

@student_bp.route("/logout")
def logout():
    reason = request.args.get("reason")

    session.clear()

    if reason == "expired":
        flash("Session expired due to inactivity.", "session_expired")

    return redirect(url_for("student.login_page"))
