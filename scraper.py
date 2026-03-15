"""
scraper.py
الاستخدام:
  python scraper.py scan        ← المرحلة ١: اكتشاف nodes الصالحة
  python scraper.py build       ← المرحلة ٢: بناء EPUB من valid_nodes.json
  python scraper.py test        ← اختبار أول 30 node فقط
  python scraper.py resume      ← استكمال scan متوقف
"""

import requests
from bs4 import BeautifulSoup
from ebooklib import epub
import json, os, re, time, sys

BASE_URL = "https://www.islamweb.net"
BOOK_ID  = 411
LAST_ID  = 8173
DELAY    = 1.2
HEADERS  = {
    "User-Agent":      "Mozilla/5.0 (compatible; research-bot/1.0)",
    "Accept-Language": "ar,en;q=0.9",
    "X-Requested-With": "XMLHttpRequest",
    "Referer":         f"{BASE_URL}/ar/library/content/{BOOK_ID}/1/",
}
SESSION = requests.Session()
SESSION.headers.update(HEADERS)

# ── تفعيل نسخة التشكيل (islamweb_font_1) ────────────────────────
def set_tashkeel_cookie():
    """
    الموقع يستخدم cookie اسمها cval لتحديد النسخة:
      cval=1 → بتشكيل
      cval=2 → بدون تشكيل
    """
    # أولاً: نزور الصفحة الرئيسية لتأسيس الـ session
    SESSION.get(f"{BASE_URL}/ar/library/content/{BOOK_ID}/1/مقدمة", timeout=15)
    # ثانياً: نضبط الـ cookie عبر endpoint الموقع
    SESSION.get(
        f"{BASE_URL}/ar/set_cookie.php",
        params={"cval": 1},
        timeout=10
    )
    # ثالثاً: نضيف الـ cookie يدوياً للتأكيد
    SESSION.cookies.set("cval", "1", domain="www.islamweb.net")
    print("✓ cookie التشكيل مضبوطة (cval=1)")

os.makedirs("output/sections", exist_ok=True)


# ══════════════════════════════════════════════════════════════════
# أدوات مشتركة
# ══════════════════════════════════════════════════════════════════
def fetch(url: str, retries=3):
    for attempt in range(retries):
        try:
            r = SESSION.get(url, timeout=20)
            r.raise_for_status()
            return BeautifulSoup(r.text, "lxml"), r.text
        except Exception as e:
            wait = 5 * (attempt + 1)
            print(f"  ⚠ محاولة {attempt+1} فشلت ({e}) — انتظار {wait}s")
            time.sleep(wait)
    return None, ""


