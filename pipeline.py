"""
pipeline.py — единый цикл генерации данных графа

Порядок выполнения:
  1. КООРДИНАТЫ  — вычисляет позиции карточек (логика cords.py)
  2. ГЕНЕРАЦИЯ   — создаёт HTML-карточки и наполняет БД (логика gen.py)
  3. ЦВЕТА       — записывает section_color / root_section в БД (логика update_colors.py)
  4. КООРДИНАТЫ  — синхронизирует cords_x/cords_y с БД (логика update_coords.py)
  5. СТРЕЛКИ     — генерирует таблицу arrows в БД (логика generate_arrows.py)

Запуск:
  python pipeline.py                # полный цикл
  python pipeline.py --from coords  # начать с определённого шага
  python pipeline.py --only arrows  # выполнить только один шаг

Доступные шаги: coords | gen | colors | sync | arrows

API-ключ:
  Вставьте свой ключ в переменную API_KEY ниже (или задайте
  переменную окружения DEEPSEEK_API_KEY перед запуском).
"""

import sqlite3
import json
import math
import colorsys
import time
import re
import sys
import os
import argparse
from pathlib import Path
from datetime import datetime
from openai import OpenAI

# ╔══════════════════════════════════════════════════════════════╗
# ║                     КОНФИГУРАЦИЯ                             ║
# ╚══════════════════════════════════════════════════════════════╝

# TODO: вставьте ваш API-ключ сюда или задайте переменную окружения DEEPSEEK_API_KEY
API_KEY      = os.environ.get("DEEPSEEK_API_KEY", "YOUR_API_KEY_HERE")
API_BASE_URL = "https://api.deepseek.com"
API_MODEL    = "deepseek-chat"

DB_PATH        = Path("cards.db")
CARDS_FOLDER   = Path("cards")
TERMS_FILE     = Path("terms.json")
CSS_FILE       = Path("style.css")
CARDS_CSS      = Path("cards/style.css")
POSITIONS_FILE = Path("cards_positions.json")
EDGES_FILE     = Path("cards_edges.json")
PROGRESS_FILE  = Path("generation_progress.json")

CARDS_FOLDER.mkdir(exist_ok=True)
if CSS_FILE.exists() and not CARDS_CSS.exists():
    CARDS_CSS.write_text(CSS_FILE.read_text(encoding="utf-8"), encoding="utf-8")


# ╔══════════════════════════════════════════════════════════════╗
# ║                     ЦВЕТА РАЗДЕЛОВ                           ║
# ╚══════════════════════════════════════════════════════════════╝

SECTION_COLORS = {
    "Арифметика":                  "#e74c3c",
    "Алгебра":                     "#3498db",
    "Геометрия":                   "#2ecc71",
    "Тригонометрия":               "#f39c12",
    "Математический анализ":       "#9b59b6",
    "Комбинаторика и вероятность": "#1abc9c",
    "Логика и множества":          "#e67e22",
    "Комплексные числа":           "#e91e63",
    "Линейная алгебра":            "#00bcd4",
    "Векторная алгебра":           "#4caf50",
    "Аналитическая геометрия":     "#ff5722",
    "Математическая логика":       "#795548",
    "Теория чисел":                "#607d8b",
    "Топология":                   "#673ab7",
}

FALLBACK_COLORS = [
    "#ff6f61", "#6b5b95", "#88b04b", "#f7cac9", "#92a8d1",
    "#955251", "#b565a7", "#009b77", "#dd4124", "#d65076",
    "#45b8ac", "#efc050", "#5b5ea6", "#9b2335", "#dfcfbe",
    "#55b4b0", "#e15d44", "#7fcdcd", "#c3447a", "#98b4d4",
]


# ╔══════════════════════════════════════════════════════════════╗
# ║                   ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ                    ║
# ╚══════════════════════════════════════════════════════════════╝

def hex_to_rgb(hex_color):
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i+2], 16) / 255.0 for i in (0, 2, 4))

def rgb_to_hex(r, g, b):
    return f"#{int(r*255):02x}{int(g*255):02x}{int(b*255):02x}"

