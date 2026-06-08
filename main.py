import os
import json
import random
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional, List

import httpx
from fastapi import FastAPI, HTTPException, BackgroundTasks, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import psycopg2
from psycopg2.extras import RealDictCursor
import groq

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="NaijaFlash API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── ENV ──
DATABASE_URL = os.getenv("DATABASE_URL")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_ADMIN_CHAT_ID = os.getenv("TELEGRAM_ADMIN_CHAT_ID")
UNSPLASH_ACCESS_KEY = os.getenv("UNSPLASH_ACCESS_KEY")
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY")

CATEGORIES = ["entertainment", "finance", "tech", "health", "education", "news", "football"]

CAT_PROMPTS = {
    "entertainment": "Nigerian entertainment, Nollywood, Afrobeats music, celebrity gossip",
    "finance": "Nigerian finance, dollar to naira rate, crypto in Nigeria, CBN policy, investments",
    "tech": "Tech and phones in Nigeria, smartphone prices, Nigerian tech startups, apps",
    "health": "Health tips for Nigerians, common illnesses, medication prices in Nigeria, wellness",
    "education": "Nigerian education, JAMB, WAEC, NECO, scholarships, job opportunities",
    "news": "Nigerian news, politics, government policy, social issues",
    "football": "Nigerian football, Super Eagles, Premier League, La Liga, Champions League, AFCON"
}

