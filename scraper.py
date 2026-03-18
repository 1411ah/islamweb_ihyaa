"""
scraper.py
python scraper.py test    <- اختبار اول 30 ID
python scraper.py scan    <- مسح كامل 1 الى 8173
python scraper.py resume  <- استكمال
python scraper.py fix     <- اصلاح العناوين
python scraper.py build   <- بناء EPUB
python scraper.py full    <- scan + build
"""

import requests
from bs4 import BeautifulSoup, Tag
from ebooklib import epub
import json, os, re, time, sys, traceback
import functools

print = functools.partial(print, flush=True)

BASE_URL = "https://www.islamweb.net"
BOOK_ID  = 411
FIRST_ID = 1
LAST_ID  = 8173
DELAY    = 0.8
HEADERS  = {
    "User-Agent":       "Mozilla/5.0 (compatible; research-bot/1.0)",
    "Accept-Language":  "ar,en;q=0.9",
    "X-Requested-With": "XMLHttpRequest",
    "Referer":          f"{BASE_URL}/ar/library/content/{BOOK_ID}/1/",
}
SESSION = requests.Session()
SESSION.headers.update(HEADERS)
os.makedirs("output/sections", exist_ok=True)

REPEATED_TEXTS = {
    "فهرس الكتاب", "اسلام ويب", "المكتبة الاسلامية",
    "تم نسخ الرابط", "التالي", "السابق",
    "اتحاف السادة المتقين بشرح احياء علوم الدين",
    "مرتضى الزبيدي", "محمد بن محمد الحسيني الزبيدي",
}


# ================================================================
# ادوات مشتركة
# ================================================================
def fetch(url, retries=3):
    for attempt in range(retries):
        try:
            r = SESSION.get(url, timeout=20)
            r.raise_for_status()
            return BeautifulSoup(r.text, "lxml"), r.text
        except Exception as e:
            wait = 5 * (attempt + 1)
            print(f"  محاولة {attempt+1} ({e}) انتظار {wait}s")
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
    SESSION.get(f"{BASE_URL}/ar/library/content/{BOOK_ID}/1/", timeout=15)
    SESSION.get(f"{BASE_URL}/ar/set_cookie.php", params={"cval": 1}, timeout=10)
    SESSION.cookies.set("cval", "1", domain="www.islamweb.net")
    print("cookie التشكيل cval=1")

def has_tashkeel(text):
    return any("\u064b" <= c <= "\u065f" for c in text[:3000])

def extract_title_and_level(soup, fallback_title, fallback_level=1):
    normalize = lambda t: re.sub(r'[إأآا]', 'ا', t or "")

    crumbs = [t for t in soup.find_all(True)
              if isinstance(t, Tag) and t.get("itemprop") == "itemListElement"]

    if crumbs:
        level = max(1, len(crumbs) - 1)
        span = crumbs[-1].find("span", itemprop="name")
        if span:
            txt = span.get_text(strip=True)
            n = normalize(txt)
            if txt and len(txt) > 3 and "اتحاف" not in n and "اسلام ويب" not in n:
                return txt, level

    pt = soup.find("title")
    if pt:
        for part in reversed(pt.get_text().split(" - ")):
            p = part.strip()
            n = normalize(p)
            if (p and len(p) > 3
                    and "اسلام ويب" not in n
                    and "اتحاف" not in n
                    and "الجزء" not in n
                    and "رقم" not in n):
                return p, fallback_level

    return fallback_title, fallback_level


