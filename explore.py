"""
explore.py — المرحلة الثالثة
الهدف: فتح bookcontents.js وملفات JS الأخرى لاكتشاف API الحقيقي
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


# ── ١. تحميل وتحليل ملفات JS ──────────────────────────────────────────────
JS_FILES = [
    "/ar/library/bookcontents.js?v=6.112",
    "/ar/js/ajax.js",
    "/ar/library/maktaba/javascript/tab.js?v=2.6",
]

def fetch_and_analyze_js():
    for path in JS_FILES:
        url = BASE_URL + path
        print(f"\n{'='*60}")
        print(f"JS: {url}")
        try:
            r = SESSION.get(url, timeout=15)
            content = r.text
            # احفظ الملف كاملاً
            fname = path.split("/")[-1].split("?")[0]
            with open(f"output/{fname}", "w", encoding="utf-8") as f:
                f.write(content)
            print(f"✓ حجم: {len(content)} حرف — محفوظ في output/{fname}")

            # ابحث عن أنماط URL/API
            patterns = {
                "URLs":        r'["\']([^"\']*\.php[^"\']*)["\']',
                "ajax url":    r'url\s*:\s*["\']([^"\']+)["\']',
                "fetch":       r'fetch\(["\']([^"\']+)["\']',
                "idfrom":      r'.{0,50}idfrom.{0,50}',
                "idto":        r'.{0,50}idto.{0,50}',
                "bk_no":       r'.{0,50}bk_no.{0,50}',
                "part":        r'.{0,50}["\']part["\'].{0,50}',
                "data:":       r'data\s*:\s*\{[^}]{0,200}\}',
            }
            for label, pat in patterns.items():
                matches = re.findall(pat, content)
                if matches:
                    print(f"\n  [{label}] — {len(matches)} نتيجة:")
                    for m in matches[:5]:
                        print(f"    {m[:120]}")

        except Exception as e:
            print(f"✗ خطأ: {e}")
        time.sleep(0.5)


# ── ٢. استخراج كل ajax calls من HTML الصفحة ──────────────────────────────
def extract_inline_ajax():
    url = f"{BASE_URL}/ar/library/content/{BOOK_ID}/1/?idfrom=1&idto=8667"
    r = SESSION.get(url, timeout=15)
    soup = BeautifulSoup(r.text, "lxml")

    print(f"\n{'='*60}")
    print("الـ ajax calls في inline scripts:")
    all_js = ""
    for script in soup.find_all("script"):
        if not script.get("src") and script.string:
            all_js += script.string + "\n"

    # احفظ كل JS المضمّن
    with open("output/inline_scripts.js", "w", encoding="utf-8") as f:
        f.write(all_js)
    print(f"✓ حجم inline JS: {len(all_js)} حرف — محفوظ في output/inline_scripts.js")

    # استخرج كتل ajax كاملة
    ajax_blocks = re.findall(r'\$\.ajax\(\{.*?\}\)', all_js, re.DOTALL)
    print(f"\nعدد كتل $.ajax: {len(ajax_blocks)}")
    for i, block in enumerate(ajax_blocks):
        print(f"\n  [كتلة {i+1}]:\n{block[:400]}")

    # ابحث عن متغيرات مرتبطة بالكتاب
    book_vars = re.findall(r'.{0,80}(?:bk_no|idfrom|idto|bookid|part_no).{0,80}', all_js)
    print(f"\n[متغيرات الكتاب في inline JS]:")
    for v in book_vars[:15]:
        print(f"  {v.strip()}")


# ── ٣. اختبار bookcontents.js API مباشرة ─────────────────────────────────
def probe_bookcontents_api():
    """بعد تحليل bookcontents.js نجرب endpoints محتملة"""
    candidates = [
        f"{BASE_URL}/ar/library/maktaba/bookcontent.php?bk_no={BOOK_ID}&part=1&idfrom=1&idto=50",
        f"{BASE_URL}/ar/library/maktaba/getbookcontent.php?bk_no={BOOK_ID}&part=1&idfrom=1",
        f"{BASE_URL}/ar/library/maktaba/content.php?bk_no={BOOK_ID}&part=1&idfrom=1&idto=50",
        f"{BASE_URL}/ar/library/bookajax.php?bk_no={BOOK_ID}&part=1&idfrom=1&idto=50",
        f"{BASE_URL}/ar/library/maktaba/javascript/../bookcontent.php?bk_no={BOOK_ID}&part=1",
    ]
    print(f"\n{'='*60}")
    print("اختبار endpoints إضافية:")
    for url in candidates:
        r = SESSION.get(url, timeout=10)
        has_arabic = any("\u0600" <= c <= "\u06ff" for c in r.text[:500])
        has_quran  = "﴿" in r.text
        mark = "✅" if has_quran else ("🔶" if has_arabic else "❌")
        print(f"  {mark} [{r.status_code}] len={len(r.text):6d} | {url.split('islamweb.net')[1][:70]}")
        if has_quran:
            print(f"     >>> {r.text[:300]}")
        time.sleep(0.5)


# ── التشغيل ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=== ١. تحليل ملفات JS ===")
    fetch_and_analyze_js()

    print("\n=== ٢. استخراج inline ajax ===")
    extract_inline_ajax()

    print("\n=== ٣. اختبار endpoints إضافية ===")
    probe_bookcontents_api()

    print("\n✅ اكتمل — انظر output/")
    print("الملف الأهم للمراجعة: output/bookcontents.js")