def mix_colors(c1, c2):
    """Смешивает два hex-цвета в пространстве HSL."""
    if c1 == c2:
        return c1
    r1, g1, b1 = hex_to_rgb(c1)
    r2, g2, b2 = hex_to_rgb(c2)
    h1, l1, s1 = colorsys.rgb_to_hls(r1, g1, b1)
    h2, l2, s2 = colorsys.rgb_to_hls(r2, g2, b2)
    h = (h1 + h2) / 2
    if abs(h1 - h2) > 0.5:
        h = (h + 0.5) % 1.0
    r, g, b = colorsys.hls_to_rgb(h, (l1+l2)/2, (s1+s2)/2)
    return rgb_to_hex(r, g, b)

def get_section_color(section_name):
    if section_name in SECTION_COLORS:
        return SECTION_COLORS[section_name]
    return FALLBACK_COLORS[hash(section_name) % len(FALLBACK_COLORS)]

def header(title):
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70)


# ╔══════════════════════════════════════════════════════════════╗
# ║                  ШАГ 1 — КООРДИНАТЫ (cords.py)               ║
# ╚══════════════════════════════════════════════════════════════╝

def step_coords(tree_root):
    """Вычисляет позиции и цвета всех узлов, сохраняет cards_positions.json
    и cards_edges.json (структурные рёбра по дереву)."""

    header("ШАГ 1 · КООРДИНАТЫ + ЦВЕТНЫЕ РЁБРА")

    def get_all_names(obj, path=""):
        items = []
        if isinstance(obj, dict):
            for key, value in obj.items():
                new_path = f"{path}/{key}" if path else key
                items.append({"name": key, "path": new_path, "is_section": True,
                               "depth": len(new_path.split("/"))})
                items.extend(get_all_names(value, new_path))
        elif isinstance(obj, list):
            for item in obj:
                p = f"{path}/{item}" if path else item
                items.append({"name": item, "path": p, "is_section": False,
                               "depth": len(p.split("/"))})
        return items

    all_items = get_all_names(tree_root)
    for i, item in enumerate(all_items):
        item["id"] = i + 1

    # Группируем по корневым разделам
    sections = {}
    for item in all_items:
        root = item["path"].split("/")[0]
        if root not in sections:
            sections[root] = {
                "items": [], "all_children": [],
                "total_cards": 0, "depth_counts": {}, "max_depth": 0,
                "color": get_section_color(root),
            }
        sections[root]["items"].append(item)
        if item["depth"] > 1:
            sections[root]["all_children"].append(item)
        if not item["is_section"]:
            sections[root]["total_cards"] += 1
            d = item["depth"]
            sections[root]["depth_counts"][d] = sections[root]["depth_counts"].get(d, 0) + 1
            sections[root]["max_depth"] = max(sections[root]["max_depth"], d)

    total_cards = sum(s["total_cards"] for s in sections.values())

    # Угловые секторы — пропорционально числу карточек
    current_angle = -90
    for section_data in sections.values():
        sa = (section_data["total_cards"] / total_cards * 360) if total_cards else 360 / len(sections)
        section_data.update(
            angle_start=current_angle,
            angle_end=current_angle + sa,
            angle_center=current_angle + sa / 2,
            angle_range=sa,
        )
        current_angle += sa

    # Радиусы корневых разделов
    sorted_secs = sorted(sections.items(), key=lambda x: x[1]["total_cards"])
    for i, (_, sd) in enumerate(sorted_secs):
        t = i / (len(sorted_secs) - 1) if len(sorted_secs) > 1 else 0.5
        sd["root_radius"] = 15 - t * (15 - 5)

    for sd in sections.values():
        sd["all_children"].sort(key=lambda x: (x["depth"], x["name"]))

    WORLD_MAX = 45
    result = []
    for item in all_items:
        parts = item["path"].split("/")
        root = parts[0]
        sec = sections[root]
        item["section_color"] = sec["color"]
        item["root_section"]  = root

        if item["depth"] == 1:
            angle = math.radians(sec["angle_center"])
            r = sec["root_radius"]
        else:
            children = sec["all_children"]
            ci = next(i for i, c in enumerate(children) if c["path"] == item["path"])
            n  = len(children)
            if n <= 1:
                r     = sec["root_radius"] + 5
                angle = math.radians(sec["angle_center"])
            else:
                golden = (1 + math.sqrt(5)) / 2
                t      = ci / (n - 1)
                r      = (sec["root_radius"] + 3) + (WORLD_MAX - sec["root_radius"] - 3) * math.sqrt(t)
                sa_rad = math.radians(sec["angle_start"])
                ea_rad = math.radians(sec["angle_end"])
                ar_rad = ea_rad - sa_rad
                spiral = (ci * 2 * math.pi / (golden ** 2)) % (2 * math.pi)
                angle  = sa_rad + (spiral / (2 * math.pi)) * ar_rad
                if item["is_section"]:
                    ca_rad = sa_rad + ar_rad / 2
                    angle  = angle + (ca_rad - angle) * 0.4

        item["x"] = round(50 + r * math.cos(angle), 1)
        item["y"] = round(50 + r * math.sin(angle), 1)
        result.append(item)

    # Структурные рёбра (дерево)
    id_to_item   = {it["id"]: it for it in result}
    name_to_id_c = {it["name"]: it["id"] for it in result}
    edges = []
    for item in result:
        if item["is_section"]:
            sname = item["name"]
            if sname in sections:
                for child in sections[sname]["all_children"]:
                    edges.append({"source": item["id"], "target": child["id"],
                                  "color": item["section_color"], "type": "section_to_child"})
        else:
            pname = item["path"].split("/")
            if len(pname) >= 2:
                parent_id = name_to_id_c.get(pname[-2])
                if parent_id and parent_id != item["id"]:
                    pi = id_to_item.get(parent_id)
                    if pi:
                        edges.append({"source": parent_id, "target": item["id"],
                                      "color": pi["section_color"], "type": "parent_to_child"})

    positions_out = [
        {"id": it["id"], "name": it["name"], "path": it["path"],
         "is_section": it["is_section"],
         "x": it.get("x", 50), "y": it.get("y", 50),
         "section_color": it["section_color"], "root_section": it["root_section"]}
        for it in result
    ]
    POSITIONS_FILE.write_text(json.dumps(positions_out, ensure_ascii=False, indent=2), encoding="utf-8")
    EDGES_FILE.write_text(json.dumps(edges, ensure_ascii=False, indent=2), encoding="utf-8")

    cards_count = sum(1 for it in result if not it["is_section"])
    print(f"  Карточек:  {cards_count}")
    print(f"  Разделов:  {sum(1 for it in result if it['is_section'])}")
    print(f"  Рёбер:     {len(edges)}")
    print(f"  Сохранено: {POSITIONS_FILE}  +  {EDGES_FILE}")

    return result, sections, name_to_id_c


