from flask import Blueprint, render_template, request, redirect, url_for, session, flash, jsonify, current_app, make_response
from ..db import get_db_connection
import os
import pandas as pd
import psycopg2
import base64
import json
import re
from werkzeug.utils import secure_filename
from io import BytesIO
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image
)
from reportlab.lib.pagesizes import letter
from reportlab.lib.enums import TA_CENTER
from reportlab.graphics.shapes import Drawing, Line
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.platypus import Image
from reportlab.lib.utils import ImageReader
from reportlab.platypus import PageBreak
from reportlab.platypus import ListFlowable, ListItem
from reportlab.lib.enums import TA_JUSTIFY
from xml.sax.saxutils import escape
from html import unescape
from psycopg2.extras import RealDictCursor
from werkzeug.utils import secure_filename
from datetime import datetime, timedelta, timezone
from flask import request
from collections import Counter
from ..description import letter_descriptions, preferred_program_map, short_letter_descriptions
from math import ceil
from groq import Groq
import smtplib
from email.message import EmailMessage
import random
import time
import requests
import bcrypt

client = Groq(api_key=os.getenv("GROQ_API_KEY"))

admin_bp = Blueprint('admin', __name__, template_folder='../../frontend/templates/admin')

DEFAULT_ADMIN = {
    "id": "1000",
    "fullname": "hertzkin",
    "username": "hk",
    "password": "hk",
    "campus": "Kabankalan Campus"

}

ALLOWED_EXTENSIONS = {"xlsx", "xls"}

UPLOAD_FOLDER = os.path.join(
    os.path.dirname(__file__),
    "..", "..", "uploads"
)

if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

def hash_password(password):
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def is_password_strong(pw):
    return (
        len(pw) >= 8 and
        re.search(r"[A-Z]", pw) and
        re.search(r"[a-z]", pw) and
        re.search(r"[0-9]", pw) and
        re.search(r"[^A-Za-z0-9]", pw)
    )

def clean_html(raw_text):
    if not raw_text:
        return ""

    text = unescape(raw_text)

    # remove HTML tags completely
    text = re.sub(r'<[^>]+>', '', text)

    # normalize bullet words into real bullet
    text = re.sub(r'\bbullet\b', '•', text, flags=re.IGNORECASE)

    # fix broken spacing after bullets
    text = re.sub(r'•\s*', '\n• ', text)

    # normalize multiple spaces
    text = re.sub(r'[ \t]+', ' ', text)

    # IMPORTANT: DO NOT force sentence splitting globally
    # (this is what breaks your paragraphs)
    
    return text.strip()

def split_ai_sections(text):
    sections = {
        "Career Letter Explanation": "",
        "Strengths": "",
        "Weaknesses": "",
        "Personalized Career Advice": ""
    }

    if not text:
        return sections

    # Split by section titles
    pattern = r"(Career Letter Explanation|Strengths|Weaknesses|Personalized Career Advice)"
    parts = re.split(pattern, text)

    current_key = None

    for part in parts:
        part = part.strip()

        if part in sections:
            current_key = part
        elif current_key:
            sections[current_key] += part.strip() + "\n"

    return sections

def generate_pdf_reportlab(student_data, logos, photo):
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=(8.5*inch, 11*inch),
        leftMargin=72,
        rightMargin=72,
        topMargin=36,
        bottomMargin=36
    )

    styles = getSampleStyleSheet()
    elements = []

    logo_imgs = []

    for key in ["cpsu", "bagong", "safe"]:
        if logos.get(key):
            img = Image(BytesIO(base64.b64decode(logos[key])))
            img.drawHeight = 50
            img.drawWidth = 50
            logo_imgs.append(img)

    logo_table = Table([logo_imgs], colWidths=[60, 60, 60])
    logo_table.setStyle(TableStyle([
        ("ALIGN", (0, 0), (-1, -1), "LEFT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))

    header_table = Table([
        [
            logo_table,
            Paragraph(f"""
                <b>Republic of the Philippines</b><br/>
                <b><font size="10">CENTRAL PHILIPPINES STATE UNIVERSITY</font></b><br/>
                <font size="10">{student_data.get('campus_name','')}</font><br/>
                <font size="7">{student_data.get('campus_address','')}</font>
            """, styles["Normal"])
        ]
    ], colWidths=[200, 300])

    header_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEAFTER", (0, 0), (0, 0), 1, colors.black),
    ]))

    elements.append(header_table)
    elements.append(Spacer(1, 12))

    center_style = ParagraphStyle(
        name="CenterTitle",
        parent=styles["Normal"],
        alignment=TA_CENTER,
        fontSize=11,
        leading=14,
    )

    elements.append(Paragraph(
        "<b>Students’ Admission and Facilitative Enhancement (S.A.F.E.) Center</b>",
        center_style
    ))

    line = Drawing(450, 10)
    line.add(Line(0, 5, 450, 5))
    elements.append(line)

    elements.append(Paragraph(
        "<b>Career Interest Survey Result</b>",
        center_style
    ))

    elements.append(Spacer(1, 12))

    styles = getSampleStyleSheet()

    info_paragraph = Paragraph(f"""
        <b>Exam ID:</b> {student_data['exam_id']}<br/><br/>
        <b>Name:</b> {student_data['fullname']}<br/><br/>
        <b>SY:</b> {student_data['school_year']}<br/><br/>
        <b>Preferred Program:</b><br/> {student_data['preferred_program']}
    """, styles["Normal"])

    photo_element = None

    try:
        if photo:

            img_data = None

            if isinstance(photo, str) and len(photo) > 200:
                img_data = base64.b64decode(photo)

            else:
                path = os.path.join(
                    current_app.static_folder,
                    "uploads",
                    "students",
                    photo
                )

                if os.path.exists(path):
                    with open(path, "rb") as f:
                        img_data = f.read()

            if img_data:
                img = Image(BytesIO(img_data))
                img.drawWidth = 120
                img.drawHeight = 120
                photo_element = img

        if not photo_element:
            photo_element = Table(
                [[""]],
                colWidths=120,
                rowHeights=120,
                style=[
                    ("BOX", (0, 0), (-1, -1), 1, colors.black),
                    ("BACKGROUND", (0, 0), (-1, -1), colors.white),
                ]
            )

    except Exception as e:
        print("PHOTO ERROR:", e)

        photo_element = Table(
            [[""]],
            colWidths=120,
            rowHeights=120,
            style=[
                ("BOX", (0, 0), (-1, -1), 1, colors.black),
            ]
        )

    info_table = Table([
        [info_paragraph, photo_element]
    ], colWidths=[380, 120])

    info_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (1, 0), (1, 0), "CENTER"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]))

    elements.append(info_table)
    elements.append(Spacer(1, 6))

    elements.append(Paragraph("<b>Most Chosen Letters</b>", styles["Heading2"]))
    from ..description import letter_descriptions
    for letter in student_data.get("top_letters", []):
        desc = letter_descriptions.get(letter, "No description available")
        elements.append(Paragraph(f"<b>{letter}:</b> {desc}", styles["Normal"]))

    elements.append(Spacer(1, 10))

    match = student_data.get("match_status", "")

    color = colors.green if match == "Match" else colors.red

    elements.append(Paragraph(
        f"<b>The preferred program and most chosen letter is </b> <font color='{color}'>{match}</font>",
        styles["Normal"]
    ))

    elements.append(Spacer(1, 12))

    elements.append(Paragraph("<b>Recommended Programs</b>", styles["Heading2"]))

    for prog in student_data.get("predicted_programs", []):
        elements.append(Paragraph(f"- {prog[0]}", styles["Normal"]))

    elements.append(Spacer(1, 12))

    note_box = Table([[""]], colWidths=450, rowHeights=120)
    note_box.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 1, colors.black),
    ]))

    elements.append(note_box)
    elements.append(Spacer(1, 20))

    gc_table = Table([
        [
            Paragraph(f"<b>{student_data.get('guidance_counselor','')}</b>", styles["Normal"])
        ],
        [
            Paragraph("Guidance Counselor", styles["Normal"])
        ]
    ], colWidths=250)

    gc_table.setStyle(TableStyle([
        ("ALIGN", (0, 0), (-1, -1), "LEFT"),

        ("ALIGN", (0, 1), (0, 5), "CENTER"),

        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),

        ("LINEBELOW", (0, 0), (0, 0), 1, colors.black),
    ]))

    wrapper = Table([[gc_table]], colWidths=[500])

    wrapper.setStyle(TableStyle([
        ("ALIGN", (0, 0), (-1, -1), "LEFT"),
    ]))

    elements.append(Spacer(1, 20))
    elements.append(wrapper)

    elements.append(PageBreak())

    clean_text_data = clean_html(student_data.get("ai_explanation", ""))
    ai_sections = split_ai_sections(clean_text_data)

    elements.append(Paragraph("<b>AI Career Explanation</b>", styles["Heading1"]))
    elements.append(Spacer(1, 12))

    def format_bullets(text):
        if not text:
            return []

        text = re.sub(r'<[^>]+>', '', text)
        text = re.sub(r'\bbullet\b', '•', text, flags=re.IGNORECASE)

        # FORCE split before capital letter + "stands for"
        text = re.split(r'(?=[A-Z]\s*stands\s*for)', text)

        bullets = []

        for part in text:
            part = part.strip()
            if not part:
                continue

            # also split long merged sentences
            sub_parts = re.split(r'(?<=\.)\s+|•', part)

            for item in sub_parts:
                item = item.strip()
                if not item:
                    continue

                # remove extra bullet symbols
                item = item.replace('•', '').strip()

                bullets.append(
                    ListItem(
                        Paragraph(item, styles["Normal"])
                    )
                )

        return bullets

    # LOOP EACH SECTION
    for title, content in ai_sections.items():
        if content.strip():
            elements.append(Paragraph(f"<b>{title}</b>", styles["Heading2"]))
            elements.append(Spacer(1, 6))

            bullet_items = format_bullets(content)

            if bullet_items:
                elements.append(
                    ListFlowable(
                        bullet_items,
                        bulletType='bullet',
                        leftIndent=15
                    )
                )

            elements.append(Spacer(1, 10))

    # BUILD PDF
    doc.build(elements)

    buffer.seek(0)
    return buffer.getvalue()

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

def base64_to_image(base64_string):
    if not base64_string:
        return None
    image_data = base64.b64decode(base64_string)
    return BytesIO(image_data)

def checkbox(value, expected=True):
    return "[✔]" if value == expected else "[ ]"

def yes_no_checkbox(main_value, bother_value):
    if not main_value:
        return "[ ] YES   [ ] NO"
    if bother_value is True:
        return "[✔] YES   [ ] NO"
    elif bother_value is False:
        return "[ ] YES   [✔] NO"
    else:
        return "[ ] YES   [ ] NO"

