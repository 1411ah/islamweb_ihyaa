"""
scraper.py — النسخة النهائية
python scraper.py test    ← اختبار أول 30 فصل
python scraper.py scan    ← مسح كامل
python scraper.py resume  ← استكمال
python scraper.py build   ← بناء EPUB
python scraper.py full    ← scan + build
"""

import requests
from bs4 import BeautifulSoup, Tag
from ebooklib import epub
import json, os, re, time, sys, traceback

BASE_URL = "https://www.islamweb.net"
BOOK_ID  = 411
DELAY    = 0.8  # قلّلنا من 1.2 إلى 0.8 → ~1.8 ساعة بدل 2.7
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
    print("✓ cookie التشكيل (cval=1)")

def has_tashkeel(text: str) -> bool:
    return any("\u064b" <= c <= "\u065f" for c in text[:500])


# ══════════════════════════════════════════════════════════════════
# ١. بناء الفهرس — مع جلب الفصول الداخلية تكرارياً
# ══════════════════════════════════════════════════════════════════
def build_toc_from_scan() -> list:
    """
    بناء الفهرس من ملفات sections المحفوظة
    كل section فيها العنوان الحقيقي من breadcrumb الصفحة
    """
    print("=== بناء الفهرس من بيانات الـ scan ===")
    sections_dir = "output/sections"
    if not os.path.exists(sections_dir):
        return []

    toc = []
    for fname in sorted(os.listdir(sections_dir),
                        key=lambda f: int(f.replace(".json","")) if f.replace(".json","").isdigit() else 0):
        if not fname.endswith(".json"):
            continue
        nid = fname.replace(".json","")
        sec = load_json(f"{sections_dir}/{fname}", None)
        if not sec:
            continue
        toc.append({
            "id":     nid,
            "idfrom": sec.get("idfrom", int(nid)),
            "idto":   sec.get("idto",   int(nid)),
            "level":  1,   # مستوى افتراضي — يُحدَّث من breadcrumb
            "text":   sec.get("title", f"قسم {nid}"),
        })

    print(f"✓ فهرس من scan: {len(toc)} عنصر")
    save_json("output/toc_from_scan.json", toc)
    return toc
    print("=== بناء الفهرس ===")

    # ── المستوى الأول من الصفحة الرئيسية ─────────────────────────
    url = f"{BASE_URL}/ar/library/content/{BOOK_ID}/1/مقدمة"
    soup, _ = fetch(url)
    if not soup:
        return []

    toc  = []
    seen = set()
    index_scroll = soup.find(id="bookIndexScroll") or soup

    top_level = []
    for el in index_scroll.find_all(["span", "label"]):
        if not isinstance(el, Tag):
            continue
        if "tree_label" not in (el.get("class") or []):
            continue
        node_id = (el.get("data-id") or "").strip()
        idfrom  = el.get("data-idfrom")
        idto    = el.get("data-idto")
        level   = int(el.get("data-level") or 1)
        dhref   = el.get("data-href") or ""
        a_inner = el.find("a")
        text    = (a_inner.get_text(strip=True) if a_inner
                   else el.get_text(strip=True)).strip()
        text    = re.sub(r'(التالي|السابق)', '', text).strip()

        if not node_id.isdigit() or not text or not idfrom:
            continue
        if node_id in seen:
            continue
        seen.add(node_id)

        item = {
            "id":     node_id,
            "idfrom": int(idfrom),
            "idto":   int(idto) if idto else int(idfrom),
            "level":  level,
            "text":   text[:120],
            "dhref":  dhref,
        }
        top_level.append(item)
        toc.append(item)

    print(f"  المستوى الأول: {len(top_level)} عنصر")

    # ── جلب الفصول الداخلية من data-href ─────────────────────────
    def crawl_node(item, depth=0):
        if depth > 6:
            return
        dhref = item.get("dhref","")
        if not dhref:
            return

        sub_url = f"{BASE_URL}/ar/library/maktaba/{dhref}"
        sub_soup, _ = fetch(sub_url)
        if not sub_soup:
            return

        # طبع HTML خام للتشخيص (أول 3 عناصر فقط)
        found = 0
        for el in sub_soup.find_all(["span","label","a","li"]):
            if not isinstance(el, Tag):
                continue
            nid = (el.get("data-id") or el.get("id") or "").strip()
            fr  = el.get("data-idfrom")
            to  = el.get("data-idto")
            dh  = el.get("data-href") or ""
            txt = el.get_text(strip=True)[:60]
            lvl = int(el.get("data-level") or item["level"] + 1)

            if not nid.isdigit() or not fr:
                continue
            if nid in seen:
                continue
            seen.add(nid)

            child = {
                "id":     nid,
                "idfrom": int(fr),
                "idto":   int(to) if to else int(fr),
                "level":  lvl,
                "text":   re.sub(r'(التالي|السابق)', '', txt).strip()[:120],
                "dhref":  dh,
            }
            toc.append(child)
            found += 1

            if dh and int(to or fr) - int(fr) > 1:
                time.sleep(0.3)
                crawl_node(child, depth + 1)

        print(f"  {'  '*depth}↳ {item['text'][:40]} → {found} فصل")
        time.sleep(0.5)

    for item in top_level:
        if item["idto"] - item["idfrom"] > 1:
            crawl_node(item)

    toc.sort(key=lambda x: x["idfrom"])

    # إزالة التكرار
    seen_ids, final = set(), []
    for item in toc:
        if item["id"] not in seen_ids:
            seen_ids.add(item["id"])
            final.append(item)

    for item in final:
        indent = "  " * (item["level"] - 1)
        print(f"  {indent}├─ [{item['idfrom']:5d}→{item['idto']:5d}] {item['text'][:50]}")

    save_json("output/toc.json", final)
    print(f"\n✓ فهرس كامل: {len(final)} عنصر")
    return final


