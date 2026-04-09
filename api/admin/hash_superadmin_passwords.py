from werkzeug.security import generate_password_hash
import psycopg2

# 🔑 Update these to match your database
conn = psycopg2.connect(
    dbname="aspirematch",
    user="postgres",
    password="Frequency",
    host="localhost"  # or your host
)
cur = conn.cursor()

# Example: hash password for super_admins
super_admins = [
    {"username": "hkml", "password": "Frequency1klhz!"}
]

for admin in super_admins:
    hashed_password = generate_password_hash(admin["password"])
    cur.execute("""
        UPDATE super_admin
        SET password = %s
        WHERE username = %s
    """, (hashed_password, admin["username"]))
    print(f"Password for {admin['username']} hashed successfully")

conn.commit()
cur.close()
conn.close()
