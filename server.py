import sqlite3
import json
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Optional, List
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

app = FastAPI(title="Math Cards Graph API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])

BASE_DIR    = Path(__file__).parent
TOPICS_ROOT = BASE_DIR / "topics"
TOPICS_ROOT.mkdir(exist_ok=True)

def load_registry() -> dict:
    p = TOPICS_ROOT / "registry.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}

@app.on_event("startup")
async def startup():
    reg = load_registry()
    for slug, info in reg.items():
        cards_dir = Path(info["cards_dir"])
        if cards_dir.exists():
            try:
                app.mount(f"/topics/{slug}/cards",
                          StaticFiles(directory=str(cards_dir), html=True),
                          name=f"cards_{slug}")
            except Exception:
                pass

def get_topic_db(slug: str) -> sqlite3.Connection:
    reg = load_registry()
    if slug not in reg:
        raise HTTPException(404, f"Topic not found: {slug}")
    conn = sqlite3.connect(reg[slug]["db"])
    conn.row_factory = sqlite3.Row
    return conn

def parse_links(value) -> List[int]:
    if not value or not str(value).strip():
        return []
    try:
        return [int(x.strip()) for x in str(value).split(',') if x.strip()]
    except ValueError:
        return []

def extract_preview(card_id: int, cards_dir: Path) -> str:
    class Extractor(HTMLParser):
        SKIP = {'script','style','math','svg','head'}
        def __init__(self):
            super().__init__(); self.parts=[]; self._d=0
        def handle_starttag(self, tag, attrs):
            if tag in self.SKIP: self._d+=1
        def handle_endtag(self, tag):
            if tag in self.SKIP and self._d>0: self._d-=1
        def handle_data(self, data):
            if self._d==0:
                t=data.strip()
                if t: self.parts.append(t)
        def text(self): return ' '.join(self.parts)
    f = cards_dir / f"{card_id}.html"
    if not f.exists(): return ""
    try:
        content = f.read_text(encoding="utf-8")
        m = re.search(r'<section[^>]*type=["\']?pref["\']?[^>]*>(.*?)</section>',
                      content, re.DOTALL|re.IGNORECASE)
        frag = m.group(1) if m else content
        frag = re.sub(r'<script[\s\S]*?</script>','',frag,flags=re.IGNORECASE)
        frag = re.sub(r'<style[\s\S]*?</style>','',frag,flags=re.IGNORECASE)
        ex = Extractor(); ex.feed(frag)
        text = re.sub(r'\s+',' ', ex.text()).strip()
        return (text[:197]+"...") if len(text)>200 else text
    except Exception as e:
        print(f"Preview error {card_id}: {e}"); return ""

class CardResponse(BaseModel):
    id: int; cords: dict; name: str
    teorems: List[int]; usein: List[int]; chapter: List[int]
    html_path: str; preview: str
    section_color: str = "#5e8ab4"; root_section: str = ""

@app.get("/", response_class=HTMLResponse)
async def root():
    p = BASE_DIR/"index.html"
    return FileResponse(p) if p.exists() else HTMLResponse("<h1>index.html not found</h1>",404)

@app.get("/{filename}")
async def static_file(filename: str):
    if filename in ("style.css","script.js","favicon.ico"):
        p = BASE_DIR/filename
        if p.exists(): return FileResponse(p)
    raise HTTPException(404)

@app.get("/api/topics")
async def list_topics():
    reg = load_registry()
    return [{"slug":s,"name":i["name"],"cards":i.get("cards",0),
             "arrows":i.get("arrows",0),"created_at":i.get("created_at","")}
            for s,i in reg.items()]

@app.get("/api/{slug}/cards", response_model=List[CardResponse])
async def get_cards(slug: str):
    reg = load_registry()
    if slug not in reg: raise HTTPException(404)
    cards_dir = Path(reg[slug]["cards_dir"])
    conn = get_topic_db(slug)
    try:
        rows = conn.execute("SELECT * FROM cards").fetchall()
        return [CardResponse(
            id=r["id"], cords={"x":r["cords_x"],"y":r["cords_y"]}, name=r["name"],
            teorems=parse_links(r["teorems"]), usein=parse_links(r["usein"]),
            chapter=parse_links(r["chapter"]),
            html_path=f"/topics/{slug}/cards/{r['id']}.html",
            preview=extract_preview(r["id"],cards_dir) or r["name"],
            section_color=r["section_color"] or "#5e8ab4",
            root_section=r["root_section"] or ""
        ) for r in rows]
    finally: conn.close()

@app.get("/api/{slug}/arrows")
async def get_arrows(slug: str):
    conn = get_topic_db(slug)
    try:
        rows = conn.execute(
            "SELECT id,source_id,target_id,x1,y1,x2,y2,color,type,source_links,target_links FROM arrows"
        ).fetchall()
        return [{"id":r["id"],"source_id":r["source_id"],"target_id":r["target_id"],
                 "x1":r["x1"],"y1":r["y1"],"x2":r["x2"],"y2":r["y2"],
                 "color":r["color"],"type":r["type"],
                 "source_links":r["source_links"] or "","target_links":r["target_links"] or ""}
                for r in rows]
    except: return []
    finally: conn.close()

if __name__ == "__main__":
    import uvicorn
    print("Server at http://localhost:8000")
    uvicorn.run(app, host="0.0.0.0", port=8000)