# ╔══════════════════════════════════════════════════════════════╗
# ║               ШАГ 2 — ГЕНЕРАЦИЯ КАРТОЧЕК (gen.py)            ║
# ╚══════════════════════════════════════════════════════════════╝

class TokenTracker:
    def __init__(self):
        self.total_prompt = self.total_completion = self.total = self.requests = 0
        self.start = datetime.now()

    def add(self, usage):
        if hasattr(usage, "prompt_tokens"):
            self.total_prompt      += usage.prompt_tokens
            self.total_completion  += usage.completion_tokens
            self.total             += usage.total_tokens
        self.requests += 1

    @property
    def cost(self):
        return self.total_prompt / 1_000_000 * 0.14 + self.total_completion / 1_000_000 * 0.28

    @property
    def elapsed(self):
        return max((datetime.now() - self.start).total_seconds(), 0.1)


class ProgressBar:
    def __init__(self, total, current=0, desc="Прогресс", width=40):
        self.total = total; self.current = current
        self.desc = desc; self.width = width
        self.start = datetime.now(); self.skipped = 0

    def update(self, current, status="", tracker=None):
        self.current = current
        pct    = min(100, current / max(self.total, 1) * 100)
        filled = int(self.width * current // max(self.total, 1))
        bar    = "█" * filled + "░" * (self.width - filled)
        el     = max((datetime.now() - self.start).total_seconds(), 0.1)
        done   = current - self.skipped
        eta    = f"{int(((el/done)*(self.total-current))//60):02d}:{int(((el/done)*(self.total-current))%60):02d}" if done > 0 else "--:--"
        spd    = done / (el / 60) if done > 0 else 0
        tok_s  = f"🪙{tracker.total:,} 💵${tracker.cost:.3f}" if tracker else ""
        sys.stdout.write("\r" + " " * 130 + "\r")
        sys.stdout.write(f"\r{self.desc} [{bar}] {pct:5.1f}% ({current}/{self.total}) "
                         f"⏱{eta} ⚡{spd:.1f}т/м {tok_s} {status}")
        sys.stdout.flush()

    def close(self, tracker=None):
        self.update(self.total, "ГОТОВО", tracker)
        print()


def get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cards (
            id        INTEGER PRIMARY KEY,
            cords_x   REAL    DEFAULT 50.0,
            cords_y   REAL    DEFAULT 50.0,
            name      TEXT    NOT NULL,
            teorems   TEXT,
            usein     TEXT,
            chapter   TEXT
        )
    """)
    conn.commit(); conn.close()

def add_card_to_db(card_id, name, x, y, teorems, usein, chapter):
    conn = get_db()
    conn.execute("""
        INSERT OR REPLACE INTO cards (id, cords_x, cords_y, name, teorems, usein, chapter)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (card_id, x, y, name, teorems, usein, chapter))
    conn.commit(); conn.close()