def load_json(path, default):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ══════════════════════════════════════════════════════════════════
# ١. استخراج فهرس الكتاب الكامل (مع الفصول الداخلية)
# ══════════════════════════════════════════════════════════════════
def build_toc() -> list:
    """
    يبني الفهرس الكامل من breadcrumbs صفحات الكتاب
    البنية: /ar/library/content/411/{NODE_ID}/{SLUG}
    """
    print("=== بناء الفهرس من breadcrumbs ===")

    # ── ١. جلب الصفحة الرئيسية لاستخراج كل الروابط الداخلية ─────
    url = f"{BASE_URL}/ar/library/content/{BOOK_ID}/1/مقدمة"
    soup, _ = fetch(url)
    if not soup:
        return []

    toc = []
    seen_ids = set()

    # ── ٢. استخرج breadcrumb من الصفحة الحالية ───────────────────
    def extract_breadcrumb(s) -> list:
        crumbs = []
        for li in s.find_all(
            lambda t: t.name in ["li","div"] and
            t.get("itemprop") == "itemListElement"
        ):
            a = li.find("a", itemprop="item")
            span = li.find("span", itemprop="name")
            pos_meta = li.find("meta", itemprop="position")

            if not span:
                continue

            text = span.get_text(strip=True)
            pos  = int(pos_meta["content"]) if pos_meta else 0

            # استخرج NODE_ID من الرابط
            node_id = None
            href    = ""
            if a:
                href = a.get("href","")
                # /ar/library/content/411/3968/ربع-العادات
                m = re.search(r'/content/\d+/(\d+)/', href)
                if m:
                    node_id = m.group(1)

            crumbs.append({
                "pos":     pos,
                "text":    text,
                "node_id": node_id,
                "href":    href,
            })
        return sorted(crumbs, key=lambda x: x["pos"])

    # ── ٣. جلب قائمة كل الروابط في الصفحة الرئيسية ──────────────
    all_links = set()
    for a in soup.find_all("a", href=re.compile(f"/library/content/{BOOK_ID}/")):
        href = a["href"]
        m = re.search(r'/content/\d+/(\d+)/', href)
        if m:
            nid = m.group(1)
            if nid not in seen_ids:
                all_links.add((nid, href))

    print(f"  روابط مكتشفة في الصفحة الرئيسية: {len(all_links)}")

    # ── ٤. لكل رابط، جلب الصفحة واستخراج breadcrumb ──────────────
    # نبدأ بالروابط الرئيسية المعروفة من hid
    known_nodes = []
    for inp in soup.find_all("input", id=re.compile("^HidParam")):
        nid = inp["id"].replace("HidParam","")
        hid = inp.get("value","")
        idfrom = re.search(r'idfrom=(\d+)', hid)
        if idfrom and nid not in seen_ids:
            known_nodes.append((nid, hid))
            seen_ids.add(nid)

    print(f"  nodes من HidParam: {len(known_nodes)}")

    for nid, hid in known_nodes:
        idfrom = re.search(r'idfrom=(\d+)', hid)
        idto   = re.search(r'idto=(\d+)',   hid)
        if not idfrom:
            continue

        fr = int(idfrom.group(1))
        to = int(idto.group(1)) if idto else fr

        # جلب الصفحة لاستخراج breadcrumb
        page_url = f"{BASE_URL}/ar/library/content/{BOOK_ID}/{fr}/"
        s, _ = fetch(page_url)
        if s:
            crumbs = extract_breadcrumb(s)
            level  = len(crumbs)
            text   = crumbs[-1]["text"] if crumbs else f"قسم {nid}"

            toc.append({
                "id":      nid,
                "level":   level,
                "text":    text,
                "idfrom":  fr,
                "idto":    to,
                "breadcrumb": [c["text"] for c in crumbs],
            })
            print(f"  {'  ' * level}{'└─' if level > 1 else '├─'} {text[:60]}")
        time.sleep(0.5)

    # ── ٥. إضافة روابط الـ dropdown (أرقام الصفحات) ──────────────
    # الموقع عنده 698 صفحة → نسحب عينة لاكتشاف عناوين إضافية
    sample_pages = list(range(1, 698, 20))  # كل 20 صفحة
    print(f"\n  جلب عينة {len(sample_pages)} صفحة لاكتشاف العناوين...")

    for pageno in sample_pages:
        page_url = (f"{BASE_URL}/ar/library/pageno_redirect.php"
                    f"?part=1&bk_no={BOOK_ID}&pageno={pageno}")
        s, _ = fetch(page_url)
        if not s:
            continue
        crumbs = extract_breadcrumb(s)
        if not crumbs:
            continue

        # أضف كل مستوى في breadcrumb إذا لم يكن موجوداً
        for c in crumbs:
            if c["node_id"] and c["node_id"] not in seen_ids:
                seen_ids.add(c["node_id"])
                m = re.search(r'/content/\d+/(\d+)/', c["href"])
                nid = m.group(1) if m else c["node_id"]
                toc.append({
                    "id":    nid,
                    "level": c["pos"],
                    "text":  c["text"],
                    "idfrom": int(nid),
                    "idto":   int(nid),
                    "breadcrumb": [x["text"] for x in crumbs[:c["pos"]]],
                })
        time.sleep(0.5)

    # ── ٦. ترتيب وحفظ ─────────────────────────────────────────────
    toc.sort(key=lambda x: x["idfrom"])
    # إزالة التكرار
    seen_final, final_toc = set(), []
    for item in toc:
        if item["id"] not in seen_final:
            seen_final.add(item["id"])
            final_toc.append(item)

    save_json("output/toc.json", final_toc)
    print(f"\n✓ فهرس كامل: {len(final_toc)} عنصر → output/toc.json")
    return final_toc