# ── DB ──
def get_db():
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    return conn

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
        CREATE TABLE IF NOT EXISTS trending_topics (
            id SERIAL PRIMARY KEY,
            topic TEXT NOT NULL,
            category VARCHAR(50),
            score INTEGER DEFAULT 0,
            used BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT NOW()
        );
        CREATE TABLE IF NOT EXISTS settings (
            key VARCHAR(100) PRIMARY KEY,
            value TEXT,
            updated_at TIMESTAMP DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS idx_articles_status ON articles(status);
        CREATE INDEX IF NOT EXISTS idx_articles_category ON articles(category);
        CREATE INDEX IF NOT EXISTS idx_articles_created ON articles(created_at DESC);
    """)
    conn.commit()
    cur.close()
    conn.close()
    logger.info("DB initialized")

# ── MODELS ──
class ArticleApprove(BaseModel):
    article_id: int
    action: str  # approve | reject | edit
    edited_title: Optional[str] = None
    edited_body: Optional[str] = None

class TelegramUpdate(BaseModel):
    update_id: int
    message: Optional[dict] = None
    callback_query: Optional[dict] = None

# ── SLUG ──
def slugify(text: str) -> str:
    import re
    text = text.lower()
    text = re.sub(r'[^a-z0-9\s-]', '', text)
    text = re.sub(r'\s+', '-', text.strip())
    text = re.sub(r'-+', '-', text)
    return text[:80] + '-' + str(int(datetime.now().timestamp()))[-6:]

# ── IMAGE FETCH ──
async def fetch_image(query: str) -> str:
    fallback = f"https://images.unsplash.com/photo-1611974789855-9c2a0a7236a3?w=900&q=80"
    try:
        if UNSPLASH_ACCESS_KEY:
            async with httpx.AsyncClient(timeout=10) as client:
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
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    "https://api.pexels.com/v1/search",
                    params={"query": query, "per_page": 5, "orientation": "landscape"},
                    headers={"Authorization": PEXELS_API_KEY}
                )
                if r.status_code == 200:
                    photos = r.json().get("photos", [])
                    if photos:
                        return random.choice(photos[:3])["src"]["large"]
        # Pollinations fallback
        encoded = query.replace(" ", "%20")
        return f"https://image.pollinations.ai/prompt/{encoded}%20Nigeria%20news%20photo?width=900&height=500&nologo=true"
    except Exception as e:
        logger.error(f"Image fetch error: {e}")
        return fallback

# ── GROQ AI WRITER ──
async def generate_article(topic: str, category: str) -> dict:
    if not GROQ_API_KEY:
        raise Exception("GROQ_API_KEY not set")

    client = groq.Groq(api_key=GROQ_API_KEY)
    cat_context = CAT_PROMPTS.get(category, "Nigerian news")

    prompt = f"""You are a Nigerian news journalist writing for NaijaFlash, Nigeria's fastest news blog.

Write a complete, SEO-optimized news article about: {topic}
Category: {cat_context}

STYLE RULES:
- Write in clear, simple English that everyday Nigerians understand
- Direct, punchy sentences. No unnecessary grammar
- Use Nigerian context, naira prices, local references where relevant
- Sound like a real journalist, not AI
- Include practical information readers can use

OUTPUT FORMAT (JSON only, no markdown):
{{
  "title": "SEO-optimized headline (max 80 chars, include keyword)",
  "excerpt": "2-sentence summary for social media and SEO meta description",
  "body": "Full article HTML using only <p>, <h2>, <h3>, <ul>, <li>, <strong> tags. Minimum 400 words. Include 2-3 subheadings.",
  "image_query": "3-word image search query for this article",
  "seo_tags": ["tag1", "tag2", "tag3"]
}}

Return ONLY valid JSON. No preamble, no explanation."""

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7,
        max_tokens=2000
    )

    text = response.choices[0].message.content.strip()
    # Strip markdown fences if present
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    text = text.strip()

    data = json.loads(text)
    return data

# ── GOOGLE TRENDS ──
async def fetch_trends() -> List[dict]:
    topics = []
    try:
        # Use pytrends via subprocess since it's sync
        from pytrends.request import TrendReq
        import asyncio
        loop = asyncio.get_event_loop()

        def get_trends():
            pytrends = TrendReq(hl='en-NG', tz=60)
            trending = pytrends.trending_searches(pn='nigeria')
            return trending[0].tolist()[:20]

        raw = await loop.run_in_executor(None, get_trends)
        for t in raw:
            cat = classify_topic(t)
            topics.append({"topic": t, "category": cat, "score": random.randint(60, 100)})
    except Exception as e:
        logger.error(f"Trends error: {e}")
        # Fallback curated topics
        topics = [
            {"topic": "Dollar to Naira rate today", "category": "finance", "score": 95},
            {"topic": "JAMB result 2025", "category": "education", "score": 90},
            {"topic": "Super Eagles latest news", "category": "football", "score": 88},
            {"topic": "Davido new song 2025", "category": "entertainment", "score": 85},
            {"topic": "Bitcoin price naira today", "category": "finance", "score": 82},
            {"topic": "Premier League results today", "category": "football", "score": 80},
            {"topic": "WAEC timetable 2025", "category": "education", "score": 78},
            {"topic": "Best phones under 200000 naira", "category": "tech", "score": 75},
            {"topic": "How to make money online Nigeria", "category": "finance", "score": 73},
            {"topic": "Malaria treatment Nigeria", "category": "health", "score": 70},
        ]
    return topics

def classify_topic(topic: str) -> str:
    topic_lower = topic.lower()
    if any(w in topic_lower for w in ["naira", "dollar", "bitcoin", "crypto", "cbn", "bank", "money", "invest", "usdt"]):
        return "finance"
    if any(w in topic_lower for w in ["football", "soccer", "epl", "ucl", "eagles", "afcon", "la liga", "score", "goal", "match"]):
        return "football"
    if any(w in topic_lower for w in ["jamb", "waec", "neco", "school", "university", "scholarship", "job", "graduate"]):
        return "education"
    if any(w in topic_lower for w in ["phone", "iphone", "samsung", "tecno", "infinix", "app", "tech", "internet"]):
        return "tech"
    if any(w in topic_lower for w in ["health", "malaria", "typhoid", "hospital", "drug", "medication", "symptom"]):
        return "health"
    if any(w in topic_lower for w in ["nollywood", "davido", "burna", "wizkid", "celebrity", "movie", "music", "afrobeats"]):
        return "entertainment"
    return "news"

# ── TELEGRAM ──
async def send_telegram(text: str, reply_markup: dict = None, parse_mode: str = "HTML"):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_ADMIN_CHAT_ID:
        logger.warning("Telegram not configured")
        return None
    try:
        payload = {
            "chat_id": TELEGRAM_ADMIN_CHAT_ID,
            "text": text,
            "parse_mode": parse_mode
        }
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
        logger.error(f"Telegram error: {e}")
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
    msg_id = await send_telegram(text, reply_markup=markup)
    if msg_id:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("UPDATE articles SET telegram_msg_id=%s WHERE id=%s", (msg_id, article_id))
        conn.commit()
        cur.close()
        conn.close()

# ── CORE PIPELINE ──
async def run_pipeline():
    logger.info("Pipeline started")
    try:
        # 1. Fetch trends
        topics = await fetch_trends()

        # 2. Save unused topics to DB
        conn = get_db()
        cur = conn.cursor()
        for t in topics[:10]:
            cur.execute("""
                INSERT INTO trending_topics (topic, category, score)
                VALUES (%s, %s, %s)
                ON CONFLICT DO NOTHING
            """, (t["topic"], t["category"], t["score"]))
        conn.commit()

        # 3. Pick top unused topic
        cur.execute("""
            SELECT * FROM trending_topics
            WHERE used=FALSE
            ORDER BY score DESC
            LIMIT 1
        """)
        topic_row = cur.fetchone()

        if not topic_row:
            logger.info("No unused topics")
            cur.close()
            conn.close()
            return

        topic = topic_row["topic"]
        category = topic_row["category"]

        # 4. Generate article
        logger.info(f"Generating article: {topic}")
        article_data = await generate_article(topic, category)

        # 5. Fetch image
        image_url = await fetch_image(article_data.get("image_query", topic))

        # 6. Save to DB as pending
        slug = slugify(article_data["title"])
        cur.execute("""
            INSERT INTO articles (slug, title, category, excerpt, body, image_url, status)
            VALUES (%s, %s, %s, %s, %s, %s, 'pending')
            RETURNING id
        """, (
            slug,
            article_data["title"],
            category,
            article_data.get("excerpt", ""),
            article_data["body"],
            image_url
        ))
        article_id = cur.fetchone()["id"]

        # 7. Mark topic as used
        cur.execute("UPDATE trending_topics SET used=TRUE WHERE id=%s", (topic_row["id"],))
        conn.commit()
        cur.close()
        conn.close()

        # 8. Send to Telegram for approval
        await send_article_for_approval(
            article_id,
            article_data["title"],
            article_data.get("excerpt", ""),
            category
        )
        logger.info(f"Article #{article_id} sent for approval")

    except Exception as e:
        logger.error(f"Pipeline error: {e}")

# ── API ROUTES ──

@app.on_event("startup")
async def startup():
    init_db()
    logger.info("NaijaFlash backend started")

@app.get("/")
async def root():
    return {"status": "NaijaFlash API running", "version": "1.0.0"}

@app.get("/api/articles")
async def get_articles(
    category: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
    status: str = "published"
):
    conn = get_db()
    cur = conn.cursor()
    if category:
        cur.execute("""
            SELECT id, slug, title, category, excerpt, image_url, views, published_at, created_at
            FROM articles
            WHERE status=%s AND category=%s
            ORDER BY published_at DESC NULLS LAST, created_at DESC
            LIMIT %s OFFSET %s
        """, (status, category, limit, offset))
    else:
        cur.execute("""
            SELECT id, slug, title, category, excerpt, image_url, views, published_at, created_at
            FROM articles
            WHERE status=%s
            ORDER BY published_at DESC NULLS LAST, created_at DESC
            LIMIT %s OFFSET %s
        """, (status, limit, offset))
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
    # Increment views
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
        FROM articles
        WHERE status='published'
        ORDER BY views DESC, published_at DESC
        LIMIT 5
    """)
    articles = cur.fetchall()
    cur.close()
    conn.close()
    return {"trending": [dict(a) for a in articles]}

