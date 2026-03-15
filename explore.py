"""
explore.py — المرحلة الثانية
الهدف: اكتشاف API الخفي الذي يحمّل نص الكتاب
"""

import requests
from bs4 import BeautifulSoup
import json
import os
import time

BASE_URL = "https://www.islamweb.net"
BOOK_ID  = 411
HEADERS  = {
    "User-Agent": "Mozilla/5.0 (compatible; research-bot/1.0)",
    "Accept": "application/json, text/html, */*",
    "Accept-Language": "ar,en;q=0.9",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": f"https://www.islamweb.net/ar/library/content/{BOOK_ID}/1/",
}
SESSION = requests.Session()
SESSION.headers.update(HEADERS)
os.makedirs("output", exist_ok=True)


# ── ١. تتبع redirect صفحة رقم ─────────────────────────────────────────────
def follow_pageno_redirect(page_no: int = 1):
    """pageno_redirect.php يعطينا URL الحقيقي للصفحة"""
    url = (
        f"{BASE_URL}/ar/library/pageno_redirect.php"
        f"?part=1&bk_no={BOOK_ID}&pageno={page_no}"
    )
    print(f"\n[pageno_redirect] page {page_no}")
    r = SESSION.get(url, timeout=15, allow_redirects=True)
    print(f"  Status: {r.status_code}")
    print(f"  Final URL: {r.url}")
    print(f"  Content (أول 300): {r.text[:300]}")

    # احفظ
    with open(f"output/redirect_page{page_no}.html", "w", encoding="utf-8") as f:
        f.write(r.text)
    return r.url, r.text


# ── ٢. اختبار نقاط API محتملة ─────────────────────────────────────────────
def probe_api_endpoints():
    candidates = [
        # نمط ajax شائع
        f"{BASE_URL}/ar/library/ajax.php?bk_no={BOOK_ID}&part=1&idfrom=1&idto=50",
        f"{BASE_URL}/ar/library/index.php?page=content&bk_no={BOOK_ID}&part=1&idfrom=1",
        # API مباشر
        f"{BASE_URL}/api/library/content?bk_no={BOOK_ID}&part=1&page=1",
        f"{BASE_URL}/ar/library/content/{BOOK_ID}/1/?idfrom=1&idto=50&format=json",
        # نمط getpage
        f"{BASE_URL}/ar/library/getpage.php?bk_no={BOOK_ID}&part=1&page=1",
        f"{BASE_URL}/ar/library/getpage.php?bk_no={BOOK_ID}&part=1&idfrom=1&idto=50",
        # idfrom/idto مباشرة
        f"{BASE_URL}/ar/library/content/{BOOK_ID}/1/?idfrom=1&idto=1",
        f"{BASE_URL}/ar/library/content/{BOOK_ID}/1/?idfrom=1&idto=100",
    ]

    results = []
    for url in candidates:
        try:
            r = SESSION.get(url, timeout=10)
            txt = r.text[:500]
            has_arabic = any("\u0600" <= c <= "\u06ff" for c in txt)
            has_quran  = "﴿" in txt or "﴾" in txt
            is_json    = txt.strip().startswith("{") or txt.strip().startswith("[")

            info = {
                "url":        url,
                "status":     r.status_code,
                "length":     len(r.text),
                "has_arabic": has_arabic,
                "has_quran":  has_quran,
                "is_json":    is_json,
                "preview":    txt,
            }
            results.append(info)

            mark = "✅" if has_quran else ("🔶" if has_arabic else "❌")
            print(f"{mark} [{r.status_code}] len={len(r.text):6d} | {url}")
            if has_quran:
                print(f"   >>> آيات موجودة! معاينة: {txt[:200]}")
            time.sleep(0.5)

        except Exception as e:
            print(f"❌ خطأ: {url} => {e}")
            results.append({"url": url, "error": str(e)})

    with open("output/api_probe.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print("\n✓ النتائج في output/api_probe.json")
    return results


# ── ٣. تحليل JS داخل الصفحة بحثاً عن API ────────────────────────────────
def find_api_in_js():
    url = f"{BASE_URL}/ar/library/content/{BOOK_ID}/1/?idfrom=1&idto=8667"
    r = SESSION.get(url, timeout=15)
    soup = BeautifulSoup(r.text, "lxml")

    print("\n[Scripts في الصفحة]")
    api_hints = []
    for script in soup.find_all("script"):
        src = script.get("src", "")
        txt = script.string or ""

        if src:
            print(f"  external: {src}")

        # ابحث عن كلمات مفتاحية في JS المضمّن
        keywords = ["ajax", "fetch", "idfrom", "idto", "getpage", "api", "content", "xhr"]
        for kw in keywords:
            if kw.lower() in txt.lower():
                # استخرج السطر الذي يحتوي الكلمة
                for line in txt.splitlines():
                    if kw.lower() in line.lower() and len(line.strip()) > 5:
                        api_hints.append({"keyword": kw, "line": line.strip()[:200]})

    print(f"\n[API hints مكتشفة: {len(api_hints)}]")
    for h in api_hints[:20]:
        print(f"  [{h['keyword']}] {h['line']}")

    with open("output/js_api_hints.json", "w", encoding="utf-8") as f:
        json.dump(api_hints, f, ensure_ascii=False, indent=2)
    print("\n✓ النتائج في output/js_api_hints.json")
    return api_hints


# ── ٤. فحص idfrom/idto — هل هي معرّفات نصية؟ ────────────────────────────
def probe_idfrom_idto():
    """اختبر نطاقات مختلفة لـ idfrom/idto"""
    print("\n[فحص نطاقات idfrom/idto]")
    tests = [
        (1, 1), (1, 5), (1, 10),
        (100, 110), (500, 510), (1000, 1010),
    ]
    for fr, to in tests:
        url = f"{BASE_URL}/ar/library/content/{BOOK_ID}/1/?idfrom={fr}&idto={to}"
        r = SESSION.get(url, timeout=10)
        soup = BeautifulSoup(r.text, "lxml")
        # أطول div
        divs = soup.find_all("div")
        best = max(divs, key=lambda d: len(d.get_text(strip=True)), default=None)
        txt = best.get_text(strip=True)[:200] if best else ""
        has_quran = "﴿" in r.text
        print(f"  idfrom={fr:5d} idto={to:5d} | quran={'✅' if has_quran else '❌'} | {txt[:80]}")
        time.sleep(0.8)


# ── التشغيل ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=== ١. تتبع redirect ===")
    follow_pageno_redirect(1)
    follow_pageno_redirect(2)

    print("\n=== ٢. اختبار API endpoints ===")
    probe_api_endpoints()

    print("\n=== ٣. تحليل JavaScript ===")
    find_api_in_js()

    print("\n=== ٤. فحص نطاقات idfrom/idto ===")
    probe_idfrom_idto()

    print("\n✅ اكتمل — انظر output/")