# ══════════════════════════════════════════════════════════════════
# ٢. استخراج النص من #pagebody_thaskeel
# ══════════════════════════════════════════════════════════════════
def clean_and_extract(soup) -> tuple:
    """يعيد (paragraphs, footnotes_unused)"""

    # احذف عناصر التنقل من كامل الصفحة أولاً
    for el in soup.find_all("h1", class_="booktitle"):
        el.decompose()
    for el in soup.find_all("u", class_=["ul", "ur"]):
        el.decompose()

    # الحاوية: pagebody_thaskeel (مشكّل) أولاً
    container = (
        soup.find(id="pagebody_thaskeel") or
        soup.find(id="pagebody")          or
        soup.find(class_="bookcontent-dic") or
        soup.body
    )
    if not container:
        return [], []

    # حذف عناصر الموقع
    for sel in ["script", "style", "noscript",
                ".hashiya_title", ".quranatt", ".hadithatt",
                ".namesatt", ".mainsubjatt"]:
        for el in container.select(sel):
            el.decompose()

    # النصوص المتوسطة (شعر/مقتبسات) → علامة خاصة
    for p in container.find_all("p", align="center"):
        if not isinstance(p, Tag):
            continue
        txt = p.get_text(strip=True)
        if txt:
            p.replace_with(f"\n⟪CENTER⟫{txt}⟪/CENTER⟫\n")

    # تحويل أرقام الصفحات → فاصل مرئي
    for font in container.find_all("font"):
        if not isinstance(font, Tag):
            continue
        txt   = font.get_text()
        color = font.get("color") or ""
        if color == "blue" and "ص:" in txt:
            m = re.search(r'ص:\s*(\d+)', txt)
            if m:
                font.replace_with(f"\n【 الجزء 1 ـ صفحة {m.group(1)} 】\n")
            else:
                font.decompose()
        elif "ص:" in txt:
            font.decompose()

    # فك روابط التفسير
    for a in container.find_all("a", onclick=True):
        a.replace_with(a.get_text())

    # mainsubj → حذف الرابط الداخلي فقط، إبقاء النص
    for span in container.find_all("span", class_="mainsubj"):
        if not isinstance(span, Tag):
            continue
        txt = span.get_text(strip=True)
        if txt:
            span.replace_with(f" {txt} ")
        else:
            span.decompose()

    # آيات قرآنية → ﴿ نص ﴾
    for span in container.find_all("span", class_="quran"):
        if not isinstance(span, Tag):
            continue
        txt = span.get_text(strip=True)
        if txt:
            span.replace_with(f" ﴿ {txt} ﴾ ")

    # أحاديث → (( نص ))
    for span in container.find_all("span", class_="hadith"):
        if not isinstance(span, Tag):
            continue
        txt = span.get_text(strip=True).strip('"\'\u201c\u201d\u00ab\u00bb')
        if txt:
            span.replace_with(f" (( {txt} )) ")

    # النصوص الخضراء → (( نص ))
    for span in list(container.find_all("span")):
        if not isinstance(span, Tag):
            continue
        style = (span.get("style") or "").replace(" ", "")
        if "color:green" in style:
            txt = span.get_text(strip=True).strip('"\'\u201c\u201d\u00ab\u00bb')
            if txt:
                span.replace_with(f" (( {txt} )) ")

    # أسماء الأعلام بدون class → (نص)
    for span in list(container.find_all("span")):
        if not isinstance(span, Tag):
            continue
        cls   = span.get("class") or []
        style = (span.get("style") or "").replace(" ", "")
        if not cls and "display:none" not in style:
            txt = span.get_text(strip=True)
            if txt:
                span.replace_with(f" ({txt}) ")
            else:
                span.decompose()

    # إزالة spans المخفية المتبقية
    for span in list(container.find_all("span")):
        if not isinstance(span, Tag):
            continue
        style = (span.get("style") or "").replace(" ", "")
        if "display:none" in style:
            span.decompose()

    # استخراج النص
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
        if any(w in txt for w in ["التالي", "السابق", "فهرس الكتاب", "إسلام ويب"]):
            continue
        if re.fullmatch(r'[\[\]\d\s:\.\-،,]+', txt):
            continue
        seen.add(txt)

        if txt.startswith("⟪CENTER⟫") and txt.endswith("⟪/CENTER⟫"):
            kind = "center"
        elif txt.startswith("【") and txt.endswith("】"):
            kind = "pagebreak"
        elif "﴿" in txt and "﴾" in txt:
            kind = "quran"
        elif "(( " in txt and " ))" in txt:
            kind = "hadith"
        elif len(txt) < 100 and txt.endswith(":"):
            kind = "heading"
        else:
            kind = "text"

        paragraphs.append({"kind": kind, "text": txt})

    return paragraphs, []


