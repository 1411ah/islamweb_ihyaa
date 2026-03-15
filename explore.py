"""
explore.py — المرحلة الرابعة
النص موجود في HTML — نحدد مكانه بدقة
"""

import requests
from bs4 import BeautifulSoup
import json, os, re, time

BASE_URL = "https://www.islamweb.net"
BOOK_ID  = 411
HEADERS  = {
    "User-Agent": "Mozilla/5.0 (compatible; research-bot/1.0)",
    "Accept-Language": "ar,en;q=0.9",
}
SESSION = requests.Session()
SESSION.headers.update(HEADERS)
os.makedirs("output", exist_ok=True)

# ── ١. تتبع الـ slug الحقيقي للصفحات ─────────────────────────────────────
def get_page_slugs(pages=[1,2,3,4,5]):
    print("=== slugs الصفحات ===")
    slugs = {}
    for p in pages:
        url = (f"{BASE_URL}/ar/library/pageno_redirect.php"
               f"?part=1&bk_no={BOOK_ID}&pageno={p}")
        r = SESSION.get(url, timeout=15, allow_redirects=True)
        slugs[p] = r.url
        print(f"  صفحة {p} => {r.url}")
        time.sleep(0.5)
    with open("output/slugs.json","w",encoding="utf-8") as f:
        json.dump(slugs, f, ensure_ascii=False, indent=2)
    return slugs

# ── ٢. تحديد container النص بدقة ──────────────────────────────────────────
TARGET = "الذنوب والمعاصي تضر"

def find_text_container():
    url = f"{BASE_URL}/ar/library/content/{BOOK_ID}/1/مقدمة"
    r = SESSION.get(url, timeout=15)
    soup = BeautifulSoup(r.text, "lxml")

    # احفظ HTML الكامل للفحص
    with open("output/full_page.html","w",encoding="utf-8") as f:
        f.write(r.text)
    print(f"✓ HTML الكامل محفوظ ({len(r.text)} حرف)")

    # ابحث عن العنصر الأصغر الذي يحتوي النص المستهدف
    print(f"\n[البحث عن '{TARGET}']")
    found = []
    for tag in soup.find_all(True):
        if TARGET in (tag.string or ""):
            found.append({
                "tag":    tag.name,
                "id":     tag.get("id",""),
                "class":  tag.get("class",[]),
                "text":   tag.get_text(strip=True)[:200],
            })

    # إن لم يكن في string، ابحث في get_text مع تحقق أن الأبناء لا يحتوونه
    if not found:
        for tag in soup.find_all(True):
            txt = tag.get_text(strip=True)
            if TARGET in txt:
                # تحقق أن أبناءه لا يحتوونه (أي هذا أصغر حاوية)
                children_have = any(
                    TARGET in (c.get_text(strip=True) if hasattr(c,'get_text') else "")
                    for c in tag.children
                )
                if not children_have:
                    found.append({
                        "tag":   tag.name,
                        "id":    tag.get("id",""),
                        "class": tag.get("class",[]),
                        "text":  txt[:200],
                    })

    print(f"  عدد العناصر التي تحتوي النص: {len(found)}")
    for el in found[:10]:
        print(f"  <{el['tag']}> id='{el['id']}' class={el['class']}")
        print(f"    {el['text'][:100]}")

    # ── طريقة بديلة: ابحث في HTML الخام عن السياق ──
    print(f"\n[السياق في HTML الخام]")
    idx = r.text.find(TARGET)
    if idx >= 0:
        context = r.text[max(0,idx-300):idx+300]
        print(context)
        with open("output/text_context.html","w",encoding="utf-8") as f:
            f.write(context)
        print("✓ السياق محفوظ في output/text_context.html")
    else:
        print("  ✗ النص غير موجود في HTML الخام — محمّل بـ JS")
        # تحقق من وجوده في JavaScript
        js_idx = r.text.find("الذنوب")
        if js_idx >= 0:
            print(f"  🔶 موجود في JS عند index {js_idx}:")
            print(r.text[max(0,js_idx-200):js_idx+200])

    return found

# ── ٣. بعد معرفة الـ container — استخراج نص صفحات متعددة ────────────────
def extract_text_from_pages(container_selector: dict, pages=5):
    """
    container_selector مثال:
      {"type":"id",    "value":"bookText"}
      {"type":"class", "value":"nass"}
    """
    print(f"\n=== استخراج النص بـ {container_selector} ===")
    results = {}
    for p in range(1, pages+1):
        url = (f"{BASE_URL}/ar/library/pageno_redirect.php"
               f"?part=1&bk_no={BOOK_ID}&pageno={p}")
        r = SESSION.get(url, timeout=15, allow_redirects=True)
        soup = BeautifulSoup(r.text, "lxml")

        t = container_selector["type"]
        v = container_selector["value"]
        el = soup.find(id=v) if t=="id" else soup.find(class_=v)

        if el:
            txt = el.get_text(separator="\n", strip=True)
            results[p] = txt[:300]
            print(f"  صفحة {p}: {txt[:80]}")
        else:
            print(f"  صفحة {p}: ✗ لم يُعثر على {container_selector}")
        time.sleep(1)

    with open("output/extracted_text.json","w",encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print("✓ النتائج في output/extracted_text.json")

# ── التشغيل ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=== ١. slugs الصفحات ===")
    get_page_slugs([1,2,3,4,5])

    print("\n=== ٢. تحديد حاوية النص ===")
    find_text_container()

    # بعد معرفة الـ container من النتائج، فعّل هذا السطر:
    # extract_text_from_pages({"type":"class","value":"nass"})

    print("\n✅ اكتمل — الملف الأهم: output/text_context.html")