def generate_pdf_inventory_reportlab(
    info,
    cpsu_logo_base64,
    student_photo_base64,
    campus_info,
    selected_reasons=None,
    other_reason=None,
    other_schools_selected=None,
    other_school=None
):
    buffer = BytesIO()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=(8.5*inch, 14*inch),
        rightMargin=30,
        leftMargin=30,
        topMargin=30,
        bottomMargin=30
    )

    styles = getSampleStyleSheet()
    elements = []

    logo_img = None
    if cpsu_logo_base64:
        logo_stream = base64_to_image(cpsu_logo_base64)
        logo_img = Image(logo_stream, width=1.2 * inch, height=1.2 * inch)

    campus_address = campus_info.get(info["campus"], "Campus Address")

    header_text = Paragraph(f"""
        <br/><b><font size="12">CENTRAL PHILIPPINES STATE UNIVERSITY</font></b><br/>
        <font size="12">{info["campus"]}</font><br/>
        <font size="9">{campus_address}</font><br/>
        <b><font size="12">Guidance and Counseling Unit</font></b>
    """, styles["Normal"])

    photo_img = None
    if student_photo_base64:
        photo_stream = base64_to_image(student_photo_base64)
        photo_img = Image(photo_stream, width=1.2 * inch, height=1.2 * inch)
    else:
        photo_img = Paragraph(" ", styles["Normal"])

    photo_table = Table([[photo_img]], colWidths=[1.2 * inch], rowHeights=[1.2 * inch])
    photo_table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 1, colors.black),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))

    header_table = Table(
        [[logo_img, header_text, photo_table]],
        colWidths=[1.5 * inch, 4.5 * inch, 1.5 * inch]
    )

    header_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (1, 0), (1, 0), 10),
    ]))

    elements.append(header_table)
    elements.append(Spacer(1, 20))

    center_style = ParagraphStyle(
        name="CenterTitle",
        parent=styles["Normal"],
        alignment=TA_CENTER,
        fontSize=11,
        leading=14,
    )

    elements.append(Paragraph(
        "<b>Individual Inventory Form</b><br/>",
        center_style
    ))

    elements.append(Spacer(1, 18))

    elements.append(Paragraph(
        "<b>DISCLAIMER</b>",
        center_style
    ))

    elements.append(Spacer(1, 12))

    styles = getSampleStyleSheet()

    paragraph1 = """
    The information provided are true and accurate to the best of your knowledge. You also agree that the information provided herewith, was prepared on your own free will, freely and voluntarily without any inducement, assurance or guarantee being made. You hereby attest to the completeness and accuracy of all the information you have given.<br/>
    """
    paragraph2 = """
    Further, you are also allowing the University to use and release the information for legitimate purposes. Likewise, you permit the University to release information only to authorized personnel for the above stated purpose in accordance with the Data Privacy Policy of the University. You are aware that any act of dishonesty or falsification will lead to forfeiture of your application or dismissal from this University. The institution may also take further legal steps against fraudulent actions if the situation demands.
    """

    disclaimer_style = ParagraphStyle(
        name="DisclaimerStyle",
        parent=styles["Normal"],
        alignment=TA_JUSTIFY,
        firstLineIndent=20,
        leading=14,
        spaceAfter=10,
    )

    instruction_style = ParagraphStyle(
        name="InstructionStyle",
        parent=styles["Normal"],
        fontSize=7,
        leading=12
    )

    elements.append(Paragraph(paragraph1.strip(), disclaimer_style))
    elements.append(Paragraph(paragraph2.strip(), disclaimer_style))

    elements.append(Spacer(1, 6))

    course_text = Paragraph(
        f"<b>COURSE:</b> {info.get('course_name', '')}",
        styles["Normal"]
    )

    instruction_text = Paragraph(
        "Please fill up the following information below. Rest assured that all information will be treated with confidentiality. Thank you for your cooperation.",
        instruction_style
    )

    course_table = Table(
        [[course_text],
        [instruction_text]]
    )

    course_table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 1, colors.black),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))

    elements.append(course_table)

    personal_header = Table(
        [[Paragraph("<b>PERSONAL INFORMATION</b>", ParagraphStyle(
            name="whiteText",
            textColor=colors.white,
            fontSize=10
        ))]]
    )

    personal_header.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.black),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))

    elements.append(personal_header)

    personal_data = [
        [
            Paragraph(f"<b>NAME:</b> {info['fullname'].upper()}", styles["Normal"]),
            "",
            "",
            Paragraph(f"<b>NICKNAME:</b> {info.get('nickname', '')}", styles["Normal"]),
            ""
        ],
        [
            Paragraph(f"<b>PRESENT ADDRESS:</b> {info.get('present_address', '')}", styles["Normal"]),
            "", "", "", ""
        ],
        [
            Paragraph(f"<b>PROVINCIAL ADDRESS:</b> {info.get('provincial_address', '')}", styles["Normal"]),
            "", "", "", ""
        ],
        [
            Paragraph(f"<b>DATE OF BIRTH:</b> {info.get('date_of_birth', '')}", styles["Normal"]),
            Paragraph(f"<b>PLACE OF BIRTH:</b> {info.get('place_of_birth', '')}", styles["Normal"]),
            "",
            Paragraph(f"<b>BIRTH ORDER:</b> {info.get('birth_order', '')}", styles["Normal"]),
            Paragraph(f"<b>NO. OF SIBLINGS:</b> {info.get('siblings_count', '')}", styles["Normal"]),
        ],
        [
            Paragraph(f"<b>AGE:</b> {info.get('age', '')}", styles["Normal"]),
            Paragraph(f"<b>GENDER:</b> {info['gender']}", styles["Normal"]),
            Paragraph(f"<b>CIVIL STATUS:</b> {info.get('civil_status', '')}", styles["Normal"]),
            Paragraph(f"<b>RELIGION:</b> {info.get('religion', '')}", styles["Normal"]),
            Paragraph(f"<b>NATIONALITY:</b> {info.get('nationality', '')}", styles["Normal"])
        ],
        [
            Paragraph(f"<b>EMAIL ADD:</b> {info['email']}", styles["Normal"]),
            "",
            Paragraph(f"<b>MOBILE NO.:</b> {info.get('mobile_no', '')}", styles["Normal"]),
            "",
            Paragraph(f"<b>HOME PHONE NO.: </b> {info.get('home_phone', '')}", styles["Normal"]),
        ],
        [
            Paragraph(f"<b>WEIGHT:</b> {info.get('weight', '')}", styles["Normal"]),
            "",
            Paragraph(f"<b>HEIGHT:</b> {info.get('height', '')}", styles["Normal"]),
            "",
            Paragraph(f"<b>BLOOD TYPE: </b> {info.get('blood_type', '')}", styles["Normal"]),
        ],
        [
            Paragraph(f"<b>HOBBIES / INTEREST:</b> {info['hobbies'].upper()}", styles["Normal"]),
            "",
            Paragraph(f"<b>TALENTS:</b> {info.get('talents', '')}", styles["Normal"]),
            "",
            ""
        ],
        [
            Paragraph(f"<b>IN CASE OF EMERGENCY, PLEASE NOTIFY:</b> {info['emergency_name'].upper()}", styles["Normal"]),
            "",
            "",
            Paragraph(f"<b>RELATIONSHIP:</b> {info.get('emergency_relationship', '')}", styles["Normal"]),
            ""
        ],
        [
            Paragraph(f"<b>ADDRESS:</b> {info['emergency_address'].upper()}", styles["Normal"]),
            "",
            "",
            Paragraph(f"<b>CONTACT NO.:</b> {info.get('emergency_contact', '')}", styles["Normal"]),
            ""
        ],
    ]

    personal_table = Table(personal_data)

    personal_table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.black),

        # Row 1
        ("SPAN", (0, 0), (2, 0)),
        ("SPAN", (3, 0), (4, 0)),

        # Row 2
        ("SPAN", (0, 1), (4, 1)),

        # Row 3
        ("SPAN", (0, 2), (4, 2)),

        # Row 4
        ("SPAN", (1, 3), (2, 3)),

        # Row 6
        ("SPAN", (0, 5), (1, 5)),
        ("SPAN", (2, 5), (3, 5)),
        ("SPAN", (4, 5), (4, 5)),

        # Row 7
        ("SPAN", (0, 6), (1, 6)),
        ("SPAN", (2, 6), (3, 6)),
        ("SPAN", (4, 6), (4, 6)),

        # Row 8
        ("SPAN", (0, 7), (1, 7)),
        ("SPAN", (2, 7), (4, 7)),

        # Row 9
        ("SPAN", (0, 8), (2, 8)),
        ("SPAN", (3, 8), (4, 8)),

        # Row 10
        ("SPAN", (0, 9), (2, 9)),
        ("SPAN", (3, 9), (4, 9)),

        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))

    elements.append(personal_table)

    familybackground_header = Table(
        [[Paragraph("<b>FAMILY BACKGOUND</b>", ParagraphStyle(
            name="whiteText",
            textColor=colors.white,
            fontSize=10
        ))]]
    )

    familybackground_header.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.black),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))

    elements.append(familybackground_header)

    center_style = ParagraphStyle(
        name="Center",
        parent=styles["Normal"],
        alignment=TA_CENTER
    )

    family_data = [
        [
            Paragraph(f"<b>FATHER:</b>", center_style),
            "",
            Paragraph(f"<b>MOTHER:</b>", center_style),
        ],
        [
            Paragraph(f"{info['father_name'].upper()}", styles["Normal"]),
            Paragraph(f"<b>NAME:</b>", center_style),
            Paragraph(f"{info['mother_name'].upper()}", styles["Normal"]),
        ],
        [
            Paragraph(f"{info.get('father_age', '')}", styles["Normal"]),
            Paragraph(f"<b>AGE:</b>", center_style),
            Paragraph(f"{info.get('mother_age', '')}", styles["Normal"]),
        ],
        [
            Paragraph(f"{info.get('father_education', '')}", styles["Normal"]),
            Paragraph(f"<b>HIGHEST EDUCATIONAL ATTAINMENT:</b>", center_style),
            Paragraph(f"{info.get('mother_education', '')}", styles["Normal"]),
        ],
        [
            Paragraph(f"{info.get('father_occupation', '')}", styles["Normal"]),
            Paragraph(f"<b>OCCUPATION:</b>", center_style),
            Paragraph(f"{info.get('mother_occupation', '')}", styles["Normal"]),
        ],
        [
            Paragraph(f"{info.get('father_income', '')}", styles["Normal"]),
            Paragraph(f"<b>AVERAGE INCOME PER MONTH:</b>", center_style),
            Paragraph(f"{info.get('mother_income', '')}", styles["Normal"]),
        ],
        [
            Paragraph(f"{info.get('father_contact', '')}", styles["Normal"]),
            Paragraph(f"<b>CONTACT NUMBER/S:</b>", center_style),
            Paragraph(f"{info.get('mother_contact', '')}", styles["Normal"]),
        ]
    ]

    family_table = Table(family_data)

    family_table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.black),

        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))

    elements.append(family_table)

    status_header = Table(
        [[Paragraph("<b>STATUS OF PARENT</b>", ParagraphStyle(
            name="whiteText2",
            textColor=colors.white,
            fontSize=10
        ))]]
    )

    status_header.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.black),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))

    elements.append(status_header)
    elements.append(Spacer(1, 8))

    status_row1 = [
        Paragraph(f"{checkbox(info.get('parent_status'), 'married_living_together')} MARRIED & LIVING TOGETHER", styles["Normal"]),
        Paragraph(f"{checkbox(info.get('parent_status'), 'legally_separated')} LEGALLY SEPARATED", styles["Normal"]),
        Paragraph(f"{checkbox(info.get('parent_status'), 'living_not_married')} LIVING IN (NOT MARRIED)", styles["Normal"]),
        Paragraph(f"{checkbox(info.get('parent_status'), 'mother_widow')} MOTHER (WIDOW)", styles["Normal"]),
        Paragraph(f"{checkbox(info.get('parent_status'), 'father_widower')} FATHER (WIDOWER)", styles["Normal"]),
    ]

    status_table1 = Table([status_row1])

    status_table1.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))

    status_row2 = [
        Paragraph(f"{checkbox(info.get('parent_status'), 'separated')} SEPARATED", styles["Normal"]),
        Paragraph(f"{'[✔]' if info.get('father_another_family') else '[ ]'} Father with another family", styles["Normal"]),
        Paragraph(f"{'[✔]' if info.get('mother_another_family') else '[ ]'} Mother with another family", styles["Normal"]),
    ]

    status_table2 = Table([status_row2])

    status_table2.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))

    elements.append(status_table1)
    elements.append(status_table2)
    elements.append(Spacer(1, 20))

    Academic_header = Table(
        [[Paragraph("<b>ACADEMIC INFORMATION</b>", ParagraphStyle(
            name="whiteText",
            textColor=colors.white,
            fontSize=10
        ))]]
    )

    Academic_header.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.black),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))

    elements.append(Academic_header)

    center_style = ParagraphStyle(
        name="Center",
        parent=styles["Normal"],
        alignment=TA_CENTER
    )

    Academic_data = [
        [
            Paragraph(f"<b>LEVEL</b>", center_style),
            Paragraph(f"<b>NAME OF SCHOOL</b>", center_style),
            Paragraph(f"<b>YEAR GRADUATED</b>", center_style),
            Paragraph(f"<b>AWARDS RECEIVED</b>", center_style),
        ],
        [
            Paragraph(f"<b>ELEMENTARY:</b>", center_style),
            Paragraph(f"{info.get('elementary_school_name', '')}", center_style),
            Paragraph(f"{info.get('elementary_year_graduated', '')}", center_style),
            Paragraph(f"{info.get('elementary_awards', '')}", center_style),
        ],
        [
            Paragraph(f"<b>JUNIOR HIGH SCHOOL:</b>", center_style),
            Paragraph(f"{info.get('junior_high_school_name', '')}", center_style),
            Paragraph(f"{info.get('junior_high_year_graduated', '')}", center_style),
            Paragraph(f"{info.get('junior_high_awards', '')}", center_style),
        ],
        [
            Paragraph(
                f"""
                <b>SENIOR HIGH SCHOOL:</b><br/>
                <font size="8">Track: {info.get('senior_high_track', '')} , Strand: {info.get('senior_high_strand', '')}</font>
                """,
                center_style
            ),
            Paragraph(f"{info.get('senior_high_school_name', '')}", center_style),
            Paragraph(f"{info.get('senior_high_year_graduated', '')}", center_style),
            Paragraph(f"{info.get('senior_high_awards', '')}", center_style),
        ]
    ]

    Academic_table = Table(Academic_data)

    Academic_table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("ALIGN", (1, 3), (1, 3), "LEFT"),

        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))

    elements.append(Academic_table)

    Academic2_data = [
        [Paragraph(f"<b>Subjects interested to:</b> {info.get('subject_interested', '')}", styles["Normal"]),],
        [Paragraph(f"<b>Organization Membership:</b> {info.get('org_membership', '')}", styles["Normal"]),],
        [Paragraph(f"<b>How is your studies financed?</b> {info.get('study_finance', '')}", styles["Normal"]),],
        [Paragraph(
            f"""
            <b>Is your present course / program your personal choice?  </b>
            {checkbox(info.get('course_personal_choice'), True)} YES &nbsp;&nbsp;&nbsp;
            {checkbox(info.get('course_personal_choice'), False)} NO
            """,
            styles["Normal"]
        )],
        [Paragraph(f"<b>If NO,</b>", styles["Normal"]),],
        [Paragraph(f"<b>A. Who influenced you? - </b> {info.get('influenced_by', '')}", styles["Normal"]),],
        [Paragraph(f"<b>B. How do you feel about the course not being your first choice? - </b> {info.get('feeling_about_course', '')}", styles["Normal"]),],
        [Paragraph(f"<b>C. What is your personal choice? - </b> {info.get('personal_choice', '')}", styles["Normal"]),]
    ]

    Academic2_table = Table(Academic2_data)

    Academic2_table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.black),

        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))

    elements.append(Academic2_table)
    
    Enroll_header = Table(
        [[Paragraph("<b>Why did you choose to enroll in CPSU?</b>", ParagraphStyle(
            name="whiteText",
            textColor=colors.white,
            fontSize=10
        ))]]
    )

    Enroll_header.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.black),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))

    elements.append(Enroll_header)

    Enroll_data = [
        [
            Paragraph(f"{checkbox('Quality education', selected_reasons)} Quality education", styles["Normal"]),
            Paragraph(f"{checkbox('Free tuition fee', selected_reasons)} Free tuition fee", styles["Normal"]),
            Paragraph(f"{checkbox('Competitive faculty members', selected_reasons)} Competitive faculty members", styles["Normal"]),
        ],
        [
            Paragraph(f"{checkbox('Good facilities', selected_reasons)} Good facilities", styles["Normal"]),
            Paragraph(f"{checkbox('Proximity and accessibility', selected_reasons)} Proximity and accessibility", styles["Normal"]),
            Paragraph(f"{checkbox('Performance in the licensure examination', selected_reasons)} Performance in the licensure examination", styles["Normal"]),
        ],
        [
            Paragraph(f"{checkbox('Recommended by friends', selected_reasons)} Recommended by friends, relatives, etc.", styles["Normal"]),
            Paragraph(f"{checkbox('Good reputation', selected_reasons)} Good reputation", styles["Normal"]),
            Paragraph(
                f"{'[✔]' if other_reason else '[ ]'} Others: {other_reason if other_reason else ''}",
                styles["Normal"]
            ),
        ]
    ]

    Enroll_table = Table(Enroll_data)

    Enroll_table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("ALIGN", (1, 3), (1, 3), "LEFT"),

        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))

    elements.append(Enroll_table)
    
    School_header = Table(
        [[Paragraph("<b>What other school do you consider?</b>", ParagraphStyle(
            name="whiteText",
            textColor=colors.white,
            fontSize=10
        ))]]
    )

    School_header.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.black),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))

    elements.append(School_header)

    School_data = [
        [
            Paragraph(f"{checkbox('SUNN', other_schools_selected)} SUNN", styles["Normal"]),
            Paragraph(f"{checkbox('CHMSU', other_schools_selected)} CHMSU", styles["Normal"]),
            Paragraph(f"{checkbox('PNU-V', other_schools_selected)} PNU-V", styles["Normal"]),
            Paragraph(f"{checkbox('TAÑON', other_schools_selected)} TAÑON", styles["Normal"]),
            Paragraph(f"{checkbox('CSTR', other_schools_selected)} CSTR", styles["Normal"]),
        ],
        [
            Paragraph(f"{checkbox('UNO R', other_schools_selected)} UNO R", styles["Normal"]),
            Paragraph(f"{checkbox('NORSU', other_schools_selected)} NORSU", styles["Normal"]),
            Paragraph(f"{checkbox('TUP-V', other_schools_selected)} TUP-V", styles["Normal"]),
            Paragraph(f"{checkbox('CSR', other_schools_selected)} CSR", styles["Normal"]),
            Paragraph(
                f"{'[✔]' if other_school else '[ ]'} Others: {other_school if other_school else ''}",
                styles["Normal"]
            ),
        ]
    ]

    School_table = Table(School_data)

    School_table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("ALIGN", (1, 3), (1, 3), "LEFT"),

        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))

    elements.append(School_table)

    behavior_header = Table(
        [[Paragraph("<b>BEHAVIOR INFORMATION:</b>", ParagraphStyle(
            name="whiteText",
            textColor=colors.white,
            fontSize=10
        ))]]
    )

    behavior_header.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.black),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))

    elements.append(behavior_header)

    center_style = ParagraphStyle(
        name="Center",
        parent=styles["Normal"],
        alignment=TA_CENTER
    )

    behavior_table_data = [
        [
            Paragraph("Check if you ever experience the following concerns:", center_style),
            Paragraph("When did it happen?", center_style),
            Paragraph("Does it still bothering you?", center_style),
        ]
    ]

    behavior_table_data += [
        [
            Paragraph(f"{checkbox(info.get('bullying'))} Bullying", styles["Normal"]),
            Paragraph(f"{info.get('bullying_when') or ''}", styles["Normal"]),
            Paragraph(f"{yes_no_checkbox(info.get('bullying'), info.get('bullying_bother'))}", center_style),
        ],
        [
            Paragraph(f"{checkbox(info.get('suicidal_thoughts'))} Suicidal thoughts", styles["Normal"]),
            Paragraph(f"{info.get('suicidal_thoughts_when') or ''}", styles["Normal"]),
            Paragraph(f"{yes_no_checkbox(info.get('suicidal_thoughts'), info.get('suicidal_thoughts_bother'))}", center_style),
        ],
        [
            Paragraph(f"{checkbox(info.get('suicidal_attempts'))} Suicidal attempt", styles["Normal"]),
            Paragraph(f"{info.get('suicidal_attempts_when') or ''}", styles["Normal"]),
            Paragraph(f"{yes_no_checkbox(info.get('suicidal_attempts'), info.get('suicidal_attempts_bother'))}", center_style),
        ],
        [
            Paragraph(f"{checkbox(info.get('panic_attacks'))} panic_attacks", styles["Normal"]),
            Paragraph(f"{info.get('panic_attacks_when') or ''}", styles["Normal"]),
            Paragraph(f"{yes_no_checkbox(info.get('panic_attacks'), info.get('panic_attacks_bother'))}", center_style),
        ],
        [
            Paragraph(f"{checkbox(info.get('anxiety'))} anxiety", styles["Normal"]),
            Paragraph(f"{info.get('anxiety_when') or ''}", styles["Normal"]),
            Paragraph(f"{yes_no_checkbox(info.get('anxiety'), info.get('anxiety_bother'))}", center_style),
        ],
        [
            Paragraph(f"{checkbox(info.get('depression'))} depression", styles["Normal"]),
            Paragraph(f"{info.get('depression_when') or ''}", styles["Normal"]),
            Paragraph(f"{yes_no_checkbox(info.get('depression'), info.get('depression_bother'))}", center_style),
        ],
        [
            Paragraph(f"{checkbox(info.get('self_anger_issues'))} self_anger_issues", styles["Normal"]),
            Paragraph(f"{info.get('self_anger_issues_when') or ''}", styles["Normal"]),
            Paragraph(f"{yes_no_checkbox(info.get('self_anger_issues'), info.get('self_anger_issues_bother'))}", center_style),
        ],
        [
            Paragraph(f"{checkbox(info.get('recurring_negative_thoughts'))} recurring_negative_thoughts", styles["Normal"]),
            Paragraph(f"{info.get('recurring_negative_thoughts_when') or ''}", styles["Normal"]),
            Paragraph(f"{yes_no_checkbox(info.get('recurring_negative_thoughts'), info.get('recurring_negative_thoughts_bother'))}", center_style),
        ],
        [
            Paragraph(f"{checkbox(info.get('low_self_esteem'))} low_self_esteem", styles["Normal"]),
            Paragraph(f"{info.get('low_self_esteem_when') or ''}", styles["Normal"]),
            Paragraph(f"{yes_no_checkbox(info.get('low_self_esteem'), info.get('low_self_esteem_bother'))}", center_style),
        ],
        [
            Paragraph(f"{checkbox(info.get('poor_study_habits'))} poor_study_habits", styles["Normal"]),
            Paragraph(f"{info.get('poor_study_habits_when') or ''}", styles["Normal"]),
            Paragraph(f"{yes_no_checkbox(info.get('poor_study_habits'), info.get('poor_study_habits_bother'))}", center_style),
        ],
        [
            Paragraph(f"{checkbox(info.get('poor_in_decision_making'))} poor_in_decision_making", styles["Normal"]),
            Paragraph(f"{info.get('poor_in_decision_making_when') or ''}", styles["Normal"]),
            Paragraph(f"{yes_no_checkbox(info.get('poor_in_decision_making'), info.get('poor_in_decision_making_bother'))}", center_style),
        ],
        [
            Paragraph(f"{checkbox(info.get('impulsivity'))} impulsivity", styles["Normal"]),
            Paragraph(f"{info.get('impulsivity_when') or ''}", styles["Normal"]),
            Paragraph(f"{yes_no_checkbox(info.get('impulsivity'), info.get('impulsivity_bother'))}", center_style),
        ],
        [
            Paragraph(f"{checkbox(info.get('poor_sleeping_habits'))} poor_sleeping_habits", styles["Normal"]),
            Paragraph(f"{info.get('poor_sleeping_habits_when') or ''}", styles["Normal"]),
            Paragraph(f"{yes_no_checkbox(info.get('poor_sleeping_habits'), info.get('poor_sleeping_habits_bother'))}", center_style),
        ],
        [
            Paragraph(f"{checkbox(info.get('loss_of_appetite'))} loss_of_appetite", styles["Normal"]),
            Paragraph(f"{info.get('loss_of_appetite_when') or ''}", styles["Normal"]),
            Paragraph(f"{yes_no_checkbox(info.get('loss_of_appetite'), info.get('loss_of_appetite_bother'))}", center_style),
        ],
        [
            Paragraph(f"{checkbox(info.get('over_eating'))} over_eating", styles["Normal"]),
            Paragraph(f"{info.get('over_eating_when') or ''}", styles["Normal"]),
            Paragraph(f"{yes_no_checkbox(info.get('over_eating'), info.get('over_eating_bother'))}",center_style),
        ],
        [
            Paragraph(f"{checkbox(info.get('poor_hygiene'))} poor_hygiene", styles["Normal"]),
            Paragraph(f"{info.get('poor_hygiene_when') or ''}", styles["Normal"]),
            Paragraph(f"{yes_no_checkbox(info.get('poor_hygiene'), info.get('poor_hygiene_bother'))}", center_style),
        ],
        [
            Paragraph(f"{checkbox(info.get('withdrawal_isolation'))} withdrawal_isolation", styles["Normal"]),
            Paragraph(f"{info.get('withdrawal_isolation_when') or ''}", styles["Normal"]),
            Paragraph(f"{yes_no_checkbox(info.get('withdrawal_isolation'), info.get('withdrawal_isolation_bother'))}", center_style),
        ],
        [
            Paragraph(f"{checkbox(info.get('family_problem'))} family_problem", styles["Normal"]),
            Paragraph(f"{info.get('family_problem_when') or ''}", styles["Normal"]),
            Paragraph(f"{yes_no_checkbox(info.get('family_problem'), info.get('family_problem_bother'))}", center_style),
        ],
        [
            Paragraph(f"{checkbox(info.get('other_relationship_problem'))} other_relationship_problem", styles["Normal"]),
            Paragraph(f"{info.get('other_relationship_problem_when') or ''}", styles["Normal"]),
            Paragraph(f"{yes_no_checkbox(info.get('other_relationship_problem'), info.get('other_relationship_problem_bother'))}", center_style),
        ],
        [
            Paragraph(f"{checkbox(info.get('alcohol_addiction'))} alcohol_addiction", styles["Normal"]),
            Paragraph(f"{info.get('alcohol_addiction_when') or ''}", styles["Normal"]),
            Paragraph(f"{yes_no_checkbox(info.get('alcohol_addiction'), info.get('alcohol_addiction_bother'))}", center_style),
        ],
        [
            Paragraph(f"{checkbox(info.get('gambling_addiction'))} gambling_addiction", styles["Normal"]),
            Paragraph(f"{info.get('gambling_addiction_when') or ''}", styles["Normal"]),
            Paragraph(f"{yes_no_checkbox(info.get('gambling_addiction'), info.get('gambling_addiction_bother'))}", center_style),
        ],
        [
            Paragraph(f"{checkbox(info.get('drug_addiction'))} drug_addiction", styles["Normal"]),
            Paragraph(f"{info.get('drug_addiction_when') or ''}", styles["Normal"]),
            Paragraph(f"{yes_no_checkbox(info.get('drug_addiction'), info.get('drug_addiction_bother'))}", center_style),
        ],
        [
            Paragraph(f"{checkbox(info.get('computer_addiction'))} computer_addiction", styles["Normal"]),
            Paragraph(f"{info.get('computer_addiction_when') or ''}", styles["Normal"]),
            Paragraph(f"{yes_no_checkbox(info.get('computer_addiction'), info.get('computer_addiction_bother'))}", center_style),
        ],
        [
            Paragraph(f"{checkbox(info.get('sexual_harassment'))} sexual_harassment", styles["Normal"]),
            Paragraph(f"{info.get('sexual_harassment_when') or ''}", styles["Normal"]),
            Paragraph(f"{yes_no_checkbox(info.get('sexual_harassment'), info.get('sexual_harassment_bother'))}", center_style),
        ],
        [
            Paragraph(f"{checkbox(info.get('sexual_abuse'))} sexual_abuse", styles["Normal"]),
            Paragraph(f"{info.get('sexual_abuse_when') or ''}", styles["Normal"]),
            Paragraph(f"{yes_no_checkbox(info.get('sexual_abuse'), info.get('sexual_abuse_bother'))}", center_style),
        ],
        [
            Paragraph(f"{checkbox(info.get('physical_abuse'))} physical_abuse", styles["Normal"]),
            Paragraph(f"{info.get('physical_abuse_when') or ''}", styles["Normal"]),
            Paragraph(f"{yes_no_checkbox(info.get('physical_abuse'), info.get('physical_abuse_bother'))}", center_style),
        ],
        [
            Paragraph(f"{checkbox(info.get('verbal_abuse'))} verbal_abuse", styles["Normal"]),
            Paragraph(f"{info.get('verbal_abuse_when') or ''}", styles["Normal"]),
            Paragraph(f"{yes_no_checkbox(info.get('verbal_abuse'), info.get('verbal_abuse_bother'))}", center_style),
        ],
        [
            Paragraph(f"{checkbox(info.get('pre_marital_sex'))} pre_marital_sex", styles["Normal"]),
            Paragraph(f"{info.get('pre_marital_sex_when') or ''}", styles["Normal"]),
            Paragraph(f"{yes_no_checkbox(info.get('pre_marital_sex'), info.get('pre_marital_sex_bother'))}", center_style),
        ],
        [
            Paragraph(f"{checkbox(info.get('teenage_pregnancy'))} teenage_pregnancy", styles["Normal"]),
            Paragraph(f"{info.get('teenage_pregnancy_when') or ''}", styles["Normal"]),
            Paragraph(f"{yes_no_checkbox(info.get('teenage_pregnancy'), info.get('teenage_pregnancy_bother'))}", center_style),
        ],
        [
            Paragraph(f"{checkbox(info.get('abortion'))} abortion", styles["Normal"]),
            Paragraph(f"{info.get('abortion_when') or ''}", styles["Normal"]),
            Paragraph(f"{yes_no_checkbox(info.get('abortion'), info.get('abortion_bother'))}", center_style),
        ],
        [
            Paragraph(f"{checkbox(info.get('extra_marital_affairs'))} extra_marital_affairs", styles["Normal"]),
            Paragraph(f"{info.get('extra_marital_affairs_when') or ''}", styles["Normal"]),
            Paragraph(f"{yes_no_checkbox(info.get('extra_marital_affairs'), info.get('extra_marital_affairs_bother'))}", center_style),
        ],
    ]

    behavior_table = Table(behavior_table_data)

    behavior_table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.black),

        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))

    elements.append(behavior_table)
    
    Psycological_header = Table(
        [[Paragraph("<b>PREVIOUS PSYCOLOGICAL CONSULTATIONS</b>", ParagraphStyle(
            name="whiteText",
            textColor=colors.white,
            fontSize=10
        ))]]
    )

    Psycological_header.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.black),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))

    elements.append(Psycological_header)

    Psycological_data = [
        [Paragraph(
            f"<b>Have you consulted a PSYCHIATRIST before?</b>", styles["Normal"]),
        Paragraph(
            f"""
            {checkbox(info.get('psychiatrist_before'), True)} YES &nbsp;&nbsp;&nbsp;
            {checkbox(info.get('psychiatrist_before'), False)} NO
            """,
            styles["Normal"]),
        ],
        [Paragraph(
            f"<b>If YES, for what reason? - {info.get('psychiatrist_reason', '')}</b>", styles["Normal"]),
        Paragraph(
            f"<b>When? - {info.get('psychiatrist_when', '')}</b>", styles["Normal"]),
        ],
        [Paragraph(
            f"<b>Have you consulted a PSYCHOLOGIST  before?</b>", styles["Normal"]),
        Paragraph(
            f"""
            {checkbox(info.get('psychologist_before'), True)} YES &nbsp;&nbsp;&nbsp;
            {checkbox(info.get('psychologist_before'), False)} NO
            """,
            styles["Normal"]),
        ],
        [Paragraph(
            f"<b>If YES, for what reason? - {info.get('psychologist_reason', '')}</b>", styles["Normal"]),
        Paragraph(
            f"<b>When? - {info.get('psychologist_when', '')}</b>", styles["Normal"]),
        ],
        [Paragraph(
            f"<b>Have you consulted a COUNSELOR  before?</b> ", styles["Normal"]),
        Paragraph(
            f"""
            {checkbox(info.get('counselor_before'), True)} YES &nbsp;&nbsp;&nbsp;
            {checkbox(info.get('counselor_before'), False)} NO
            """,
            styles["Normal"]),
        ],
        [Paragraph(
            f"<b>If YES, for what reason? - {info.get('counselor_reason', '')}</b>", styles["Normal"]),
        Paragraph(
            f"<b>When? - {info.get('counselor_when', '')}</b>", styles["Normal"]),
        ],
    ]

    Psycological_table = Table(Psycological_data)

    Psycological_table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.black),

        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))

    elements.append(Psycological_table)
    elements.append(Spacer(1, 18))

    elements.append(Paragraph(
        "<b>DATA PRIVACY CONSENT</b>",
        center_style
    ))

    elements.append(Spacer(1, 12))

    paragraph3 = """By accepting this Data Privacy Statement, I (as “Data Subject”) grant my free, voluntary and unconditional consent to the collection and processing of all Personal Data to the information database system of the Central Philippine State University-San Carlos by whatever means in accordance with Republic Act (R.A.) 10173, otherwise known as the “Data Privacy Act of 2012” of the Republic of the Philippines, including its Implementing Rules and Regulations (IRR) as well as all other guidelines and issuances by the National Privacy Commission (NPC). I also consent to the following:<br/>"""
    paragraph4 = """1. The University may collect personal data during my application for admission purposes. <br/>
    2. My personal information can be accessed and used only by authorized personnel and officials connected to the University for legitimate purposes only. <br/>
    3. The University may share or disclose some of my personal information to others in its process to deliver necessary services for the stakeholders and the institution, including but not limited to:"""
    paragraph5 = """a. web posting of the CPSU-College Admission Test (CPSU-CAT) results. <br/> 
    b. web posting of Room Assignments for CPSU-CAT. <br/>
    c. web posting pertinent to admission and enrolment. <br/> 
    d. sharing information for accreditation purposes (Accrediting Agency of Chartered Colleges and Universities in the Philippines (AACCUP). <br/>
    e. conducting research or surveys for purposes of institutional development. <br/>
    f. all guidance and counseling-related functions and services. <br/>
    g. other legitimate processes of the University."""
    paragraph6 = """I hereby declare that all the information provided during my application are true and accurate to the best of my knowledge. I hereby attest to the completeness and accuracy of all the information that I have provided."""
    paragraph7 = """I am fully aware that any false information provided may lead to my automatic dismissal and I will comply with all the terms and conditions set by your University."""

    disclaimer_style = ParagraphStyle(
        name="DisclaimerStyle",
        parent=styles["Normal"],
        alignment=TA_JUSTIFY,
        firstLineIndent=20,
        leading=14,
        spaceAfter=10,
    )
    
    disclaimer_style2 = ParagraphStyle(
        name="DisclaimerStyle",
        parent=styles["Normal"],
        alignment=TA_JUSTIFY,
        leading=14,
        spaceAfter=10,
        fontSize=10,
        leftIndent=20,
    )
    
    disclaimer_style3 = ParagraphStyle(
        name="DisclaimerStyle",
        parent=styles["Normal"],
        alignment=TA_JUSTIFY,
        leading=14,
        spaceAfter=10,
        fontSize=10,
        leftIndent=30,
    )

    instruction_style = ParagraphStyle(
        name="InstructionStyle",
        parent=styles["Normal"],
        fontSize=7,
        leading=12
    )

    elements.append(Paragraph(paragraph3.strip(), disclaimer_style))
    elements.append(Paragraph(paragraph4.strip(), disclaimer_style2))
    elements.append(Paragraph(paragraph5.strip(), disclaimer_style3))
    elements.append(Paragraph(paragraph6.strip(), disclaimer_style))
    elements.append(Paragraph(paragraph7.strip(), disclaimer_style))

    elements.append(Spacer(1, 20))

    center_big = ParagraphStyle(
        name="CenterBig",
        alignment=TA_CENTER,
        fontSize=11
    )

    center_small = ParagraphStyle(
        name="CenterSmall",
        alignment=TA_CENTER,
        fontSize=9
    )

    signature_data = [
        [
            Paragraph(f"{info['fullname'].upper()}", center_big),
            "",
            Paragraph(f"{info.get('consent_date') or ''}", center_big)
        ]
    ]

    signature_table = Table(signature_data, colWidths=[2.8*inch, 1.4*inch, 2.8*inch])

    signature_table.setStyle(TableStyle([
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
    ]))

    elements.append(signature_table)

    label_data = [
        [
            Paragraph("SIGNATURE OVER PRINTED NAME", center_small),
            "",
            Paragraph("DATE ACCOMPLISHED", center_small)
        ]
    ]

    label_table = Table(label_data, colWidths=[2.8*inch, 1.4*inch, 2.8*inch])

    label_table.setStyle(TableStyle([
        ("LINEABOVE", (0, 0), (0, 0), 1, colors.black),
        ("LINEABOVE", (2, 0), (2, 0), 1, colors.black),

        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
    ]))

    elements.append(label_table)

    doc.build(elements)

    pdf = buffer.getvalue()
    buffer.close()

    return pdf
    
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
            f'<div class="ai-subtitle">{title}</div>'
        )

    lines = formatted.split("\n")
    html_lines = []
    in_list = False

    for line in lines:
        if line.strip().startswith("•"):
            if not in_list:
                html_lines.append("<ul>")
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

