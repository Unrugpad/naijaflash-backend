import os
import json
import random
import asyncio
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Optional, List

import httpx
from fastapi import FastAPI, HTTPException, BackgroundTasks, Request
from fastapi.middleware.cors import CORSMiddleware
import psycopg2
from psycopg2.extras import RealDictCursor
import groq

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="NaijaFlash API", version="3.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

# ── ENV ──
DATABASE_URL = os.getenv("DATABASE_URL")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_ADMIN_CHAT_ID = os.getenv("TELEGRAM_ADMIN_CHAT_ID")
UNSPLASH_ACCESS_KEY = os.getenv("UNSPLASH_ACCESS_KEY")
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY")
NEWS_API_KEY = os.getenv("NEWS_API_KEY")
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY")

CATEGORIES = ["entertainment", "finance", "tech", "health", "education", "news", "football"]

CAT_KEYWORDS = {
    "football": ["football","soccer","epl","ucl","champions league","la liga","serie a","bundesliga",
                 "premier league","afcon","super eagles","fifa","goal","match","score","vs","fc ",
                 "united","city","arsenal","chelsea","liverpool","barcelona","madrid","osimhen",
                 "lookman","saka","mbappe","haaland","yamal","ronaldo","messi","bundesliga"],
    "finance": ["naira","dollar","bitcoin","crypto","cbn","bank","money","invest","usdt","eth","bnb",
                "exchange rate","interest rate","inflation","stock","forex","binance","coinbase",
                "paystack","flutterwave","gtbank","access bank","zenith","uba","stanbic"],
    "entertainment": ["nollywood","davido","burna","wizkid","asake","olamide","tems","ckay","rema",
                      "celebrity","movie","music","album","concert","grammy","award","netflix","amvca",
                      "bbnaija","big brother","afrobeats","singer","actor","actress"],
    "tech": ["phone","iphone","samsung","tecno","infinix","itel","xiaomi","app","startup","software",
             "android","ios","laptop","computer","gadget","data","internet","5g","ai","artificial intelligence"],
    "health": ["health","malaria","typhoid","hospital","drug","medication","symptom","diabetes","blood pressure",
               "cancer","hiv","covid","vaccine","doctor","nurse","ministry of health","lassa","cholera"],
    "education": ["jamb","waec","neco","school","university","polytechnic","scholarship","job","graduate",
                  "nysc","admission","cut off","result","strike","asuu","education ministry"],
    "news": ["tinubu","obi","atiku","governor","senate","house of rep","court","police","army","military",
             "attack","protest","strike","government","minister","policy","law","election","vote"]
}

CAT_PROMPTS = {
    "entertainment": "Nigerian entertainment, Nollywood, Afrobeats music, celebrity news",
    "finance": "Nigerian finance, exchange rates, crypto, CBN policy, investments",
    "tech": "Tech and phones in Nigeria, smartphones, startups, apps",
    "health": "Health tips for Nigerians, illnesses, medications, wellness",
    "education": "Nigerian education, JAMB, WAEC, scholarships, jobs",
    "news": "Nigerian news, politics, government policy, social issues",
    "football": "Nigerian and international football, match reports, player stats, Super Eagles"
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
    if TELEGRAM_ADMIN_CHAT_ID:
        try:
            cur.execute("""
                INSERT INTO admins (chat_id, username, added_by)
                VALUES (%s, 'owner', %s) ON CONFLICT (chat_id) DO NOTHING
            """, (int(TELEGRAM_ADMIN_CHAT_ID), int(TELEGRAM_ADMIN_CHAT_ID)))
        except Exception as e:
            logger.error(f"Admin seed error: {e}")
    conn.commit()
    cur.close()
    conn.close()
    logger.info("DB initialized")

# ── ADMIN ──
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

# ── CLASSIFY TOPIC ──
def classify_topic(topic: str) -> Optional[str]:
    t = topic.lower()
    scores = {cat: 0 for cat in CATEGORIES}
    for cat, keywords in CAT_KEYWORDS.items():
        for kw in keywords:
            if kw in t:
                scores[cat] += 1
    best = max(scores, key=scores.get)
    if scores[best] == 0:
        return None  # Not relevant to any category
    return best

# ── GOOGLE TRENDS RSS ──
async def fetch_nigeria_trends() -> List[dict]:
    """Fetch real trending topics in Nigeria from Google Trends RSS."""
    trends = []
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                "https://trends.google.com/trending/rss?geo=NG",
                headers={"User-Agent": "Mozilla/5.0 (compatible; NaijaFlash/1.0)"}
            )
            if r.status_code == 200:
                root = ET.fromstring(r.text)
                items = root.findall(".//item")
                for item in items:
                    title_el = item.find("title")
                    if title_el is not None and title_el.text:
                        topic = title_el.text.strip()
                        cat = classify_topic(topic)
                        if cat:  # Only include if it matches our categories
                            trends.append({"topic": topic, "category": cat})
                logger.info(f"Google Trends: found {len(trends)} relevant trends from Nigeria")
    except Exception as e:
        logger.error(f"Google Trends RSS error: {e}")
    return trends