# ================================================================
# 1. بناء الفهرس من الشجرة
# ================================================================
def build_toc():
    print("=== بناء الفهرس ===")
    url = f"{BASE_URL}/ar/library/content/{BOOK_ID}/1/"
    soup, _ = fetch(url)
    if not soup:
        return []

    toc, seen = [], set()
    scroll = soup.find(id="bookIndexScroll") or soup
    top = []

    for el in scroll.find_all(["span", "label"]):
        if not isinstance(el, Tag):
            continue
        if "tree_label" not in (el.get("class") or []):
            continue
        nid   = (el.get("data-id") or "").strip()
        fr    = el.get("data-idfrom")
        to    = el.get("data-idto")
        lvl   = int(el.get("data-level") or 1)
        dh    = el.get("data-href") or ""
        a     = el.find("a")
        txt   = (a.get_text(strip=True) if a else el.get_text(strip=True)).strip()
        txt   = re.sub(r"(التالي|السابق)", "", txt).strip()
        if not nid.isdigit() or not txt or not fr or nid in seen:
            continue
        seen.add(nid)
        item = {"id": nid, "idfrom": int(fr), "idto": int(to) if to else int(fr),
                "level": lvl, "text": txt[:120], "dhref": dh}
        top.append(item)
        toc.append(item)

    print(f"  المستوى الاول: {len(top)}")

    def crawl(item, depth=0):
        if depth > 6 or not item.get("dhref"):
            return
        ss, _ = fetch(f"{BASE_URL}/ar/library/maktaba/{item['dhref']}")
        if not ss:
            return
        found = 0
        for el in ss.find_all(["span", "label", "a", "li"]):
            if not isinstance(el, Tag):
                continue
            nid = (el.get("data-id") or el.get("id") or "").strip()
            fr  = el.get("data-idfrom")
            to  = el.get("data-idto")
            dh  = el.get("data-href") or ""
            lvl = int(el.get("data-level") or item["level"] + 1)
            txt = re.sub(r"(التالي|السابق)", "", el.get_text(strip=True)[:120]).strip()
            if not nid.isdigit() or not fr or not txt or nid in seen:
                continue
            seen.add(nid)
            child = {"id": nid, "idfrom": int(fr), "idto": int(to) if to else int(fr),
                     "level": lvl, "text": txt, "dhref": dh}
            toc.append(child)
            found += 1
            if dh and depth < 6:
                time.sleep(0.3)
                crawl(child, depth + 1)
        print(f"  {'  '*depth}{item['text'][:40]} {found}")
        time.sleep(0.4)

    for item in top:
        crawl(item)

    toc.sort(key=lambda x: x["idfrom"])
    seen2, final = set(), []
    for item in toc:
        if item["id"] not in seen2:
            seen2.add(item["id"])
            final.append(item)

    save_json("output/toc.json", final)
    print(f"فهرس الشجرة: {len(final)}")
    return final


# ================================================================
# 2. بناء الفهرس من ملفات sections
# ================================================================
def build_toc_from_scan():
    print("=== فهرس من scan ===")
    d = "output/sections"
    if not os.path.exists(d):
        return []
    tree_map = {t["id"]: t.get("level", 1) for t in load_json("output/toc.json", [])}
    toc = []
    files = sorted([f for f in os.listdir(d) if f.endswith(".json")],
                   key=lambda f: int(f.replace(".json","")) if f.replace(".json","").isdigit() else 0)
    for fname in files:
        nid = fname.replace(".json","")
        sec = load_json(f"{d}/{fname}", None)
        if not sec:
            continue
        toc.append({"id": nid, "idfrom": sec.get("idfrom", int(nid)),
                    "idto": sec.get("idto", int(nid)),
                    "level": sec.get("level") or tree_map.get(nid, 1),
                    "text": sec.get("title", f"قسم {nid}")})
    save_json("output/toc_from_scan.json", toc)
    print(f"فهرس كامل: {len(toc)}")
    return toc


# ================================================================
# 3. اصلاح العناوين
# ================================================================
def fix_titles():
    print("=== اصلاح العناوين ===")
    d = "output/sections"
    fixed = 0
    skipped = 0
    files = [f for f in os.listdir(d) if f.endswith(".json")]
    total = len(files)
    print(f"  اجمالي: {total}")

    for i, fname in enumerate(files):
        path = f"{d}/{fname}"
        sec  = load_json(path, None)
        if not sec:
            continue
        title = sec.get("title", "")
        bad = (re.match(r"^قسم \d+$", title) or
               len(title) < 4 or
               "اتحاف السادة" in title)
        if not bad:
            skipped += 1
            continue

        nid    = sec["node_id"]
        idfrom = sec.get("idfrom", int(nid))

        url = f"{BASE_URL}/ar/library/content/{BOOK_ID}/{idfrom}/"
        try:
            r = SESSION.get(url, timeout=15, allow_redirects=True)
            soup = BeautifulSoup(r.text, "lxml")
            old_level = sec.get("level", 1)
            new_title, new_level = extract_title_and_level(soup, title, old_level)

            changed = False
            if new_title and new_title != title and len(new_title) > 3:
                sec["title"] = new_title
                changed = True
            if new_level != old_level:
                sec["level"] = new_level
                changed = True

            if changed:
                save_json(path, sec)
                fixed += 1
                print(f"  [{i+1}/{total}] {nid} (L{new_level}): {sec['title'][:55]}")
            else:
                print(f"  [{i+1}/{total}] {nid}: فشل")

        except Exception as e:
            print(f"  [{i+1}/{total}] خطا {nid}: {e}")

        if (i + 1) % 100 == 0:
            print(f"  === تقدم {i+1}/{total} | اصلح: {fixed} ===")

        time.sleep(0.4)

    print(f"\n=== اكتمل ===")
    print(f"  صحيحة مسبقاً: {skipped}")
    print(f"  تم اصلاحها:   {fixed}")
    print(f"  لم تُصلح:     {total - skipped - fixed}")