def get_client_ip():
    return request.headers.get("X-Forwarded-For", request.remote_addr)

def send_email(subject, to_email, body):
    EMAIL_USER = os.getenv("EMAIL_USER")
    EMAIL_PASS = os.getenv("EMAIL_PASS")

    if not EMAIL_USER or not EMAIL_PASS:
        current_app.logger.error("Email credentials not configured")
        return False

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = EMAIL_USER
    msg["To"] = to_email
    msg.set_content(body)

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=10) as server:
            server.login(EMAIL_USER, EMAIL_PASS)
            server.send_message(msg)
        return True

    except Exception as e:
        current_app.logger.error(f"Email send failed: {e}")
        return False

def send_security_alert(ip, username):
    body = f"""
Suspicious admin login detected.

Username: {username}
IP Address: {ip}
Time: {datetime.now(timezone.utc)}
"""
    return send_email(
        subject="⚠️ Admin Login Alert",
        to_email=os.getenv("SECURITY_ALERT_EMAIL", "hertzkin@gmail.com"),
        body=body
    )

def generate_otp():
    return str(random.randint(100000, 999999))

def send_otp_email(email, otp):
    SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY")

    if not SENDGRID_API_KEY:
        current_app.logger.error("❌ SENDGRID_API_KEY not set.")
        return False

    data = {
        "personalizations": [
            {"to": [{"email": email}], "subject": "Your AspireMatch Login OTP"}
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

@admin_bp.route("/test-db")
def test_db():
    conn = get_db_connection()
    return "DB CONNECTED"

@admin_bp.route("/")
def home():
    return redirect(url_for("admin.login"))

MAX_LOGIN_ATTEMPTS = 5
LOCKOUT_MINUTES = 3

@admin_bp.route("/login", methods=["GET", "POST"])
def login():
    error = None
    username = ""
    password = ""

    session.setdefault("admin_login_attempts", 0)
    session.setdefault("admin_lock_until", None)

    if session["admin_lock_until"]:
        if datetime.now(timezone.utc) < session["admin_lock_until"]:
            remaining = int(
                (session["admin_lock_until"] - datetime.now(timezone.utc)).total_seconds() / 60
            )
            error = f"Account locked. Try again in {remaining} minutes."
            return render_template("admin/adminLogin.html", error=error)
        else:
            session["admin_login_attempts"] = 0
            session["admin_lock_until"] = None

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()

        user = None
        user_type = None
        campus = None

        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        cur.execute("SELECT * FROM admin WHERE username = %s", (username,))
        user = cur.fetchone()
        if user:
            user_type = "admin"
            campus = user["campus"]
        else:
            cur.execute("SELECT * FROM super_admin WHERE username = %s", (username,))
            user = cur.fetchone()
            if user:
                user_type = "super_admin"
                campus = user.get("campus", "ALL")

        cur.close()
        conn.close()

        if user:
            valid = False

            try:
                valid = bcrypt.checkpw(
                    password.encode('utf-8'),
                    user["password"].encode('utf-8')
                )
            except Exception:
                valid = False

            if valid:
                # 🔄 OPTIONAL AUTO-UPGRADE (only if old hashes exist)
                if user["password"].startswith("scrypt"):
                    new_hash = bcrypt.hashpw(
                        password.encode('utf-8'),
                        bcrypt.gensalt()
                    ).decode('utf-8')

                    conn = get_db_connection()
                    cur = conn.cursor()
                    table = "admin" if user_type == "admin" else "super_admin"

                    cur.execute(
                        f"UPDATE {table} SET password = %s WHERE username = %s",
                        (new_hash, username)
                    )
                    conn.commit()
                    cur.close()
                    conn.close()

                # ✅ LOGIN SUCCESS (MUST ALWAYS RUN)
                session.clear()
                session["admin_username"] = username
                session["admin_role"] = user_type
                session["campus"] = campus
                session["last_activity"] = datetime.now(timezone.utc)
                session.permanent = True
                session["admin_login_attempts"] = 0
                session["admin_lock_until"] = None

                return redirect(url_for("admin.dashboard"))

        ip = request.headers.get("X-Forwarded-For", request.remote_addr)

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO admin_login_attempts (ip_address, username, attempts)
            VALUES (%s, %s, 1)
            ON CONFLICT (ip_address)
            DO UPDATE SET
                attempts = admin_login_attempts.attempts + 1,
                last_attempt = CURRENT_TIMESTAMP
        """, (ip, username))
        conn.commit()
        cur.close()
        conn.close()

        session["admin_login_attempts"] += 1

        if session["admin_login_attempts"] == MAX_LOGIN_ATTEMPTS:
            send_security_alert(ip, username)

        if session["admin_login_attempts"] >= MAX_LOGIN_ATTEMPTS:
            session["admin_lock_until"] = datetime.now(timezone.utc) + timedelta(minutes=LOCKOUT_MINUTES)
            error = "Too many failed attempts. Account locked for 5 minutes."
        else:
            remaining = MAX_LOGIN_ATTEMPTS - session["admin_login_attempts"]
            error = f"Invalid credentials. {remaining} attempts remaining."

    locked = session.get("admin_login_attempts", 0) >= MAX_LOGIN_ATTEMPTS

    return render_template("admin/adminLogin.html", error=error, locked=locked, username=username)

@admin_bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    error = success = None

    if request.method == "POST":
        email = request.form.get("email")
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)

        user = None
        role = None

        cur.execute("SELECT id FROM super_admin WHERE email = %s", (email,))
        user = cur.fetchone()
        if user:
            role = "super_admin"
        else:
            cur.execute("SELECT id FROM admin WHERE email = %s", (email,))
            user = cur.fetchone()
            if user:
                role = "admin"

        cur.close()
        conn.close()

        if not user:
            error = "No admin account found with this email."
        else:
            otp = generate_otp()
            session["admin_otp"] = otp
            session["admin_otp_email"] = email
            session["admin_otp_time"] = time.time()
            session["admin_role"] = role

            if send_otp_email(email, otp):
                return redirect(url_for("admin.verify_reset_otp"))
            else:
                error = "Unable to send OTP. Please try again later."

    return render_template("admin/adminForgotPassword.html", error=error, success=success)

# ---------- Verify OTP ----------
@admin_bp.route("/verify-reset-otp", methods=["GET", "POST"])
def verify_reset_otp():
    error = success = remaining = None
    email = session.get("admin_otp_email")
    role = session.get("admin_role")

    if not email:
        return redirect(url_for("admin.forgot_password"))

    # fetch admin details
    conn = get_db_connection()
    cur = conn.cursor()
    table = "super_admin" if role == "super_admin" else "admin"
    cur.execute(f"SELECT fullname, campus FROM {table} WHERE email = %s", (email,))
    admin_row = cur.fetchone()
    cur.close()
    conn.close()

    if not admin_row:
        return redirect(url_for("admin.login"))

    fullname, admin_campus = admin_row

    if request.method == "POST":
        action = request.form.get("action")

        if action == "resend":
            last_sent = session.get("admin_otp_time", 0)
            elapsed = int(time.time() - last_sent)
            if elapsed < 60:
                remaining = 60 - elapsed
                error = f"Please wait {remaining} seconds before resending OTP."
            else:
                otp = generate_otp()
                session["admin_otp"] = otp
                session["admin_otp_time"] = time.time()
                if send_otp_email(email, otp):
                    success = "A new OTP has been sent to your email."
                else:
                    error = "Unable to send OTP. Please try again later."

        elif action == "verify":
            user_otp = request.form.get("otp", "").strip()
            if not user_otp:
                error = "Please enter the OTP."
            elif time.time() - session.get("admin_otp_time", 0) > 300:
                error = "OTP expired. Please request a new one."
            elif user_otp != session.get("admin_otp"):
                error = "Invalid OTP."
            else:
                session["admin_reset_email"] = email
                session.pop("admin_otp", None)
                session.pop("admin_otp_email", None)
                session.pop("admin_otp_time", None)
                return redirect(url_for("admin.reset_password"))

    return render_template(
        "admin/adminVerifyOtp.html",
        error=error,
        success=success,
        remaining=remaining,
        fullname=fullname,
        admin_campus=admin_campus
    )

# ---------- Reset Password ----------
@admin_bp.route("/reset-password", methods=["GET", "POST"])
def reset_password():
    error = None
    email = session.get("admin_reset_email")
    if not email:
        return redirect(url_for("admin.login"))

    role = session.get("admin_role")

    if request.method == "POST":
        password = request.form.get("password")
        confirm = request.form.get("confirm")

        if password != confirm:
            error = "Passwords do not match."
        else:
            hashed = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
            conn = get_db_connection()
            cur = conn.cursor()
            table = "super_admin" if role == "super_admin" else "admin"
            cur.execute(f"UPDATE {table} SET password = %s WHERE email = %s", (hashed, email))
            conn.commit()
            cur.close()
            conn.close()

            session.pop("admin_reset_email", None)
            return redirect(url_for("admin.login"))

    return render_template("admin/adminResetPassword.html", error=error)

@admin_bp.route("/dashboard", methods=["GET"])
def dashboard():
    if "admin_username" not in session:
        return redirect(url_for("admin.login"))
    
    error = request.args.get("error")
    success = request.args.get("success")
    message = request.args.get("message")

    conn = get_db_connection()
    cur = conn.cursor()

    role = session.get("admin_role")
    table = "super_admin" if role == "super_admin" else "admin"

    cur.execute(
        f"SELECT fullname, campus FROM {table} WHERE username = %s;",
        (session["admin_username"],)
    )
    admin_row = cur.fetchone()

    if not admin_row:
        cur.close()
        conn.close()
        return redirect(url_for("admin.login"))

    fullname, admin_campus = admin_row
        
    cur.execute("""
        SELECT campus_name, campus_address 
        FROM campus 
        WHERE campus_name = %s
    """, (admin_campus,))

    campus_data = cur.fetchone()

    if campus_data:
        campus_name = campus_data[0]
        campus_address = campus_data[1]
    else:
        campus_name = admin_campus
        campus_address = ""

    role = session.get("admin_role")
    is_super_admin = role == "super_admin"

    cur.execute("""
        SELECT DISTINCT school_year
        FROM student
        WHERE school_year IS NOT NULL
        ORDER BY school_year DESC;
    """)
    available_years = [row[0] for row in cur.fetchall()]

    selected_year = request.args.get("year")
    if not selected_year:
        selected_year = available_years[0] if available_years else None

    search_query = request.args.get("q", "").strip()

    if is_super_admin:
        # Super admin can select any campus
        cur.execute("SELECT campus_name FROM campus ORDER BY campus_name ASC")
        campuses = [c[0] for c in cur.fetchall()]
        selected_campus = request.args.get("campus", "")
    else:
        # Sub-admin: fixed campus
        selected_campus = admin_campus
        campuses = [admin_campus]

    cur.execute("""
        SELECT DISTINCT school_year
        FROM student
        WHERE school_year IS NOT NULL
        ORDER BY school_year DESC;
    """)
    available_years = [row[0] for row in cur.fetchall()]

    student_query = """
        SELECT id, exam_id, fullname, gender, email, campus
        FROM student
        WHERE school_year = %s
    """
    params = [selected_year]

    if selected_campus:
        student_query += " AND campus = %s"
        params.append(selected_campus)

    if search_query:
        student_query += " AND (LOWER(fullname) LIKE LOWER(%s) OR exam_id ILIKE %s)"
        params.extend([f"%{search_query}%", f"%{search_query}%"])

    student_query += " ORDER BY fullname ASC;"
    cur.execute(student_query, tuple(params))
    searched_students = cur.fetchall()

    total_query = """
        SELECT COUNT(*)
        FROM student
        WHERE school_year = %s
    """
    params = [selected_year]

    if selected_campus:
        total_query += " AND campus = %s"
        params.append(selected_campus)

    cur.execute(total_query, tuple(params))
    total_students = cur.fetchone()[0]

    pending_query = """
        SELECT COUNT(*)
        FROM student s
        LEFT JOIN student_survey_answer a
            ON a.student_id = s.id OR a.exam_id = s.exam_id
        WHERE school_year = %s
        AND (a.preferred_program IS NULL OR a.preferred_program = '')
    """
    params = [selected_year]

    if selected_campus:
        pending_query += " AND s.campus = %s"
        params.append(selected_campus)

    cur.execute(pending_query, tuple(params))
    pending_students = cur.fetchone()[0]

    cur.execute("""
        SELECT COUNT(DISTINCT admin_username)
        FROM admin_logs
        WHERE created_at >= NOW() - INTERVAL '1 month';
    """)
    active_admins = cur.fetchone()[0]

    admin_query = """
        SELECT a.fullname,
            CASE 
                WHEN l.last_login >= NOW() - INTERVAL '1 month' THEN 'Active'
                ELSE 'Inactive'
            END AS status
        FROM admin a
        LEFT JOIN (
            SELECT admin_username, MAX(created_at) AS last_login
            FROM admin_logs
            GROUP BY admin_username
        ) l ON a.username = l.admin_username
    """
    params = []

    if selected_campus:
        admin_query += " WHERE a.campus = %s"
        params.append(selected_campus)

    admin_query += " ORDER BY a.fullname ASC;"

    cur.execute(admin_query, tuple(params))
    admins = cur.fetchall()

    cur.close()
    conn.close()

    return render_template(
        "admin/dashboard.html",
        admin_username=session["admin_username"],
        fullname=fullname,
        admin_campus=admin_campus,
        campus_name=campus_name,
        campus_address=campus_address,
        is_super_admin=is_super_admin,
        selected_campus=selected_campus,
        campuses=campuses,
        total_students=total_students,
        pending_students=pending_students,
        active_admins=active_admins,
        year=selected_year,
        available_years=available_years,
        searched_students=searched_students,
        search_query=search_query,
        admins=admins,
        error=error,
        success=success,
        message=message
    )

@admin_bp.route("/edit-student", methods=["POST"])
def edit_student():
    if "admin_username" not in session:
        return redirect(url_for("admin.login"))

    admin_username = session["admin_username"]
    role = session.get("admin_role")

    student_id = request.form["student_id"]
    new_fullname = request.form["fullname"]
    new_gender = request.form["gender"]
    new_email = request.form["email"]

    conn = get_db_connection()
    cur = conn.cursor()

    # ✅ Get admin campus based on role
    table = "super_admin" if role == "super_admin" else "admin"
    cur.execute(
        f"SELECT campus FROM {table} WHERE username = %s;",
        (admin_username,)
    )
    admin_campus = cur.fetchone()[0]

    # ✅ Get student info (including campus)
    cur.execute("""
        SELECT fullname, gender, email, campus
        FROM student
        WHERE id = %s;
    """, (student_id,))
    student = cur.fetchone()

    if not student:
        cur.close()
        conn.close()
        return redirect(url_for("admin.dashboard"))

    old_fullname, old_gender, old_email, student_campus = student

    # ✅ Restrict sub-admin
    if role != "super_admin" and student_campus != admin_campus:
        cur.close()
        conn.close()
        return "Unauthorized", 403

    # ✅ Update student
    cur.execute("""
        UPDATE student
        SET fullname = %s,
            gender = %s,
            email = %s
        WHERE id = %s;
    """, (new_fullname, new_gender, new_email, student_id))

    # ✅ Logs
    if old_fullname != new_fullname:
        cur.execute("""
            INSERT INTO admin_logs (admin_username, campus, action)
            VALUES (%s, %s, %s);
        """, (admin_username, admin_campus,
              f"Edited student name: {old_fullname} → {new_fullname}"))

    if old_gender != new_gender:
        cur.execute("""
            INSERT INTO admin_logs (admin_username, campus, action)
            VALUES (%s, %s, %s);
        """, (admin_username, admin_campus,
              f"Edited student gender: {old_gender} → {new_gender}"))

    if old_email != new_email:
        cur.execute("""
            INSERT INTO admin_logs (admin_username, campus, action)
            VALUES (%s, %s, %s);
        """, (admin_username, admin_campus,
              f"Edited student email: {old_email} → {new_email}"))

    conn.commit()
    cur.close()
    conn.close()

    return redirect(url_for("admin.dashboard"))

@admin_bp.route("/delete-student", methods=["POST"])
def delete_student():
    if "admin_username" not in session:
        return redirect(url_for("admin.login"))

    admin_username = session["admin_username"]
    role = session.get("admin_role")

    student_id = request.form["student_id"]

    conn = get_db_connection()
    cur = conn.cursor()

    # ✅ Get admin campus
    table = "super_admin" if role == "super_admin" else "admin"
    cur.execute(
        f"SELECT campus FROM {table} WHERE username = %s;",
        (admin_username,)
    )
    admin_campus = cur.fetchone()[0]

    # ✅ Get student info
    cur.execute("""
        SELECT fullname, campus
        FROM student
        WHERE id = %s;
    """, (student_id,))
    student = cur.fetchone()

    if not student:
        cur.close()
        conn.close()
        return redirect(url_for("admin.dashboard"))

    student_fullname, student_campus = student

    # ✅ Restrict sub-admin
    if role != "super_admin" and student_campus != admin_campus:
        cur.close()
        conn.close()
        return "Unauthorized", 403

    # ✅ Delete
    cur.execute(
        "DELETE FROM student WHERE id = %s;",
        (student_id,)
    )

    # ✅ Log
    cur.execute("""
        INSERT INTO admin_logs (admin_username, campus, action)
        VALUES (%s, %s, %s);
    """, (
        admin_username,
        admin_campus,
        f"Deleted student: {student_fullname}"
    ))

    conn.commit()
    cur.close()
    conn.close()

    return redirect(url_for("admin.dashboard"))

@admin_bp.route("/addSuper", methods=["GET", "POST"])
def addSuper():
    if "admin_username" not in session:
        return redirect(url_for("admin.login"))

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    # Fetch campuses
    cur.execute("SELECT campus_name FROM campus ORDER BY campus_name ASC")
    campuses = cur.fetchall()

    message = None
    category = None

    if request.method == "POST":
        fullname = request.form.get("fullname")
        username = request.form.get("user_name")
        email = request.form.get("email")
        password = request.form.get("password")
        campus = request.form.get("campus")

        pattern = r'^(?=.*[A-Z])(?=.*[a-z])(?=.*\d)(?=.*[\W_]).{8,}$'
        if not re.match(pattern, password):
            message = "Password is not strong enough!"
            category = "danger"
            return render_template("admin/add_super.html",
                                   campuses=campuses,
                                   message=message,
                                   category=category)

        # Check duplicate
        cur.execute("SELECT * FROM super_admin WHERE username=%s OR email=%s", (username, email))
        existing = cur.fetchone()

        if existing:
            message = "Username or Email already exists!"
            category = "danger"
        else:
            hashed_password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

            cur.execute("""
                INSERT INTO super_admin (fullname, username, email, password, campus, created_at)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (fullname, username, email, hashed_password, campus, datetime.now()))

            conn.commit()

            message = "Super Admin added successfully!"
            category = "success"

    cur.close()
    conn.close()

    return render_template("admin/super_admin.html",
                           campuses=campuses,
                           message=message,
                           category=category)

@admin_bp.route("/addAdmin", methods=["GET", "POST"])
def addAdmin():
    if "admin_username" not in session:
        return redirect(url_for("admin.login"))

    message = None
    category = None
    admin_username = session["admin_username"]
    role = session.get("admin_role", "admin")  # "super_admin" or "admin"
    is_super_admin = role == "super_admin"

    # Determine admin campus
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    table = "super_admin" if is_super_admin else "admin"
    cur.execute(f"SELECT campus, fullname FROM {table} WHERE username = %s", (admin_username,))
    admin_row = cur.fetchone()
    if not admin_row:
        cur.close()
        conn.close()
        return redirect(url_for("admin.login"))

    admin_campus = admin_row.get("campus") or "ALL"
    admin_fullname = admin_row["fullname"]

    # Get campus info
    cur.execute("""
        SELECT campus_name, campus_address 
        FROM campus 
        WHERE campus_name = %s
    """, (admin_campus,))
    campus_data = cur.fetchone()
    campus_name = campus_data["campus_name"] if campus_data else admin_campus
    campus_address = campus_data["campus_address"] if campus_data else ""

    # Fetch campuses for dropdown
    if is_super_admin:
        cur.execute("SELECT campus_name FROM campus ORDER BY campus_name ASC")
    else:
        cur.execute("SELECT campus_name FROM campus WHERE campus_name = %s", (admin_campus,))
    campuses = [c["campus_name"] for c in cur.fetchall()]

    cur.close()
    conn.close()

    if request.method == "POST":
        fullname = request.form["fullname"]
        username = request.form["user_name"]
        email = request.form["email"]
        campus = request.form["campus"]
        password = request.form["password"]

        if not is_password_strong(password):
            return render_template(
                "admin/addAdmin.html",
                admin_username=admin_username,
                is_super_admin=is_super_admin,
                message="Password is too weak! Must include: uppercase, lowercase, number, symbol, and min 8 chars.",
                category="danger",
                admins=[],
                campuses=campuses,
                admin_campus=admin_campus
            )

        hashed_pw = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)

        # Check duplicates
        cur.execute("SELECT 1 FROM admin WHERE username = %s OR email = %s", (username, email))
        if cur.fetchone():
            cur.close()
            conn.close()
            return render_template(
                "admin/addAdmin.html",
                admin_username=admin_username,
                is_super_admin=is_super_admin,
                message="Username or email already exists.",
                category="danger",
                admins=[],
                campuses=campuses,
                admin_campus=admin_campus
            )

        # Insert new admin
        cur.execute("""
            INSERT INTO admin (fullname, username, email, campus, password)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
        """, (fullname, username, email, campus, hashed_pw))
        new_admin_id = cur.fetchone()["id"]

        # Log who added the admin and their role
        cur.execute("""
            INSERT INTO admin_logs (admin_username, campus, action)
            VALUES (%s, %s, %s)
        """, (
            admin_username,
            admin_campus,
            f"{'Super Admin' if is_super_admin else 'Admin'} {admin_fullname} added new admin '{fullname}' ({username}) to campus {campus}"
        ))

        conn.commit()
        cur.close()
        conn.close()

        message = "Admin account created successfully."
        category = "success"

    # Fetch current admins for table
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    if is_super_admin:
        cur.execute("""
            SELECT id, fullname, username, email, COALESCE(campus, 'ALL') AS campus, 'Admin' AS role
            FROM admin
            UNION ALL
            SELECT id, fullname, username, email, COALESCE(campus, 'ALL') AS campus, 'Super Admin' AS role
            FROM super_admin
            ORDER BY role DESC, campus ASC, fullname ASC
        """)
    else:
        cur.execute("""
            SELECT id, fullname, username, email, campus, 'Admin' AS role
            FROM admin
            WHERE campus = %s
            ORDER BY fullname ASC
        """, (admin_campus,))
    admins = cur.fetchall()
    cur.close()
    conn.close()

    return render_template(
        "admin/addAdmin.html",
        admin_username=admin_username,
        is_super_admin=is_super_admin,
        message=message,
        category=category,
        admins=admins,
        campuses=campuses,
        admin_campus=admin_campus,
        campus_name=campus_name,
        campus_address=campus_address
    )

