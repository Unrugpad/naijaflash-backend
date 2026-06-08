import os
import json
import random
import asyncio
import logging
from datetime import datetime
from typing import Optional, List

import httpx
from fastapi import FastAPI, HTTPException, BackgroundTasks, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import psycopg2
from psycopg2.extras import RealDictCursor
import groq

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="NaijaFlash API", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

DATABASE_URL = os.getenv("DATABASE_URL")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_ADMIN_CHAT_ID = os.getenv("TELEGRAM_ADMIN_CHAT_ID")
UNSPLASH_ACCESS_KEY = os.getenv("UNSPLASH_ACCESS_KEY")
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY")

CATEGORIES = ["entertainment", "finance", "tech", "health", "education", "news", "football"]

CAT_PROMPTS = {
    "entertainment": "Nigerian entertainment, Nollywood, Afrobeats music, celebrity gossip, Nigerian musicians",
    "finance": "Nigerian finance, dollar to naira rate, crypto in Nigeria, CBN policy, investments, how to make money",
    "tech": "Tech and phones in Nigeria, smartphone prices, Nigerian tech startups, apps, gadgets",
    "health": "Health tips for Nigerians, common illnesses, medication prices in Nigeria, wellness, hospitals",
    "education": "Nigerian education, JAMB, WAEC, NECO, scholarships, job opportunities, university admission",
    "news": "Nigerian news, politics, government policy, social issues, Tinubu, economy",
    "football": "Nigerian football, Super Eagles, Premier League, La Liga, Champions League, AFCON, Osimhen"
}

# Balanced topic pool - 5 per category = 35 total
TOPIC_POOL = {
    "entertainment": [
        "Davido new music 2025",
        "Burna Boy world tour",
        "Best Nollywood movies 2025",
        "Wizkid latest news",
        "Nigerian music awards 2025",
    ],
    "finance": [
        "Dollar to Naira rate today",
        "How to make money online Nigeria 2025",
        "Bitcoin price in Naira today",
        "USDT to Naira best rate",
        "Best investment in Nigeria 2025",
    ],
    "tech": [
        "Best phones under 200000 naira 2025",
        "Tecno Camon 30 review Nigeria",
        "iPhone 16 price in Nigeria",
        "Samsung Galaxy A55 price Nigeria",
        "Best data plan in Nigeria 2025",
    ],
    "health": [
        "Malaria symptoms and treatment Nigeria",
        "Best medication for typhoid in Nigeria",
        "How to treat stomach ulcer in Nigeria",
        "High blood pressure remedy Nigeria",
        "Diabetes management tips Nigeria",
    ],
    "education": [
        "JAMB 2025 result check",
        "WAEC 2025 timetable subjects",
        "Scholarship opportunities for Nigerians 2025",
        "How to apply for NYSC 2025",
        "Best universities in Nigeria 2025",
    ],
    "news": [
        "Nigeria fuel price update 2025",
        "CBN new policy Nigerians",
        "Nigeria economic news today",
        "Tinubu government latest news",
        "Cost of living Nigeria 2025",
    ],
    "football": [
        "Super Eagles AFCON qualifiers 2025",
        "Victor Osimhen latest news",
        "Premier League results today",
        "Champions League highlights",
        "Ademola Lookman latest news",
    ],
}