# ================================================================
# 4. استخراج النص
# ================================================================
def clean_and_extract(soup):
    for el in soup.find_all("h1", class_="booktitle"):
        el.decompose()
    for el in soup.find_all("u", class_=["ul", "ur"]):
        el.decompose()

    # تحويل hashiya_title الى فاصل بدل حذفه
    for el in soup.find_all("span", class_="hashiya_title"):
        el.replace_with("\n[SECTION_BREAK]\n")

    container = (soup.find(id="pagebody_thaskeel") or
                 soup.find(id="pagebody") or
                 soup.find(class_="bookcontent-dic") or
                 soup.body)
    if not container:
        return [], []

    for sel in ["script","style","noscript",".quranatt",".hadithatt",
                ".namesatt",".mainsubjatt"]:
        for el in container.select(sel):
            el.decompose()

    for p in container.find_all("p", align="center"):
        if not isinstance(p, Tag):
            continue
        txt = p.get_text(strip=True)
        if txt:
            p.replace_with(f"\n[CENTER]{txt}[/CENTER]\n")

    for font in container.find_all("font"):
        if not isinstance(font, Tag) or not hasattr(font, 'attrs'):
            continue
        txt   = font.get_text()
        color = font.get("color") or ""
        if color == "blue" and "ص:" in txt:
            m = re.search(r"ص:\s*(\d+)", txt)
            font.replace_with(f"\n[ الجزء 1 صفحة {m.group(1)} ]\n" if m else "")
        elif "ص:" in txt:
            font.decompose()

    for a in container.find_all("a", onclick=True):
        a.replace_with(a.get_text())

    for span in container.find_all("span", class_="mainsubj"):
        if not isinstance(span, Tag):
            continue
        txt = span.get_text(strip=True)
        span.replace_with(f" {txt} " if txt else "")

    for span in container.find_all("span", class_="quran"):
        if not isinstance(span, Tag):
            continue
        txt = span.get_text(strip=True)
        if txt:
            span.replace_with(f" {chr(0xFD3E)} {txt} {chr(0xFD3F)} ")

    for span in container.find_all("span", class_="hadith"):
        if not isinstance(span, Tag):
            continue
        txt = span.get_text(strip=True).strip('"\'')
        if txt:
            span.replace_with(f" (( {txt} )) ")

    for span in list(container.find_all("span")):
        if not isinstance(span, Tag):
            continue
        style = (span.get("style") or "").replace(" ","")
        if "color:green" in style:
            txt = span.get_text(strip=True).strip('"\'')
            if txt:
                span.replace_with(f" (( {txt} )) ")

    for span in list(container.find_all("span")):
        if not isinstance(span, Tag):
            continue
        cls   = span.get("class") or []
        style = (span.get("style") or "").replace(" ","")
        if not cls and "display:none" not in style:
            txt = span.get_text(strip=True)
            span.replace_with(f" ({txt}) " if txt else "")
        elif "display:none" in style:
            span.decompose()

    for br in container.find_all("br"):
        br.replace_with("\n")

    raw  = container.get_text(separator="\n")
    seen, paragraphs = set(), []
    after_break = False  # تتبع: هل نحن مباشرة بعد فاصل؟

    for line in raw.splitlines():
        txt = line.strip()
        if len(txt) < 5:
            continue
        if txt in seen and txt != "[SECTION_BREAK]":
            continue
        if any(w in txt for w in ["التالي","السابق","فهرس الكتاب","اسلام ويب"]):
            continue
        if re.fullmatch(r"[\[\]\d\s:\.\-،,]+", txt):
            continue
        seen.add(txt)

        # تصنيف النوع
        if txt == "[SECTION_BREAK]":
            kind = "break"
            after_break = True
        elif txt.startswith("[CENTER]") and txt.endswith("[/CENTER]"):
            kind = "center"
            after_break = False
        elif txt.startswith("[") and txt.endswith("]") and "صفحة" in txt:
            kind = "pagebreak"
            after_break = False
        elif chr(0xFD3E) in txt:
            kind = "quran"
            after_break = False
        elif "(( " in txt and " ))" in txt:
            kind = "hadith"
            after_break = False
        elif after_break and txt.startswith("("):
            # المتن: فقط النص الأول بعد الفاصل المبدوء بقوس
            kind = "asl"
            after_break = False
        elif len(txt) < 100 and txt.endswith(":"):
            kind = "heading"
            after_break = False
        else:
            kind = "text"
            after_break = False

        paragraphs.append({"kind": kind, "text": txt})

    return paragraphs, []