# ── FETCH REAL NEWS INFO ──
async def fetch_news_context(topic: str) -> str:
    """Fetch real news/info about a topic to give AI real facts."""
    context = ""

    # Try GNews API (free tier: 100 requests/day)
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                "https://gnews.io/api/v4/search",
                params={
                    "q": topic,
                    "lang": "en",
                    "country": "ng",
                    "max": 5,
                    "token": os.getenv("GNEWS_API_KEY", "")
                }
            )
            if r.status_code == 200:
                articles = r.json().get("articles", [])
                if articles:
                    summaries = []
                    for a in articles[:4]:
                        summaries.append(
                            f"Headline: {a.get('title','')}\n"
                            f"Source: {a.get('source',{}).get('name','')}\n"
                            f"Summary: {a.get('description','')}"
                        )
                    context = "\n\n".join(summaries)
                    logger.info(f"GNews: got {len(articles)} articles for '{topic}'")
                    return context
    except Exception as e:
        logger.info(f"GNews failed: {e}")

    # Try NewsAPI as fallback
    if NEWS_API_KEY:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    "https://newsapi.org/v2/everything",
                    params={
                        "q": topic,
                        "language": "en",
                        "sortBy": "publishedAt",
                        "pageSize": 5,
                        "apiKey": NEWS_API_KEY
                    }
                )
                if r.status_code == 200:
                    articles = r.json().get("articles", [])
                    if articles:
                        summaries = []
                        for a in articles[:4]:
                            summaries.append(
                                f"Headline: {a.get('title','')}\n"
                                f"Source: {a.get('source',{}).get('name','')}\n"
                                f"Summary: {a.get('description','')}"
                            )
                        context = "\n\n".join(summaries)
                        logger.info(f"NewsAPI: got {len(articles)} articles for '{topic}'")
                        return context
        except Exception as e:
            logger.info(f"NewsAPI failed: {e}")

    return context

# ── FETCH FOOTBALL STATS ──
async def fetch_football_data(topic: str) -> str:
    """Fetch real football match data when a football topic is trending."""
    if not API_FOOTBALL_KEY:
        return ""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            # Search for fixtures related to the topic
            r = await client.get(
                "https://v3.football.api-sports.io/fixtures",
                params={"last": 5, "timezone": "Africa/Lagos"},
                headers={"x-apisports-key": API_FOOTBALL_KEY}
            )
            if r.status_code == 200:
                fixtures = r.json().get("response", [])
                topic_lower = topic.lower()
                relevant = []
                for f in fixtures:
                    home = f["teams"]["home"]["name"].lower()
                    away = f["teams"]["away"]["name"].lower()
                    league = f["league"]["name"].lower()
                    # Check if topic mentions any of these teams
                    if any(word in topic_lower for word in [home.split()[0], away.split()[0], league.split()[0]]):
                        home_score = f["goals"]["home"]
                        away_score = f["goals"]["away"]
                        relevant.append(
                            f"Match: {f['teams']['home']['name']} {home_score} - {away_score} {f['teams']['away']['name']}\n"
                            f"League: {f['league']['name']}\n"
                            f"Date: {f['fixture']['date'][:10]}\n"
                            f"Status: {f['fixture']['status']['long']}"
                        )
                if relevant:
                    logger.info(f"API-Football: found {len(relevant)} relevant fixtures")
                    return "\n\n".join(relevant[:3])
    except Exception as e:
        logger.error(f"API-Football error: {e}")
    return ""

