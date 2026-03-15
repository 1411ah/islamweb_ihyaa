"""
explore.py
الهدف: استكشاف بنية صفحات كتاب إسلام ويب
- اكتشاف حاوية المحتوى
- رصد أنماط: آيات، أحاديث، عناوين، تصحيح/تضعيف
- استكشاف فهرس الكتاب
النتائج تُحفظ في output/
"""

import requests
from bs4 import BeautifulSoup
import json
import os
import time
from collections import Counter

# ── إعدادات ────────────────────────────────────────────────────────────────
BASE_URL = "https://www.islamweb.net"
BOOK_ID  = 411
PART     = 1
HEADERS  = {
    "User-Agent": "Mozilla/5.0 (compatible; research-bot/1.0)",
    "Accept-Language": "ar,en;q=0.9",
}
SESSION = requests.Session()
SESSION.headers.update(HEADERS)
os.makedirs("output", exist_ok=True)


# ── ١. جلب صفحة ────────────────────────────────────────────────────────────
def fetch_page(page_no: int, part: int = PART):
    url = (
        f"{BASE_URL}/ar/library/content/{BOOK_ID}/{part}/"
        f"?idfrom={page_no}&idto={page_no}"
    )
    r = SESSION.get(url, timeout=15)
    r.raise_for_status()
    return BeautifulSoup(r.text, "lxml"), url


# ── ٢. اكتشاف حاوية المحتوى ────────────────────────────────────────────────
def find_content_container(soup: BeautifulSoup):
    # جرّب class names شائعة
    candidates_cls = [
        "content", "book-content", "library-content", "readArea",
        "text", "article-body", "entry-content", "page-content",
    ]
    for c in candidates_cls:
        el = soup.find(class_=c)
        if el and len(el.get_text(strip=True)) > 200:
            return el, f"class={c}"

    # جرّب IDs
    candidates_id = ["content", "main", "readArea", "bookText"]
    for i in candidates_id:
        el = soup.find(id=i)
        if el and len(el.get_text(strip=True)) > 200:
            return el, f"id={i}"

    # الاحتياط: أكبر div نصاً
    divs = soup.find_all("div")
    best = max(divs, key=lambda d: len(d.get_text(strip=True)), default=None)
    return best, "largest-div"


# ── ٣. رصد الأنماط النصية ──────────────────────────────────────────────────
PATTERNS = {
    "آية_قرآنية":  lambda t: "﴿" in t or "﴾" in t,
    "حديث_نبوي":   lambda t: any(w in t for w in [
                        "قال رسول", "صلى الله عليه وسلم",
                        "أخرجه", "رواه", "أخبرنا", "حدثنا"
                    ]),
    "عنوان":       lambda t: (
                        len(t) < 100 and
                        not any(c in t for c in "﴿﴾") and
                        t.strip()
                    ),
    "تصحيح_تضعيف": lambda t: any(w in t for w in [
                        "ضعيف", "صحيح", "حسن", "موضوع",
                        "إسناده", "سنده", "رجاله"
                    ]),
}

def detect_patterns(soup: BeautifulSoup) -> dict:
    results = {k: [] for k in PATTERNS}
    container, _ = find_content_container(soup)
    scope = container if container else soup

    for tag in scope.find_all(["p", "span", "div", "h1", "h2", "h3", "b", "strong"]):
        txt = tag.get_text(strip=True)
        if len(txt) < 5:
            continue
        for label, fn in PATTERNS.items():
            if fn(txt):
                results[label].append({
                    "tag":   tag.name,
                    "class": tag.get("class", []),
                    "text":  txt[:150],
                })
    return results


# ── ٤. استكشاف صفحة واحدة بالتفصيل ────────────────────────────────────────
def explore_single_page(page_no: int = 1):
    print(f"\n{'='*60}")
    print(f"جاري استكشاف الصفحة {page_no} ...")
    soup, url = fetch_page(page_no)

    # حاوية المحتوى
    container, how = find_content_container(soup)
    print(f"✓ حاوية المحتوى: {how}")
    if container:
        preview = container.get_text(strip=True)[:300]
        print(f"  معاينة: {preview}\n")

    # أكثر class names
    all_cls = []
    for tag in soup.find_all(True):
        all_cls.extend(tag.get("class", []))
    top_cls = Counter(all_cls).most_common(20)
    print("أكثر 20 class تكراراً:")
    for cls, cnt in top_cls:
        print(f"  .{cls:35s} × {cnt}")

    # الأنماط
    patterns = detect_patterns(soup)
    print("\nالأنماط المكتشفة:")
    for label, items in patterns.items():
        print(f"  {label}: {len(items)} عنصر")
        for item in items[:2]:  # أمثلة
            print(f"    <{item['tag']}> {item['text'][:80]}")

    # احفظ النتيجة
    result = {
        "url": url,
        "container": how,
        "top_classes": dict(top_cls),
        "patterns": {k: v[:5] for k, v in patterns.items()},
    }
    path = f"output/explore_page_{page_no}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\n✓ حُفظت النتيجة في {path}")
    return result


# ── ٥. مسح عدة صفحات ──────────────────────────────────────────────────────
def survey_pages(pages: list = [1, 5, 10, 20]):
    all_results = {}
    for p in pages:
        try:
            all_results[p] = explore_single_page(p)
            time.sleep(2)  # احترام السيرفر
        except Exception as e:
            print(f"✗ خطأ في صفحة {p}: {e}")

    path = "output/survey_summary.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)
    print(f"\n✓ ملخص المسح في {path}")


# ── ٦. استكشاف الفهرس (TOC) ───────────────────────────────────────────────
def explore_toc():
    print("\nجاري استكشاف الفهرس...")
    url = f"{BASE_URL}/ar/library/content/{BOOK_ID}/1/"
    r = SESSION.get(url, timeout=15)
    soup = BeautifulSoup(r.text, "lxml")

    toc_items = []
    # البحث عن روابط الفهرس في القائمة الجانبية
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if f"/library/content/{BOOK_ID}/" in href:
            text = a.get_text(strip=True)
            if text and len(text) > 3:
                toc_items.append({"text": text, "href": href})

    print(f"✓ عدد عناصر الفهرس المكتشفة: {len(toc_items)}")
    for item in toc_items[:10]:
        print(f"  {item['text'][:60]} => {item['href']}")

    path = "output/toc.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(toc_items, f, ensure_ascii=False, indent=2)
    print(f"✓ الفهرس محفوظ في {path}")
    return toc_items


# ── التشغيل الرئيسي ────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=== مرحلة ١: استكشاف الفهرس ===")
    explore_toc()

    print("\n=== مرحلة ٢: مسح عينة من الصفحات ===")
    survey_pages([1, 5, 10, 20])

    print("\n✅ الاستكشاف اكتمل — انظر مجلد output/")