# ================================================================
# 5. جلب section
# ================================================================
def fetch_section(item):
    nid    = str(item["id"])
    idfrom = item["idfrom"]
    idto   = item.get("idto", idfrom)
    title  = item["text"]

    cache = f"output/sections/{nid}.json"
    if os.path.exists(cache):
        return load_json(cache, None)

    # المحاولة الاولى: nindex.php
    url = (f"{BASE_URL}/ar/library/maktaba/nindex.php"
           f"?id={nid}&bookid={BOOK_ID}&idfrom={idfrom}&idto={idto}&page=bookpages")
    soup, raw = fetch(url)

    # المحاولة الثانية: URL المباشر اذا nindex رجع فارغ
    if not soup or len(raw) < 200 or not BeautifulSoup(raw, "lxml").find(id=["pagebody","pagebody_thaskeel"]):
        url = f"{BASE_URL}/ar/library/content/{BOOK_ID}/{nid}/"
        soup, raw = fetch(url)
        if not soup or len(raw) < 200:
            return None

    if not has_tashkeel(raw):
        set_tashkeel_cookie()
        soup, raw = fetch(url)
        if not soup:
            return None

    real_title, real_level = extract_title_and_level(soup, title, item.get("level", 1))
    paragraphs, _ = clean_and_extract(soup)
    if not paragraphs:
        return None

    result = {
        "node_id":    nid,
        "title":      real_title,
        "level":      real_level,
        "idfrom":     idfrom,
        "idto":       idto,
        "paragraphs": paragraphs,
        "has_quran":  any(p["kind"] == "quran"  for p in paragraphs),
        "has_hadith": any(p["kind"] == "hadith" for p in paragraphs),
    }
    save_json(cache, result)
    return result


# ================================================================
# 6. SCAN
# ================================================================
def phase_scan(end_id=None):
    progress_file = "output/scan_progress.json"
    valid_file    = "output/valid_nodes.json"
    progress  = load_json(progress_file, {"last_id": FIRST_ID - 1})
    valid     = load_json(valid_file, [])
    valid_set = set(v["id"] for v in valid)
    toc_map   = {int(t["id"]): t for t in load_json("output/toc.json", [])}

    start = progress["last_id"] + 1
    end   = end_id or LAST_ID
    total = end - start + 1
    print(f"=== SCAN {start} الى {end} ({total}) ===")

    for nid in range(start, end + 1):
        item = dict(toc_map.get(nid, {
            "id": str(nid), "idfrom": nid, "idto": nid, "level": 1, "text": f"قسم {nid}"
        }))
        item["id"] = str(nid)

        try:
            result = fetch_section(item)
        except Exception as e:
            print(f"  خطا id={nid}: {e}")
            traceback.print_exc()
            result = None

        if result and result["paragraphs"]:
            q = "Q" if result["has_quran"] else ("H" if result["has_hadith"] else "+")
            print(f"  {q} {nid:5d} | {result['title'][:55]}")
            if str(nid) not in valid_set:
                valid.append({"id": str(nid), "title": result["title"],
                              "level": item.get("level", 1)})
                valid_set.add(str(nid))
        else:
            print(f"    {nid:5d} فارغ")

        if nid % 50 == 0:
            progress["last_id"] = nid
            save_json(progress_file, progress)
            save_json(valid_file, valid)
            pct = round((nid - start + 1) / total * 100)
            print(f"  checkpoint {nid}/{end} ({pct}%) صالح: {len(valid)}")
            os.system(
                'git config user.name "github-actions[bot]" && '
                'git config user.email "github-actions[bot]@users.noreply.github.com" && '
                'git add output/ && git diff --cached --quiet || '
                f'git commit -m "scan {nid}/{end} ({pct}%)" && git push'
            )
        time.sleep(DELAY)

    progress["last_id"] = end
    save_json(progress_file, progress)
    save_json(valid_file, valid)
    print(f"SCAN اكتمل صالح: {len(valid)}/{total}")
    return valid