# ── FETCH FINANCE DATA ──
async def fetch_finance_data(topic: str) -> str:
    """Fetch real exchange rates and crypto prices for finance articles."""
    data_parts = []
    topic_lower = topic.lower()
    try:
        # Dollar/Naira rate from ExchangeRate API (free)
        if any(w in topic_lower for w in ["dollar","naira","rate","forex","exchange"]):
            async with httpx.AsyncClient(timeout=8) as client:
                r = await client.get("https://open.er-api.com/v6/latest/USD")
                if r.status_code == 200:
                    rates = r.json().get("rates", {})
                    ngn = rates.get("NGN", 0)
                    gbp_usd = 1 / rates.get("GBP", 1)
                    eur_usd = 1 / rates.get("EUR", 1)
                    data_parts.append(
                        f"Live Exchange Rates (Official):\n"
                        f"USD/NGN: ₦{ngn:.2f}\n"
                        f"GBP/NGN: ₦{ngn * gbp_usd:.2f}\n"
                        f"EUR/NGN: ₦{ngn * eur_usd:.2f}"
                    )

        # Crypto prices from CoinGecko (free, no key needed)
        if any(w in topic_lower for w in ["bitcoin","btc","crypto","ethereum","eth","usdt","bnb","solana"]):
            async with httpx.AsyncClient(timeout=8) as client:
                r = await client.get(
                    "https://api.coingecko.com/api/v3/simple/price",
                    params={
                        "ids": "bitcoin,ethereum,tether,binancecoin,solana",
                        "vs_currencies": "usd,ngn"
                    }
                )
                if r.status_code == 200:
                    prices = r.json()
                    lines = []
                    if "bitcoin" in prices:
                        lines.append(f"BTC: ${prices['bitcoin']['usd']:,.0f} / ₦{prices['bitcoin']['ngn']:,.0f}")
                    if "ethereum" in prices:
                        lines.append(f"ETH: ${prices['ethereum']['usd']:,.0f} / ₦{prices['ethereum']['ngn']:,.0f}")
                    if "tether" in prices:
                        lines.append(f"USDT: ${prices['tether']['usd']:.2f} / ₦{prices['tether']['ngn']:,.0f}")
                    if "binancecoin" in prices:
                        lines.append(f"BNB: ${prices['binancecoin']['usd']:,.0f} / ₦{prices['binancecoin']['ngn']:,.0f}")
                    if lines:
                        data_parts.append("Live Crypto Prices:\n" + "\n".join(lines))

    except Exception as e:
        logger.error(f"Finance data error: {e}")

    return "\n\n".join(data_parts)

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

# ── SLUG ──
def slugify(text: str) -> str:
    text = text.lower()
    text = re.sub(r'[^a-z0-9\s-]', '', text)
    text = re.sub(r'\s+', '-', text.strip())
    text = re.sub(r'-+', '-', text)
    return text[:80] + '-' + str(int(datetime.now().timestamp()))[-6:]

# ── AI WRITER ──
async def generate_article(topic: str, category: str, real_data: str = "") -> dict:
    cat_context = CAT_PROMPTS.get(category, "Nigerian news")

    data_section = ""
    if real_data:
        data_section = f"\n\nREAL DATA TO USE IN YOUR ARTICLE:\n{real_data}\n\nUse these real facts in the article. Do not make up statistics or figures."

    prompt = f"""You are a Nigerian news journalist writing for NaijaFlash, Nigeria's fastest news blog.

Write a complete, SEO-optimized news article about: {topic}
Category: {cat_context}{data_section}

STYLE RULES:
- Write in clear, simple English that everyday Nigerians understand
- Direct, punchy sentences — no unnecessary grammar
- Use Nigerian context, naira prices, local references where relevant
- Sound like a real journalist, not AI
- Only include facts you are confident about — do not fabricate statistics
- For football: include match stats, player ratings, goal details if data is provided
- For finance: include real figures if provided
- Minimum 400 words. Include 2-3 subheadings.

OUTPUT FORMAT — return ONLY this JSON, no markdown, no explanation:
{{
  "title": "SEO headline max 80 chars with main keyword",
  "excerpt": "2-sentence summary for SEO meta description",
  "body": "Full article HTML using only <p><h2><h3><ul><li><strong> tags",
  "image_query": "3-word image search query"
}}"""

    # Try Groq first
    if GROQ_API_KEY:
        try:
            client = groq.Groq(api_key=GROQ_API_KEY)
            response = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.6,
                max_tokens=2500
            )
            text = response.choices[0].message.content.strip()
            text = text.replace("```json", "").replace("```", "").strip()
            return json.loads(text)
        except Exception as e:
            logger.error(f"Groq failed: {e}")

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
                        "max_tokens": 2500,
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

# ── TELEGRAM ──
async def send_to_all_admins(text: str, reply_markup: dict = None):
    admins = get_all_admins()
    for chat_id in admins:
        await send_telegram(chat_id, text, reply_markup)

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

async def send_article_for_approval(article_id: int, title: str, excerpt: str, category: str, topic: str):
    text = (
        f"📰 <b>New Article Ready</b>\n\n"
        f"<b>Trending Topic:</b> {topic}\n"
        f"<b>Category:</b> {category.upper()}\n\n"
        f"<b>Title:</b>\n{title}\n\n"
        f"<b>Excerpt:</b>\n{excerpt}\n\n"
        f"<b>ID:</b> #{article_id}"
    )
    markup = {
        "inline_keyboard": [[
            {"text": "✅ Approve", "callback_data": f"approve_{article_id}"},
            {"text": "❌ Reject", "callback_data": f"reject_{article_id}"}
        ]]
    }
    await send_to_all_admins(text, reply_markup=markup)