# ── DB ──
def get_db():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS articles (
            id SERIAL PRIMARY KEY,
            slug VARCHAR(255) UNIQUE NOT NULL,
            title TEXT NOT NULL,
            category VARCHAR(50) NOT NULL,
            excerpt TEXT,
            body TEXT NOT NULL,
            image_url TEXT,
            status VARCHAR(20) DEFAULT 'pending',
            views INTEGER DEFAULT 0,
            telegram_msg_id INTEGER,
            created_at TIMESTAMP DEFAULT NOW(),
            published_at TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS used_topics (
            id SERIAL PRIMARY KEY,
            topic TEXT UNIQUE NOT NULL,
            created_at TIMESTAMP DEFAULT NOW()
        );
        CREATE TABLE IF NOT EXISTS admins (
            id SERIAL PRIMARY KEY,
            chat_id BIGINT UNIQUE NOT NULL,
            username VARCHAR(100),
            added_by BIGINT,
            added_at TIMESTAMP DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS idx_articles_status ON articles(status);
        CREATE INDEX IF NOT EXISTS idx_articles_category ON articles(category);
        CREATE INDEX IF NOT EXISTS idx_articles_created ON articles(created_at DESC);
    """)
    # Seed owner as first admin
    if TELEGRAM_ADMIN_CHAT_ID:
        try:
            cur.execute("""
                INSERT INTO admins (chat_id, username, added_by)
                VALUES (%s, 'owner', %s)
                ON CONFLICT (chat_id) DO NOTHING
            """, (int(TELEGRAM_ADMIN_CHAT_ID), int(TELEGRAM_ADMIN_CHAT_ID)))
        except Exception as e:
            logger.error(f"Admin seed error: {e}")
    conn.commit()
    cur.close()
    conn.close()
    logger.info("DB initialized")

# ── ADMIN CHECK ──
def get_all_admins() -> List[int]:
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT chat_id FROM admins")
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return [r["chat_id"] for r in rows]
    except:
        return [int(TELEGRAM_ADMIN_CHAT_ID)] if TELEGRAM_ADMIN_CHAT_ID else []

def is_owner(chat_id: int) -> bool:
    return str(chat_id) == str(TELEGRAM_ADMIN_CHAT_ID)

def is_admin(chat_id: int) -> bool:
    return chat_id in get_all_admins()

# ── PICK NEXT TOPIC ──
def pick_topic() -> dict:
    """Pick next unused topic, rotating across all categories fairly."""
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT topic FROM used_topics")
        used = {r["topic"] for r in cur.fetchall()}

        # Find category with most unused topics (ensures variety)
        best_cat = None
        best_topics = []
        for cat, topics in TOPIC_POOL.items():
            available = [t for t in topics if t not in used]
            if len(available) > len(best_topics):
                best_cat = cat
                best_topics = available

        if not best_topics:
            # All topics used — reset
            cur.execute("DELETE FROM used_topics")
            conn.commit()
            best_cat = random.choice(CATEGORIES)
            best_topics = TOPIC_POOL[best_cat]

        topic = random.choice(best_topics)

        # Also try Google Trends RSS for Nigeria
        try:
            import xml.etree.ElementTree as ET
            import urllib.request
            url = "https://trends.google.com/trending/rss?geo=NG"
            with urllib.request.urlopen(url, timeout=5) as resp:
                tree = ET.parse(resp)
                root = tree.getroot()
                items = root.findall(".//item/title")
                trend_topics = [i.text for i in items if i.text and i.text not in used]
                if trend_topics:
                    trend_topic = trend_topics[0]
                    trend_cat = classify_topic(trend_topic)
                    cur.close()
                    conn.close()
                    return {"topic": trend_topic, "category": trend_cat}
        except Exception as e:
            logger.info(f"Google Trends RSS failed, using pool: {e}")

        cur.close()
        conn.close()
        return {"topic": topic, "category": best_cat}

    except Exception as e:
        logger.error(f"pick_topic error: {e}")
        cat = random.choice(CATEGORIES)
        return {"topic": random.choice(TOPIC_POOL[cat]), "category": cat}

def mark_topic_used(topic: str):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("INSERT INTO used_topics (topic) VALUES (%s) ON CONFLICT DO NOTHING", (topic,))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        logger.error(f"mark_topic_used error: {e}")

def classify_topic(topic: str) -> str:
    t = topic.lower()
    if any(w in t for w in ["naira","dollar","bitcoin","crypto","cbn","bank","money","invest","usdt","stock","fintech"]):
        return "finance"
    if any(w in t for w in ["football","soccer","epl","ucl","eagles","afcon","liga","score","goal","match","osimhen","saka"]):
        return "football"
    if any(w in t for w in ["jamb","waec","neco","school","university","scholarship","job","graduate","nysc"]):
        return "education"
    if any(w in t for w in ["phone","iphone","samsung","tecno","infinix","app","tech","internet","gadget","laptop"]):
        return "tech"
    if any(w in t for w in ["health","malaria","typhoid","hospital","drug","medication","symptom","diabetes","blood"]):
        return "health"
    if any(w in t for w in ["nollywood","davido","burna","wizkid","celebrity","movie","music","afrobeats","award"]):
        return "entertainment"
    return "news"

# ── IMAGE FETCH ──
async def fetch_image(query: str, category: str) -> str:
    fallbacks = {
        "entertainment": "https://images.unsplash.com/photo-1493225457124-a3eb161ffa5f?w=900&q=80",
        "finance": "https://images.unsplash.com/photo-1611974789855-9c2a0a7236a3?w=900&q=80",
        "tech": "https://images.unsplash.com/photo-1574944985070-8f3ebc6b79d2?w=900&q=80",
        "health": "https://images.unsplash.com/photo-1559757148-5c350d0d3c56?w=900&q=80",
        "education": "https://images.unsplash.com/photo-1523050854058-8df90110c9f1?w=900&q=80",
        "news": "https://images.unsplash.com/photo-1508921912186-1d1a45ebb3c1?w=900&q=80",
        "football": "https://images.unsplash.com/photo-1574629810360-7efbbe195018?w=900&q=80",
    }
    default = fallbacks.get(category, fallbacks["news"])
    try:
        if UNSPLASH_ACCESS_KEY:
            async with httpx.AsyncClient(timeout=8) as client:
                r = await client.get(
                    "https://api.unsplash.com/search/photos",
                    params={"query": query, "per_page": 5, "orientation": "landscape"},
                    headers={"Authorization": f"Client-ID {UNSPLASH_ACCESS_KEY}"}
                )
                if r.status_code == 200:
                    results = r.json().get("results", [])
                    if results:
                        return random.choice(results[:3])["urls"]["regular"]
        if PEXELS_API_KEY:
            async with httpx.AsyncClient(timeout=8) as client:
                r = await client.get(
                    "https://api.pexels.com/v1/search",
                    params={"query": query, "per_page": 5, "orientation": "landscape"},
                    headers={"Authorization": PEXELS_API_KEY}
                )
                if r.status_code == 200:
                    photos = r.json().get("photos", [])
                    if photos:
                        return random.choice(photos[:3])["src"]["large"]
    except Exception as e:
        logger.error(f"Image fetch error: {e}")
    return default

# ── AI WRITER ──
async def generate_article(topic: str, category: str) -> dict:
    cat_context = CAT_PROMPTS.get(category, "Nigerian news")
    prompt = f"""You are a Nigerian news journalist writing for NaijaFlash, Nigeria's fastest news blog.

Write a complete, SEO-optimized news article about: {topic}
Category context: {cat_context}

STYLE RULES:
- Write in clear, simple English that everyday Nigerians understand
- Direct, punchy sentences. No unnecessary grammar
- Use Nigerian context, naira prices, local references where relevant
- Sound like a real journalist, not AI
- Include practical information readers can use

OUTPUT FORMAT (JSON only, no markdown backticks):
{{
  "title": "SEO-optimized headline (max 80 chars, include main keyword)",
  "excerpt": "2-sentence summary for SEO meta description",
  "body": "Full article HTML using only <p>, <h2>, <h3>, <ul>, <li>, <strong> tags. Minimum 400 words. Include 2-3 subheadings.",
  "image_query": "3-word image search query for this article"
}}

Return ONLY valid JSON. No explanation, no preamble, no markdown."""

    # Try Groq first
    if GROQ_API_KEY:
        try:
            client = groq.Groq(api_key=GROQ_API_KEY)
            response = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                max_tokens=2000
            )
            text = response.choices[0].message.content.strip()
            text = text.replace("```json", "").replace("```", "").strip()
            return json.loads(text)
        except Exception as e:
            logger.error(f"Groq failed: {e}, trying Claude fallback")

    # Claude fallback
    if ANTHROPIC_API_KEY:
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": ANTHROPIC_API_KEY,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json"
                    },
                    json={
                        "model": "claude-haiku-4-5-20251001",
                        "max_tokens": 2000,
                        "messages": [{"role": "user", "content": prompt}]
                    }
                )
                data = r.json()
                text = data["content"][0]["text"].strip()
                text = text.replace("```json", "").replace("```", "").strip()
                return json.loads(text)
        except Exception as e:
            logger.error(f"Claude fallback failed: {e}")

    raise Exception("Both Groq and Claude API failed")

# ── SLUG ──
def slugify(text: str) -> str:
    import re
    text = text.lower()
    text = re.sub(r'[^a-z0-9\s-]', '', text)
    text = re.sub(r'\s+', '-', text.strip())
    text = re.sub(r'-+', '-', text)
    return text[:80] + '-' + str(int(datetime.now().timestamp()))[-6:]

# ── TELEGRAM ──
async def send_to_all_admins(text: str, reply_markup: dict = None):
    admins = get_all_admins()
    msg_ids = {}
    for chat_id in admins:
        msg_id = await send_telegram(chat_id, text, reply_markup)
        if msg_id:
            msg_ids[chat_id] = msg_id
    return msg_ids

async def send_telegram(chat_id: int, text: str, reply_markup: dict = None, parse_mode: str = "HTML"):
    if not TELEGRAM_BOT_TOKEN:
        return None
    try:
        payload = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
        if reply_markup:
            payload["reply_markup"] = json.dumps(reply_markup)
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json=payload
            )
            data = r.json()
            if data.get("ok"):
                return data["result"]["message_id"]
    except Exception as e:
        logger.error(f"Telegram error to {chat_id}: {e}")
    return None

async def send_article_for_approval(article_id: int, title: str, excerpt: str, category: str):
    text = (
        f"📰 <b>New Article Ready</b>\n\n"
        f"<b>Category:</b> {category.upper()}\n\n"
        f"<b>Title:</b>\n{title}\n\n"
        f"<b>Excerpt:</b>\n{excerpt}\n\n"
        f"<b>ID:</b> #{article_id}\n\n"
        f"Approve to publish or reject to discard."
    )
    markup = {
        "inline_keyboard": [[
            {"text": "✅ Approve", "callback_data": f"approve_{article_id}"},
            {"text": "❌ Reject", "callback_data": f"reject_{article_id}"}
        ]]
    }
    await send_to_all_admins(text, reply_markup=markup)

# ── PIPELINE ──
async def run_pipeline():
    logger.info("Pipeline started")
    try:
        topic_data = pick_topic()
        topic = topic_data["topic"]
        category = topic_data["category"]
        logger.info(f"Generating: [{category}] {topic}")

        article_data = await generate_article(topic, category)
        image_url = await fetch_image(article_data.get("image_query", topic), category)

        slug = slugify(article_data["title"])
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO articles (slug, title, category, excerpt, body, image_url, status)
            VALUES (%s, %s, %s, %s, %s, %s, 'pending')
            RETURNING id
        """, (slug, article_data["title"], category, article_data.get("excerpt",""), article_data["body"], image_url))
        article_id = cur.fetchone()["id"]
        conn.commit()
        cur.close()
        conn.close()

        mark_topic_used(topic)

        await send_article_for_approval(article_id, article_data["title"], article_data.get("excerpt",""), category)
        logger.info(f"Article #{article_id} sent for approval")

    except Exception as e:
        logger.error(f"Pipeline error: {e}")
        await send_to_all_admins(f"⚠️ Pipeline error: {str(e)[:200]}")

# ── API ROUTES ──
@app.on_event("startup")
async def startup():
    init_db()
    logger.info("NaijaFlash backend started")

@app.get("/")
async def root():
    return {"status": "NaijaFlash API running", "version": "2.0.0"}

@app.get("/api/articles")
async def get_articles(category: Optional[str] = None, limit: int = 20, offset: int = 0):
    conn = get_db()
    cur = conn.cursor()
    if category:
        cur.execute("""
            SELECT id, slug, title, category, excerpt, image_url, views, published_at, created_at
            FROM articles WHERE status='published' AND category=%s
            ORDER BY published_at DESC NULLS LAST LIMIT %s OFFSET %s
        """, (category, limit, offset))
    else:
        cur.execute("""
            SELECT id, slug, title, category, excerpt, image_url, views, published_at, created_at
            FROM articles WHERE status='published'
            ORDER BY published_at DESC NULLS LAST LIMIT %s OFFSET %s
        """, (limit, offset))
    articles = cur.fetchall()
    cur.close()
    conn.close()
    return {"articles": [dict(a) for a in articles], "count": len(articles)}

@app.get("/api/articles/{slug}")
async def get_article(slug: str):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM articles WHERE slug=%s AND status='published'", (slug,))
    article = cur.fetchone()
    if not article:
        cur.close()
        conn.close()
        raise HTTPException(status_code=404, detail="Article not found")
    cur.execute("UPDATE articles SET views=views+1 WHERE slug=%s", (slug,))
    conn.commit()
    cur.close()
    conn.close()
    return dict(article)

@app.get("/api/trending")
async def get_trending():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, slug, title, category, excerpt, image_url, views, published_at
        FROM articles WHERE status='published'
        ORDER BY views DESC, published_at DESC LIMIT 5
    """)
    articles = cur.fetchall()
    cur.close()
    conn.close()
    return {"trending": [dict(a) for a in articles]}

@app.get("/api/stats")
async def get_stats():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) as t FROM articles WHERE status='published'")
    total = cur.fetchone()["t"]
    cur.execute("SELECT COUNT(*) as p FROM articles WHERE status='pending'")
    pending = cur.fetchone()["p"]
    cur.execute("SELECT COALESCE(SUM(views),0) as v FROM articles")
    views = cur.fetchone()["v"]
    cur.execute("SELECT category, COUNT(*) as count FROM articles WHERE status='published' GROUP BY category ORDER BY count DESC")
    by_cat = cur.fetchall()
    cur.execute("SELECT id, title, category, views, created_at FROM articles WHERE status='published' ORDER BY created_at DESC LIMIT 5")
    recent = cur.fetchall()
    cur.close()
    conn.close()
    return {
        "total_published": total,
        "pending_approval": pending,
        "total_views": views,
        "by_category": [dict(r) for r in by_cat],
        "recent": [dict(r) for r in recent]
    }

@app.post("/api/pipeline/run")
async def trigger_pipeline(background_tasks: BackgroundTasks):
    background_tasks.add_task(run_pipeline)
    return {"status": "Pipeline started"}

@app.get("/api/health")
async def health():
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.close()
        conn.close()
        db_ok = True
    except:
        db_ok = False
    return {
        "status": "ok",
        "db": "connected" if db_ok else "error",
        "groq": "configured" if GROQ_API_KEY else "not set",
        "claude": "configured" if ANTHROPIC_API_KEY else "not set",
        "telegram": "configured" if TELEGRAM_BOT_TOKEN else "not set"
    }

@app.post("/api/telegram/webhook")
async def telegram_webhook(request: Request):
    try:
        data = await request.json()

        # ── CALLBACK (button clicks) ──
        if "callback_query" in data:
            cb = data["callback_query"]
            cb_data = cb.get("data","")
            user_id = cb["from"]["id"]

            if not is_admin(user_id):
                return {"ok": True}

            if cb_data.startswith("approve_"):
                article_id = int(cb_data.split("_")[1])
                conn = get_db()
                cur = conn.cursor()
                cur.execute("UPDATE articles SET status='published', published_at=NOW() WHERE id=%s RETURNING title", (article_id,))
                row = cur.fetchone()
                conn.commit()
                cur.close()
                conn.close()
                await send_to_all_admins(f"✅ Published: {row['title'] if row else 'Article'}")

            elif cb_data.startswith("reject_"):
                article_id = int(cb_data.split("_")[1])
                conn = get_db()
                cur = conn.cursor()
                cur.execute("UPDATE articles SET status='rejected' WHERE id=%s", (article_id,))
                conn.commit()
                cur.close()
                conn.close()
                await send_to_all_admins(f"❌ Article #{article_id} rejected.")

            async with httpx.AsyncClient() as client:
                await client.post(
                    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery",
                    json={"callback_query_id": cb["id"]}
                )

        # ── TEXT COMMANDS ──
        elif "message" in data:
            msg = data["message"]
            text = msg.get("text","").strip()
            chat_id = msg["chat"]["id"]
            user_id = msg["from"]["id"]
            username = msg["from"].get("username","")

            # /start — welcome
            if text == "/start":
                await send_telegram(chat_id,
                    "👋 Welcome to <b>NaijaFlash Bot</b>!\n\nSend /help to see all commands."
                )

            # /help
            elif text == "/help":
                cmd = (
                    "🤖 <b>NaijaFlash Bot Commands</b>\n\n"
                    "/stats — View blog statistics\n"
                    "/generate — Generate a new article now\n"
                    "/pending — View pending articles\n"
                    "/help — Show this message"
                )
                if is_owner(user_id):
                    cmd += (
                        "\n\n👑 <b>Owner Commands</b>\n"
                        "/addadmin [chat_id] — Add new admin\n"
                        "/removeadmin [chat_id] — Remove admin\n"
                        "/listadmins — List all admins"
                    )
                await send_telegram(chat_id, cmd)

            # /stats
            elif text == "/stats":
                if not is_admin(user_id):
                    await send_telegram(chat_id, "⛔ Admins only.")
                    return {"ok": True}
                conn = get_db()
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) as t FROM articles WHERE status='published'")
                pub = cur.fetchone()["t"]
                cur.execute("SELECT COUNT(*) as p FROM articles WHERE status='pending'")
                pend = cur.fetchone()["p"]
                cur.execute("SELECT COALESCE(SUM(views),0) as v FROM articles")
                views = cur.fetchone()["v"]
                cur.execute("SELECT category, COUNT(*) as c FROM articles WHERE status='published' GROUP BY category ORDER BY c DESC")
                cats = cur.fetchall()
                cur.close()
                conn.close()
                cat_lines = "\n".join([f"  • {r['category']}: {r['c']}" for r in cats]) or "  None yet"
                await send_telegram(chat_id,
                    f"📊 <b>NaijaFlash Stats</b>\n\n"
                    f"✅ Published: {pub}\n"
                    f"⏳ Pending: {pend}\n"
                    f"👁 Total Views: {views}\n\n"
                    f"<b>By Category:</b>\n{cat_lines}"
                )

            # /generate
            elif text == "/generate":
                if not is_admin(user_id):
                    await send_telegram(chat_id, "⛔ Admins only.")
                    return {"ok": True}
                await send_telegram(chat_id, "⚙️ Running article pipeline...")
                asyncio.create_task(run_pipeline())

            # /pending
            elif text == "/pending":
                if not is_admin(user_id):
                    await send_telegram(chat_id, "⛔ Admins only.")
                    return {"ok": True}
                conn = get_db()
                cur = conn.cursor()
                cur.execute("SELECT id, title, category FROM articles WHERE status='pending' ORDER BY created_at DESC LIMIT 5")
                rows = cur.fetchall()
                cur.close()
                conn.close()
                if rows:
                    lines = "\n".join([f"#{r['id']} [{r['category']}] {r['title'][:50]}..." for r in rows])
                    await send_telegram(chat_id, f"⏳ <b>Pending Articles</b>\n\n{lines}")
                else:
                    await send_telegram(chat_id, "No pending articles.")

            # /addadmin [chat_id]
            elif text.startswith("/addadmin"):
                if not is_owner(user_id):
                    await send_telegram(chat_id, "⛔ Only the owner can add admins.")
                    return {"ok": True}
                parts = text.split()
                if len(parts) < 2:
                    await send_telegram(chat_id, "Usage: /addadmin [chat_id]")
                    return {"ok": True}
                try:
                    new_id = int(parts[1])
                    conn = get_db()
                    cur = conn.cursor()
                    cur.execute("INSERT INTO admins (chat_id, added_by) VALUES (%s, %s) ON CONFLICT (chat_id) DO NOTHING", (new_id, user_id))
                    conn.commit()
                    cur.close()
                    conn.close()
                    await send_telegram(chat_id, f"✅ Admin {new_id} added successfully.")
                    await send_telegram(new_id, "🎉 You have been added as a NaijaFlash admin! Send /help to get started.")
                except Exception as e:
                    await send_telegram(chat_id, f"❌ Error: {e}")

            # /removeadmin [chat_id]
            elif text.startswith("/removeadmin"):
                if not is_owner(user_id):
                    await send_telegram(chat_id, "⛔ Only the owner can remove admins.")
                    return {"ok": True}
                parts = text.split()
                if len(parts) < 2:
                    await send_telegram(chat_id, "Usage: /removeadmin [chat_id]")
                    return {"ok": True}
                try:
                    rem_id = int(parts[1])
                    if rem_id == int(TELEGRAM_ADMIN_CHAT_ID):
                        await send_telegram(chat_id, "⛔ Cannot remove the owner.")
                        return {"ok": True}
                    conn = get_db()
                    cur = conn.cursor()
                    cur.execute("DELETE FROM admins WHERE chat_id=%s", (rem_id,))
                    conn.commit()
                    cur.close()
                    conn.close()
                    await send_telegram(chat_id, f"✅ Admin {rem_id} removed.")
                except Exception as e:
                    await send_telegram(chat_id, f"❌ Error: {e}")

            # /listadmins
            elif text == "/listadmins":
                if not is_owner(user_id):
                    await send_telegram(chat_id, "⛔ Owner only.")
                    return {"ok": True}
                conn = get_db()
                cur = conn.cursor()
                cur.execute("SELECT chat_id, username, added_at FROM admins ORDER BY added_at")
                rows = cur.fetchall()
                cur.close()
                conn.close()
                lines = "\n".join([f"• {r['chat_id']} (@{r['username'] or 'unknown'}) — {str(r['added_at'])[:10]}" for r in rows])
                await send_telegram(chat_id, f"👥 <b>Admins ({len(rows)})</b>\n\n{lines}")

    except Exception as e:
        logger.error(f"Webhook error: {e}")

    return {"ok": True}
