"""
explore.py — المرحلة الخامسة
قراءة bookcontents.js كاملاً واكتشاف API الحقيقي
"""

import requests
from bs4 import BeautifulSoup
import json, os, re, time

BASE_URL = "https://www.islamweb.net"
BOOK_ID  = 411
HEADERS  = {
    "User-Agent": "Mozilla/5.0 (compatible; research-bot/1.0)",
    "Accept-Language": "ar,en;q=0.9",
    "Referer": f"https://www.islamweb.net/ar/library/content/{BOOK_ID}/1/",
}
SESSION = requests.Session()
SESSION.headers.update(HEADERS)
os.makedirs("output", exist_ok=True)


# ── ١. تحميل bookcontents.js كاملاً ──────────────────────────────────────
def fetch_bookcontents_js():
    url = f"{BASE_URL}/ar/library/bookcontents.js?v=6.112"
    r = SESSION.get(url, timeout=15)
    with open("output/bookcontents.js", "w", encoding="utf-8") as f:
        f.write(r.text)
    print(f"✓ bookcontents.js: {len(r.text)} حرف")
    print("\n=== المحتوى الكامل ===")
    print(r.text)  # نطبع كل شيء
    return r.text


# ── ٢. تحميل tab.js و ajax.js ─────────────────────────────────────────────
def fetch_other_js():
    files = [
        "/ar/js/ajax.js",
        "/ar/library/maktaba/javascript/tab.js?v=2.6",
    ]
    for path in files:
        url = BASE_URL + path
        r = SESSION.get(url, timeout=15)
        fname = path.split("/")[-1].split("?")[0]
        with open(f"output/{fname}", "w", encoding="utf-8") as f:
            f.write(r.text)
        print(f"\n=== {fname} ({len(r.text)} حرف) ===")
        print(r.text[:3000])


# ── ٣. استخراج inline JS من الصفحة كاملاً ───────────────────────────────
def fetch_inline_js():
    url = f"{BASE_URL}/ar/library/content/{BOOK_ID}/1/مقدمة"
    r = SESSION.get(url, timeout=15)
    soup = BeautifulSoup(r.text, "lxml")

    all_inline = ""
    for script in soup.find_all("script"):
        if not script.get("src") and script.string:
            all_inline += f"\n// --- script block ---\n{script.string}"

    with open("output/inline_scripts.js", "w", encoding="utf-8") as f:
        f.write(all_inline)
    print(f"\n=== inline scripts ({len(all_inline)} حرف) ===")
    print(all_inline)


# ── التشغيل ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=== ١. bookcontents.js ===")
    fetch_bookcontents_js()

    print("\n=== ٢. ملفات JS أخرى ===")
    fetch_other_js()

    print("\n=== ٣. inline scripts ===")
    fetch_inline_js()

    print("\n✅ اكتمل")