# ══════════════════════════════════════════════════════════════════
# ١ب. جلب المحتوى بـ idfrom/idto (الطريقة الصحيحة)
# ══════════════════════════════════════════════════════════════════
def fetch_section_by_range(nid: str, idfrom: int, idto: int, title: str) -> dict | None:
    cache = f"output/sections/{nid}.json"
    if os.path.exists(cache):
        return load_json(cache, None)

    url = (f"{BASE_URL}/ar/library/maktaba/nindex.php"
           f"?id={nid}&bookid={BOOK_ID}&idfrom={idfrom}&idto={idto}&page=bookpages")
    soup, raw = fetch(url)
    if not soup or len(raw) < 200:
        return None

    # تحقق أن النص مشكّل (يحتوي حركات)
    has_tashkeel = any(
        "\u064b" <= c <= "\u065f"   # نطاق حركات التشكيل
        for c in raw[:2000]
    )
    if not has_tashkeel:
        # أعد ضبط الـ cookie وحاول مرة أخرى
        set_tashkeel_cookie()
        soup, raw = fetch(url)
        if not soup:
            return None

    # احذف عناصر الموقع
    for sel in REMOVE_SELECTORS:
        for el in soup.select(sel):
            el.decompose()

    # استهدف container النص
    container = (
        soup.find(class_="bookcontenttxt") or
        soup.find(class_="right-side")     or
        soup.find(class_="nass")           or
        soup.find(id="updates")            or
        soup.body
    )
    if not container:
        return None

def clean_and_extract(soup) -> list:
    """
    يستخرج النص النظيف من pagebody/bookcontent-dic
    مع تحويل الأنماط الصحيحة
    """
    # ── الحاوية الرئيسية ──────────────────────────────────────────
    container = (
        soup.find(id="pagebody") or
        soup.find(class_="bookcontent-dic") or
        soup.find(class_="bookcontenttxt") or
        soup.body
    )
    if not container:
        return []

    # ── ١. احذف العناصر غير المطلوبة ─────────────────────────────
    for sel in [
        "script", "style", "noscript",
        ".hashiya_title",           # عنوان حاشية فارغ
        ".quranatt", ".hadithatt",  # روابط تفسير مخفية
        ".namesatt", ".mainsubjatt",
    ]:
        for el in container.select(sel):
            el.decompose()

    # ── ٢. احذف أرقام الصفحات  [ ص: 414 ] ───────────────────────
    for font in container.find_all("font"):
        txt = font.get_text()
        if "ص:" in txt or font.get("color") == "blue":
            font.decompose()

    # ── ٣. فك روابط التفسير (onclick) واترك النص ─────────────────
    for a in container.find_all("a", onclick=True):
        a.replace_with(a.get_text())

    # ── ٤. تحويل الآيات → ﴿ نص ﴾ ───────────────────────────────
    for span in container.find_all("span", class_="quran"):
        txt = span.get_text(strip=True)
        if txt:
            span.replace_with(f" ﴿{txt}﴾ ")

    # ── ٥. تحويل الأحاديث المصنّفة → (( نص )) ───────────────────
    for span in container.find_all("span", class_="hadith"):
        txt = span.get_text(strip=True)
        if txt:
            span.replace_with(f" (({txt})) ")

    # ── ٦. تحويل النصوص الخضراء → (( نص )) ──────────────────────
    for span in container.find_all("span", style=lambda s: s and "color:green" in s):
        txt = span.get_text(strip=True)
        if txt:
            span.replace_with(f" (({txt})) ")

    # ── ٧. إزالة الـ spans المخفية المتبقية ──────────────────────
    for span in container.find_all("span", style=lambda s: s and "display:none" in s):
        span.decompose()

    # ── ٨. استخرج النص كتدفق متواصل ─────────────────────────────
    for br in container.find_all("br"):
        br.replace_with("\n")

    raw = container.get_text(separator="\n")

    # ── ٩. نظّف وصنّف السطور ─────────────────────────────────────
    seen, paragraphs = set(), []

    for line in raw.splitlines():
        txt = line.strip()
        if len(txt) < 5:
            continue
        if txt in seen or txt in REPEATED_TEXTS:
            continue
        if any(w in txt for w in ["التالي","السابق","فهرس الكتاب","إسلام ويب"]):
            continue
        # تجاهل بقايا أرقام صفحات
        import re as _re
        if _re.fullmatch(r'[\[\]\d\s:]+', txt):
            continue

        seen.add(txt)

        if "﴿" in txt and "﴾" in txt:
            kind = "quran"
        elif txt.startswith("((") or txt.endswith("))"):
            kind = "hadith"
        elif len(txt) < 100 and txt.endswith(":"):
            kind = "heading"
        else:
            kind = "text"

        paragraphs.append({"kind": kind, "text": txt})

    return paragraphs

    if not paragraphs:
        return None

    result = {
        "node_id":    nid,
        "title":      title,
        "idfrom":     idfrom,
        "idto":       idto,
        "paragraphs": paragraphs,
        "has_quran":  any(p["kind"]=="quran"  for p in paragraphs),
        "has_hadith": any(p["kind"]=="hadith" for p in paragraphs),
    }
    save_json(cache, result)
    return result


