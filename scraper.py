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
# ١. استخراج فهرس الكتاب
# ══════════════════════════════════════════════════════════════════
def build_toc() -> list:
    url = f"{BASE_URL}/ar/library/content/{BOOK_ID}/1/مقدمة"
    soup, _ = fetch(url)
    if not soup:
        return []

    hid = {}
    for inp in soup.find_all("input", id=re.compile("^HidParam")):
        nid = inp["id"].replace("HidParam", "")
        hid[nid] = inp.get("value", "")

    toc = []
    for el in soup.find_all(class_=["tree_label", "plusbutton", "BookDetail_1"]):
        nid   = el.get("id") or el.get("data-id")
        text  = el.get_text(strip=True)[:120]
        level = el.get("data-level", "1")
        if nid and text:
            toc.append({
                "id":    nid,
                "level": level,
                "text":  text,
                "hid":   hid.get(nid, ""),
            })

    save_json("output/toc.json", toc)
    print(f"✓ فهرس: {len(toc)} عنصر → output/toc.json")
    return toc


# ══════════════════════════════════════════════════════════════════
# ٢. جلب محتوى node واحد
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

    text_all = soup.get_text(strip=True)
    if len(text_all) < 50:
        return None

    # العنوان
    title_el = soup.find(["h1","h2","h3","b","strong"])
    title    = title_el.get_text(strip=True)[:120] if title_el else f"قسم {nid}"

    # الفقرات مع الأنماط
    seen, paragraphs = set(), []
    for tag in soup.find_all(["p","div","span","b","h1","h2","h3"]):
        txt = tag.get_text(strip=True)
        if len(txt) < 8 or txt in seen:
            continue
        seen.add(txt)
        cls = " ".join(tag.get("class", []))

        if "﴿" in txt or "﴾" in txt:
            kind = "quran"
        elif any(c in cls for c in ["hadith","hadithatt"]):
            kind = "hadith"
        elif any(c in cls for c in ["names","namesatt"]):
            kind = "name"
        elif tag.name in ["h1","h2","h3"] or (tag.name == "b" and len(txt) < 80):
            kind = "heading"
        else:
            kind = "text"

        paragraphs.append({"kind": kind, "text": txt, "tag": tag.name})

    if not paragraphs:
        return None

    result = {
        "node_id":    nid,
        "title":      title,
        "paragraphs": paragraphs,
        "has_quran":  any(p["kind"] == "quran"  for p in paragraphs),
        "has_hadith": any(p["kind"] == "hadith" for p in paragraphs),
    }
    save_json(cache, result)
    return result


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
        phase_scan(1, 30)
        phase_build()

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