from flask import Flask, session, redirect, url_for, request, flash
import os
from dotenv import load_dotenv
from datetime import datetime, timezone, timedelta

load_dotenv()

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../frontend"))

app = Flask(
    __name__,
    template_folder=os.path.join(BASE_DIR, "templates"),
    static_folder=os.path.join(BASE_DIR, "static")
)

app.secret_key = os.getenv("SECRET_KEY", "supersecretkey")

# ✅ IMPORTANT FOR VERCEL SESSION
app.config["SESSION_COOKIE_SECURE"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "None"

app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(minutes=10)

UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER


# ===============================
# SESSION TIMEOUT (UNCHANGED)
# ===============================
@app.before_request
def check_session_timeout():

    now = datetime.now(timezone.utc)
    timeout = app.permanent_session_lifetime.total_seconds()
    
    if request.blueprint == "admin":

        if request.endpoint == "admin.login":
            return

        if "admin_username" not in session:
            return

        last_activity = session.get("last_activity")

        if last_activity:
            idle_time = (now - last_activity).total_seconds()

            if idle_time > timeout:
                flash("Session expired due to inactivity.", "session_expired")
                session.pop("admin_username", None)
                session.pop("last_activity", None)
                session.pop("admin_login_attempts", None)
                session.pop("admin_lock_until", None)
                return redirect(url_for("admin.login"))

        session["last_activity"] = now
        session.permanent = True

    # -------- STUDENT CHECK --------
    if request.blueprint == "student":

        if request.endpoint in ["student.studentlogin", "student.login_page"]:
            return

        if "student_id" not in session:
            return

        last_activity = session.get("last_activity")

        if last_activity:
            idle_time = (now - last_activity).total_seconds()

            if idle_time > timeout:
                flash("Session expired due to inactivity.", "session_expired")
                session.clear()
                return redirect(url_for("student.login_page"))

        session["last_activity"] = now
        session.permanent = True


# ===============================
# IMPORT BLUEPRINTS
# ===============================
from .admin.routes import admin_bp
from .student.routes import student_bp

# REGISTER
app.register_blueprint(admin_bp, url_prefix='/admin')
app.register_blueprint(student_bp, url_prefix='/student')


# ===============================
# VERCEL HANDLER (REQUIRED)
# ===============================
def handler(request, context):
    return app(request.environ, start_response=lambda *args: None)


# ===============================
# LOCAL RUN (KEEP THIS)
# ===============================
if __name__ == "__main__":
    #app.run()
    app.run(debug=True)
    #python -m backend.app