# ══════════════════════════════════════════════════════════════════
# عناصر يجب حذفها من الصفحة
# ══════════════════════════════════════════════════════════════════
REMOVE_SELECTORS = [
    # تنقل الموقع
    "header", "footer", "nav",
    ".navbar", ".breadcrumb", ".topPath",
    # أزرار التنقل بين الصفحات
    ".nextpage", ".prvpage", ".pagination",
    # الحواشي والتعليقات الجانبية
    ".hasiyaTextArea", ".hashiya", ".scienceArea",
    ".sciencetabs", ".footnotecontent",
    # تبويبات الآيات والأحاديث الجانبية
    "#modaltabsinfo", ".qurantab", ".hadithtab",
    ".alamtab", ".treetab",
    "#quran-ajax-content", "#hadith-ajax-content",
    "#names-ajax-content", "#subjnames-ajax-content",
    # شجرة الفهرس الجانبية
    ".book-index", "#bookIndexScroll", ".plusbutton",
    ".BookDetail_1", ".tree_label",
    # عناصر الموقع العامة
    ".islamweb-font", ".dropdown", ".modal",
    "script", "style", "noscript",
    # أرقام الصفحات وأدوات الخط
    ".dropdown-menu", "#hidden-font-content",
    ".book-content > div:first-child",   # header الكتاب المتكرر
]

# نصوص تتكرر في كل صفحة — نحذفها
REPEATED_TEXTS = {
    "فهرس الكتاب", "السابق", "التالي", "إسلام ويب",
    "المكتبة الإسلامية", "تم نسخ الرابط",
    "إتحاف السادة المتقين بشرح إحياء علوم الدين",
    "مرتضى الزبيدي", "محمد بن محمد الحسيني الزبيدي",
}

# ══════════════════════════════════════════════════════════════════
# ٢. جلب محتوى node واحد — نظيف
# ══════════════════════════════════════════════════════════════════
def fetch_section(nid: int) -> dict | None:
    cache = f"output/sections/{nid}.json"
    if os.path.exists(cache):
        return load_json(cache, None)

    url = (f"{BASE_URL}/ar/library/maktaba/nindex.php"
           f"?id={nid}&bookid={BOOK_ID}&page=bookpages")
    soup, raw = fetch(url)
    if not soup:
        return None

    # ── ١. احذف العناصر غير المطلوبة ─────────────────────────────
    for sel in REMOVE_SELECTORS:
        for el in soup.select(sel):
            el.decompose()

    # ── ٢. استهدف container النص الرئيسي ─────────────────────────
    # .bookcontenttxt هو الـ div الذي يحمل النص حسب bookcontents.js
    container = (
        soup.find(class_="bookcontenttxt") or
        soup.find(class_="right-side")     or
        soup.find(class_="nass")           or
        soup.find("article")               or
        soup.find(id="updates")            or
        soup.body
    )
    if not container:
        return None

    # ── ٣. العنوان ───────────────────────────────────────────────
    title_el = container.find(["h1","h2","h3"])
    title    = title_el.get_text(strip=True)[:120] if title_el else f"قسم {nid}"

    # ── ٤. استخرج الفقرات النظيفة ────────────────────────────────
    seen, paragraphs = set(), []