@admin_bp.route("/delete-admin", methods=["POST"])
def delete_admin():
    if "admin_username" not in session:
        return redirect(url_for("admin.login"))

    deleter = session["admin_username"]

    conn = get_db_connection()
    cur = conn.cursor()

    # Check if the logged-in user is a super admin
    cur.execute("SELECT username FROM super_admin WHERE username = %s", (deleter,))
    if not cur.fetchone():
        cur.close()
        conn.close()
        # Not a super admin → cannot delete
        return redirect(url_for("admin.addAdmin"))

    deleted_admin_id = request.form["admin_id"]
    new_admin_id = request.form["reassign_admin_id"]

    # Fetch the admin to delete
    cur.execute("""
        SELECT id, fullname, username, email, campus
        FROM admin
        WHERE id = %s
    """, (deleted_admin_id,))
    admin_row = cur.fetchone()

    if not admin_row:
        cur.close()
        conn.close()
        return redirect(url_for("admin.addAdmin"))

    admin_id, fullname, username, email, campus = admin_row

    # Prevent deleting self (even if super admin added in admin table)
    if username == deleter:
        cur.close()
        conn.close()
        return redirect(url_for("admin.addAdmin"))

    # Get username of new admin for logs
    cur.execute("SELECT username FROM admin WHERE id = %s", (new_admin_id,))
    new_admin_row = cur.fetchone()
    if not new_admin_row:
        cur.close()
        conn.close()
        return redirect(url_for("admin.addAdmin"))
    new_admin_username = new_admin_row[0]

    # Reassign students
    cur.execute("""
        UPDATE student
        SET added_by = %s
        WHERE added_by = %s
    """, (new_admin_id, admin_id))

    # Move deleted admin to deleted_admin table
    cur.execute("""
        INSERT INTO deleted_admin (id, fullname, username, email, campus, deleted_by, deleted_at)
        VALUES (%s, %s, %s, %s, %s, %s, NOW())
    """, (admin_id, fullname, username, email, campus, deleter))

    # Delete from admin
    cur.execute("DELETE FROM admin WHERE id = %s", (admin_id,))

    # Log the deletion
    cur.execute("""
        INSERT INTO admin_logs (admin_username, campus, action, created_at)
        VALUES (%s, %s, %s, NOW())
    """, (
        deleter,
        campus,
        f"Deleted admin: {fullname} ({username}) and reassigned students to {new_admin_username}"
    ))

    conn.commit()
    cur.close()
    conn.close()

    return redirect(url_for("admin.addAdmin"))

