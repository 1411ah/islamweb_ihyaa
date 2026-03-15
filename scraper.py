"""
scraper.py
- يستخرج شجرة الفهرس الكاملة
- يجلب محتوى كل section
- يبني EPUB مع التنسيق
- يحفظ النتائج في output/ (يُرفع للريبو تلقائياً)
"""

import requests
from bs4 import BeautifulSoup
from ebooklib import epub
import json, os, re, time, urllib.parse

BASE_URL  = "https://www.islamweb.net"
BOOK_ID   = 411
FIRST_ID  = 1
LAST_ID   = 8173
PART      = 1
DELAY     = 1.2        # ثانية بين الطلبات
HEADERS   = {
    "User-Agent":      "Mozilla/5.0 (compatible; research-bot/1.0)",
    "Accept-Language": "ar,en;q=0.9",
    "Referer":         f"{BASE_URL}/ar/library/content/{BOOK_ID}/1/",
}
SESSION = requests.Session()
SESSION.headers.update(HEADERS)
os.makedirs("output", exist_ok=True)
os.makedirs("output/sections", exist_ok=True)


# ══════════════════════════════════════════════════════════════════
# ١. جلب صفحة وإعادة BeautifulSoup
# ══════════════════════════════════════════════════════════════════
def fetch(url: str, retries=3) -> BeautifulSoup | None:
    for attempt in range(retries):
        try:
            r = SESSION.get(url, timeout=20)
            r.raise_for_status()
            return BeautifulSoup(r.text, "lxml"), r.text
        except Exception as e:
            print(f"  ⚠ محاولة {attempt+1} فشلت: {e}")
            time.sleep(3)
    return None, ""


# ══════════════════════════════════════════════════════════════════
# ٢. استخراج شجرة الفهرس (TOC)
# ══════════════════════════════════════════════════════════════════
def build_toc() -> list:
    """
    يجلب الصفحة الرئيسية ويستخرج:
    - .plusbutton  → فصول رئيسية
    - .BookDetail_1 → sections أوراق
    - HidParam inputs → parameters كل node
    """
    url = f"{BASE_URL}/ar/library/content/{BOOK_ID}/1/مقدمة"
    soup, _ = fetch(url)
    if not soup:
        print("✗ فشل جلب الفهرس")
        return []

    toc = []

    # HidParam inputs
    hid = {}
    for inp in soup.find_all("input", id=re.compile("^HidParam")):
        nid = inp["id"].replace("HidParam", "")
        hid[nid] = inp.get("value", "")

    # tree_label + plusbutton
    for el in soup.find_all(class_=["tree_label", "plusbutton", "BookDetail_1"]):
        nid   = el.get("id") or el.get("data-id")
        level = el.get("data-level", "?")
        text  = el.get_text(strip=True)
        href  = el.get("data-href", "")
        if nid and text:
            toc.append({
                "id":      nid,
                "level":   level,
                "text":    text[:120],
                "href":    href,
                "hid":     hid.get(nid, ""),
                "source":  el.get("class", [""])[0],
            })

    # إضافة first/last كـ anchor معروف
    toc.insert(0, {"id": str(FIRST_ID), "level": "0", "text": "مقدمة",          "href": "", "hid": "", "source": "manual"})
    toc.append(   {"id": str(LAST_ID),  "level": "0", "text": "آخر صفحة",       "href": "", "hid": "", "source": "manual"})

    with open("output/toc.json", "w", encoding="utf-8") as f:
        json.dump(toc, f, ensure_ascii=False, indent=2)
    print(f"✓ شجرة الفهرس: {len(toc)} عنصر → output/toc.json")
    return toc