# ══════════════════════════════════════════════════════════════════
# ٣. المرحلة ١ — scan: اكتشاف الـ nodes الصالحة
# ══════════════════════════════════════════════════════════════════
def phase_scan(start=1, end=LAST_ID):
    progress_file = "output/scan_progress.json"
    valid_file    = "output/valid_nodes.json"

    progress = load_json(progress_file, {"last": start - 1})
    valid    = load_json(valid_file, [])
    valid_set = set(valid)

    start = progress["last"] + 1
    print(f"=== SCAN: {start} → {end} ===")
    print(f"  nodes صالحة حتى الآن: {len(valid)}")

    empty_streak = 0  # عدّاد الـ nodes الفارغة المتتالية

    for nid in range(start, end + 1):
        result = fetch_section(nid)

        if result and result["paragraphs"]:
            q = "✅" if result["has_quran"] else ("📖" if result["has_hadith"] else "🔶")
            print(f"  {q} {nid:5d} | {result['title'][:55]:55s} | ف={len(result['paragraphs'])}")
            if nid not in valid_set:
                valid.append(nid)
                valid_set.add(nid)
            empty_streak = 0
        else:
            print(f"  ⬜ {nid:5d} | فارغ")
            empty_streak += 1

        # حفظ التقدم كل 50 node + commit للريبو
        if nid % 50 == 0:
            progress["last"] = nid
            save_json(progress_file, progress)
            save_json(valid_file, sorted(valid))
            print(f"  💾 تقدم محفوظ عند {nid} | صالح: {len(valid)}")
            # commit تلقائي أثناء التشغيل
            os.system(
                f'git add output/ && '
                f'git diff --cached --quiet || '
                f'git commit -m "scan checkpoint {nid}/{LAST_ID}" && '
                f'git push'
            )

        time.sleep(DELAY)

    # حفظ نهائي
    progress["last"] = end
    save_json(progress_file, progress)
    save_json(valid_file, sorted(valid))
    print(f"\n✅ SCAN اكتمل | nodes صالحة: {len(valid)} من {end}")
    return valid


# ══════════════════════════════════════════════════════════════════
# ٤. المرحلة ٢ — build: بناء EPUB
# ══════════════════════════════════════════════════════════════════
EPUB_CSS = """
body    { font-family: 'Traditional Arabic', serif; direction: rtl;
          text-align: right; line-height: 2.2; margin: 1em 1.5em; }
h1      { color: #8B0000; border-bottom: 2px solid #8B0000; margin-top: 1.5em; }
h2      { color: #5A3E1B; margin-top: 1.2em; }
.quran  { color: #006400; font-size: 1.1em; margin: 0.8em 0;
          padding: 0.5em; border-right: 4px solid #006400; background: #f0fff0; }
.hadith { color: #4B0082; margin: 0.8em 0; padding: 0.5em;
          border-right: 4px solid #4B0082; background: #f5f0ff; }
.name   { color: #8B4513; font-weight: bold; }
.text   { margin: 0.5em 0; }
"""