def get_existing_card_ids():
    existing = set()
    try:
        conn = get_db()
        existing.update(r["id"] for r in conn.execute("SELECT id FROM cards").fetchall())
        conn.close()
    except Exception:
        pass
    for f in CARDS_FOLDER.glob("*.html"):
        try:
            existing.add(int(f.stem))
        except ValueError:
            pass
    return existing

def save_progress(completed, failed, index, total):
    PROGRESS_FILE.write_text(json.dumps({
        "completed": list(completed), "failed": list(failed),
        "index": index, "total": total,
        "updated": datetime.now().isoformat(),
    }, ensure_ascii=False, indent=2), encoding="utf-8")

def parse_tree(tree, parent_path=None):
    if parent_path is None:
        parent_path = []
    items = []
    if isinstance(tree, dict):
        for key, value in tree.items():
            path = parent_path + [key]
            if isinstance(value, dict):
                items.append({"name": key, "path": path, "is_section": True, "depth": len(path)})
                items.extend(parse_tree(value, path))
            elif isinstance(value, list):
                for item in value:
                    items.append({"name": item, "path": path + [item],
                                  "is_section": False, "depth": len(path) + 1})
    return items

def build_relationships(items, name_to_id):
    relationships = {}
    for item in items:
        if item["is_section"]:
            continue
        card_id = name_to_id[item["name"]]
        path    = item["path"]
        name    = item["name"]

        chapter_ids = []
        if len(path) >= 2:
            pid = name_to_id.get(path[-2])
            if pid:
                chapter_ids.append(str(pid))

        usein_ids = set()
        for other in items:
            if other["is_section"] or other["name"] == name:
                continue
            op = other["path"]
            if len(op) > len(path) and op[:len(path)] == path:
                oid = name_to_id.get(other["name"])
                if oid and oid != card_id:
                    usein_ids.add(str(oid))

        teorems_ids  = set()
        name_words   = set(name.lower().split())
        for other in items:
            if other["is_section"] or other["name"] == name:
                continue
            op  = other["path"]
            oid = name_to_id.get(other["name"])
            if not oid or oid == card_id:
                continue
            if len(op) == len(path) and op[:-1] == path[:-1] and op != path:
                teorems_ids.add(str(oid))
            elif op[0] != path[0]:
                if name_words & set(other["name"].lower().split()):
                    teorems_ids.add(str(oid))

        relationships[card_id] = {
            "chapter": ",".join(chapter_ids),
            "usein":   ",".join(sorted(usein_ids)),
            "teorems": ",".join(sorted(teorems_ids)),
        }
    return relationships

def generate_text_via_api(client, name, path, related_names, tracker):
    """Запрашивает у API только plain-text (preview + main)."""
    prompt = f"""Напиши краткое описание (preview) и подробный текст (main) для математической темы.

Тема: {name}
Раздел: {' → '.join(path)}
Связанные темы: {', '.join(related_names[:5]) if related_names else 'нет'}

Ответ дай СТРОГО в формате:

PREVIEW:: 1-2 предложения с определением {name}.

MAIN:: Подробный текст (3-4 абзаца):
- Определение и основные формулы (используй ^ для степени, √ для корня)
- Свойства и примеры
- Где применяется, связь с другими темами
- Упомяни связанные темы: {', '.join(related_names[:3]) if related_names else ''}

НЕ ИСПОЛЬЗУЙ HTML-теги. Только чистый текст."""

    try:
        resp = client.chat.completions.create(
            model=API_MODEL,
            messages=[
                {"role": "system", "content": "Ты — эксперт по математике. Пиши ТОЛЬКО в указанном формате. Не используй HTML."},
                {"role": "user",   "content": prompt},
            ],
            temperature=0.7,
            max_tokens=600,
        )
        if hasattr(resp, "usage"):
            tracker.add(resp.usage)
        text    = resp.choices[0].message.content.strip()
        prev_m  = re.search(r"PREVIEW::\s*(.+?)(?=MAIN::|$)", text, re.DOTALL)
        main_m  = re.search(r"MAIN::\s*(.+?)$",               text, re.DOTALL)
        return (prev_m.group(1).strip() if prev_m else "",
                main_m.group(1).strip() if main_m else "")
    except Exception as e:
        print(f"\n  ❌ Ошибка API: {e}")
        return None, None

