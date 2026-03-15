"""
scraper.py — النسخة الكاملة المصحّحة
الإصلاحات:
  ١. العنوان من <a id=N href=.../411/{idfrom}/...>
  ٢. الفهرس من نفس البنية
  ٣. mainsubj → حواشي مرقّمة مستقلة لكل صفحة
  ٤. التشكيل مضمون عبر cookie cval=1
  ٥. أسماء الأعلام <span> بدون class → (نص)
  ٦. حذف التالي/السابق من العناوين
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
    "User-Agent":       "Mozilla/5.0 (compatible; research-bot/1.0)",
    "Accept-Language":  "ar,en;q=0.9",
    "X-Requested-With": "XMLHttpRequest",
    "Referer":          f"{BASE_URL}/ar/library/content/{BOOK_ID}/1/",
}
SESSION = requests.Session()
SESSION.headers.update(HEADERS)
os.makedirs("output/sections", exist_ok=True)

# نصوص تتكرر في كل صفحة
REPEATED_TEXTS = {
    "فهرس الكتاب", "إسلام ويب", "المكتبة الإسلامية",
    "تم نسخ الرابط", "التالي", "السابق",
    "إتحاف السادة المتقين بشرح إحياء علوم الدين",
    "مرتضى الزبيدي", "محمد بن محمد الحسيني الزبيدي",
}


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
            print(f"  ⚠ محاولة {attempt+1} ({e}) — انتظار {wait}s")
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

def set_tashkeel_cookie():
    SESSION.get(f"{BASE_URL}/ar/library/content/{BOOK_ID}/1/مقدمة", timeout=15)
    SESSION.get(f"{BASE_URL}/ar/set_cookie.php", params={"cval": 1}, timeout=10)
    SESSION.cookies.set("cval", "1", domain="www.islamweb.net")
    print("✓ cookie التشكيل مضبوطة (cval=1)")

def has_tashkeel(text: str) -> bool:
    return any("\u064b" <= c <= "\u065f" for c in text[:500])


# ══════════════════════════════════════════════════════════════════
# ١. بناء الفهرس من <a id=N href=.../411/{idfrom}/...>
# ══════════════════════════════════════════════════════════════════
def build_toc() -> list:
    print("=== بناء الفهرس ===")
    url = f"{BASE_URL}/ar/library/content/{BOOK_ID}/1/مقدمة"
    soup, _ = fetch(url)
    if not soup:
        return []

    toc  = []
    seen = set()

    # البنية الحقيقية:
    # <span class="tree_label" data-level=1 data-id="4" data-idfrom=3 data-idto=41>
    #   <a href="...">عنوان</a>   أو النص مباشرة
    # </span>
    # أو:
    # <label class="plusbutton tree_label" data-level=1 data-id="4"
    #        data-idfrom=3 data-idto=41>عنوان</label>

    from bs4 import Tag

    for el in soup.find_all(["span","label"]):
        if not isinstance(el, Tag):
            continue
        if "tree_label" not in el.get("class", []):
            continue
        node_id = el.get("data-id", "").strip()
        level   = int(el.get("data-level", 1))
        idfrom  = el.get("data-idfrom")
        idto    = el.get("data-idto")

        # النص: من <a> الداخلي أو من النص المباشر
        a_inner = el.find("a")
        text    = a_inner.get_text(strip=True) if a_inner else el.get_text(strip=True)
        text    = text.strip()

        if not node_id.isdigit() or not text or not idfrom:
            continue
        if any(w in text for w in ["التالي","السابق"]):
            continue
        if node_id in seen:
            continue
        seen.add(node_id)

        toc.append({
            "id":     node_id,
            "idfrom": int(idfrom),
            "idto":   int(idto) if idto else int(idfrom),
            "level":  level,
            "text":   text[:120],
        })

    # ترتيب تصاعدي حسب idfrom
    toc.sort(key=lambda x: x["idfrom"])

    # طباعة الشجرة
    for item in toc:
        indent = "  " * (item["level"] - 1)
        print(f"  {indent}├─ [{item['idfrom']:5d}→{item['idto']:5d}] {item['text'][:55]}")

    save_json("output/toc.json", toc)
    print(f"\n✓ فهرس: {len(toc)} عنصر → output/toc.json")
    return toc


# ══════════════════════════════════════════════════════════════════
# ٢. استخراج النص النظيف مع الحواشي
# ══════════════════════════════════════════════════════════════════
def clean_and_extract(soup) -> tuple[list, list]:
    """
    يعيد: (paragraphs, footnotes)
    footnotes = قائمة مرقّمة مستقلة لكل صفحة
    """
    container = (
        soup.find(id="pagebody") or
        soup.find(class_="bookcontent-dic") or
        soup.find(class_="bookcontenttxt") or
        soup.body
    )
    if not container:
        return [], []

    # ── حذف عناصر التنقل ─────────────────────────────────────────
    for sel in [
        "script", "style", "noscript",
        "u.ul", "u.ur",                    # التالي / السابق
        ".quranatt", ".hadithatt",
        ".namesatt", ".mainsubjatt",
        ".hashiya_title",
    ]:
        for el in container.select(sel):
            el.decompose()

    # ── حذف أرقام الصفحات [ ص: N ] ──────────────────────────────
    for font in container.find_all("font"):
        if "ص:" in font.get_text() or font.get("color") == "blue":
            font.decompose()

    # ── فك روابط التفسير واترك النص ──────────────────────────────
    for a in container.find_all("a", onclick=True):
        a.replace_with(a.get_text())

    # ── mainsubj → جمعه في حواشي مرقّمة ─────────────────────────
    footnotes   = []
    fn_counter  = [0]
    fn_map      = {}   # نص → رقم الحاشية

    for span in container.find_all("span", class_="mainsubj"):
        txt = span.get_text(strip=True)
        if not txt:
            span.decompose()
            continue
        if txt not in fn_map:
            fn_counter[0] += 1
            fn_map[txt]    = fn_counter[0]
            footnotes.append({"num": fn_counter[0], "text": txt})
        num = fn_map[txt]
        span.replace_with(f"[{num}]")

    # ── آيات قرآنية → ﴿ نص ﴾ ─────────────────────────────────────
    for span in container.find_all("span", class_="quran"):
        txt = span.get_text(strip=True)
        if txt:
            span.replace_with(f" ﴿ {txt} ﴾ ")

    # ── أحاديث مصنّفة → (( نص )) ─────────────────────────────────
    for span in container.find_all("span", class_="hadith"):
        txt = span.get_text(strip=True)
        if txt:
            # احذف الأقواس الموجودة مسبقاً في المصدر
            txt = txt.strip('"\'""«»')
            span.replace_with(f" (( {txt} )) ")

    # ── النصوص الخضراء → (( نص )) ────────────────────────────────
    for span in container.find_all(
        "span", style=lambda s: s and "color:green" in s.replace(" ", "")
    ):
        txt = span.get_text(strip=True)
        if txt:
            txt = txt.strip('"\'""«»')
            span.replace_with(f" (( {txt} )) ")

    # ── أسماء الأعلام <span> بدون class → (نص) ──────────────────
    for span in container.find_all("span"):
        cls = span.get("class", [])
        sty = span.get("style", "")
        if not cls and "display:none" not in sty:
            txt = span.get_text(strip=True)
            if txt:
                span.replace_with(f" ({txt}) ")
            else:
                span.decompose()

    # ── إزالة spans المخفية المتبقية ─────────────────────────────
    for span in container.find_all(
        "span", style=lambda s: s and "display:none" in s
    ):
        span.decompose()

    # ── استخراج النص كتدفق ───────────────────────────────────────
    for br in container.find_all("br"):
        br.replace_with("\n")

    raw = container.get_text(separator="\n")

    seen, paragraphs = set(), []
    for line in raw.splitlines():
        txt = line.strip()
        if len(txt) < 5:
            continue
        if txt in seen or txt in REPEATED_TEXTS:
            continue
        if any(w in txt for w in ["التالي","السابق","فهرس الكتاب","إسلام ويب"]):
            continue
        if re.fullmatch(r'[\[\]\d\s:\.\-]+', txt):
            continue
        seen.add(txt)

        if "﴿" in txt and "﴾" in txt:
            kind = "quran"
        elif "(( " in txt and " ))" in txt:
            kind = "hadith"
        elif len(txt) < 100 and txt.endswith(":"):
            kind = "heading"
        else:
            kind = "text"

        paragraphs.append({"kind": kind, "text": txt})

    return paragraphs, footnotes


# ══════════════════════════════════════════════════════════════════
# ٣. جلب section بـ idfrom
# ══════════════════════════════════════════════════════════════════
def fetch_section(item: dict) -> dict | None:
    nid    = item["id"]
    idfrom = item["idfrom"]
    idto   = item.get("idto", idfrom)
    title  = item["text"]

    cache = f"output/sections/{nid}.json"
    if os.path.exists(cache):
        return load_json(cache, None)

    url = (f"{BASE_URL}/ar/library/maktaba/nindex.php"
           f"?id={nid}&bookid={BOOK_ID}&idfrom={idfrom}&idto={idto}&page=bookpages")
    soup, raw = fetch(url)
    if not soup or len(raw) < 200:
        return None

    # تحقق التشكيل — أعد المحاولة إن غاب
    if not has_tashkeel(raw):
        set_tashkeel_cookie()
        soup, raw = fetch(url)
        if not soup:
            return None

    paragraphs, footnotes = clean_and_extract(soup)
    if not paragraphs:
        return None

    result = {
        "node_id":   nid,
        "title":     title,
        "idfrom":    idfrom,
        "idto":      idto,
        "paragraphs":paragraphs,
        "footnotes": footnotes,
        "has_quran": any(p["kind"] == "quran"  for p in paragraphs),
        "has_hadith":any(p["kind"] == "hadith" for p in paragraphs),
    }
    save_json(cache, result)
    return result


# ══════════════════════════════════════════════════════════════════
# ٤. SCAN
# ══════════════════════════════════════════════════════════════════
def phase_scan(end_idx=None):
    toc = load_json("output/toc.json", [])
    if not toc:
        print("✗ toc.json فارغ"); return []

    progress_file = "output/scan_progress.json"
    valid_file    = "output/valid_nodes.json"
    progress  = load_json(progress_file, {"last_idx": -1})
    valid     = load_json(valid_file, [])
    valid_set = set(v["id"] for v in valid)

    start = progress["last_idx"] + 1
    end   = end_idx or len(toc)
    print(f"=== SCAN {start}→{end} ({len(toc)} في الفهرس) ===")

    for idx in range(start, min(end, len(toc))):
        item   = toc[idx]
        result = fetch_section(item)

        if result and result["paragraphs"]:
            q = "✅" if result["has_quran"] else ("📖" if result["has_hadith"] else "🔶")
            fn = f" | ح={len(result['footnotes'])}" if result["footnotes"] else ""
            print(f"  {q} {idx:4d} | {result['title'][:50]:50s}{fn}")
            if item["id"] not in valid_set:
                valid.append({"id": item["id"], "title": item["text"], "level": item["level"]})
                valid_set.add(item["id"])
        else:
            print(f"  ⬜ {idx:4d} | {item['text'][:50]}")

        if idx % 50 == 0:
            progress["last_idx"] = idx
            save_json(progress_file, progress)
            save_json(valid_file, valid)
            os.system(
                'git add output/ && git diff --cached --quiet || '
                f'git commit -m "scan checkpoint {idx}/{len(toc)}" && git push'
            )
        time.sleep(DELAY)

    progress["last_idx"] = end - 1
    save_json(progress_file, progress)
    save_json(valid_file, valid)
    print(f"\n✅ SCAN | صالح: {len(valid)}/{len(toc)}")
    return valid


# ══════════════════════════════════════════════════════════════════
# ٥. BUILD EPUB
# ══════════════════════════════════════════════════════════════════
EPUB_CSS = """
body    { font-family:'Traditional Arabic',serif; direction:rtl;
          text-align:right; line-height:2.2; margin:1em 1.5em; }