def phase_build():
    valid = load_json("output/valid_nodes.json", [])
    if not valid:
        print("✗ valid_nodes.json فارغ — شغّل scan أولاً")
        return

    toc_data = load_json("output/toc.json", [])
    toc_map  = {item["id"]: item for item in toc_data}

    print(f"=== BUILD EPUB: {len(valid)} فصل ===")

    book = epub.EpubBook()
    book.set_identifier(f"islamweb-{BOOK_ID}")
    book.set_title("إتحاف السادة المتقين بشرح إحياء علوم الدين")
    book.set_language("ar")
    book.add_author("مرتضى الزبيدي")

    css_item = epub.EpubItem(
        uid="style", file_name="style/main.css",
        media_type="text/css", content=EPUB_CSS
    )
    book.add_item(css_item)

    # غلاف
    cover = epub.EpubHtml(title="الغلاف", file_name="cover.xhtml", lang="ar")
    cover.content = """<html><body dir="rtl" style="text-align:center">
    <h1 style="color:#8B0000;margin-top:3em">
        إتحاف السادة المتقين<br/>بشرح إحياء علوم الدين
    </h1>
    <p style="font-size:1.3em">الإمام مرتضى الزبيدي</p>
    </body></html>"""
    cover.add_item(css_item)
    book.add_item(cover)

    chapters  = [cover]
    spine     = ["nav", cover]
    toc_items = []

    # بناء TOC هرمي من toc_data
    toc_by_level = {}
    for item in toc_data:
        lvl = str(item.get("level","1"))
        toc_by_level.setdefault(lvl, []).append(item["id"])

    for i, nid in enumerate(valid):
        sec = load_json(f"output/sections/{nid}.json", None)
        if not sec:
            continue

        # HTML الفصل
        body = f'<h1>{sec["title"]}</h1>\n'
        for p in sec["paragraphs"]:
            t = p["text"].replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
            if p["kind"] == "quran":
                body += f'<p class="quran">{t}</p>\n'
            elif p["kind"] == "hadith":
                body += f'<p class="hadith">{t}</p>\n'
            elif p["kind"] == "heading":
                body += f'<h2>{t}</h2>\n'
            else:
                body += f'<p class="text">{t}</p>\n'

        ch = epub.EpubHtml(
            title=sec["title"],
            file_name=f"s{nid}.xhtml",
            lang="ar"
        )
        ch.content = f'<html><body dir="rtl">{body}</body></html>'
        ch.add_item(css_item)
        book.add_item(ch)
        chapters.append(ch)
        spine.append(ch)
        toc_items.append(epub.Link(f"s{nid}.xhtml", sec["title"], f"s{nid}"))

        if i % 100 == 0:
            print(f"  📄 {i}/{len(valid)} — {sec['title'][:50]}")

    book.toc   = toc_items
    book.spine = spine
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())

    out = "output/ithaf_alsada.epub"
    epub.write_epub(out, book)

    size_mb = os.path.getsize(out) / 1024 / 1024
    print(f"\n✅ EPUB جاهز: {out} ({size_mb:.1f} MB)")
    print(f"   فصول: {len(chapters)-1} | آيات: {sum(1 for v in valid if load_json(f'output/sections/{v}.json',{}).get('has_quran'))}")


# ══════════════════════════════════════════════════════════════════
# ٥. التشغيل
# ══════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "test"

    print(f"{'='*55}")
    print(f"  إتحاف السادة المتقين — mode={mode}")
    print(f"{'='*55}\n")

    # ── أول شيء: ضبط cookie التشكيل ──────────────────────────────
    set_tashkeel_cookie()

    if mode == "test":
        build_toc()

        # ── تشخيص: اطبع HTML الخام لأول 5 nodes ──────────────────
        print("\n=== تشخيص nindex.php (أول 5 nodes) ===")
        for nid in range(1, 6):
            url = (f"{BASE_URL}/ar/library/maktaba/nindex.php"
                   f"?id={nid}&bookid={BOOK_ID}&page=bookpages")
            soup, raw = fetch(url)
            txt = soup.get_text(strip=True)[:200] if soup else "FAILED"
            print(f"\n  ── node {nid} ──")
            print(f"  URL    : {url}")
            print(f"  الحجم  : {len(raw)} حرف")
            print(f"  النص   : {txt[:150]}")
            print(f"  آيات   : {'✅' if '﴿' in raw else '❌'}")
            # احفظ HTML الخام للفحص
            with open(f"output/raw_node_{nid}.html","w",encoding="utf-8") as f:
                f.write(raw)
            time.sleep(1)

        print("\n=== scan أول 30 ===")
        phase_scan(1, 30)

        valid = load_json("output/valid_nodes.json", [])
        print(f"\n  nodes صالحة: {len(valid)}")

        if valid:
            phase_build()
        else:
            print("\n⚠ لا توجد nodes صالحة — راجع output/raw_node_1.html")
            print("  تحقق: هل nindex.php يعيد المحتوى أم صفحة فارغة؟")

    elif mode == "scan":
        build_toc()
        phase_scan(1, LAST_ID)

    elif mode == "resume":
        phase_scan(1, LAST_ID)   # يكمل من آخر نقطة

    elif mode == "build":
        phase_build()

    elif mode == "full":
        build_toc()
        phase_scan(1, LAST_ID)
        phase_build()