@admin_bp.route("/verify-new-admin", methods=["GET", "POST"])
def verify_new_admin():
    error = None
    success = None
    remaining = None

    if "new_admin_email" not in session:
        return redirect(url_for("admin.addAdmin"))

    if request.method == "POST":
        action = request.form.get("action")

        if action == "resend":
            elapsed = int(time.time() - session.get("new_admin_otp_time", 0))

            if elapsed < 60:
                remaining = 60 - elapsed
                error = "Please wait before resending OTP."
            else:
                otp = generate_otp()
                session["new_admin_otp"] = otp
                session["new_admin_otp_time"] = time.time()
                sent = send_otp_email(session["new_admin_email"], otp)

                if not sent:
                    error = "Unable to send OTP. Please try again later."
                    return render_template("admin/adminForgotPassword.html", error=error)
                success = "A new OTP has been sent."

        if action == "verify":
            user_otp = request.form.get("otp", "").strip()

            if not user_otp:
                error = "Please enter the OTP."
            elif time.time() - session["new_admin_otp_time"] > 300:
                error = "OTP expired."
            elif user_otp != session["new_admin_otp"]:
                error = "Invalid OTP."
            else:
                data = session["new_admin_data"]

                conn = get_db_connection()
                cur = conn.cursor()

                cur.execute("""
                    INSERT INTO admin (fullname, username, email, campus, password)
                    VALUES (%s, %s, %s, %s, %s)
                """, (
                    data["fullname"],
                    data["username"],
                    data["email"],
                    data["campus"],
                    data["password"]
                ))

                cur.execute("""
                    INSERT INTO admin_logs (admin_username, campus, action)
                    VALUES (%s, %s, %s)
                """, (
                    session["admin_username"],
                    data["campus"],
                    f"Added new admin '{data['username']}' (email verified)"
                ))

                conn.commit()
                cur.close()
                conn.close()

                session.pop("new_admin_data", None)
                session.pop("new_admin_otp", None)
                session.pop("new_admin_otp_time", None)
                session.pop("new_admin_email", None)

                return redirect(url_for("admin.addAdmin", success="verified"))

    return render_template(
        "admin/adminVerifyOtp.html",
        error=error,
        success=success,
        remaining=remaining
    )

@admin_bp.route("/admin_logs/<username>")
def get_admin_logs(username):
    if "admin_username" not in session:
        return jsonify([])

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT admin_username, action, created_at
        FROM admin_logs
        WHERE admin_username = %s
        ORDER BY created_at DESC
    """, (username,))

    logs = cur.fetchall()
    cur.close()
    conn.close()

    return jsonify([
        {
            "admin_username": log[0],
            "action": log[1],
            "created_at": log[2].strftime("%Y-%m-%d %H:%M")
        }
        for log in logs
    ])

@admin_bp.route("/editAdmin", methods=["POST"])
def editAdmin():
    if "admin_username" not in session:
        return jsonify(success=False, message="Unauthorized")

    deleter = session["admin_username"]

    conn = get_db_connection()
    cur = conn.cursor()

    # Check if logged-in user is super admin
    cur.execute("SELECT username FROM super_admin WHERE username = %s", (deleter,))
    if not cur.fetchone():
        cur.close()
        conn.close()
        return jsonify(success=False, message="Only super admins can edit admins")

    data = request.get_json()
    admin_id = data.get("id")
    fullname = data.get("fullname")
    username = data.get("username")
    email = data.get("email")
    campus = data.get("campus")

    if not admin_id:
        cur.close()
        conn.close()
        return jsonify(success=False, message="Missing admin ID")

    try:
        # Fetch current admin info
        cur.execute("""
            SELECT fullname, username, email, campus
            FROM admin
            WHERE id = %s
        """, (admin_id,))
        old = cur.fetchone()

        if not old:
            cur.close()
            conn.close()
            return jsonify(success=False, message="Admin not found")

        old_fullname, old_username, old_email, old_campus = old

        # Prevent editing self (optional)
        if old_username == deleter:
            cur.close()
            conn.close()
            return jsonify(success=False, message="Cannot edit your own account")

        changes = []
        if fullname != old_fullname:
            changes.append(f"fullname '{old_fullname}' → '{fullname}'")
        if username != old_username:
            changes.append(f"username '{old_username}' → '{username}'")
        if email != old_email:
            changes.append(f"email '{old_email}' → '{email}'")
        if campus != old_campus:
            changes.append(f"campus '{old_campus}' → '{campus}'")

        if not changes:
            cur.close()
            conn.close()
            return jsonify(success=False, message="No changes detected")

        # Update admin record
        cur.execute("""
            UPDATE admin
            SET fullname=%s, username=%s, email=%s, campus=%s
            WHERE id=%s
        """, (fullname, username, email, campus, admin_id))

        # Get deleter's campus for logging
        cur.execute("SELECT campus FROM admin WHERE username = %s", (deleter,))
        row = cur.fetchone()
        admin_campus = row[0] if row else "ALL"

        # Log the changes
        action = f"Edited admin '{old_username}': " + ", ".join(changes)
        cur.execute("""
            INSERT INTO admin_logs (admin_username, campus, action, created_at)
            VALUES (%s, %s, %s, NOW())
        """, (deleter, admin_campus, action))

        conn.commit()
        cur.close()
        conn.close()

        return jsonify(success=True)

    except psycopg2.Error as e:
        cur.close()
        conn.close()
        return jsonify(success=False, message=str(e))
    
@admin_bp.route("/program")
def program():
    if "admin_username" not in session:
        return redirect(url_for("admin.login"))

    admin_username = session["admin_username"]
    selected_campus = request.args.get("campus", "")

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    # Determine role
    cur.execute("SELECT id, campus FROM super_admin WHERE username = %s", (admin_username,))
    super_admin_row = cur.fetchone()
    is_super_admin = bool(super_admin_row)

    if is_super_admin:
        admin_campus = super_admin_row.get("campus") or "ALL"
        # Fetch all campuses for dropdown
        cur.execute("SELECT campus_name FROM campus ORDER BY campus_name ASC")
        campuses = cur.fetchall()

        # Fetch programs
        if selected_campus and selected_campus != "":
            # Filtered by selected campus
            cur.execute("""
                SELECT id, program_name, campus, created_at, is_active, color
                FROM program
                WHERE campus = %s
                ORDER BY created_at DESC
            """, (selected_campus,))
            programs_by_campus = {selected_campus: cur.fetchall()}
        else:
            # All campuses, grouped
            cur.execute("""
                SELECT campus, id, program_name, created_at, is_active, color
                FROM program
                ORDER BY campus ASC, created_at DESC
            """)
            programs = cur.fetchall()
            programs_by_campus = {}
            for p in programs:
                campus_name = p["campus"]
                programs_by_campus.setdefault(campus_name, []).append(p)

    else:
        # Sub admin: only their campus
        cur.execute("SELECT id, campus FROM admin WHERE username = %s", (admin_username,))
        admin_row = cur.fetchone()
        if not admin_row:
            cur.close()
            conn.close()
            return redirect(url_for("admin.login"))

        admin_campus = admin_row["campus"]
        campuses = [{"campus_name": admin_campus}]
        cur.execute("""
            SELECT id, program_name, campus, created_at, is_active, color
            FROM program
            WHERE campus = %s
            ORDER BY created_at DESC
        """, (admin_campus,))
        programs_by_campus = {admin_campus: cur.fetchall()}

    cur.close()
    conn.close()

    if request.args.get("ajax"):
        return render_template(
            "admin/_program_rows.html",
            programs_by_campus=programs_by_campus
        )

    return render_template(
        "admin/program.html",
        admin_username=admin_username,
        is_super_admin=is_super_admin,
        campuses=campuses,
        selected_campus=selected_campus,
        programs_by_campus=programs_by_campus,
    )

@admin_bp.route("/addProgram", methods=["POST"])
def addProgram():
    if "admin_username" not in session:
        return jsonify(success=False, message="Unauthorized")

    program_name = request.form.get("program_name")
    category_letters = request.form.get("category_letters")
    category_descriptions = request.form.get("category_descriptions")
    color = request.form.get("color") or "#166D3B"

    if not category_letters or not category_descriptions:
        return jsonify(success=False, message="Select at least one category")

    admin_username = session["admin_username"]

    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)

        # Determine role
        cur.execute("SELECT username, campus FROM super_admin WHERE username = %s", (admin_username,))
        row = cur.fetchone()

        if row:
            is_super_admin = True
            admin_campus = row.get("campus") or "ALL"
            campus = request.form.get("campus")  # ✅ now assigned here

        else:
            cur.execute("SELECT username, campus FROM admin WHERE username = %s", (admin_username,))
            row = cur.fetchone()

            if row:
                is_super_admin = False
                admin_campus = row["campus"]
                campus = admin_campus  # ✅ forced
            else:
                cur.close()
                conn.close()
                return jsonify(success=False, message="Unauthorized")

        # ✅ NOW validate AFTER campus is set
        if not program_name or not campus:
            return jsonify(success=False, message="Missing data")

        # Insert program
        cur.execute("""
            INSERT INTO program (program_name, campus, category_letter, category_description, color)
            VALUES (%s, %s, %s, %s, %s)
        """, (program_name, campus, category_letters, category_descriptions, color))

        # Log action
        cur.execute("""
            INSERT INTO admin_logs (admin_username, campus, action)
            VALUES (%s, %s, %s)
        """, (admin_username, admin_campus, f"Added new program '{program_name}' at campus '{campus}'"))

        conn.commit()
        cur.close()
        conn.close()
        return jsonify(success=True)

    except Exception as e:
        return jsonify(success=False, message=str(e))


@admin_bp.route("/addProgramColor", methods=["POST"])
def addProgramColor():
    if "admin_username" not in session:
        return jsonify(success=False, message="Unauthorized")

    data = request.get_json()
    program_name = data.get("program_name")
    color = data.get("color")
    admin_username = session["admin_username"]

    if not program_name or not color:
        return jsonify(success=False, message="Missing data")

    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)

        # Determine role
        cur.execute("SELECT username, campus FROM super_admin WHERE username = %s", (admin_username,))
        row = cur.fetchone()
        if row:
            is_super_admin = True
            admin_campus = row.get("campus") or "ALL"
        else:
            cur.execute("SELECT username, campus FROM admin WHERE username = %s", (admin_username,))
            row = cur.fetchone()
            if row:
                is_super_admin = False
                admin_campus = row["campus"]
            else:
                cur.close()
                conn.close()
                return jsonify(success=False, message="Unauthorized")

        # Update program color
        cur.execute("UPDATE program SET color = %s WHERE program_name = %s", (color, program_name))

        # Log action
        cur.execute("""
            INSERT INTO admin_logs (admin_username, campus, action)
            VALUES (%s, %s, %s)
        """, (admin_username, admin_campus, f"Set color '{color}' for program '{program_name}'"))

        conn.commit()
        cur.close()
        conn.close()
        return jsonify(success=True)

    except Exception as e:
        return jsonify(success=False, message=str(e))


@admin_bp.route("/deleteProgram", methods=["POST"])
def deleteProgram():
    if "admin_username" not in session:
        return jsonify(success=False, message="Unauthorized")

    data = request.get_json()
    program_id = data.get("program_id")
    admin_username = session["admin_username"]

    if not program_id:
        return jsonify(success=False, message="Missing program ID")

    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)

        # Determine role
        cur.execute("SELECT username, campus FROM super_admin WHERE username = %s", (admin_username,))
        row = cur.fetchone()
        if row:
            is_super_admin = True
            admin_campus = row.get("campus") or "ALL"
        else:
            cur.execute("SELECT username, campus FROM admin WHERE username = %s", (admin_username,))
            row = cur.fetchone()
            if row:
                is_super_admin = False
                admin_campus = row["campus"]
            else:
                cur.close()
                conn.close()
                return jsonify(success=False, message="Unauthorized")

        cur.execute("SELECT program_name FROM program WHERE id = %s", (program_id,))
        row = cur.fetchone()
        if not row:
            return jsonify(success=False, message="Program not found")

        program_name = row["program_name"]
        cur.execute("DELETE FROM program WHERE id = %s", (program_id,))

        # Log action
        cur.execute("""
            INSERT INTO admin_logs (admin_username, campus, action)
            VALUES (%s, %s, %s)
        """, (admin_username, admin_campus, f"Deleted program '{program_name}'"))

        conn.commit()
        cur.close()
        conn.close()
        return jsonify(success=True)

    except Exception as e:
        return jsonify(success=False, message=str(e))


@admin_bp.route("/editProgram", methods=["POST"])
def editProgram():
    if "admin_username" not in session:
        return jsonify(success=False, message="Unauthorized")

    data = request.get_json()
    program_id = data.get("id")
    new_name = data.get("name")
    new_color = data.get("color")
    new_letters = data.get("category_letters")
    new_descriptions = data.get("category_description")
    admin_username = session["admin_username"]

    if not program_id or (not new_name and not new_color):
        return jsonify(success=False, message="Missing data")

    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)

        # Determine role
        cur.execute("SELECT username, campus FROM super_admin WHERE username = %s", (admin_username,))
        row = cur.fetchone()
        if row:
            is_super_admin = True
            admin_campus = row.get("campus") or "ALL"
        else:
            cur.execute("SELECT username, campus FROM admin WHERE username = %s", (admin_username,))
            row = cur.fetchone()
            if row:
                is_super_admin = False
                admin_campus = row["campus"]
            else:
                cur.close()
                conn.close()
                return jsonify(success=False, message="Unauthorized")

        # Fetch old program
        cur.execute("SELECT program_name, color FROM program WHERE id = %s", (program_id,))
        old_row = cur.fetchone()
        if not old_row:
            return jsonify(success=False, message="Program not found")
        old_name, old_color = old_row["program_name"], old_row["color"]

        fields_to_update = []
        params = []
        action_parts = []

        if new_name and new_name != old_name:
            fields_to_update.append("program_name = %s")
            params.append(new_name)
            action_parts.append(f"Edited program '{old_name}' → '{new_name}'")

        if new_color and new_color != old_color:
            fields_to_update.append("color = %s")
            params.append(new_color)
            action_parts.append(f"Edited program color '{old_color}' → '{new_color}'")

        if new_letters:
            fields_to_update.append("category_letter = %s")
            params.append(new_letters)

        if new_descriptions:
            fields_to_update.append("category_description = %s")
            params.append(new_descriptions)

        if not fields_to_update:
            return jsonify(success=False, message="No changes detected")

        params.append(program_id)
        sql = f"UPDATE program SET {', '.join(fields_to_update)} WHERE id = %s"
        cur.execute(sql, params)

        # Log changes
        action_text = "; ".join(action_parts)
        cur.execute("""
            INSERT INTO admin_logs (admin_username, campus, action)
            VALUES (%s, %s, %s)
        """, (admin_username, admin_campus, action_text))

        conn.commit()
        cur.close()
        conn.close()
        return jsonify(success=True)

    except Exception as e:
        return jsonify(success=False, message=str(e))

@admin_bp.route("/campuses", methods=["GET", "POST"])
def campuses():
    if "admin_username" not in session:
        return redirect(url_for("admin.login"))

    admin_username = session["admin_username"]

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    # Check if super admin
    cur.execute("SELECT * FROM super_admin WHERE username = %s", (admin_username,))
    super_admin = cur.fetchone()
    is_super_admin = bool(super_admin)

    # Check if sub admin
    if not is_super_admin:
        cur.execute("SELECT * FROM admin WHERE username = %s", (admin_username,))
        sub_admin = cur.fetchone()
        if not sub_admin:
            cur.close()
            conn.close()
            return redirect(url_for("admin.login"))
        admin_campus = sub_admin["campus"]
    else:
        admin_campus = super_admin.get("campus") or "ALL"

    # Determine campus info for header
    if is_super_admin and admin_campus != "ALL":
        cur.execute("""
            SELECT campus_name, campus_address
            FROM campus
            WHERE campus_name = %s
        """, (admin_campus,))
        campus_data = cur.fetchone()
        campus_name = campus_data["campus_name"] if campus_data else admin_campus
        campus_address = campus_data["campus_address"] if campus_data else ""
    elif not is_super_admin:
        cur.execute("""
            SELECT campus_name, campus_address
            FROM campus
            WHERE campus_name = %s
        """, (admin_campus,))
        campus_data = cur.fetchone()
        campus_name = campus_data["campus_name"] if campus_data else admin_campus
        campus_address = campus_data["campus_address"] if campus_data else ""
    else:
        campus_name = "ALL CAMPUSES"
        campus_address = ""

    # Handle POST actions (add/edit/delete)
    action = request.form.get("action")
    if action == "add" and is_super_admin:
        campus_name_input = request.form.get("campus_name")
        campus_address_input = request.form.get("campus_address")
        guidance_counselor = request.form.get("guidance_counselor")

        # Check duplicate campus
        cur.execute("SELECT id FROM campus WHERE LOWER(campus_name) = LOWER(%s)", (campus_name_input,))
        existing = cur.fetchone()
        if existing:
            duplicate = True
        else:
            cur.execute("""
                INSERT INTO campus (campus_name, campus_address, guidance_counselor)
                VALUES (%s, %s, %s)
            """, (campus_name_input, campus_address_input, guidance_counselor))
            conn.commit()
            duplicate = False

    elif action == "edit" and is_super_admin:
        campus_id = request.form.get("campus_id")
        campus_name_input = request.form.get("campus_name")
        campus_address_input = request.form.get("campus_address")
        guidance_counselor = request.form.get("guidance_counselor")

        cur.execute("""
            UPDATE campus
            SET campus_name = %s,
                campus_address = %s,
                guidance_counselor = %s
            WHERE id = %s
        """, (campus_name_input, campus_address_input, guidance_counselor, campus_id))
        conn.commit()

    elif action == "delete" and is_super_admin:
        campus_id = request.form.get("campus_id")
        cur.execute("DELETE FROM campus WHERE id = %s", (campus_id,))
        conn.commit()

    # Fetch campuses
    if is_super_admin:
        cur.execute("SELECT * FROM campus ORDER BY campus_name ASC")
    else:
        # Sub admin → only their campus
        cur.execute("SELECT * FROM campus WHERE campus_name = %s", (admin_campus,))
    campuses = cur.fetchall()

    cur.close()
    conn.close()

    return render_template(
        "admin/campuses.html",
        campuses=campuses,
        campus_name=campus_name,
        campus_address=campus_address,
        is_super_admin=is_super_admin,
        duplicate=locals().get("duplicate", False)
    )

@admin_bp.route("/addParticipant", methods=["POST"])
def addParticipant():
    if "admin_username" not in session:
        return redirect(url_for("admin.login"))

    fullname = request.form["fullname"].strip().upper()
    exam_id = request.form["exam_id"].strip()
    gender = request.form["gender"]
    email = request.form["email"].strip()
    school_year = request.form["school_year"].strip()

    admin_username = session["admin_username"]

    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # Identify if super or sub admin
        cur.execute("SELECT id, campus FROM super_admin WHERE username = %s", (admin_username,))
        super_admin = cur.fetchone()
        if super_admin:
            added_by_id = super_admin[0]
            added_by_type = "super"
            admin_campus = super_admin[1] or "ALL"
        else:
            cur.execute("SELECT id, campus FROM admin WHERE username = %s", (admin_username,))
            sub_admin = cur.fetchone()
            if not sub_admin:
                cur.close()
                conn.close()
                return redirect(url_for("admin.dashboard", error="Admin not found"))
            added_by_id = sub_admin[0]
            added_by_type = "sub"
            admin_campus = sub_admin[1]

        # Check duplicate exam_id or email
        cur.execute("SELECT 1 FROM student WHERE exam_id = %s OR email = %s", (exam_id, email))
        if cur.fetchone():
            cur.close()
            conn.close()
            return redirect(url_for("admin.dashboard", error="❌ Examination ID or Email already exists!"))

        # Insert participant
        cur.execute("""
            INSERT INTO student 
                (fullname, exam_id, gender, email, school_year, campus, added_by, added_by_type)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (fullname, exam_id, gender, email, school_year, admin_campus, added_by_id, added_by_type))

        # Log admin action
        cur.execute("""
            INSERT INTO admin_logs (admin_username, campus, action)
            VALUES (%s, %s, %s)
        """, (admin_username, admin_campus, f"Added new student '{fullname}'"))

        conn.commit()

        # Fetch updated dashboard stats
        cur.execute("""
            SELECT COUNT(*) FROM student
            WHERE campus = %s AND EXTRACT(YEAR FROM created_at) = %s
        """, (admin_campus, datetime.now().year))
        total_students = cur.fetchone()[0]

        cur.execute("""
            SELECT COUNT(*) 
            FROM student s
            LEFT JOIN student_survey_answer a
                ON a.student_id = s.id OR a.exam_id = s.exam_id
            WHERE s.campus = %s
            AND EXTRACT(YEAR FROM s.created_at) = %s
            AND (a.preferred_program IS NULL OR a.preferred_program = '')
        """, (admin_campus, datetime.now().year))
        pending_students = cur.fetchone()[0]

        cur.execute("""
            SELECT COUNT(DISTINCT admin_username)
            FROM admin_logs
            WHERE created_at >= NOW() - INTERVAL '1 month'
        """)
        active_admins = cur.fetchone()[0]

        cur.execute("""
            SELECT a.fullname,
                   CASE 
                       WHEN l.last_login >= NOW() - INTERVAL '1 month' THEN 'Active'
                       ELSE 'Inactive'
                   END AS status
            FROM admin a
            LEFT JOIN (
                SELECT admin_username, MAX(created_at) AS last_login
                FROM admin_logs
                GROUP BY admin_username
            ) l ON a.username = l.admin_username
            ORDER BY a.fullname ASC
        """)
        admins = cur.fetchall()

        cur.close()
        conn.close()

        return render_template(
            "admin/dashboard.html",
            admin_username=admin_username,
            fullname=admin_username,
            admin_campus=admin_campus,
            total_students=total_students,
            pending_students=pending_students,
            active_admins=active_admins,
            year=datetime.now().year,
            available_years=[datetime.now().year],
            searched_students=[],
            search_query="",
            admins=admins,
            success=True,
            message=f"Participant added successfully!"
        )

    except Exception as e:
        return render_template(
            "admin/dashboard.html",
            manual_error=f"⚠️ Error: {str(e)}"
        )