h1      { color:#8B0000; border-bottom:2px solid #8B0000; margin-top:1.5em; }
h2      { color:#5A3E1B; margin-top:1.2em; }
.quran  { color:#006400; font-size:1.1em; margin:.8em 0; padding:.5em;
          border-right:4px solid #006400; background:#f0fff0; }
.hadith { color:#4B0082; margin:.8em 0; padding:.5em;
          border-right:4px solid #4B0082; background:#f5f0ff; }
.text   { margin:.5em 0; }
.footnotes { border-top:1px solid #ccc; margin-top:2em; padding-top:.5em;
             font-size:.9em; color:#555; }
.fn-item { margin:.3em 0; }
"""

def phase_build():
    valid = load_json("output/valid_nodes.json", [])
    if not valid:
        print("✗ valid_nodes.json فارغ"); return

    # توافق مع النسخ القديمة التي تحتوي أرقاماً بدل قواميس
    if valid and isinstance(valid[0], int):
        valid = [{"id": str(v), "title": f"قسم {v}", "level": 1} for v in valid]

    toc_data = load_json("output/toc.json", [])
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

    chapters = [cover]
    spine    = ["nav", cover]
    toc_epub = []

    for i, v in enumerate(valid):
        sec = load_json(f"output/sections/{v['id']}.json", None)
        if not sec:
            continue

        # بناء HTML الفصل
        body = f'<h1>{sec["title"]}</h1>\n'

        for p in sec["paragraphs"]:
            t = (p["text"]
                 .replace("&","&amp;")
                 .replace("<","&lt;")
                 .replace(">","&gt;"))
            if p["kind"] == "quran":
                body += f'<p class="quran">{t}</p>\n'
            elif p["kind"] == "hadith":
                body += f'<p class="hadith">{t}</p>\n'
            elif p["kind"] == "heading":
                body += f'<h2>{t}</h2>\n'
            else:
                body += f'<p class="text">{t}</p>\n'

        # الحواشي
        if sec.get("footnotes"):
            body += '<div class="footnotes">\n'
            for fn in sec["footnotes"]:
                t = (fn["text"]
                     .replace("&","&amp;")
                     .replace("<","&lt;")
                     .replace(">","&gt;"))
                body += f'<p class="fn-item">[{fn["num"]}] {t}</p>\n'
            body += '</div>\n'

        ch = epub.EpubHtml(
            title=sec["title"],
            file_name=f"s{v['id']}.xhtml",
            lang="ar"
        )
        ch.content = f'<html><body dir="rtl">{body}</body></html>'
        ch.add_item(css_item)
        book.add_item(ch)
        chapters.append(ch)
        spine.append(ch)
        toc_epub.append(epub.Link(f"s{v['id']}.xhtml", sec["title"], f"s{v['id']}"))

        if i % 100 == 0:
            print(f"  📄 {i}/{len(valid)}")

    book.toc   = toc_epub
    book.spine = spine
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())

    out = "output/ithaf_alsada.epub"
    epub.write_epub(out, book)
    size = os.path.getsize(out) / 1024 / 1024
    print(f"\n✅ EPUB: {out} ({size:.1f} MB) | {len(chapters)-1} فصل")


# ══════════════════════════════════════════════════════════════════
# ٦. التشغيل
# ══════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "test"
    print(f"{'='*55}\n  إتحاف السادة المتقين — mode={mode}\n{'='*55}\n")

    set_tashkeel_cookie()

    if mode == "test":
        toc = build_toc()
        phase_scan(end_idx=min(30, len(toc)))
        phase_build()

    elif mode == "scan":
        build_toc()
        phase_scan()

    elif mode == "resume":
        phase_scan()

    elif mode == "build":
        phase_build()

    elif mode == "full":
        build_toc()
        phase_scan()
        phase_build()