# ══════════════════════════════════════════════════════════════════
# ٣. جلب section
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

    if not has_tashkeel(raw):
        set_tashkeel_cookie()
        soup, raw = fetch(url)
        if not soup:
            return None

    # ── استخرج العنوان الحقيقي من breadcrumb الصفحة ──────────────
    real_title = title  # افتراضي من toc أو "قسم N"
    # breadcrumb: آخر عنصر في itemprop="itemListElement" هو العنوان الحقيقي
    crumbs = soup.find_all(
        lambda t: isinstance(t, Tag) and
        t.get("itemprop") == "itemListElement"
    )
    if crumbs:
        last = crumbs[-1]
        span = last.find("span", itemprop="name")
        if span:
            txt = span.get_text(strip=True)
            if txt and txt not in REPEATED_TEXTS:
                real_title = txt

    paragraphs, _ = clean_and_extract(soup)
    if not paragraphs:
        return None

    result = {
        "node_id":    nid,
        "title":      real_title,
        "idfrom":     idfrom,
        "idto":       idto,
        "paragraphs": paragraphs,
        "has_quran":  any(p["kind"] == "quran"  for p in paragraphs),
        "has_hadith": any(p["kind"] == "hadith" for p in paragraphs),
    }
    save_json(cache, result)
    return result