@admin_bp.route("/upload", methods=["POST"])
def upload():
    if "admin_username" not in session:
        return redirect(url_for("admin.login"))

    if "file" not in request.files:
        return redirect(url_for("admin.dashboard", error="No file part"))

    file = request.files["file"]
    if file.filename == "":
        return redirect(url_for("admin.dashboard", error="No selected file"))

    if not allowed_file(file.filename):
        return redirect(url_for(
            "admin.dashboard",
            error="Only Excel files (.xlsx, .xls) are allowed"
        ))

    admin_username = session["admin_username"]

    try:
        df = pd.read_excel(file, dtype=str)
        df.columns = df.columns.str.lower().str.strip()

        # Enforce exact required columns
        required_cols = ["exam_id", "fullname", "email", "gender", "school_year"]
        if list(df.columns) != required_cols:
            return redirect(url_for(
                "admin.dashboard",
                error=f"Excel must contain exactly these columns in this order: {', '.join(required_cols)}"
            ))

        conn = get_db_connection()
        cur = conn.cursor()

        # Identify if super admin
        cur.execute("SELECT id, campus FROM super_admin WHERE username = %s", (admin_username,))
        super_admin = cur.fetchone()
        if super_admin:
            added_by_id = super_admin[0]
            added_by_type = "super"
            admin_campus = super_admin[1] or "ALL"
        else:
            # Then check sub admin
            cur.execute("SELECT id, campus FROM admin WHERE username = %s", (admin_username,))
            sub_admin = cur.fetchone()
            if not sub_admin:
                cur.close()
                conn.close()
                return redirect(url_for("admin.dashboard", error="Admin not found"))
            added_by_id = sub_admin[0]
            added_by_type = "sub"
            admin_campus = sub_admin[1]

        inserted = 0
        skipped = 0

        for _, row in df.iterrows():
            exam_id = (row.get("exam_id") or "").strip()
            fullname = (row.get("fullname") or "").strip().upper()
            email = (row.get("email") or "").strip()
            gender = (row.get("gender") or "").strip()
            school_year = (row.get("school_year") or "").strip()

            # Skip invalid rows
            if not all([exam_id, fullname, email, gender, school_year]) or "-" not in school_year:
                skipped += 1
                continue

            # Check duplicates
            cur.execute("SELECT 1 FROM student WHERE exam_id = %s OR email = %s", (exam_id, email))
            if cur.fetchone():
                skipped += 1
                continue

            # Insert student with added_by_id and added_by_type
            cur.execute("""
                INSERT INTO student
                    (exam_id, fullname, email, gender, campus, added_by, added_by_type, school_year)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (exam_id, fullname, email, gender, admin_campus, added_by_id, added_by_type, school_year))
            inserted += 1

        # Log admin action
        if inserted > 0:
            cur.execute("""
                INSERT INTO admin_logs (admin_username, campus, action)
                VALUES (%s, %s, %s)
            """, (admin_username, admin_campus, f"Added {inserted} new student(s) through Excel upload"))

        conn.commit()
        cur.close()
        conn.close()

        # Redirect to respondents page like addParticipant
        return redirect(url_for(
            "admin.dashboard",
            success=1,
            message=f"Upload complete! Inserted: {inserted}, Skipped: {skipped}"
        ))

    except Exception as e:
        return redirect(url_for("admin.dashboard", error=f"Error reading Excel file: {str(e)}"))

PER_PAGE = 20

@admin_bp.route("/respondents")
def respondents():
    if "admin_username" not in session:
        return redirect(url_for("admin.login"))

    username = session["admin_username"]

    conn = get_db_connection()
    cur = conn.cursor()

    # Check super admin
    cur.execute("SELECT username, campus FROM super_admin WHERE username = %s", (username,))
    super_admin = cur.fetchone()
    is_super_admin = bool(super_admin)

    if is_super_admin:
        admin_campus = super_admin[1]
        # fetch all campuses for dropdown
        cur.execute("SELECT campus_name FROM campus ORDER BY campus_name ASC")
        campuses = [c[0] for c in cur.fetchall()]
    else:
        cur.execute("SELECT username, campus FROM admin WHERE username = %s", (username,))
        admin = cur.fetchone()
        if not admin:
            cur.close()
            conn.close()
            return redirect(url_for("admin.login"))
        admin_campus = admin[1]
        campuses = [{"campus_name": admin_campus}]  # only their campus

    cur.execute("""
        SELECT campus_name, campus_address 
        FROM campus 
        WHERE campus_name = %s
    """, (admin_campus,))

    campus_data = cur.fetchone()

    if campus_data:
        campus_name = campus_data[0]
        campus_address = campus_data[1]
    else:
        campus_name = admin_campus
        campus_address = ""

    selected_campus = request.args.get("campus", "")
    selected_program = request.args.get("program", "")
    search_query = request.args.get("q", "").strip()
    status_filter = request.args.get("status", "")
    page = request.args.get("page", 1, type=int)

    # Fetch programs dynamically based on campus
    if is_super_admin:
        if selected_campus:
            cur.execute("""
                SELECT DISTINCT program_name FROM program WHERE campus = %s
                UNION
                SELECT DISTINCT preferred_program
                FROM student_survey_answer sa
                JOIN student s ON sa.exam_id = s.exam_id
                WHERE s.campus = %s AND preferred_program IS NOT NULL
                ORDER BY 1;
            """, (selected_campus, selected_campus))
        else:
            cur.execute("""
                SELECT DISTINCT program_name FROM program
                UNION
                SELECT DISTINCT preferred_program
                FROM student_survey_answer
                WHERE preferred_program IS NOT NULL
                ORDER BY 1;
            """)
    else:
        cur.execute("""
            SELECT DISTINCT program_name FROM program WHERE campus = %s
            UNION
            SELECT DISTINCT preferred_program
            FROM student_survey_answer sa
            JOIN student s ON sa.exam_id = s.exam_id
            WHERE s.campus = %s AND preferred_program IS NOT NULL
            ORDER BY 1;
        """, (admin_campus, admin_campus))

    programs = [row[0] for row in cur.fetchall()]

    cur.execute("""
        SELECT DISTINCT school_year
        FROM student
        WHERE school_year IS NOT NULL
        ORDER BY school_year DESC;
    """)
    available_years = [row[0] for row in cur.fetchall()]

    # default selection
    selected_year = request.args.get("year")
    if not selected_year:
        selected_year = available_years[0] if available_years else None

    params = [selected_year]
    conditions = []

    if is_super_admin:
        if selected_campus:
            conditions.append("s.campus = %s")
            params.append(selected_campus)
    else:
        conditions.append("s.campus = %s")
        params.append(admin_campus)

    if selected_program:
        conditions.append("TRIM(sa.preferred_program) ILIKE TRIM(%s)")
        params.append(selected_program)

    if search_query:
        conditions.append("(s.fullname ILIKE %s OR s.exam_id ILIKE %s)")
        params.extend([f"%{search_query}%", f"%{search_query}%"])

    where_clause = " AND ".join(conditions)
    if where_clause:
        where_clause = "AND " + where_clause

    sql = f"""
        SELECT s.exam_id, s.fullname, sa.preferred_program,
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
        LEFT JOIN student_survey_answer sa ON s.exam_id = sa.exam_id
        WHERE s.school_year = %s
        {where_clause}
        ORDER BY s.fullname ASC;
    """

    cur.execute(sql, params)
    raw_students = cur.fetchall()

    students = []

    for row in raw_students:
        exam_id, fullname, preferred_program, *pairs = row
        answers_clean = [p for p in pairs if p]

        top_letters = [l for l, _ in Counter(answers_clean).most_common(3)]
        top_letters = [l.strip().upper() for l in top_letters]

        program_letters = []

        if preferred_program:
            cur.execute("""
                SELECT category_letter 
                FROM program 
                WHERE LOWER(TRIM(program_name)) = LOWER(TRIM(%s))
                AND LOWER(TRIM(campus)) = LOWER(TRIM(%s))
                LIMIT 1
            """, (preferred_program, admin_campus if not is_super_admin else (selected_campus or admin_campus)))

            result = cur.fetchone()

            if result and result[0]:
                program_letters = [l.strip().upper() for l in result[0].split(",")]
            else:
                program_letters = []

        common_letters = set(top_letters) & set(program_letters)

        if not preferred_program and not answers_clean:
            match_status = "——"
        elif common_letters:
            match_status = "Match"
        else:
            match_status = "Not Match"

        students.append((exam_id, fullname, preferred_program, match_status))

    cur.close()
    conn.close()

    if status_filter == "match":
        students = [s for s in students if s[3] == "Match"]
    elif status_filter == "not_match":
        students = [s for s in students if s[3] == "Not Match"]

    total_students = len(students)
    total_pages = ceil(total_students / PER_PAGE)
    start = (page - 1) * PER_PAGE
    end = start + PER_PAGE
    students_paginated = students[start:end]

    return render_template(
        "admin/respondents.html",
        admin_username=username,
        campus_name=campus_name,
        campus_address=campus_address,
        admin_campus=admin_campus,
        is_super_admin=is_super_admin,
        available_years=available_years,
        year=selected_year,
        students=students_paginated,
        search_query=search_query,
        status_filter=status_filter,
        selected_campus=selected_campus,
        selected_program=selected_program,
        programs=programs,
        page=page,
        total_pages=total_pages,
        campuses=campuses
    )

@admin_bp.route("/adminSurveyResult")
def adminSurveyResult():
    exam_id = request.args.get("exam_id")
    if not exam_id:
        flash("Invalid request. No Exam ID provided.")
        return redirect(url_for("admin.dashboard"))
    
    admin_username = session["admin_username"]

    conn = get_db_connection()
    cur = conn.cursor()

    # --- Get admin info ---
    cur.execute("SELECT fullname, campus FROM super_admin WHERE username = %s", (admin_username,))
    super_admin = cur.fetchone()
    is_super_admin = bool(super_admin)

    if super_admin:
        admin_fullname = super_admin[0]
        admin_campus = super_admin[1]
    else:
        cur.execute("SELECT fullname, campus FROM admin WHERE username = %s", (admin_username,))
        admin = cur.fetchone()
        if not admin:
            cur.close()
            conn.close()
            return redirect(url_for("admin.login"))
        admin_fullname = admin[0]
        admin_campus = admin[1]

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
        LEFT JOIN student_survey_answer sa ON s.exam_id = sa.exam_id
        LEFT JOIN campus c ON s.campus = c.campus_name
        WHERE s.exam_id = %s;
    """, (exam_id,))
    
    row = cur.fetchone()

    if not row:
        return "No survey results found."

    year = row[2]

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
        "ai_explanation": format_ai_explanation_for_pdf(row[9]),
        "answers": [row[i] for i in range(10, 96)]
    }

    # --- Fetch all campuses and addresses ---
    cur.execute("SELECT campus_name, campus_address FROM campus")
    campus_info = {c[0]: c[1] for c in cur.fetchall()}

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

    conn.close()

    return render_template(
        "admin/adminSurveyResult.html",
        admin_username=session["admin_username"],
        guidance_counselor=student_results["guidance_counselor"],
        campus_name=student_results["campus_name"],
        campus_address=student_results["campus_address"],
        admin_campus=admin_campus,
        is_super_admin=is_super_admin,
        student_results=student_results,
        student_campus=student_results["campus"],
        campus_info=campus_info,
        top_letters=top_letters,
        letter_descriptions=letter_descriptions,
        ai_explanation=student_results["ai_explanation"],
        match_status=match_status,
        predicted_programs=predicted_programs,
        year=year
    )