# ── MARK TOPIC USED ──
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

def is_topic_used(topic: str) -> bool:
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM used_topics WHERE topic=%s", (topic,))
        exists = cur.fetchone() is not None
        cur.close()
        conn.close()
        return exists
    except:
        return False

# ── MAIN PIPELINE ──
async def run_pipeline():
    """
    Full pipeline:
    1. Fetch all trending topics in Nigeria from Google Trends
    2. Filter by our categories
    3. For each new trend, fetch real data (news/football stats/rates)
    4. Generate article with real facts
    5. Send for approval
    """
    logger.info("Pipeline started")
    generated = 0

    try:
        trends = await fetch_nigeria_trends()

        if not trends:
            await send_to_all_admins("⚠️ No trends fetched from Google Trends. Check connection.")
            return

        new_trends = [t for t in trends if not is_topic_used(t["topic"])]
        logger.info(f"Found {len(trends)} trends, {len(new_trends)} are new")

        if not new_trends:
            await send_to_all_admins("ℹ️ All current trending topics already covered. Try again later.")
            return

        # Process up to 3 new trends per run to avoid spamming
        for trend in new_trends[:3]:
            topic = trend["topic"]
            category = trend["category"]

            try:
                logger.info(f"Processing: [{category}] {topic}")
                real_data = ""

                # Fetch real data based on category
                if category == "football":
                    football_data = await fetch_football_data(topic)
                    news_data = await fetch_news_context(topic)
                    real_data = "\n\n".join(filter(None, [football_data, news_data]))
                elif category == "finance":
                    finance_data = await fetch_finance_data(topic)
                    news_data = await fetch_news_context(topic)
                    real_data = "\n\n".join(filter(None, [finance_data, news_data]))
                else:
                    real_data = await fetch_news_context(topic)

                # Generate article
                article_data = await generate_article(topic, category, real_data)
                image_url = await fetch_image(article_data.get("image_query", topic), category)

                # Save to DB
                slug = slugify(article_data["title"])
                conn = get_db()
                cur = conn.cursor()
                cur.execute("""
                    INSERT INTO articles (slug, title, category, excerpt, body, image_url, status)
                    VALUES (%s, %s, %s, %s, %s, %s, 'pending') RETURNING id
                """, (slug, article_data["title"], category, article_data.get("excerpt",""), article_data["body"], image_url))
                article_id = cur.fetchone()["id"]
                conn.commit()
                cur.close()
                conn.close()

                mark_topic_used(topic)

                await send_article_for_approval(article_id, article_data["title"], article_data.get("excerpt",""), category, topic)
                generated += 1
                logger.info(f"Article #{article_id} sent for approval: {article_data['title']}")

                # Small delay between articles
                await asyncio.sleep(2)

            except Exception as e:
                logger.error(f"Error processing trend '{topic}': {e}")
                continue

        if generated == 0:
            await send_to_all_admins("⚠️ Pipeline ran but no articles were generated. Check logs.")
        else:
            logger.info(f"Pipeline complete. {generated} articles generated.")

    except Exception as e:
        logger.error(f"Pipeline error: {e}")
        await send_to_all_admins(f"⚠️ Pipeline error: {str(e)[:200]}")

# ── API ROUTES ──
@app.on_event("startup")
async def startup():
    init_db()
    logger.info("NaijaFlash backend v3 started")

@app.get("/")
async def root():
    return {"status": "NaijaFlash API running", "version": "3.0.0"}

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
        "telegram": "configured" if TELEGRAM_BOT_TOKEN else "not set",
        "news_api": "configured" if NEWS_API_KEY else "not set",
        "api_football": "configured" if API_FOOTBALL_KEY else "not set"
    }

@app.post("/api/telegram/webhook")
async def telegram_webhook(request: Request):
    try:
        data = await request.json()

        if "callback_query" in data:
            cb = data["callback_query"]
            cb_data = cb.get("data", "")
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

        elif "message" in data:
            msg = data["message"]
            text = msg.get("text", "").strip()
            chat_id = msg["chat"]["id"]
            user_id = msg["from"]["id"]

            if text == "/start":
                await send_telegram(chat_id, "👋 Welcome to <b>NaijaFlash Bot</b>!\n\nSend /help to see all commands.")

            elif text == "/help":
                cmd = (
                    "🤖 <b>NaijaFlash Bot Commands</b>\n\n"
                    "/stats — View blog statistics\n"
                    "/generate — Fetch trends & generate articles now\n"
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

            elif text == "/generate":
                if not is_admin(user_id):
                    await send_telegram(chat_id, "⛔ Admins only.")
                    return {"ok": True}
                await send_telegram(chat_id, "⚙️ Fetching Nigeria trends & generating articles...")
                asyncio.create_task(run_pipeline())

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