def add_links_to_text(text, related_ids, id_to_name):
    for rid in related_ids:
        rname = id_to_name.get(int(rid), "")
        if rname and rname.lower() in text.lower():
            text = re.compile(re.escape(rname), re.IGNORECASE).sub(
                f'<a href="/cards/{rid}.html" class="card-link">{rname}</a>', text, count=1)
    return text

def build_card_html(card_id, name, path, preview, main, relations, id_to_name):
    chapter_ids  = [c for c in relations.get("chapter", "").split(",") if c]
    usein_ids    = [c for c in relations.get("usein",   "").split(",") if c][:5]
    teorems_ids  = [c for c in relations.get("teorems", "").split(",") if c][:5]

    crumbs = []
    for p in path[:-1]:
        pid = next((nid for nname, nid in id_to_name.items() if nname == p), None)
        crumbs.append(f'<a href="/cards/{pid}.html">{p}</a>' if pid else f"<span>{p}</span>")
    crumbs.append(f"<span>{name}</span>")

    all_related = []
    for cid in usein_ids:
        cname = id_to_name.get(int(cid), f"тема_{cid}")
        all_related.append(f'<a href="/cards/{cid}.html" class="card-link">{cname}</a>')
    for cid in teorems_ids:
        if cid not in usein_ids:
            cname = id_to_name.get(int(cid), f"тема_{cid}")
            all_related.append(f'<a href="/cards/{cid}.html" class="card-link">{cname}</a>')

    all_ids       = list(set(usein_ids + teorems_ids))
    main_with_lnk = add_links_to_text(main, all_ids[:5], id_to_name)

    return f"""<div class="card-id">карточка #{card_id}</div>
<h1 class="card-title">{name}</h1>
<div class="breadcrumbs">{' → '.join(crumbs)}</div>

<section type="pref">
  <div class="section-title">preview</div>
  <div class="pref-text">{add_links_to_text(preview, all_ids[:2], id_to_name)}</div>
</section>

<section type="link">
  <div class="section-title">связанные темы</div>
  <div class="links-list">{' '.join(all_related) if all_related else '<span style="color:#3d5065;">нет связей</span>'}</div>
</section>

<section type="main">
  <div class="section-title">основной текст</div>
  <div class="main-text">{main_with_lnk}</div>
</section>

<a href="/" class="back-link">← назад к графу</a>"""

def save_card_html(card_id, name, content):
    full = f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>#{card_id} — {name}</title>
  <link rel="stylesheet" href="style.css">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600;700&display=swap" rel="stylesheet">
</head>
<body>
  <article id="{card_id}" class="card-full">
    {content}
  </article>