@admin_bp.route('/download_result/<exam_id>')
def download_result(exam_id):
    if not exam_id:
        flash("Invalid request.")
        return redirect(url_for('admin.dashboard'))

    admin_username = session.get("admin_username")
    if not admin_username:
        return redirect(url_for("admin.login"))

    conn = get_db_connection()
    cur = conn.cursor()

    # --- Get admin info ---
    cur.execute("SELECT fullname, campus FROM super_admin WHERE username = %s", (admin_username,))
    super_admin = cur.fetchone()
    if super_admin:
        admin_fullname, admin_campus = super_admin
    else:
        cur.execute("SELECT fullname, campus FROM admin WHERE username = %s", (admin_username,))
        admin = cur.fetchone()
        if not admin:
            cur.close()
            conn.close()
            return redirect(url_for("admin.login"))
        admin_fullname, admin_campus = admin

    # --- Get student + survey data ---
    cur.execute("""
        SELECT s.exam_id, s.fullname, s.school_year, s.campus, s.photo,
               c.campus_name, c.guidance_counselor,
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
        LEFT JOIN student_survey_answer sa ON s.exam_id = sa.exam_id
        LEFT JOIN campus c ON s.campus = c.campus_name
        WHERE s.exam_id = %s
    """, (exam_id,))

    row = cur.fetchone()
    if not row:
        cur.close()
        conn.close()
        return "Survey results not found", 404

    year = row[2]
    student_data = {
        "exam_id": row[0],
        "fullname": row[1],
        "school_year": row[2],
        "campus": row[3],
        "photo": row[4],
        "campus_name": row[5],
        "guidance_counselor": row[6],
        "preferred_program": row[7],
        "ai_explanation": format_ai_explanation_for_pdf(row[8]),
        "answers": [row[i] for i in range(9, 95)]
    }

    # --- Campus info ---
    cur.execute("""
        SELECT campus_name, campus_address
        FROM campus
        WHERE campus_name = %s
        LIMIT 1
    """, (student_data["campus"],))

    campus_row = cur.fetchone()

    campus_name = campus_row[0] if campus_row else student_data["campus"]
    campus_address = campus_row[1] if campus_row else ""

    cur.execute("SELECT campus_name, campus_address FROM campus")
    campus_info = {c[0]: c[1] for c in cur.fetchall()}

    answers_clean = student_data["answers"]
    preferred = student_data["preferred_program"]
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
        """, (preferred, student_data["campus"]))

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

    # --- Predicted programs ---
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
        values.append(student_data["campus"])
        cur.execute(query, values)
        predicted_programs = cur.fetchall()

    # --- Images ---
    cpsu_logo = image_to_base64("cpsulogo.png")
    bagong_logo = image_to_base64("bagong-pilipinas-logo.png")
    safe_logo = image_to_base64("logo.png")
    student_photo_base64 = student_photo_to_base64(student_data.get("photo"))

    cur.close()
    conn.close()
    
    html = render_template(
        "admin/adminSurveyResultPDF.html",
        guidance_counselor=student_data["guidance_counselor"],
        campus_name=student_data["campus_name"],
        student_data=student_data,
        top_letters=top_letters,
        match_status=match_status,
        student_campus=student_data["campus"],
        campus_info=campus_info,
        letter_descriptions=letter_descriptions,
        ai_explanation=student_data["ai_explanation"],
        year=year,
        predicted_programs=predicted_programs,
        cpsu_logo_base64=cpsu_logo,
        bagong_logo_base64=bagong_logo,
        safe_logo_base64=safe_logo,
        student_photo_base64=student_photo_base64
    )

    # ✅ GENERATE PDF
    pdf = generate_pdf_reportlab(
        {
            **student_data,
            "top_letters": top_letters,
            "match_status": match_status,
            "predicted_programs": predicted_programs,
            "campus_name": campus_name,
            "campus_address": campus_address
        },
        {
            "cpsu": cpsu_logo,
            "bagong": bagong_logo,
            "safe": safe_logo
        },
        student_photo_base64
    )

    safe_name = secure_filename(student_data["fullname"])
    filename = f"Career_Interest_Result_{exam_id}_{safe_name}.pdf"
    response = make_response(pdf)
    response.headers["Content-Type"] = "application/pdf"
    response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'

    return response

PER_PAGE = 20

@admin_bp.route("/adminInventory")
def adminInventory():
    if "admin_username" not in session:
        return redirect(url_for("admin.login"))

    username = session["admin_username"]

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    # Check super admin
    cur.execute("SELECT id, campus FROM super_admin WHERE username = %s", (username,))
    super_admin_row = cur.fetchone()
    is_super_admin = bool(super_admin_row)

    if is_super_admin:
        admin_campus = super_admin_row["campus"]
        # Fetch all campuses for dropdown
        cur.execute("SELECT campus_name FROM campus ORDER BY campus_name ASC")
        campuses = cur.fetchall()
    else:
        # Sub admin
        cur.execute("SELECT id, campus FROM admin WHERE username = %s", (username,))
        admin_row = cur.fetchone()
        if not admin_row:
            cur.close()
            conn.close()
            return redirect(url_for("admin.login"))
        admin_campus = admin_row["campus"]
        campuses = [{"campus_name": admin_campus}]

    cur.execute("""
        SELECT campus_name, campus_address 
        FROM campus 
        WHERE campus_name = %s
    """, (admin_campus,))

    campus_data = cur.fetchone()

    if campus_data:
        campus_name = campus_data["campus_name"]
        campus_address = campus_data["campus_address"]
    else:
        campus_name = admin_campus
        campus_address = ""

    cur.execute("""
        SELECT DISTINCT school_year
        FROM student
        WHERE school_year IS NOT NULL
        ORDER BY school_year DESC;
    """)
    available_years = [row[0] for row in cur.fetchall()]

    selected_year = request.args.get("year", type=int) or (available_years[0] if available_years else None)
    selected_campus = request.args.get("campus", "")
    search_query = request.args.get("q", "")
    page = request.args.get("page", 1, type=int)
    sort = request.args.get("sort", "income_asc")

    query = """
        SELECT 
            s.id,
            s.exam_id,
            s.fullname,
            COALESCE(f.father_income, 0) + COALESCE(f.mother_income, 0) AS total_income
        FROM student s
        LEFT JOIN family_background f 
            ON f.student_id = s.id
        WHERE s.school_year = %s
          AND (%s = '' OR s.fullname ILIKE %s OR s.exam_id ILIKE %s)
    """

    params = [
        selected_year,
        search_query,
        f"%{search_query}%",
        f"%{search_query}%"
    ]

    if is_super_admin:
        if selected_campus:
            query += " AND s.campus = %s"
            params.append(selected_campus)
    else:
        query += " AND s.campus = %s"
        params.append(admin_campus)

    query += " ORDER BY s.fullname ASC"

    cur.execute(query, params)
    students = cur.fetchall()

    classified_students = []
    for row in students:
        id, exam_id, fullname, total_income = row

        if total_income == 0:
            category = "____"
            income_display = None
        else:
            income_display = total_income
            if total_income <= 10000:
                category = "Low Income"
            elif total_income <= 20000:
                category = "Lower-Middle"
            elif total_income <= 40000:
                category = "Middle"
            elif total_income <= 80000:
                category = "Middle-Upper"
            else:
                category = "High Income"

        classified_students.append(
            (id, exam_id, fullname, income_display, category)
        )

    if sort in ["name_asc", "name_desc"]:
        classified_students.sort(
            key=lambda x: x[2].lower(),
            reverse=(sort == "name_desc")
        )
    elif sort == "income_asc":
        classified_students.sort(
            key=lambda x: x[3] if x[3] is not None else float("inf")
        )
    elif sort == "income_desc":
        classified_students.sort(
            key=lambda x: -(x[3] if x[3] is not None else 0)
        )

    cur.close()
    conn.close()

    total_students = len(classified_students)
    total_pages = max(1, ceil(total_students / PER_PAGE))
    start = (page - 1) * PER_PAGE
    end = start + PER_PAGE
    students_paginated = classified_students[start:end]

    return render_template(
        "admin/adminInventory.html",
        admin_username=username,
        admin_campus=admin_campus,
        is_super_admin=is_super_admin,
        available_years=available_years,
        year=selected_year,
        students=students_paginated,
        search_query=search_query,
        page=page,
        total_pages=total_pages,
        sort=sort,
        selected_campus=selected_campus,
        campuses=campuses,
        campus_address=campus_address,
        campus_name=campus_name
    )

@admin_bp.route("/adminInventoryResult")
def adminInventoryResult():
    if "admin_username" not in session:
        return redirect(url_for("admin.login"))

    student_id = request.args.get("student_id")
    if not student_id:
        flash("Invalid request. No student ID provided.")
        return redirect(url_for("admin.adminInventory"))

    username = session["admin_username"]

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    # --- Get admin info ---
    cur.execute("SELECT id, campus FROM super_admin WHERE username = %s", (username,))
    admin = cur.fetchone()

    is_super_admin = False
    admin_campus = None

    if admin:
        is_super_admin = True
        admin_id, admin_campus = admin
    else:
        cur.execute("SELECT id, campus FROM admin WHERE username = %s", (username,))
        admin = cur.fetchone()
        if not admin:
            cur.close()
            conn.close()
            return redirect(url_for("admin.login"))
        admin_id, admin_campus = admin

    cur.execute("""
        SELECT campus_name, campus_address 
        FROM campus 
        WHERE campus_name = %s
    """, (admin_campus,))

    campus_data = cur.fetchone()

    if campus_data:
        campus_name = campus_data["campus_name"]
        campus_address = campus_data["campus_address"]
    else:
        campus_name = admin_campus
        campus_address = ""

    cur.execute("""
        SELECT 
            s.id AS id,
            s.fullname, s.gender, s.email, s.campus, s.photo,
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
        WHERE s.id = %s
    """, (student_id,))

    info = cur.fetchone()

    student_photo_base64 = None
    if info and info["photo"]:
        student_photo_base64 = student_photo_to_base64(info["photo"])

    cur.execute("SELECT campus_name, campus_address FROM campus")
    campus_info = {c[0]: c[1] for c in cur.fetchall()}

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
        "admin/adminInventoryResult.html",
        admin_username=username,
        admin_campus=admin_campus,
        is_super_admin=is_super_admin,
        info=info,
        student_photo_base64=student_photo_base64,
        selected_reasons=selected_reasons,
        other_reason=other_reason,
        other_schools_selected=other_schools_selected,
        other_school=other_school,
        campus_info=campus_info,
        campus_address=campus_address,
        campus_name=campus_name
    )

@admin_bp.route('/download_admin_inventory_pdf/<int:student_id>')
def download_admin_inventory_pdf(student_id):
    if "admin_username" not in session:
        return redirect(url_for("admin.login"))

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    cur.execute("""
        SELECT 
            s.id AS id,
            s.fullname, s.exam_id, s.gender, s.email, s.campus, s.photo,
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
            sd.study_finance, sd.course_personal_choice, sd.influenced_by,
            sd.feeling_about_course, sd.personal_choice,
            se.*, sf.*, sg.personal_description, sg.consent, sg.consent_date, sh.course_name
        FROM student s
        LEFT JOIN personal_information sa ON sa.student_id = s.id
        LEFT JOIN family_background sb ON sb.student_id = s.id
        LEFT JOIN status_of_parent sc ON sc.student_id = s.id
        LEFT JOIN academic_information sd ON sd.student_id = s.id
        LEFT JOIN behavior_information se ON se.student_id = s.id
        LEFT JOIN psychological_consultations sf ON sf.student_id = s.id
        LEFT JOIN personal_descriptions sg ON sg.student_id = s.id
        LEFT JOIN course sh ON sh.student_id = s.id
        WHERE s.id = %s
    """, (student_id,))

    info = cur.fetchone()
    if not info:
        return "Student Inventory results not found.", 404

    student_photo_base64 = None
    if info and info["photo"]:
        student_photo_base64 = student_photo_to_base64(info["photo"])

    cur.execute("SELECT campus_name, campus_address FROM campus")
    campus_info = {c[0]: c[1] for c in cur.fetchall()}

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

    selected_reasons = enroll_reason[0].split(",") if enroll_reason and enroll_reason[0] else []
    other_reason = enroll_reason[1] if enroll_reason else ""

    other_schools_selected = other_school_data[0].split(",") if other_school_data and other_school_data[0] else []
    other_school = other_school_data[1] if other_school_data else ""

    cpsu_logo_base64 = image_to_base64("cpsulogo.png")

    # ✅ GENERATE PDF
    pdf = generate_pdf_inventory_reportlab(
        info,
        cpsu_logo_base64,
        student_photo_base64,
        campus_info,
        selected_reasons,
        other_reason,
        other_schools_selected,
        other_school
    )

    if not pdf:
        return "Error generating PDF", 500

    filename = secure_filename(f"Inventory_Result_{info['fullname']}.pdf")

    response = make_response(pdf)
    response.headers.clear()  # 🔥 clears any duplicate headers
    response.headers["Content-Type"] = "application/pdf"
    response.headers["Content-Disposition"] = f"attachment; filename={filename}"

    return response

@admin_bp.route("/interviewAI/<int:student_id>")
def interviewAI(student_id):
    if "admin_username" not in session:
        return jsonify({"error": "Unauthorized"}), 403

    username = session["admin_username"]

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    # ===== ROLE CHECK =====
    cur.execute("SELECT campus FROM super_admin WHERE username = %s", (username,))
    super_admin = cur.fetchone()

    print("SESSION USERNAME:", username)
    print("SUPER ADMIN RESULT:", super_admin)

    if super_admin:
        admin_campus = super_admin["campus"]
        is_super_admin = True
    else:
        cur.execute("SELECT campus FROM admin WHERE username = %s", (username,))
        sub_admin = cur.fetchone()

        if not sub_admin:
            return jsonify({"error": "Unauthorized"}), 403

        admin_campus = sub_admin["campus"]
        is_super_admin = False

    # ===== STUDENT ACCESS CHECK =====
    cur.execute("SELECT campus FROM student WHERE id = %s", (student_id,))
    student = cur.fetchone()

    if not student:
        return jsonify({"error": "Student not found"}), 404

    if not is_super_admin and student["campus"] != admin_campus:
        return jsonify({"error": "Forbidden"}), 403

    try:
        # ===== CHECK EXISTING (VIEW) =====
        cur.execute(
            "SELECT questions FROM interview_questions WHERE student_id = %s",
            (student_id,)
        )
        existing = cur.fetchone()

        if existing:
            data = json.loads(existing[0])

            # Safety check
            if "questions" not in data:
                return jsonify({"error": "Invalid stored data"}), 500

            return jsonify(data)

        # ===== FETCH STUDENT DATA =====
        cur.execute("""
            SELECT 
                s.fullname,
                sa.preferred_program,
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
            LEFT JOIN student_survey_answer sa ON s.id = sa.student_id
            WHERE s.id = %s
        """, (student_id,))

        row = cur.fetchone()

        if not row:
            return jsonify({"error": "Student not found"}), 404

        fullname = row[0]
        preferred_program = row[1]
        letters = [l for l in row[2:] if l]

        if not letters:
            return jsonify({"error": "No survey answers"}), 400

        # ===== PROGRAM LETTERS =====
        program_letters = []
        if preferred_program:
            cur.execute(
                "SELECT category_letter FROM program WHERE program_name = %s",
                (preferred_program,)
            )
            res = cur.fetchone()
            if res and res[0]:
                program_letters = [x.strip() for x in res[0].split(",")]

        # ===== ANALYSIS =====
        counts = Counter(letters)
        top_three = [l for l, _ in counts.most_common(3)]

        top_three_descriptions = [
            short_letter_descriptions.get(l, "Unknown")
            for l in top_three
        ]

        all_letter_descriptions = [
            short_letter_descriptions.get(l, "Unknown")
            for l in letters
        ]

        program_descriptions = [
            short_letter_descriptions.get(l, "Unknown")
            for l in program_letters
        ]

        # ===== AI PROMPT =====
        prompt = f"""
You are an expert educational guidance counselor AI.

Your job is to analyze if a student's chosen program aligns with their interests,
and generate SMART, NATURAL, and VARIED interview questions.

---

🎯 GOALS:

1. Detect alignment level:
   - STRONG MATCH → interests align well
   - PARTIAL MATCH → some overlap
   - MISMATCH → little to no overlap

2. Adjust explanation tone:
   - If mismatch is strong → clearly explain concern
   - If partial → suggest exploration
   - If strong match → reinforce decision

---

🧠 QUESTION GENERATION RULES:

Generate EXACTLY 6 questions that are:

✔ Natural and conversational (like a real counselor)
✔ NOT repetitive in structure
✔ RANDOMIZED phrasing each time
✔ Personalized using:
   - Preferred program
   - Student top interests

✔ Mix of:
   - 2 program-focused questions
   - 2 interest-based questions
   - 2 hybrid (program + interest)

✔ Use varied sentence starters such as:
   - "What draws you to..."
   - "How do you see yourself..."
   - "Have you considered..."
   - "In what ways do you think..."
   - "Would you be interested in..."
   - "Can you imagine..."

❌ DO NOT repeat patterns
❌ DO NOT make generic questions
❌ DO NOT use identical structure

---

📊 STUDENT DATA:

Student Name: {fullname}
Preferred Program: {preferred_program}

Program Category Letters: {program_letters}
Program Descriptions: {program_descriptions}

Top 3 Interest Letters: {top_three}
Top 3 Interest Descriptions: {top_three_descriptions}

---

🧩 ANALYSIS TASK:

Compare:
- Program descriptions vs student interest descriptions

Determine:
- Alignment level (strong / partial / mismatch)

---

📌 OUTPUT REQUIREMENTS:

Return STRICT JSON ONLY:

{{
  "questions": [
    "6 unique, varied, natural questions here"
  ],
  "mismatch_reason": "Clear explanation of alignment level and reasoning",
  "talking_points": [
    "3 smart counseling suggestions based on alignment level"
  ]
}}

---

💡 TALKING POINTS GUIDE:

If STRONG MATCH:
- Reinforce choice
- Suggest growth paths
- Encourage specialization

If PARTIAL:
- Suggest combining interests
- Recommend electives or minors
- Encourage exploration

If MISMATCH:
- Suggest alternative programs
- Suggest hybrid careers
- Encourage reconsideration or deeper reflection

---