@app.get("/api/stats")
async def get_stats():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) as total FROM articles WHERE status='published'")
    total = cur.fetchone()["total"]
    cur.execute("SELECT COUNT(*) as pending FROM articles WHERE status='pending'")
    pending = cur.fetchone()["pending"]
    cur.execute("SELECT COALESCE(SUM(views),0) as total_views FROM articles")
    total_views = cur.fetchone()["total_views"]
    cur.execute("""
        SELECT category, COUNT(*) as count
        FROM articles WHERE status='published'
        GROUP BY category ORDER BY count DESC
    """)
    by_cat = cur.fetchall()
    cur.execute("""
        SELECT id, title, category, views, created_at
        FROM articles WHERE status='published'
        ORDER BY created_at DESC LIMIT 5
    """)
    recent = cur.fetchall()
    cur.close()
    conn.close()
    return {
        "total_published": total,
        "pending_approval": pending,
        "total_views": total_views,
        "by_category": [dict(r) for r in by_cat],
        "recent": [dict(r) for r in recent]
    }

@app.post("/api/pipeline/run")
async def trigger_pipeline(background_tasks: BackgroundTasks):
    background_tasks.add_task(run_pipeline)
    return {"status": "Pipeline started"}

@app.post("/api/telegram/webhook")
async def telegram_webhook(request: Request):
    try:
        data = await request.json()
        logger.info(f"Telegram update: {data}")

        # Handle callback queries (button clicks)
        if "callback_query" in data:
            cb = data["callback_query"]
            cb_data = cb.get("data", "")
            chat_id = cb["message"]["chat"]["id"]
            msg_id = cb["message"]["message_id"]

            if cb_data.startswith("approve_"):
                article_id = int(cb_data.split("_")[1])
                conn = get_db()
                cur = conn.cursor()
                cur.execute("""
                    UPDATE articles
                    SET status='published', published_at=NOW()
                    WHERE id=%s
                    RETURNING title
                """, (article_id,))
                row = cur.fetchone()
                conn.commit()
                cur.close()
                conn.close()
                title = row["title"] if row else "Unknown"
                await send_telegram(f"✅ Published: {title}")

            elif cb_data.startswith("reject_"):
                article_id = int(cb_data.split("_")[1])
                conn = get_db()
                cur = conn.cursor()
                cur.execute("UPDATE articles SET status='rejected' WHERE id=%s", (article_id,))
                conn.commit()
                cur.close()
                conn.close()
                await send_telegram(f"❌ Article #{article_id} rejected.")

            # Answer callback
            async with httpx.AsyncClient() as client:
                await client.post(
                    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery",
                    json={"callback_query_id": cb["id"]}
                )

        # Handle text commands
        elif "message" in data:
            msg = data["message"]
            text = msg.get("text", "")
            chat_id = msg["chat"]["id"]

            if text == "/stats":
                conn = get_db()
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) as t FROM articles WHERE status='published'")
                pub = cur.fetchone()["t"]
                cur.execute("SELECT COUNT(*) as p FROM articles WHERE status='pending'")
                pend = cur.fetchone()["p"]
                cur.execute("SELECT COALESCE(SUM(views),0) as v FROM articles")
                views = cur.fetchone()["v"]
                cur.close()
                conn.close()
                await send_telegram(
                    f"📊 <b>NaijaFlash Stats</b>\n\n"
                    f"✅ Published: {pub}\n"
                    f"⏳ Pending: {pend}\n"
                    f"👁 Total Views: {views}"
                )

            elif text == "/generate":
                await send_telegram("⚙️ Running article pipeline...")
                asyncio.create_task(run_pipeline())

            elif text == "/pending":
                conn = get_db()
                cur = conn.cursor()
                cur.execute("""
                    SELECT id, title, category FROM articles
                    WHERE status='pending' ORDER BY created_at DESC LIMIT 5
                """)
                rows = cur.fetchall()
                cur.close()
                conn.close()
                if rows:
                    lines = "\n".join([f"#{r['id']} [{r['category']}] {r['title'][:50]}..." for r in rows])
                    await send_telegram(f"⏳ <b>Pending Articles</b>\n\n{lines}")
                else:
                    await send_telegram("No pending articles.")

            elif text == "/help":
                await send_telegram(
                    "🤖 <b>NaijaFlash Bot Commands</b>\n\n"
                    "/stats — View blog statistics\n"
                    "/generate — Generate a new article now\n"
                    "/pending — View pending articles\n"
                    "/help — Show this message"
                )

    except Exception as e:
        logger.error(f"Webhook error: {e}")

    return {"ok": True}

@app.post("/api/articles/{article_id}/publish")
async def publish_article(article_id: int):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        UPDATE articles SET status='published', published_at=NOW()
        WHERE id=%s RETURNING id, title
    """, (article_id,))
    row = cur.fetchone()
    conn.commit()
    cur.close()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Article not found")
    return {"success": True, "article": dict(row)}

@app.delete("/api/articles/{article_id}")
async def delete_article(article_id: int):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM articles WHERE id=%s", (article_id,))
    conn.commit()
    cur.close()
    conn.close()
    return {"success": True}

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
        "telegram": "configured" if TELEGRAM_BOT_TOKEN else "not set"
    }