# ══════════════════════════════════════════════════════════════════
# ٣. جلب محتوى section واحد عبر nindex.php
# ══════════════════════════════════════════════════════════════════
def fetch_section(node_id: str, hid_param: str = "") -> dict | None:
    cache = f"output/sections/{node_id}.json"
    if os.path.exists(cache):
        with open(cache, encoding="utf-8") as f:
            return json.load(f)

    url  = f"{BASE_URL}/ar/library/maktaba/nindex.php?id={node_id}{hid_param}&bookid={BOOK_ID}&page=bookpages"
    soup, raw = fetch(url)
    if not soup:
        return None

    # استخرج العنوان
    title_el = soup.find(["h1","h2","h3","b","strong"])
    title    = title_el.get_text(strip=True) if title_el else f"قسم {node_id}"

    # استخرج الفقرات مع الأنماط
    paragraphs = []
    for tag in soup.find_all(["p","div","span","b","h1","h2","h3"]):
        txt = tag.get_text(strip=True)
        if len(txt) < 10:
            continue
        cls = " ".join(tag.get("class", []))

        # كشف النوع
        if "﴿" in txt or "﴾" in txt:
            kind = "quran"
        elif any(c in cls for c in ["hadith","hadithatt"]):
            kind = "hadith"
        elif any(c in cls for c in ["names","namesatt"]):
            kind = "name"
        elif tag.name in ["h1","h2","h3"] or (len(txt) < 80 and tag.name == "b"):
            kind = "heading"
        else:
            kind = "text"

        paragraphs.append({"kind": kind, "text": txt, "tag": tag.name, "class": cls})

    # فلتر التكرار
    seen = set()
    unique = []
    for p in paragraphs:
        if p["text"] not in seen:
            seen.add(p["text"])
            unique.append(p)

    # كشف pagination: التالي/السابق
    next_url = prev_url = None
    for a in soup.find_all("a", class_=["nextpage","prvpage"]):
        href = a.get("data-url","")
        if "nextpage" in " ".join(a.get("class",[])):
            next_url = href
        else:
            prev_url = href

    result = {
        "node_id":    node_id,
        "title":      title,
        "url":        url,
        "paragraphs": unique,
        "next_url":   next_url,
        "prev_url":   prev_url,
        "has_quran":  any(p["kind"]=="quran"  for p in unique),
        "has_hadith": any(p["kind"]=="hadith" for p in unique),
    }

    with open(cache, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    return result


# ══════════════════════════════════════════════════════════════════
# ٤. اكتشاف كل sections عبر scan نطاق محدود (للاختبار أولاً)
# ══════════════════════════════════════════════════════════════════
def scan_range(start=1, end=50, step=1) -> list:
    """مسح نطاق من node IDs لاكتشاف الـ sections الفعلية"""
    valid = []
    print(f"\n=== مسح IDs {start}→{end} ===")
    for nid in range(start, end+1, step):
        result = fetch_section(str(nid))
        if result and result["paragraphs"]:
            q = "✅" if result["has_quran"] else ("📖" if result["has_hadith"] else "🔶")
            print(f"  {q} id={nid:5d} | {result['title'][:50]:50s} | فقرات={len(result['paragraphs'])}")
            valid.append(nid)
        else:
            print(f"  ⬜ id={nid:5d} | فارغ")
        time.sleep(DELAY)

    print(f"\n✓ sections صالحة: {len(valid)}/{end-start+1}")
    return valid


# ══════════════════════════════════════════════════════════════════
# ٥. بناء EPUB
# ══════════════════════════════════════════════════════════════════
EPUB_CSS = """
@charset "UTF-8";
body    { font-family: 'Traditional Arabic', serif; direction: rtl;
          text-align: right; line-height: 2; margin: 1em 1.5em; }
h1      { color: #8B0000; border-bottom: 2px solid #8B0000; margin-top: 1.5em; }
h2      { color: #5A3E1B; margin-top: 1.2em; }
.quran  { color: #006400; font-size: 1.1em; margin: 0.8em 0; padding: 0.5em;
          border-right: 4px solid #006400; background: #f0fff0; }
.hadith { color: #4B0082; margin: 0.8em 0; padding: 0.5em;
          border-right: 4px solid #4B0082; background: #f5f0ff; }
.name   { color: #8B0000; }
.text   { margin: 0.5em 0; }
"""

def build_epub(toc: list, valid_ids: list) -> str:
    book = epub.EpubBook()
    book.set_identifier(f"islamweb-{BOOK_ID}")
    book.set_title("إتحاف السادة المتقين بشرح إحياء علوم الدين")
    book.set_language("ar")
    book.add_author("مرتضى الزبيدي")

    css = epub.EpubItem(
        uid="style", file_name="style/main.css",
        media_type="text/css", content=EPUB_CSS
    )
    book.add_item(css)

    chapters   = []
    spine      = ["nav"]
    toc_items  = []

    # صفحة غلاف
    cover_html = epub.EpubHtml(
        title="الغلاف", file_name="cover.xhtml", lang="ar"
    )
    cover_html.content = """<html><body dir="rtl">
    <h1 style="text-align:center;color:#8B0000;margin-top:3em">
        إتحاف السادة المتقين<br/>بشرح إحياء علوم الدين
    </h1>
    <p style="text-align:center;font-size:1.2em">مرتضى الزبيدي</p>
    </body></html>"""
    cover_html.add_item(css)
    book.add_item(cover_html)
    chapters.append(cover_html)
    spine.append(cover_html)

    # الفصول
    for nid in valid_ids:
        cache = f"output/sections/{nid}.json"
        if not os.path.exists(cache):
            continue
        with open(cache, encoding="utf-8") as f:
            sec = json.load(f)

        # بناء HTML الفصل
        body = f'<h1>{sec["title"]}</h1>\n'
        for p in sec["paragraphs"]:
            t = p["text"].replace("<","&lt;").replace(">","&gt;")
            if p["kind"] == "quran":
                body += f'<p class="quran">{t}</p>\n'
            elif p["kind"] == "hadith":
                body += f'<p class="hadith">{t}</p>\n'
            elif p["kind"] == "heading":
                body += f'<h2>{t}</h2>\n'
            elif p["kind"] == "name":
                body += f'<span class="name">{t}</span> '
            else:
                body += f'<p class="text">{t}</p>\n'

        ch = epub.EpubHtml(
            title=sec["title"],
            file_name=f"chap_{nid}.xhtml",
            lang="ar"
        )
        ch.content = f'<html><body dir="rtl">{body}</body></html>'
        ch.add_item(css)
        book.add_item(ch)
        chapters.append(ch)
        spine.append(ch)
        toc_items.append(epub.Link(f"chap_{nid}.xhtml", sec["title"], f"chap{nid}"))

    book.toc   = toc_items
    book.spine = spine
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())

    path = f"output/ithaf_alsada.epub"
    epub.write_epub(path, book)
    print(f"\n✓ EPUB محفوظ: {path}")
    return path


# ══════════════════════════════════════════════════════════════════
# ٦. التشغيل الرئيسي
# ══════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    import sys
    mode = sys.argv[1] if len(sys.argv) > 1 else "test"

    print("=== ١. بناء الفهرس ===")
    toc = build_toc()

    if mode == "test":
        # اختبار: مسح أول 30 node فقط
        print("\n=== ٢. مسح اختباري (أول 30) ===")
        valid = scan_range(1, 30)

    elif mode == "full":
        # استخراج كامل (698 صفحة × ~1.2 ث ≈ 14 دقيقة)
        print("\n=== ٢. مسح كامل 1→8173 ===")
        valid = scan_range(1, LAST_ID, step=1)

    else:
        valid = [int(x) for x in mode.split(",")]

    if valid:
        print(f"\n=== ٣. بناء EPUB ({len(valid)} فصل) ===")
        build_epub(toc, valid)
    else:
        print("✗ لا توجد sections صالحة")

    print("\n✅ اكتمل")