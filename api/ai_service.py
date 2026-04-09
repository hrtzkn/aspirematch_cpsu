import os
import requests
from openai import OpenAI

ENV = os.getenv("FLASK_ENV", "local")

# =========================
# MAIN ENTRY FUNCTION
# =========================
def ask_ai(prompt):
    if ENV == "local":
        return ask_offline_ai(prompt)
    else:
        return ask_online_ai(prompt)

# =========================
# OFFLINE AI (OLLAMA)
# =========================
def ask_offline_ai(prompt):
    try:
        res = requests.post(
            "http://localhost:11434/api/generate",
            json={
                "model": "mistral",
                "prompt": prompt,
                "stream": False
            },
            timeout=120
        )

        data = res.json()

        # DEBUG (optional but helpful)
        print("Ollama response:", data)

        if "response" in data:
            return data["response"]

        if "error" in data:
            return f"Ollama error: {data['error']}"

        return "Offline AI returned an unexpected response."

    except requests.exceptions.ConnectionError:
        return "Offline AI not running. Please start Ollama."

    except Exception as e:
        return f"Offline AI exception: {str(e)}"

# =========================
# ONLINE AI (AUTO FALLBACK)
# =========================
def ask_online_ai(prompt):
    # Order matters
    providers = [
        ask_gemini,
        ask_openai
    ]

    for provider in providers:
        try:
            return provider(prompt)
        except Exception as e:
            print(f"AI failed, switching... ({e})")

    return "All AI services are currently unavailable."

# =========================
# GEMINI
# =========================
def ask_gemini(prompt):
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise Exception("Gemini API key missing")

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-pro:generateContent?key={api_key}"
    payload = {
        "contents": [
            {
                "parts": [{"text": prompt}]
            }
        ]
    }

    res = requests.post(url, json=payload, timeout=30)
    res.raise_for_status()

    return res.json()["candidates"][0]["content"]["parts"][0]["text"]

# =========================
# OPENAI
# =========================
def ask_openai(prompt):
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}]
    )

    return response.choices[0].message.content