</body>
</html>"""
    (CARDS_FOLDER / f"{card_id}.html").write_text(full, encoding="utf-8")

def step_gen(tree_root):
    """Генерирует HTML-карточки и записывает строки в таблицу cards."""

    header("ШАГ 2 · ГЕНЕРАЦИЯ КАРТОЧЕК (API)")

    if API_KEY == "YOUR_API_KEY_HERE":
        print("  ⚠  API_KEY не задан — шаг генерации пропущен.")
        print("     Задайте переменную окружения DEEPSEEK_API_KEY или")
        print("     вставьте ключ в переменную API_KEY в начале файла.")
        return

    client  = OpenAI(api_key=API_KEY, base_url=API_BASE_URL)
    tracker = TokenTracker()

    init_db()
    items       = parse_tree(tree_root)
    for i, item in enumerate(items):
        item["id"] = i + 1

    name_to_id  = {it["name"]: it["id"] for it in items}
    id_to_name  = {it["id"]: it["name"] for it in items}
    topics      = [it for it in items if not it["is_section"]]
    existing    = get_existing_card_ids()
    skipped_n   = sum(1 for t in topics if t["id"] in existing)

    print("  Построение связей (программно)...")
    rels        = build_relationships(items, name_to_id)

    print(f"  Элементов:  {len(items)} ({len(topics)} тем, {len(items)-len(topics)} разделов)")
    print(f"  Уже есть:   {skipped_n}   Осталось: {len(topics)-skipped_n}")
    print()

    pbar = ProgressBar(len(topics), current=skipped_n, desc="🔄 Генерация")
    pbar.skipped = skipped_n
    success = fail = 0
    completed_set = set(existing)
    failed_set    = set()

    for i, topic in enumerate(topics):
        cid = topic["id"]
        if cid in existing:
            pbar.update(i + 1, f"⏭ #{cid} {topic['name'][:30]}", tracker)
            continue

        name  = topic["name"]
        path  = topic["path"]
        x, y  = topic.get("x", 50), topic.get("y", 50)
        r     = rels.get(cid, {"chapter": "", "usein": "", "teorems": ""})

        rel_ids   = [c for f in ("usein", "teorems") for c in r.get(f,"").split(",") if c]
        rel_names = [id_to_name.get(int(c), "") for c in rel_ids[:5] if c]

        pbar.update(i + 1, f"⏳ #{cid} {name[:30]}", tracker)
        preview, main = generate_text_via_api(client, name, path, rel_names, tracker)

        if preview and main:
            html_content = build_card_html(cid, name, path, preview, main, r, id_to_name)
            save_card_html(cid, name, html_content)
            add_card_to_db(cid, name, x, y, r["teorems"], r["usein"], r["chapter"])
            success += 1
            completed_set.add(cid)
            pbar.update(i + 1, f"✅ #{cid} {name[:30]}", tracker)
        else:
            fail += 1
            failed_set.add(cid)
            pbar.update(i + 1, f"❌ #{cid} {name[:30]}", tracker)

        if (success + fail) % 5 == 0:
            save_progress(completed_set, failed_set, i + 1, len(topics))

        time.sleep(0.3)

    pbar.close(tracker)
    print(f"\n  ✅ Успешно: {success}  ⏭ Пропущено: {skipped_n}  ❌ Ошибок: {fail}")
    print(f"  🪙 Токенов: {tracker.total:,}   💵 ~${tracker.cost:.4f}")

    save_progress(completed_set, failed_set, len(topics), len(topics))
    if fail == 0 and (len(topics) - skipped_n) == success:
        PROGRESS_FILE.unlink(missing_ok=True)


# ╔══════════════════════════════════════════════════════════════╗
# ║            ШАГ 3 — ЦВЕТА В БД (update_colors.py)             ║
# ╚══════════════════════════════════════════════════════════════╝

def step_colors():
    """Обновляет section_color и root_section в таблице cards из positions-файла."""

    header("ШАГ 3 · ЦВЕТА РАЗДЕЛОВ → БД")

    if not POSITIONS_FILE.exists():
        print(f"  ❌ {POSITIONS_FILE} не найден — сначала выполните шаг coords.")
        return

    positions = json.loads(POSITIONS_FILE.read_text(encoding="utf-8"))
    edges     = json.loads(EDGES_FILE.read_text(encoding="utf-8")) if EDGES_FILE.exists() else []

    conn = get_db()

    for col, default in [("section_color", "#5e8ab4"), ("root_section", "")]:
        try:
            conn.execute(f"ALTER TABLE cards ADD COLUMN {col} TEXT DEFAULT '{default}'")
            print(f"  ✅ Добавлена колонка {col}")
        except sqlite3.OperationalError:
            pass

    updated = 0
    for item in positions:
        cid = item.get("id")
        if not cid:
            continue
        if conn.execute("SELECT id FROM cards WHERE id = ?", (cid,)).fetchone():
            conn.execute("UPDATE cards SET section_color = ?, root_section = ? WHERE id = ?",
                         (item.get("section_color", "#5e8ab4"), item.get("root_section", ""), cid))
            updated += 1

    conn.execute("""
        CREATE TABLE IF NOT EXISTS edges (
            id     INTEGER PRIMARY KEY AUTOINCREMENT,
            source INTEGER NOT NULL,
            target INTEGER NOT NULL,
            color  TEXT    DEFAULT '#5e8ab4',
            type   TEXT    DEFAULT 'sibling'
        )
    """)
    conn.execute("DELETE FROM edges")
    for edge in edges:
        conn.execute("INSERT INTO edges (source, target, color, type) VALUES (?, ?, ?, ?)",
                     (edge["source"], edge["target"], edge["color"], edge.get("type", "sibling")))

    conn.commit(); conn.close()
    print(f"  ✅ Обновлено цветов: {updated}")
    print(f"  ✅ Записано рёбер:   {len(edges)}")


# ╔══════════════════════════════════════════════════════════════╗
# ║         ШАГ 4 — СИНХРОНИЗАЦИЯ КООРДИНАТ (update_coords.py)  ║
# ╚══════════════════════════════════════════════════════════════╝

def step_sync_coords():
    """Записывает cords_x / cords_y из cards_positions.json в таблицу cards."""

    header("ШАГ 4 · СИНХРОНИЗАЦИЯ КООРДИНАТ → БД")

    if not POSITIONS_FILE.exists():
        print(f"  ❌ {POSITIONS_FILE} не найден — сначала выполните шаг coords.")
        return

    positions = json.loads(POSITIONS_FILE.read_text(encoding="utf-8"))
    conn      = get_db()
    updated = created = 0

    for item in positions:
        cid  = item.get("id")
        x, y = item.get("x", 50), item.get("y", 50)
        name = item.get("name", "")
        if not cid:
            continue
        if conn.execute("SELECT id FROM cards WHERE id = ?", (cid,)).fetchone():
            conn.execute("UPDATE cards SET cords_x = ?, cords_y = ? WHERE id = ?", (x, y, cid))
            updated += 1
        else:
            conn.execute("INSERT INTO cards (id, cords_x, cords_y, name) VALUES (?, ?, ?, ?)",
                         (cid, x, y, name))
            created += 1

    conn.commit(); conn.close()
    print(f"  ✅ Обновлено: {updated}   🆕 Создано: {created}   Всего: {len(positions)}")


# ╔══════════════════════════════════════════════════════════════╗
# ║           ШАГ 5 — СТРЕЛКИ В БД (generate_arrows.py)          ║
# ╚══════════════════════════════════════════════════════════════╝

def step_arrows():
    """Генерирует таблицу arrows на основе positions + связей из таблицы cards."""

    header("ШАГ 5 · ГЕНЕРАЦИЯ СТРЕЛОК → БД")

    if not POSITIONS_FILE.exists():
        print(f"  ❌ {POSITIONS_FILE} не найден — сначала выполните шаг coords.")
        return

    positions  = json.loads(POSITIONS_FILE.read_text(encoding="utf-8"))
    id_to_item = {it["id"]: it for it in positions}

    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS arrows (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id    INTEGER NOT NULL,
            target_id    INTEGER NOT NULL,
            x1 REAL, y1 REAL, x2 REAL, y2 REAL,
            color        TEXT    DEFAULT '#5e8ab4',
            type         TEXT    DEFAULT 'sibling',
            source_links TEXT,
            target_links TEXT,
            FOREIGN KEY (source_id) REFERENCES cards(id),
            FOREIGN KEY (target_id) REFERENCES cards(id)
        )
    """)
    conn.execute("DELETE FROM arrows")

    def card_color(cid):
        r = conn.execute("SELECT section_color FROM cards WHERE id = ?", (cid,)).fetchone()
        return r["section_color"] if r and r["section_color"] else "#5e8ab4"

    def card_links(cid):
        r = conn.execute("SELECT teorems, usein, chapter FROM cards WHERE id = ?", (cid,)).fetchone()
        if not r:
            return []
        out = []
        for f in ("teorems", "usein", "chapter"):
            for x in (r[f] or "").split(","):
                x = x.strip()
                if x:
                    try:
                        out.append(int(x))
                    except ValueError:
                        pass
        return list(set(out))

    def _mix(c1, c2):
        if c1 == c2:
            return c1
        r1,g1,b1 = int(c1[1:3],16), int(c1[3:5],16), int(c1[5:7],16)
        r2,g2,b2 = int(c2[1:3],16), int(c2[3:5],16), int(c2[5:7],16)
        return f"#{(r1+r2)//2:02x}{(g1+g2)//2:02x}{(b1+b2)//2:02x}"

    arrows     = []
    seen_pairs = set()

    # Иерархические стрелки (раздел → прямые дети)
    for item in positions:
        if not item.get("is_section"):
            continue
        sid   = item["id"]
        sc    = card_color(sid)
        sdepth = len(item["path"].split("/"))
        for child in positions:
            if child["id"] == sid:
                continue
            if child["path"].startswith(item["path"] + "/"):
                if len(child["path"].split("/")) == sdepth + 1:
                    key = (sid, child["id"])
                    if key not in seen_pairs:
                        seen_pairs.add(key)
                        arrows.append({
                            "source_id": sid, "target_id": child["id"],
                            "x1": item["x"], "y1": item["y"],
                            "x2": child["x"], "y2": child["y"],
                            "color": sc, "type": "hierarchy",
                            "source_links": ",".join(map(str, card_links(sid))),
                            "target_links": ",".join(map(str, card_links(child["id"]))),
                        })

    # Связи из БД (teorems / usein / chapter)
    for item in positions:
        if item.get("is_section"):
            continue
        cid   = item["id"]
        cc    = card_color(cid)
        links = card_links(cid)
        for lid in links:
            if lid == cid:
                continue
            key = tuple(sorted([cid, lid]))
            if key in seen_pairs:
                continue
            seen_pairs.add(key)
            li = id_to_item.get(lid)
            if not li:
                continue
            lc    = card_color(lid)
            atype = "same_section" if item["root_section"] == li.get("root_section") else "cross_section"
            arrows.append({
                "source_id": cid, "target_id": lid,
                "x1": item["x"], "y1": item["y"],
                "x2": li["x"],  "y2": li["y"],
                "color": _mix(cc, lc), "type": atype,
                "source_links": ",".join(map(str, links)),
                "target_links": ",".join(map(str, card_links(lid))),
            })

    for a in arrows:
        conn.execute("""
            INSERT INTO arrows
              (source_id, target_id, x1, y1, x2, y2, color, type, source_links, target_links)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (a["source_id"], a["target_id"],
              a["x1"], a["y1"], a["x2"], a["y2"],
              a["color"], a["type"], a["source_links"], a["target_links"]))

    conn.commit(); conn.close()

    hier  = sum(1 for a in arrows if a["type"] == "hierarchy")
    same  = sum(1 for a in arrows if a["type"] == "same_section")
    cross = sum(1 for a in arrows if a["type"] == "cross_section")
    print(f"  ✅ Сгенерировано стрелок: {len(arrows)}")
    print(f"     ├─ Иерархических:      {hier}")
    print(f"     ├─ Внутри раздела:     {same}")
    print(f"     └─ Между разделами:    {cross}")


# ╔══════════════════════════════════════════════════════════════╗
# ║                         MAIN                                 ║
# ╚══════════════════════════════════════════════════════════════╝

STEPS = ["coords", "gen", "colors", "sync", "arrows"]

def run_pipeline(from_step="coords", only_step=None):
    if not TERMS_FILE.exists():
        print(f"❌ Файл {TERMS_FILE} не найден! Создайте его перед запуском.")
        sys.exit(1)

    tree      = json.loads(TERMS_FILE.read_text(encoding="utf-8"))
    tree_root = tree.get("Математика", tree)

    active = {s for s in STEPS if STEPS.index(s) >= STEPS.index(from_step)}
    if only_step:
        active = {only_step}

    positions_data = sections_data = name_to_id_data = None

    if "coords" in active:
        positions_data, sections_data, name_to_id_data = step_coords(tree_root)

    if "gen" in active:
        step_gen(tree_root)

    if "colors" in active:
        step_colors()

    if "sync" in active:
        step_sync_coords()

    if "arrows" in active:
        step_arrows()

    header("✨ ГОТОВО")
    print("  Запустите сервер:  python server.py")
    print("  Откройте браузер:  http://localhost:8000")
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="pipeline.py — единый цикл генерации данных графа"
    )
    parser.add_argument("--from",  dest="from_step", choices=STEPS, default="coords",
                        help="начать с указанного шага (default: coords)")
    parser.add_argument("--only",  dest="only_step", choices=STEPS, default=None,
                        help="выполнить только один шаг")
    args = parser.parse_args()

    try:
        run_pipeline(from_step=args.from_step, only_step=args.only_step)
    except KeyboardInterrupt:
        print("\n\n⚠  Прервано. Прогресс сохранён.")
    except Exception as e:
        import traceback
        print(f"\n❌ Ошибка: {e}")
        traceback.print_exc()