# ================================================================
# 7. BUILD EPUB
# ================================================================
EPUB_CSS = """
body      { font-family: 'Traditional Arabic', serif; direction: rtl;
            text-align: right; line-height: 2.2; margin: 1em 1.5em;
            font-size: 1em; }
h1        { color: #8B0000; border-bottom: 2px solid #8B0000;
            margin-top: 1.5em; font-size: 1em; font-weight: bold; }
h2        { color: #5A3E1B; margin-top: 1.2em;
            font-size: 1em; font-weight: bold; }
.quran    { color: #006400; margin: .8em 0; padding: .5em;
            border-right: 4px solid #006400; background: #f0fff0;
            font-size: 1em; }
.hadith   { color: #4B0082; margin: .8em 0; padding: .5em;
            border-right: 4px solid #4B0082; background: #f5f0ff;
            font-size: 1em; }
.text     { margin: .5em 0; font-size: 1em; }
.center   { text-align: center; font-style: italic;
            margin: 1em 2em; color: #4a0080; font-size: 1em; }
.pagebreak{ text-align: center; color: #8B0000; font-size: 1em;
            border-top: 1px solid #ccc; border-bottom: 1px solid #ccc;
            margin: 1em 0; padding: .3em 0; }
.asl      { background: #fef9e7; border-right: 4px solid #c9a227;
            padding: .6em .8em; margin: .8em 0;
            color: #3a2a00; font-size: 1em; }
.section-break { border: none; border-top: 1px dashed #b0a080;
                 margin: 1em 0; }
.page-ref      { margin-top: 2em; padding-top: .5em;
                 border-top: 1px solid #999;
                 color: #555; font-size: .85em;
                 width: 50%; direction: rtl; text-align: right; }
"""

