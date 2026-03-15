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
def parse_hid(hid: str) -> tuple:
    """استخرج idfrom و idto من hid string"""
    idfrom = re.search(r'idfrom=(\d+)', hid)
    idto   = re.search(r'idto=(\d+)',   hid)
    return (
        int(idfrom.group(1)) if idfrom else None,
        int(idto.group(1))   if idto   else None,
    )

def fetch_subtree(node_id: str, level: int = 1) -> list:
    """جلب الفصول الداخلية لـ node عبر AJAX"""
    # نجرب نمطين شائعين لروابط الشجرة
    urls = [
        f"{BASE_URL}/ar/library/maktaba/nindex.php?id={node_id}&bookid={BOOK_ID}&page=roottreedetail",
        f"{BASE_URL}/ar/library/maktaba/nindex.php?id={node_id}&bookid={BOOK_ID}&page=treedetail",
    ]
    items = []
    for url in urls:
        soup, _ = fetch(url)
        if not soup:
            continue
        found = False
        for inp in soup.find_all("input", id=re.compile("^HidParam")):
            nid  = inp["id"].replace("HidParam","")
            hid  = inp.get("value","")
            # ابحث عن العنصر المرتبط
            label = soup.find(id=nid) or soup.find(attrs={"data-id": nid})
            text  = label.get_text(strip=True)[:120] if label else f"قسم {nid}"
            if hid and nid:
                items.append({"id": nid, "level": level, "text": text, "hid": hid})
                found = True
        if found:
            break
        time.sleep(0.3)
    return items

def build_toc() -> list:
    url = f"{BASE_URL}/ar/library/content/{BOOK_ID}/1/مقدمة"
    soup, _ = fetch(url)
    if not soup:
        return []

    # ── المستوى الأول من HidParam ─────────────────────────────────
    hid = {}
    for inp in soup.find_all("input", id=re.compile("^HidParam")):
        nid = inp["id"].replace("HidParam","")
        hid[nid] = inp.get("value","")

    toc = []
    seen = set()

    for el in soup.find_all(class_=["tree_label","plusbutton","BookDetail_1"]):
        nid   = el.get("id") or el.get("data-id")
        text  = el.get_text(strip=True)[:120]
        level = int(el.get("data-level", 1))
        h     = hid.get(nid,"")
        if nid and text and nid not in seen and h:
            seen.add(nid)
            toc.append({"id": nid, "level": level, "text": text, "hid": h})

    print(f"  المستوى الأول: {len(toc)} عنصر")

    # ── جلب الفصول الداخلية لكل عنصر رئيسي ──────────────────────
    expanded = []
    for item in toc:
        expanded.append(item)
        idfrom, idto = parse_hid(item["hid"])
        # إذا النطاق كبير → يحتوي فصول داخلية
        if idfrom and idto and (idto - idfrom) > 5:
            print(f"  ↳ جلب فصول '{item['text'][:40]}' (id={item['id']})...")
            children = fetch_subtree(item["id"], item["level"] + 1)
            print(f"    {len(children)} فصل داخلي")
            for ch in children:
                if ch["id"] not in seen:
                    seen.add(ch["id"])
                    expanded.append(ch)
            time.sleep(0.5)

    # ── ترتيب حسب idfrom ─────────────────────────────────────────
    def sort_key(x):
        fr, _ = parse_hid(x["hid"])
        return fr or 0

    expanded.sort(key=sort_key)

    save_json("output/toc.json", expanded)
    print(f"✓ فهرس كامل: {len(expanded)} عنصر → output/toc.json")
    return expanded


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

    seen, paragraphs = set(), []
    for tag in container.find_all(["p","h1","h2","h3","b","span"]):
        txt = tag.get_text(strip=True)
        if len(txt) < 8 or txt in seen or txt in REPEATED_TEXTS:
            continue
        # تجاهل نصوص التنقل
        if any(w in txt for w in ["التالي","السابق","الصفحة","فهرس"]):
            continue
        seen.add(txt)
        cls = " ".join(tag.get("class",[]))

        if "﴿" in txt or "﴾" in txt:
            kind = "quran"
        elif any(c in cls for c in ["hadith","hadithatt"]):
            kind = "hadith"
        elif tag.name in ["h1","h2","h3"] or (tag.name=="b" and len(txt)<80):
            kind = "heading"
        else:
            kind = "text"

        paragraphs.append({"kind": kind, "text": txt})

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
            elif p["kind"] == "name":
                body += f'<span class="name">{t}</span>\n'
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