⚠️ IMPORTANT:
- Use ONLY given descriptions
- Do NOT invent traits
- Do NOT mention Holland or theory names
- Keep tone supportive, not judgmental
"""

        # ===== CALL AI =====
        response = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {"role": "system", "content": "Return ONLY valid JSON."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3,
            max_tokens=1000
        )

        raw = response.choices[0].message.content.strip()

        # ===== SAFE JSON PARSE =====
        try:
            data = json.loads(raw)
        except:
            match = re.search(r"\{.*\}", raw, re.S)
            if not match:
                raise ValueError("Invalid JSON from AI")
            data = json.loads(match.group())

        # ===== SAVE (NO CONFLICT VERSION) =====
        cur.execute(
            "DELETE FROM interview_questions WHERE student_id = %s",
            (student_id,)
        )

        cur.execute(
            "INSERT INTO interview_questions (student_id, questions) VALUES (%s, %s)",
            (student_id, json.dumps(data))
        )

        conn.commit()

        return jsonify(data)

    except Exception as e:
        conn.rollback()
        print("ERROR:", e)
        return jsonify({"error": "AI generation failed"}), 500

    finally:
        cur.close()
        conn.close()

PER_PAGE = 20

@admin_bp.route("/interviewList")
def interviewList():
    if "admin_username" not in session:
        return redirect(url_for("admin.login"))

    conn = get_db_connection()
    cur = conn.cursor()
    username = session["admin_username"]

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    # Check super admin
    cur.execute("SELECT id, campus FROM super_admin WHERE username = %s", (username,))
    super_admin_row = cur.fetchone()
    is_super_admin = bool(super_admin_row)

    if is_super_admin:
        admin_campus = super_admin_row["campus"]
        # Fetch all campuses for dropdown
        cur.execute("SELECT campus_name FROM campus ORDER BY campus_name ASC")
        campuses = cur.fetchall()
    else:
        # Sub admin
        cur.execute("SELECT id, campus FROM admin WHERE username = %s", (username,))
        admin_row = cur.fetchone()
        if not admin_row:
            cur.close()
            conn.close()
            return redirect(url_for("admin.login"))
        admin_campus = admin_row["campus"]
        campuses = [{"campus_name": admin_campus}]

    cur.execute("""
        SELECT campus_name, campus_address 
        FROM campus 
        WHERE campus_name = %s
    """, (admin_campus,))

    campus_data = cur.fetchone()

    if campus_data:
        campus_name = campus_data["campus_name"]
        campus_address = campus_data["campus_address"]
    else:
        campus_name = admin_campus
        campus_address = ""

    selected_campus = request.args.get("campus", "")
    search_query = request.args.get("q", "")
    page = request.args.get("page", 1, type=int)

    # Fetch available years first
    cur.execute("""
        SELECT DISTINCT school_year
        FROM student
        WHERE school_year IS NOT NULL
        ORDER BY school_year DESC;
    """)
    available_years = [row[0] for row in cur.fetchall()]

    # Get selected year from query string, default to latest available year
    selected_year = request.args.get("year") or (available_years[0] if available_years else None)

    query = """
        SELECT 
            s.id,
            s.exam_id,
            s.fullname,
            sa.preferred_program,
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
            sa.pair86,
            sch.schedule_date,
            sch.start_time,
            sch.end_time,
            CASE 
                WHEN iq.student_id IS NOT NULL THEN TRUE 
                ELSE FALSE 
            END AS has_interview
        FROM student s
        LEFT JOIN student_survey_answer sa ON s.id = sa.student_id
        LEFT JOIN student_schedules ss ON s.id = ss.student_id
        LEFT JOIN schedules sch ON ss.schedule_id = sch.id
        LEFT JOIN interview_questions iq ON s.id = iq.student_id
        WHERE s.school_year = %s
        AND (%s = '' OR s.fullname ILIKE %s OR s.exam_id ILIKE %s)
    """

    params = [
        selected_year,
        search_query,
        f"%{search_query}%",
        f"%{search_query}%"
    ]

    if is_super_admin:
        if selected_campus:
            query += " AND s.campus = %s"
            params.append(selected_campus)
    else:
        query += " AND s.campus = %s"
        params.append(admin_campus)

    query += " ORDER BY s.fullname ASC"

    cur.execute(query, tuple(params))
    raw_students = cur.fetchall()

    students = []

    for row in raw_students:
        student_id, exam_id, fullname, preferred_program, *rest = row
        pairs = rest[:-4]
        schedule_date, start_time, end_time, has_interview = rest[-4:]

        answers_clean = [p for p in pairs if p]
        top_letters = [l for l, _ in Counter(answers_clean).most_common(3)]

        # ✅ CLEAN top letters
        top_letters = [l.strip().upper() for l in top_letters]

        program_letters = []

        # ✅ Determine correct campus to use
        student_campus = selected_campus if is_super_admin and selected_campus else admin_campus

        if preferred_program:
            cur.execute("""
                SELECT category_letter 
                FROM program 
                WHERE LOWER(TRIM(program_name)) = LOWER(TRIM(%s))
                AND LOWER(TRIM(campus)) = LOWER(TRIM(%s))
                LIMIT 1
            """, (preferred_program, student_campus))

            res = cur.fetchone()

            if res and res[0]:
                program_letters = [l.strip().upper() for l in res[0].split(",")]

        # ✅ Better match logic
        common_letters = set(top_letters) & set(program_letters)

        if not preferred_program and not answers_clean:
            match_status = "Not Yet Answer"
        elif common_letters:
            match_status = "Match"
        else:
            match_status = "Not Match"

        if match_status == "Not Match":
            schedule_str = (
                f"{schedule_date.strftime('%Y-%m-%d')} "
                f"({start_time.strftime('%I:%M %p')} - {end_time.strftime('%I:%M %p')})"
                if schedule_date else None
            )

            students.append((student_id, exam_id, fullname, schedule_str, has_interview))

    cur.close()
    conn.close()

    total_students = len(students)
    total_pages = max(1, ceil(total_students / PER_PAGE))
    start = (page - 1) * PER_PAGE
    end = start + PER_PAGE

    return render_template(
        "admin/interviewList.html",
        admin_username=username,
        admin_campus=admin_campus,
        selected_campus=selected_campus,
        available_years=available_years,
        year=selected_year,
        students=students[start:end],
        search_query=search_query,
        page=page,
        total_pages=total_pages,
        is_super_admin=is_super_admin,
        campuses=campuses,
        campus_name=campus_name,
        campus_address=campus_address
    )

@admin_bp.route("/save_schedule", methods=["POST"])
def save_schedule():
    if "admin_username" not in session:
        return redirect(url_for("admin.login"))

    data = request.get_json()
    schedule_date = data.get("date")
    start_time = data.get("start_time")
    end_time = data.get("end_time")
    slot_count = data.get("slot_count")

    admin_username = session["admin_username"]

    conn = get_db_connection()
    cur = conn.cursor()

    try:
        cur.execute("""
            SELECT campus FROM admin WHERE username = %s
            UNION
            SELECT campus FROM super_admin WHERE username = %s
        """, (admin_username, admin_username))

        result = cur.fetchone()

        if not result:
            return jsonify({
                "status": "error",
                "error": "Admin campus not found."
            }), 400

        admin_campus = result[0]

        cur.execute("""
            SELECT 1 FROM schedules 
            WHERE schedule_date = %s AND campus = %s
        """, (schedule_date, admin_campus))

        if cur.fetchone():
            return jsonify({
                "status": "error",
                "error": "A schedule already exists for this date in your campus."
            }), 400

        cur.execute("""
            INSERT INTO schedules
                (schedule_date, start_time, end_time, slot_count, admin_username, campus)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (
            schedule_date,
            start_time,
            end_time,
            slot_count,
            admin_username,
            admin_campus
        ))

        cur.execute("""
            INSERT INTO admin_logs (admin_username, campus, action)
            VALUES (%s, %s, %s)
        """, (
            admin_username,
            admin_campus,
            f"Added new interview date '{schedule_date}' for campus '{admin_campus}'"
        ))

        conn.commit()

        return jsonify({
            "status": "success",
            "message": "Schedule saved successfully!"
        }), 200

    except psycopg2.Error as e:
        conn.rollback()
        return jsonify({
            "status": "error",
            "error": str(e)
        }), 500

    finally:
        cur.close()
        conn.close()

@admin_bp.route("/visualization")
def visualization():
    if "admin_username" not in session:
        return redirect(url_for("admin.login"))

    username = session["admin_username"]
    conn = get_db_connection()
    cur = conn.cursor()

    # --- Check if super admin ---
    cur.execute("SELECT id, campus FROM super_admin WHERE username = %s", (username,))
    admin = cur.fetchone()
    is_super_admin = bool(admin)
    admin_campus = admin[1] if admin else None

    if not is_super_admin:
        cur.execute("SELECT id, campus FROM admin WHERE username = %s", (username,))
        admin = cur.fetchone()
        if not admin:
            cur.close()
            conn.close()
            return redirect(url_for("admin.login"))
        admin_campus = admin[1]

    selected_year = request.args.get("year", str(datetime.now().year))
    selected_gender = request.args.get("gender", "All")
    selected_campus = request.args.get("campus", "")

    # --- Fetch available campuses ---
    available_campuses = []
    if is_super_admin:
        cur.execute("SELECT DISTINCT campus FROM student ORDER BY campus ASC;")
        available_campuses = [r[0] for r in cur.fetchall()]

    # Fetch available school_years in descending order
    year_query = "SELECT DISTINCT school_year FROM student WHERE school_year IS NOT NULL"
    params = []
    if not is_super_admin:
        year_query += " AND campus = %s"
        params.append(admin_campus)
    elif selected_campus:
        year_query += " AND campus = %s"
        params.append(selected_campus)
    year_query += " ORDER BY school_year DESC"  # highest first
    cur.execute(year_query, tuple(params))
    available_years = [str(row[0]) for row in cur.fetchall()]  # cast to string

    # Handle selected_year
    selected_year = request.args.get("year")
    if not selected_year:
        selected_year = available_years[0] if available_years else "All"
    elif selected_year != "All" and selected_year not in available_years:
        selected_year = available_years[0] if available_years else "All"

    # --- Fetch programs ---
    cur.execute("SELECT id, program_name, color, campus FROM program ORDER BY id ASC;")
    all_programs = cur.fetchall()

    # --- Fetch data for visualization ---
    def fetch_data_for_year(year=None, gender=None, campus_filter=None):
        filters = []
        params = []

        # --- CAMPUS FILTER ---
        if campus_filter:
            filters.append("s.campus = %s")
            params.append(campus_filter)

        # --- YEAR FILTER ---
        if year and str(year).lower() != "all":
            filters.append("s.school_year = %s")
            params.append(str(year))

        # --- GENDER FILTER ---
        if gender and gender != "All":
            filters.append("LOWER(s.gender) = LOWER(%s)")
            params.append(gender)

        where_clause = "WHERE " + " AND ".join(filters) if filters else ""

        # --- Preferred programs ---
        cur.execute(f"""
            SELECT COALESCE(ssa.preferred_program, 'Unknown'), COUNT(*)
            FROM student_survey_answer ssa
            JOIN student s ON ssa.student_id = s.id
            {where_clause}
            GROUP BY COALESCE(ssa.preferred_program, 'Unknown')
            ORDER BY COUNT(*) DESC
        """, tuple(params))
        preferred = cur.fetchall()

        # --- Letter counts ---
        letter_cols = [f"pair{i}" for i in range(1, 87)]
        unions = [
            f"""SELECT {c} AS letter
                FROM student_survey_answer ssa
                JOIN student s ON ssa.student_id = s.id
                {where_clause} AND {c} BETWEEN 'A' AND 'R'
            """
            for c in letter_cols
        ]

        cur.execute(f"""
            SELECT letter, COUNT(*) FROM (
                {" UNION ALL ".join(unions)}
            ) t
            GROUP BY letter
            ORDER BY COUNT(*) DESC
            LIMIT 18
        """, tuple(params * len(letter_cols)))
        letters = cur.fetchall()

        return {
            "year": str(year) if year else "All",
            "gender": gender or "All",
            "campus": campus_filter if campus_filter else "ALL",
            "preferred_labels": [r[0] for r in preferred],
            "preferred_counts": [r[1] for r in preferred],
            "top_labels": [r[0] for r in letters],
            "top_counts": [r[1] for r in letters]
        }

    all_years_data = []

    if is_super_admin:

        # 👉 If specific campus selected
        if selected_campus:
            if selected_year.lower() == "all":
                for y in available_years:
                    all_years_data.append(
                        fetch_data_for_year(y, selected_gender, selected_campus)
                    )
            else:
                all_years_data.append(
                    fetch_data_for_year(selected_year, selected_gender, selected_campus)
                )

        # 👉 ALL CAMPUSES → SEPARATE PER CAMPUS
        else:
            for campus in available_campuses:
                if selected_year.lower() == "all":
                    for y in available_years:
                        all_years_data.append(
                            fetch_data_for_year(y, selected_gender, campus)
                        )
                else:
                    all_years_data.append(
                        fetch_data_for_year(selected_year, selected_gender, campus)
                    )

    else:
        # 👉 SUB ADMIN → ONLY THEIR CAMPUS
        if selected_year.lower() == "all":
            for y in available_years:
                all_years_data.append(
                    fetch_data_for_year(y, selected_gender, admin_campus)
                )
        else:
            all_years_data.append(
                fetch_data_for_year(selected_year, selected_gender, admin_campus)
            )

    # --- Campus details ---
    campus_to_fetch = selected_campus or (admin_campus if admin_campus != "ALL" else None)
    if campus_to_fetch:
        cur.execute("SELECT campus_name, campus_address FROM campus WHERE campus_name = %s", (campus_to_fetch,))
        campus_data = cur.fetchone()
        campus_name = campus_data[0] if campus_data else campus_to_fetch
        campus_address = campus_data[1] if campus_data else ""
    else:
        campus_name = "ALL CAMPUSES"
        campus_address = ""

    cur.close()
    conn.close()

    return render_template(
        "admin/visualization.html",
        admin_username=username,
        admin_campus=admin_campus,
        is_super_admin=is_super_admin,
        available_campuses=available_campuses,
        selected_campus=selected_campus,
        available_years=available_years,
        year=selected_year,
        gender=selected_gender,
        all_years_data=all_years_data,
        all_programs=all_programs,
        letter_descriptions=letter_descriptions,
        campus_name=campus_name,
        campus_address=campus_address
    )

@admin_bp.route("/adminProfile", methods=["GET", "POST"])
def adminProfile():
    if "admin_username" not in session:
        return redirect(url_for("admin.login"))

    username = session["admin_username"]

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT fullname, username, email, campus
        FROM super_admin
        WHERE username = %s
    """, (username,))
    admin = cur.fetchone()

    is_super_admin = False
    table_name = "admin"

    if admin:
        is_super_admin = True
        table_name = "super_admin"
    else:
        cur.execute("""
            SELECT fullname, username, email, campus
            FROM admin
            WHERE username = %s
        """, (username,))
        admin = cur.fetchone()

        if not admin:
            cur.close()
            conn.close()
            return redirect(url_for("admin.login"))

    admin_fullname, admin_username, admin_email, admin_campus = admin

    # Fetch campus details
    if admin_campus and admin_campus != "ALL":
        cur.execute("""
            SELECT campus_name, campus_address
            FROM campus
            WHERE campus_name = %s
        """, (admin_campus,))
        
        campus_data = cur.fetchone()
        
        if campus_data:
            campus_name = campus_data[0]
            campus_address = campus_data[1]
        else:
            campus_name = admin_campus
            campus_address = ""
    else:
        campus_name = "ALL CAMPUSES"
        campus_address = ""

    if request.method == "POST":
        fullname = request.form.get("fullname")
        new_email = request.form.get("email")

        cur.execute(f"""
            UPDATE {table_name}
            SET fullname = %s
            WHERE username = %s
        """, (fullname, username))
        conn.commit()

        if new_email != admin_email:
            otp = generate_otp()

            session["email_change"] = {
                "otp": otp,
                "new_email": new_email,
                "username": username,
                "table": table_name,
                "time": time.time(),
                "attempts": 0
            }

            sent = send_otp_email(new_email, otp)

            if not sent:
                error = "Unable to send OTP. Please try again later."
                return render_template("admin/adminForgotPassword.html", error=error)

            flash("Verification code sent to new email.", "info")
            cur.close()
            conn.close()
            return redirect(url_for("admin.verify_email_change"))

        flash("Profile updated successfully.", "success")

    cur.close()
    conn.close()

    return render_template(
        "admin/adminProfile.html",
        admin=admin,
        admin_username=username,
        admin_campus=admin_campus,
        is_super_admin=is_super_admin,
        campus_name=campus_name,
        campus_address=campus_address
    )

@admin_bp.route("/verify-email-change", methods=["GET", "POST"])
def verify_email_change():
    if "email_change" not in session:
        flash("No email change request found.", "error")
        return redirect(url_for("admin.adminProfile"))

    data = session["email_change"]

    # Expire OTP after 5 minutes
    if time.time() - data["time"] > 300:
        session.pop("email_change")
        flash("Verification code expired. Please try again.", "error")
        return redirect(url_for("admin.adminProfile"))

    if request.method == "POST":
        action = request.form.get("action")
        if action == "back":
            session.pop("email_change")
            flash("Email change cancelled.", "info")
            return redirect(url_for("admin.adminProfile"))

        entered_otp = request.form.get("otp")
        data["attempts"] += 1
        session["email_change"] = data

        # Max attempts
        if data["attempts"] >= 5:
            session.pop("email_change")
            flash("Too many failed attempts. Email change cancelled.", "error")
            return redirect(url_for("admin.adminProfile"))

        if entered_otp != data["otp"]:
            flash(f"Invalid OTP. Attempts left: {5 - data['attempts']}", "error")
            return redirect(url_for("admin.verify_email_change"))

        # OTP correct → update email
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(f"UPDATE {data['table']} SET email = %s WHERE username = %s",
                    (data["new_email"], data["username"]))
        conn.commit()
        cur.close()
        conn.close()

        session.pop("email_change")
        flash("Email updated successfully.", "success")
        return redirect(url_for("admin.adminProfile"))

    return render_template("admin/verify_email_change.html")

@admin_bp.route("/resend-email-otp")
def resend_email_otp():
    if "email_change" not in session:
        flash("No email change request found.", "error")
        return redirect(url_for("admin.adminProfile"))

    data = session["email_change"]
    otp = generate_otp()
    data["otp"] = otp
    data["time"] = time.time()
    data["attempts"] = 0
    session["email_change"] = data

    sent = send_otp_email(data["new_email"], otp)
    if not sent:
        flash("Unable to resend OTP. Please try again later.", "error")
        return redirect(url_for("admin.adminProfile"))

    flash("A new verification code has been sent to your email.", "info")
    return redirect(url_for("admin.verify_email_change"))

@admin_bp.route("/logout")
def logout():
    session.pop("admin_username", None)
    session.pop("last_activity", None)
    session.pop("admin_login_attempts", None)
    session.pop("admin_lock_until", None)

    flash("Session expired due to inactivity.", "session_expired")
    return redirect(url_for("admin.login"))