FIRST_ID = 1
LAST_ID  = 8173

# ══════════════════════════════════════════════════════════════════
# ٤. SCAN — يمر على كل node ID من 1 إلى 8173
# ══════════════════════════════════════════════════════════════════
def phase_scan(end_id=None):
    progress_file = "output/scan_progress.json"
    valid_file    = "output/valid_nodes.json"
    progress  = load_json(progress_file, {"last_id": FIRST_ID - 1})
    valid     = load_json(valid_file, [])
    valid_set = set(v["id"] for v in valid)

    start = progress["last_id"] + 1
    end   = end_id or LAST_ID
    total = end - start + 1
    print(f"=== SCAN IDs {start}→{end} ({total} طلب) ===")

    for nid in range(start, end + 1):
        item = {
            "id":     str(nid),
            "idfrom": nid,
            "idto":   nid,
            "level":  1,
            "text":   f"قسم {nid}",
        }

        # حاول جلب العنوان من toc.json إن وُجد
        toc = load_json("output/toc.json", [])
        toc_map = {int(t["id"]): t for t in toc}
        if nid in toc_map:
            item = toc_map[nid]

        try:
            result = fetch_section(item)
        except Exception as e:
            print(f"  ✗ id={nid}: {e}")
            traceback.print_exc()
            result = None

        if result and result["paragraphs"]:
            q = "✅" if result["has_quran"] else ("📖" if result["has_hadith"] else "🔶")
            print(f"  {q} {nid:5d} | {result['title'][:55]}")
            if str(nid) not in valid_set:
                valid.append({
                    "id":    str(nid),
                    "title": result["title"],  # العنوان الحقيقي من الصفحة
                    "level": item.get("level", 1),
                })
                valid_set.add(str(nid))
        else:
            print(f"  ⬜ {nid:5d}")

        # حفظ كل 50 node
        if nid % 50 == 0:
            progress["last_id"] = nid
            save_json(progress_file, progress)
            save_json(valid_file, valid)
            pct = round((nid - start) / total * 100)
            print(f"  💾 checkpoint {nid}/{end} ({pct}%) | صالح: {len(valid)}")
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
    print(f"\n✅ SCAN اكتمل | صالح: {len(valid)}/{total}")
    return valid


# ══════════════════════════════════════════════════════════════════
# ٥. BUILD EPUB
# ══════════════════════════════════════════════════════════════════
EPUB_CSS = """
body      { font-family:'Traditional Arabic',serif; direction:rtl;
            text-align:right; line-height:2.2; margin:1em 1.5em; }
h1        { color:#8B0000; border-bottom:2px solid #8B0000; margin-top:1.5em; }
h2        { color:#5A3E1B; margin-top:1.2em; }
.quran    { color:#006400; font-size:1.1em; margin:.8em 0; padding:.5em;
            border-right:4px solid #006400; background:#f0fff0; }
.hadith   { color:#4B0082; margin:.8em 0; padding:.5em;
            border-right:4px solid #4B0082; background:#f5f0ff; }
.text     { margin:.5em 0; }
.center   { text-align:center; font-style:italic; margin:1em 2em; color:#4a0080; }
.pagebreak{ text-align:center; color:#8B0000; font-size:.85em;
            border-top:1px solid #ccc; border-bottom:1px solid #ccc;
            margin:1em 0; padding:.3em 0; }
"""