def phase_build():
    valid = load_json("output/valid_nodes.json", [])
    if not valid:
        print("valid_nodes.json فارغ")
        return
    if valid and isinstance(valid[0], int):
        valid = [{"id": str(v), "title": f"قسم {v}", "level": 1} for v in valid]

    toc_data = (load_json("output/toc_from_scan.json", None) or
                load_json("output/toc.json", []))
    print(f"=== BUILD EPUB: {len(valid)} فصل ===")

    book = epub.EpubBook()
    book.set_identifier(f"islamweb-{BOOK_ID}")
    book.set_title("اتحاف السادة المتقين بشرح احياء علوم الدين")
    book.set_language("ar")
    book.add_author("مرتضى الزبيدي")
    book.set_direction("rtl")
    book.add_metadata("OPF", "meta", "", {"name": "primary-writing-mode", "content": "horizontal-rl"})

    css_item = epub.EpubItem(uid="style", file_name="style/main.css",
                             media_type="text/css", content=EPUB_CSS)
    book.add_item(css_item)

    cover = epub.EpubHtml(title="الغلاف", file_name="cover.xhtml", lang="ar")
    cover.content = (
        '<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="ar" lang="ar" dir="rtl">'
        '<head><meta charset="utf-8"/><title>الغلاف</title></head>'
        '<body dir="rtl" style="text-align:center">'
        '<h1 style="color:#8B0000;margin-top:3em">'
        'اتحاف السادة المتقين<br/>بشرح احياء علوم الدين</h1>'
        '<p style="font-size:1.3em">الامام مرتضى الزبيدي</p>'
        '</body></html>'
    )
    cover.add_item(css_item)
    book.add_item(cover)

    chapters, spine, toc_epub, id_to_ch = [cover], ["nav", cover], [], {}

    for i, v in enumerate(valid):
        sec = load_json(f"output/sections/{v['id']}.json", None)
        if not sec:
            continue
        title_safe = sec["title"].replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
        body = f"<h1>{title_safe}</h1>\n"

        for p in sec["paragraphs"]:
            t = (p["text"].replace("&","&amp;").replace("<","&lt;").replace(">","&gt;"))
            if p["kind"] == "break":
                body += '<hr class="section-break"/>\n'
            elif p["kind"] == "asl":
                body += f'<p class="asl">{t}</p>\n'
            elif p["kind"] == "center":
                t2 = t.replace("[CENTER]","").replace("[/CENTER]","")
                body += f'<p class="center">{t2}</p>\n'
            elif p["kind"] == "pagebreak":
                body += f'<p class="pagebreak">{t}</p>\n'
            elif p["kind"] == "quran":
                body += f'<p class="quran">{t}</p>\n'
            elif p["kind"] == "hadith":
                body += f'<p class="hadith">{t}</p>\n'
            elif p["kind"] == "heading":
                body += f"<h2>{t}</h2>\n"
            else:
                body += f'<p class="text">{t}</p>\n'

        # اجمع كل ارقام الصفحات من داخل الفصل
        page_refs = [p["text"] for p in sec["paragraphs"] if p["kind"] == "pagebreak"]
        if page_refs:
            refs_text = " ← ".join(
                r.replace("[","").replace("]","").strip() for r in page_refs
            )
            body += f'<p class="page-ref">المصدر: {refs_text}</p>\n'

        ch = epub.EpubHtml(title=sec["title"], file_name=f"s{v['id']}.xhtml", lang="ar")
        ch.content = (
            '<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="ar" lang="ar" dir="rtl">'
            f'<head><meta charset="utf-8"/><title>{title_safe}</title></head>'
            f'<body dir="rtl">{body}</body></html>'
        )
        ch.add_item(css_item)
        book.add_item(ch)
        chapters.append(ch)
        spine.append(ch)
        toc_epub.append(epub.Link(f"s{v['id']}.xhtml", sec["title"], f"s{v['id']}"))
        id_to_ch[v["id"]] = ch
        if i % 100 == 0:
            print(f"  {i}/{len(valid)}")

    def build_epub_toc(items):
        result, stack = [], []
        for item in items:
            if item["id"] not in id_to_ch:
                continue
            title = item.get("text") or item.get("title") or f"قسم {item['id']}"
            lnk   = epub.Link(f"s{item['id']}.xhtml", title, f"s{item['id']}")
            lvl   = item.get("level", 1)
            entry = (lnk, [])
            if not stack or lvl == 1:
                result.append(entry)
                stack = [(lvl, entry)]
            else:
                while len(stack) > 1 and stack[-1][0] >= lvl:
                    stack.pop()
                stack[-1][1][1].append(entry)
                stack.append((lvl, entry))
        return result

    valid_map = {v["id"]: v for v in valid}
    # ادمج: toc_data للترتيب والمستوى، وأي node ناقصة تُضاف من valid
    toc_ids_seen = set()
    toc_items = []
    for t in toc_data:
        if t["id"] in valid_map:
            toc_items.append(valid_map[t["id"]])
            toc_ids_seen.add(t["id"])
    # أضف الناقصة بالترتيب
    for v in valid:
        if v["id"] not in toc_ids_seen:
            toc_items.append(v)
    # رتّب حسب idfrom
    toc_items.sort(key=lambda x: int(x.get("idfrom", x["id"])))
    book.toc   = build_epub_toc(toc_items) or toc_epub
    book.spine = spine
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())

    out  = "output/ithaf_alsada.epub"
    epub.write_epub(out, book)
    size = os.path.getsize(out) / 1024 / 1024
    print(f"EPUB: {out} ({size:.1f} MB) {len(chapters)-1} فصل")

    # تقرير المفقودين
    built_ids = set(id_to_ch.keys())
    missing = [v for v in valid if v["id"] not in built_ids]
    if missing:
        print(f"\n=== مفقود من EPUB: {len(missing)} ===")
        for v in missing:
            sec = load_json(f"output/sections/{v['id']}.json", None)
            reason = "sections/ مو موجود" if not sec else "paragraphs فارغ"
            print(f"  {v['id']:5s} | {v.get('title','')[:50]} | {reason}")
        save_json("output/missing.json", missing)
        print("  محفوظ في output/missing.json")
    else:
        print("لا يوجد مفقودين")


# ================================================================
# 8. التشغيل
# ================================================================
if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "test"
    print(f"mode={mode}")

    set_tashkeel_cookie()

    if mode == "test":
        build_toc()
        phase_scan(end_id=FIRST_ID + 29)
        build_toc_from_scan()
        phase_build()
    elif mode == "scan":
        build_toc()
        phase_scan()
    elif mode == "resume":
        phase_scan()
    elif mode == "fix":
        fix_titles()
        build_toc_from_scan()
    elif mode == "build":
        build_toc_from_scan()
        phase_build()
    elif mode == "full":
        build_toc()
        phase_scan()
        build_toc_from_scan()
        phase_build()