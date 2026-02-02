import requests
import os
from dotenv import load_dotenv

load_dotenv()

MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY")
MISTRAL_API_URL = os.getenv("MISTRAL_API_URL", "https://api.mistral.ai/v1/chat/completions")
MISTRAL_MODEL = os.getenv("MISTRAL_MODEL", "mistral-small-latest")

LANGUAGE_MAP = {
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "zh": "Chinese (Simplified)",
    "ja": "Japanese",
    "pt": "Portuguese",
    "ru": "Russian",
    "it": "Italian",
    "nl": "Dutch",
    "ko": "Korean"
}

def translate_text(text: str, target_language: str) -> str:
    """Translate text using Mistral API"""
    if not MISTRAL_API_KEY:
        return "Error: MISTRAL_API_KEY not configured"
    
    target_lang_name = LANGUAGE_MAP.get(target_language, target_language)
    
    headers = {
        "Authorization": f"Bearer {MISTRAL_API_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    
    messages = [
        {
            "role": "system",
            "content": f"You are a professional translator. Translate the following text to {target_lang_name}. Return ONLY the translation, nothing else."
        },
        {
            "role": "user",
            "content": text
        }
    ]
    
    payload = {
        "model": MISTRAL_MODEL,
        "messages": messages,
        "temperature": 0.1,
        "max_tokens": 1000
    }
    
    try:
        response = requests.post(MISTRAL_API_URL, headers=headers, json=payload, timeout=30)
        
        if response.status_code == 200:
            result = response.json()
            translated = result['choices'][0]['message']['content'].strip()
            return translated
        else:
            return f"Translation API Error: {response.status_code}"
    except Exception as e:
        return f"Translation failed: {e}"