def phase_build():
    valid = load_json("output/valid_nodes.json", [])
    if not valid:
        print("✗ valid_nodes.json فارغ"); return

    if valid and isinstance(valid[0], int):
        valid = [{"id": str(v), "title": f"قسم {v}", "level": 1} for v in valid]

    # استخدم toc_from_scan إن وُجد (أشمل)، وإلا toc.json
    toc_data = (load_json("output/toc_from_scan.json", None) or
                load_json("output/toc.json", []))
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

    cover = epub.EpubHtml(title="الغلاف", file_name="cover.xhtml", lang="ar")
    cover.content = (
        '<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="ar" lang="ar" dir="rtl">'
        '<head><meta charset="utf-8"/><title>الغلاف</title></head>'
        '<body dir="rtl" style="text-align:center">'
        '<h1 style="color:#8B0000;margin-top:3em">'
        'إتحاف السادة المتقين<br/>بشرح إحياء علوم الدين'
        '</h1>'
        '<p style="font-size:1.3em">الإمام مرتضى الزبيدي</p>'
        '</body></html>'
    )
    cover.add_item(css_item)
    book.add_item(cover)

    chapters  = [cover]
    spine     = ["nav", cover]
    toc_epub  = []
    id_to_ch  = {}

    for i, v in enumerate(valid):
        sec = load_json(f"output/sections/{v['id']}.json", None)
        if not sec:
            continue

        body = f'<h1>{sec["title"]}</h1>\n'
        for p in sec["paragraphs"]:
            t = (p["text"]
                 .replace("&", "&amp;")
                 .replace("<", "&lt;")
                 .replace(">", "&gt;"))
            if p["kind"] == "center":
                t_c = t.replace("⟪CENTER⟫","").replace("⟪/CENTER⟫","")
                body += f'<p class="center">{t_c}</p>\n'
            elif p["kind"] == "pagebreak":
                body += f'<p class="pagebreak">{t}</p>\n'
            elif p["kind"] == "quran":
                body += f'<p class="quran">{t}</p>\n'
            elif p["kind"] == "hadith":
                body += f'<p class="hadith">{t}</p>\n'
            elif p["kind"] == "heading":
                body += f'<h2>{t}</h2>\n'
            else:
                body += f'<p class="text">{t}</p>\n'

        ch = epub.EpubHtml(
            title=sec["title"],
            file_name=f"s{v['id']}.xhtml",
            lang="ar"
        )
        ch.content = (
            f'<html xmlns="http://www.w3.org/1999/xhtml" '
            f'xml:lang="ar" lang="ar" dir="rtl">'
            f'<head><meta charset="utf-8"/>'
            f'<title>{sec["title"]}</title></head>'
            f'<body dir="rtl">{body}</body></html>'
        )
        ch.add_item(css_item)
        book.add_item(ch)
        chapters.append(ch)
        spine.append(ch)
        toc_epub.append(epub.Link(f"s{v['id']}.xhtml", sec["title"], f"s{v['id']}"))
        id_to_ch[v["id"]] = ch

        if i % 100 == 0:
            print(f"  📄 {i}/{len(valid)}")

    # فهرس هرمي
    def build_epub_toc(items):
        result, stack = [], []
        for item in items:
            if item["id"] not in id_to_ch:
                continue
            lnk = epub.Link(f"s{item['id']}.xhtml", item["title"], f"s{item['id']}")
            lvl = item.get("level", 1)
            entry = (lnk, [])
            if lvl == 1 or not stack:
                result.append(entry)
                stack = [(lvl, entry)]
            else:
                while len(stack) > 1 and stack[-1][0] >= lvl:
                    stack.pop()
                stack[-1][1][1].append(entry)
                stack.append((lvl, entry))
        return result

    valid_map  = {v["id"]: v for v in valid}
    toc_items  = [valid_map[t["id"]] for t in toc_data if t["id"] in valid_map]
    book.toc   = build_epub_toc(toc_items) or toc_epub
    book.spine = spine
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())

    out  = "output/ithaf_alsada.epub"
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
        build_toc()
        phase_scan(end_id=FIRST_ID + 29)
        build_toc_from_scan()
        phase_build()
    elif mode == "scan":
        build_toc()
        phase_scan()
    elif mode == "resume":
        phase_scan()
    elif mode == "build":
        build_toc_from_scan()
        phase_build()
    elif mode == "full":
        build_toc()
        phase_scan()
        build_toc_from_scan()
        phase_build()