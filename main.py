import os
import json
import random
import asyncio
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Optional, List

import httpx
from fastapi import FastAPI, HTTPException, BackgroundTasks, Request
from fastapi.middleware.cors import CORSMiddleware
import psycopg2
from psycopg2.extras import RealDictCursor
import groq

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="NaijaFlash API", version="4.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

DATABASE_URL = os.getenv("DATABASE_URL")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_ADMIN_CHAT_ID = os.getenv("TELEGRAM_ADMIN_CHAT_ID")
UNSPLASH_ACCESS_KEY = os.getenv("UNSPLASH_ACCESS_KEY")
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY")
NEWS_API_KEY = os.getenv("NEWS_API_KEY")
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY")
GNEWS_API_KEY = os.getenv("GNEWS_API_KEY")

CATEGORIES = ["entertainment", "finance", "tech", "health", "education", "news", "football"]

# ── KEYWORD SETS ──
# "vs" pattern handled separately — always football
FOOTBALL_KEYWORDS = [
    "football","soccer","epl","ucl","champions league","la liga","serie a","bundesliga",
    "premier league","afcon","super eagles","fifa","goal","match","score","fixture",
    "united","arsenal","chelsea","liverpool","barcelona","madrid","juventus","psg","milan",
    "osimhen","lookman","saka","mbappe","mbappé","haaland","yamal","vinicius","bellingham",
    "ronaldo","messi","neymar","salah","kane","transfer","signing","winger","striker",
    "goalkeeper","defender","midfielder","manager","coach","league table","standings",
    "hat trick","penalty","red card","yellow card","offside","var","half time","full time",
    "iwobi","chukwueze","ndidi","aribo","troost-ekong","nkwocha","ighalo","enyimba",
    "kano pillars","rangers fc nigeria","naija football"
]

FINANCE_KEYWORDS = [
    "naira","dollar","pound","euro","exchange rate","cbn","central bank","inflation",
    "bitcoin","btc","ethereum","eth","crypto","usdt","bnb","solana","binance","coinbase",
    "invest","investment","stock","shares","nse","dangote","flutterwave","paystack",
    "gtbank","access bank","zenith","uba","fidelity bank","interest rate","loan","mortgage",
    "forex","money","fund","budget","economy","gdp","recession","salary","wage","tax",
    "pension","saving","opay","palmpay","kuda","piggyvest","cowrywise"
]

ENTERTAINMENT_KEYWORDS = [
    "nollywood","davido","burna boy","wizkid","asake","olamide","tems","ckay","rema",
    "celebrity","movie","film","music","album","song","concert","grammy","award","amvca",
    "bbnaija","big brother","afrobeats","singer","actor","actress","rapper","producer",
    "tiwa savage","yemi alade","patoranking","fireboy","omah lay","kizz daniel",
    "adekunle gold","simi","netflix","amazon prime","iroko tv","festival","tour",
    "music video","collaboration","feature","remix","ep","playlist"
]

TECH_KEYWORDS = [
    "iphone","samsung","tecno","infinix","itel","xiaomi","oppo","vivo","oneplus","realme",
    "phone","smartphone","laptop","computer","tablet","gadget","app","software","android",
    "ios","data plan","internet","5g","4g","wifi","broadband","mtn","airtel","glo","9mobile",
    "startup","tech company","artificial intelligence","ai","chatgpt","robot","drone",
    "electric car","ev","solar","renewable","cybersecurity","hack","data breach"
]

HEALTH_KEYWORDS = [
    "health","malaria","typhoid","cholera","lassa fever","hiv","aids","covid","monkeypox",
    "hospital","clinic","doctor","nurse","medication","drug","symptom","treatment","vaccine",
    "diabetes","blood pressure","hypertension","cancer","stroke","heart attack","fever",
    "headache","pregnancy","birth","infant","child health","ministry of health","who","nafdac",
    "pharmacy","chemist","herbal","remedy","diet","nutrition","fitness","mental health"
]

EDUCATION_KEYWORDS = [
    "jamb","waec","neco","nabteb","school","university","polytechnic","college","student",
    "scholarship","bursary","grant","job","employment","graduate","nysc","admission",
    "cut off mark","result","timetable","examination","exam","asuu","strike","lecturer",
    "professor","vice chancellor","education ministry","subeb","ubec","post utme",
    "direct entry","degree","hnd","ond","masters","phd","fellowship","internship"
]

NEWS_KEYWORDS = [
    "tinubu","peter obi","atiku","el-rufai","wike","obi cubana","governor","senate",
    "house of rep","court","supreme court","efcc","icpc","police","army","military",
    "attack","bandits","boko haram","ipob","protest","strike","riot","government",
    "minister","commissioner","policy","law","bill","election","vote","inec","tribunal",
    "budget","subsidy","fuel","petrol","electricity","power","tariff","tax","customs",
    "immigration","visa","passport","foreign affairs","united nations","african union"
]

CAT_KEYWORD_MAP = {
    "football": FOOTBALL_KEYWORDS,
    "finance": FINANCE_KEYWORDS,
    "entertainment": ENTERTAINMENT_KEYWORDS,
    "tech": TECH_KEYWORDS,
    "health": HEALTH_KEYWORDS,
    "education": EDUCATION_KEYWORDS,
    "news": NEWS_KEYWORDS,
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

# Known footballer names and teams for classification boost
KNOWN_FOOTBALLERS = [
    "mbappe","mbappé","haaland","yamal","vinicius","bellingham","salah","kane",
    "osimhen","lookman","saka","ronaldo","messi","neymar","rashford","de bruyne",
    "rodri","pedri","gavi","lewandowski","benzema","modric","kroos","alisson",
    "ederson","neuer","courtois","ter stegen","dembele","griezmann","kante",
    "pogba","lukaku","sterling","mount","trent","robertson","van dijk"
]

KNOWN_TEAMS = [
    "arsenal","chelsea","liverpool","manchester","city","united","tottenham","spurs",
    "barcelona","madrid","juventus","milan","inter","napoli","roma","psg","dortmund",
    "bayern","atletico","sevilla","valencia","villarreal","porto","benfica","ajax",
    "rangers","celtic","feyenoord","club brugge","lazio","atalanta","leicester",
    "everton","newcastle","aston villa","west ham","wolves","brighton","brentford"
]

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

# ── SMART CLASSIFIER ──
def classify_topic(topic: str) -> Optional[str]:
    """
    Classify a trending topic into one of our 7 categories.
    Returns None if not confident enough — topic will be skipped.
    """
    t = topic.lower().strip()

    # Rule 1: "X vs Y" or "X v Y" pattern = always football
    if re.search(r'\bvs?\b', t) or ' v ' in t:
        return "football"

    # Rule 2: Check for known footballer names
    for name in KNOWN_FOOTBALLERS:
        if name in t:
            return "football"

    # Rule 3: Check for known team names
    for team in KNOWN_TEAMS:
        if team in t:
            return "football"

    # Rule 4: Score each category by keyword matches
    scores = {}
    for cat, keywords in CAT_KEYWORD_MAP.items():
        score = 0
        for kw in keywords:
            if kw in t:
                # Longer keyword matches = more confident
                score += len(kw.split())
        if score > 0:
            scores[cat] = score

    if not scores:
        return None  # Not confident — skip this topic

    # Rule 5: Require minimum confidence score of 1
    best_cat = max(scores, key=scores.get)
    if scores[best_cat] < 1:
        return None

    # Rule 6: If two categories tie, prefer more specific one
    sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    if len(sorted_scores) > 1 and sorted_scores[0][1] == sorted_scores[1][1]:
        # Tie — pick whichever is not "news" (more specific)
        for cat, score in sorted_scores:
            if cat != "news":
                return cat

    return best_cat

# ── GOOGLE TRENDS RSS ──
async def fetch_nigeria_trends() -> List[dict]:
    """
    Fetch trending topics in Nigeria from Google Trends RSS.
    Only returns topics from last 24 hours, correctly classified.
    """
    trends = []
    skipped = []

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                "https://trends.google.com/trending/rss?geo=NG",
                headers={"User-Agent": "Mozilla/5.0 (compatible; NaijaFlash/1.0)"}
            )
            if r.status_code != 200:
                logger.error(f"Google Trends returned {r.status_code}")
                return []

            root = ET.fromstring(r.text)
            items = root.findall(".//item")
            now = datetime.now(timezone.utc)

            for item in items:
                title_el = item.find("title")
                pubdate_el = item.find("pubDate")

                if title_el is None or not title_el.text:
                    continue

                topic = title_el.text.strip()

                # Recency filter — skip if older than 24 hours
                if pubdate_el is not None and pubdate_el.text:
                    try:
                        pub_dt = parsedate_to_datetime(pubdate_el.text)
                        age_hours = (now - pub_dt).total_seconds() / 3600
                        if age_hours > 24:
                            skipped.append(f"OLD({age_hours:.0f}h): {topic}")
                            continue
                    except Exception:
                        pass  # If we can't parse date, allow it through

                # Category classification
                cat = classify_topic(topic)
                if cat is None:
                    skipped.append(f"UNCLASSIFIED: {topic}")
                    continue

                trends.append({"topic": topic, "category": cat})

            logger.info(f"Google Trends Nigeria: {len(trends)} valid, {len(skipped)} skipped")
            if skipped:
                logger.info(f"Skipped: {skipped[:10]}")

    except Exception as e:
        logger.error(f"Google Trends RSS error: {e}")

    return trends

# ── TAVILY SEARCH ──
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")

def build_search_query(topic: str, category: str) -> str:
    """Build the best search query for a topic based on category."""
    t = topic.lower()
    year = datetime.now().year

    if category == "football":
        if re.search(r'\bvs?\b', t) or ' v ' in t:
            return f"{topic} result score goals {year}"
        else:
            return f"{topic} football news {year}"
    elif category == "finance":
        return f"{topic} Nigeria rate naira {year}"
    elif category == "education":
        return f"{topic} Nigeria {year}"
    else:
        return f"{topic} Nigeria {year}"

async def fetch_news_context(topic: str, category: str = "news") -> str:
    """
    Fetch real current news using Tavily — live Google search results.
    Falls back to GNews/NewsAPI if Tavily not available.
    """
    query = build_search_query(topic, category)

    # ── Tavily (primary) ──
    if TAVILY_API_KEY:
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.post(
                    "https://api.tavily.com/search",
                    json={
                        "api_key": TAVILY_API_KEY,
                        "query": query,
                        "search_depth": "basic",
                        "max_results": 5,
                        "include_answer": True,
                        "include_raw_content": False,
                    }
                )
                if r.status_code == 200:
                    data = r.json()
                    parts = []

                    # Include Tavily's auto-generated answer summary if available
                    answer = data.get("answer", "")
                    if answer:
                        parts.append(f"Summary: {answer}")

                    # Include individual search results
                    results = data.get("results", [])
                    for res in results[:5]:
                        title = res.get("title", "")
                        url = res.get("url", "")
                        content = res.get("content", "")[:400]
                        if title:
                            parts.append(f"Source: {title}\nURL: {url}\nContent: {content}")

                    if parts:
                        logger.info(f"Tavily: {len(results)} results for '{query}'")
                        return "\n\n".join(parts)
        except Exception as e:
            logger.error(f"Tavily error: {e}")

    # ── GNews fallback ──
    if GNEWS_API_KEY:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    "https://gnews.io/api/v4/search",
                    params={"q": query, "lang": "en", "max": 5,
                            "token": GNEWS_API_KEY, "sortby": "publishedAt"}
                )
                if r.status_code == 200:
                    articles = r.json().get("articles", [])
                    if articles:
                        summaries = [
                            f"Headline: {a.get('title','')}\nSource: {a.get('source',{}).get('name','')}\nPublished: {a.get('publishedAt','')[:10]}\nSummary: {a.get('description','')[:300]}"
                            for a in articles[:5] if a.get('title')
                        ]
                        if summaries:
                            logger.info(f"GNews fallback: {len(summaries)} for '{query}'")
                            return "\n\n".join(summaries)
        except Exception as e:
            logger.info(f"GNews error: {e}")

    # ── NewsAPI fallback ──
    if NEWS_API_KEY:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    "https://newsapi.org/v2/everything",
                    params={"q": query, "language": "en", "sortBy": "publishedAt",
                            "pageSize": 5, "apiKey": NEWS_API_KEY}
                )
                if r.status_code == 200:
                    articles = r.json().get("articles", [])
                    clean = [a for a in articles if a.get('title') and '[Removed]' not in a.get('title','')]
                    if clean:
                        summaries = [
                            f"Headline: {a.get('title','')}\nSource: {a.get('source',{}).get('name','')}\nPublished: {a.get('publishedAt','')[:10]}\nSummary: {a.get('description','')[:300]}"
                            for a in clean[:5]
                        ]
                        logger.info(f"NewsAPI fallback: {len(summaries)} for '{query}'")
                        return "\n\n".join(summaries)
        except Exception as e:
            logger.info(f"NewsAPI error: {e}")

    logger.info(f"No news context found for: {topic}")
    return ""

# ── FETCH FOOTBALL DATA ──
async def fetch_football_data(topic: str) -> str:
    """Fetch real match stats from API-Football using smart team matching."""
    if not API_FOOTBALL_KEY:
        return ""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            # Fetch both recent and upcoming fixtures
            results = []
            for endpoint_params in [{"last": 15}, {"next": 5}]:
                r = await client.get(
                    "https://v3.football.api-sports.io/fixtures",
                    params={**endpoint_params, "timezone": "Africa/Lagos"},
                    headers={"x-apisports-key": API_FOOTBALL_KEY}
                )
                if r.status_code == 200:
                    results.extend(r.json().get("response", []))

            topic_lower = topic.lower()
            # Extract meaningful words from topic (min 3 chars, not stopwords)
            stopwords = {"versus","match","game","news","latest","today","result","score","update","vs","the","and","for"}
            topic_words = [w for w in re.split(r'\W+', topic_lower) if len(w) >= 3 and w not in stopwords]

            relevant = []
            for f in results:
                home = f["teams"]["home"]["name"].lower()
                away = f["teams"]["away"]["name"].lower()
                league = f["league"]["name"].lower()
                home_words = home.split()
                away_words = away.split()

                matched = False
                for tw in topic_words:
                    if any(tw in hw for hw in home_words) or any(tw in aw for aw in away_words) or tw in league:
                        matched = True
                        break

                if matched:
                    h_score = f["goals"]["home"]
                    a_score = f["goals"]["away"]
                    status = f["fixture"]["status"]["long"]
                    date = f["fixture"]["date"][:10]

                    # Build detailed match info
                    match_info = [
                        f"Match: {f['teams']['home']['name']} vs {f['teams']['away']['name']}",
                        f"Score: {h_score} - {a_score}" if h_score is not None else "Score: Not yet played",
                        f"Status: {status}",
                        f"Date: {date}",
                        f"League: {f['league']['name']}",
                        f"Round: {f['league'].get('round','')}"
                    ]

                    # Add scorers if available
                    events = f.get("events", [])
                    goals = [e for e in events if e.get("type") == "Goal"]
                    if goals:
                        goal_lines = []
                        for g in goals:
                            scorer = g.get("player",{}).get("name","Unknown")
                            minute = g.get("time",{}).get("elapsed","?")
                            team = g.get("team",{}).get("name","")
                            goal_lines.append(f"{scorer} ({team}) {minute}'")
                        match_info.append("Goals: " + ", ".join(goal_lines))

                    relevant.append("\n".join(match_info))
                if relevant:
                    logger.info(f"API-Football: {len(relevant)} fixtures for '{topic}'")
                    return "\n\n".join(relevant[:3])
    except Exception as e:
        logger.error(f"API-Football error: {e}")
    return ""

# ── FETCH FINANCE DATA ──
async def fetch_finance_data(topic: str) -> str:
    """Fetch real live rates for finance articles."""
    parts = []
    t = topic.lower()
    try:
        if any(w in t for w in ["dollar","naira","rate","forex","exchange","pound","euro","cbn"]):
            async with httpx.AsyncClient(timeout=8) as client:
                r = await client.get("https://open.er-api.com/v6/latest/USD")
                if r.status_code == 200:
                    rates = r.json().get("rates", {})
                    ngn = rates.get("NGN", 0)
                    gbp = rates.get("GBP", 1)
                    eur = rates.get("EUR", 1)
                    parts.append(
                        f"Live Exchange Rates (Official):\n"
                        f"USD/NGN: ₦{ngn:.2f}\n"
                        f"GBP/NGN: ₦{ngn/gbp:.2f}\n"
                        f"EUR/NGN: ₦{ngn/eur:.2f}"
                    )

        if any(w in t for w in ["bitcoin","btc","crypto","ethereum","eth","usdt","bnb","solana","sol"]):
            async with httpx.AsyncClient(timeout=8) as client:
                r = await client.get(
                    "https://api.coingecko.com/api/v3/simple/price",
                    params={"ids": "bitcoin,ethereum,tether,binancecoin,solana", "vs_currencies": "usd,ngn"}
                )
                if r.status_code == 200:
                    prices = r.json()
                    lines = []
                    mapping = [("bitcoin","BTC"),("ethereum","ETH"),("tether","USDT"),("binancecoin","BNB"),("solana","SOL")]
                    for coin_id, symbol in mapping:
                        if coin_id in prices:
                            usd = prices[coin_id].get("usd", 0)
                            ngn = prices[coin_id].get("ngn", 0)
                            lines.append(f"{symbol}: ${usd:,.2f} / ₦{ngn:,.0f}")
                    if lines:
                        parts.append("Live Crypto Prices:\n" + "\n".join(lines))
    except Exception as e:
        logger.error(f"Finance data error: {e}")
    return "\n\n".join(parts)

# ── IMAGE ──
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
    data_section = f"\n\nREAL DATA TO USE:\n{real_data}\n\nOnly use facts from this data. Do not invent statistics." if real_data else ""

    extra_instructions = ""
    if category == "football":
        extra_instructions = (
            "\nFOOTBALL RULES:"
            "\n- If real data shows a COMPLETED match: write in PAST TENSE, include exact scoreline, goalscorers, minute of goals, player ratings (1-10), key moments"
            "\n- If real data shows an UPCOMING match: write in FUTURE TENSE as a preview, include team form, key players, prediction"
            "\n- NEVER write about a match as upcoming if the real data shows it already happened"
            "\n- Include a proper descriptive headline — not just 'Team A vs Team B'"
            "\n- Example good title: 'Michael Olise Hat-Trick Fires France Past Northern Ireland 3-1'"
        )
    elif category == "finance":
        extra_instructions = (
            "\nFINANCE RULES:"
            "\n- Use the exact rates provided in real data — do not guess or estimate"
            "\n- Explain what the rate means practically for Nigerians (e.g. what ₦50,000 buys in USD)"
            "\n- Include comparison to previous week/month if available"
        )

    prompt = f"""You are a Nigerian news journalist writing for NaijaFlash, Nigeria's fastest news blog.

Topic trending in Nigeria right now: {topic}
Category: {cat_context}{data_section}{extra_instructions}

RULES:
- Write in clear simple English Nigerians understand
- Only state facts you are sure of — never fabricate scores, statistics, or quotes
- If real data is provided, base the article on it
- If no real data, write a general informational article about the topic
- Minimum 400 words, 2-3 subheadings
- Nigerian context and naira prices where relevant

Return ONLY this JSON, no markdown, no explanation:
{{
  "title": "SEO headline max 80 chars",
  "excerpt": "2-sentence SEO summary",
  "body": "Full HTML article using only <p><h2><h3><ul><li><strong>",
  "image_query": "3-word image search query"
}}"""

    # Try Groq first
    if GROQ_API_KEY:
        try:
            client = groq.Groq(api_key=GROQ_API_KEY)
            response = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.5,
                max_tokens=2500
            )
            text = response.choices[0].message.content.strip()
            text = text.replace("```json","").replace("```","").strip()
            return json.loads(text)
        except Exception as e:
            logger.error(f"Groq failed: {e}")

    # Claude fallback
    if ANTHROPIC_API_KEY:
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                    json={"model": "claude-haiku-4-5-20251001", "max_tokens": 2500, "messages": [{"role": "user", "content": prompt}]}
                )
                data = r.json()
                text = data["content"][0]["text"].strip()
                text = text.replace("```json","").replace("```","").strip()
                return json.loads(text)
        except Exception as e:
            logger.error(f"Claude fallback failed: {e}")

    raise Exception("Both Groq and Claude API failed")

# ── TELEGRAM ──
async def send_to_all_admins(text: str, reply_markup: dict = None):
    for chat_id in get_all_admins():
        await send_telegram(chat_id, text, reply_markup)

async def send_telegram(chat_id: int, text: str, reply_markup: dict = None, parse_mode: str = "HTML"):
    if not TELEGRAM_BOT_TOKEN:
        return None
    try:
        payload = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
        if reply_markup:
            payload["reply_markup"] = json.dumps(reply_markup)
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json=payload)
            data = r.json()
            if data.get("ok"):
                return data["result"]["message_id"]
    except Exception as e:
        logger.error(f"Telegram error to {chat_id}: {e}")
    return None

async def send_article_for_approval(article_id: int, title: str, excerpt: str, category: str, topic: str):
    text = (
        f"📰 <b>New Article Ready</b>\n\n"
        f"<b>Trending:</b> {topic}\n"
        f"<b>Category:</b> {category.upper()}\n\n"
        f"<b>Title:</b>\n{title}\n\n"
        f"<b>Excerpt:</b>\n{excerpt}\n\n"
        f"<b>ID:</b> #{article_id}"
    )
    markup = {"inline_keyboard": [[
        {"text": "✅ Approve", "callback_data": f"approve_{article_id}"},
        {"text": "❌ Reject", "callback_data": f"reject_{article_id}"}
    ]]}
    await send_to_all_admins(text, reply_markup=markup)

# ── TOPIC TRACKING ──
def mark_topic_used(topic: str):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("INSERT INTO used_topics (topic) VALUES (%s) ON CONFLICT DO NOTHING", (topic,))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        logger.error(f"mark_topic_used: {e}")

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
    logger.info("Pipeline v4 started")
    generated = 0

    try:
        trends = await fetch_nigeria_trends()

        if not trends:
            await send_to_all_admins(
                "⚠️ No relevant trends found in Nigeria right now.\n"
                "Google Trends may be slow or all current trends don't match our categories."
            )
            return

        new_trends = [t for t in trends if not is_topic_used(t["topic"])]
        logger.info(f"{len(trends)} valid trends, {len(new_trends)} new")

        if not new_trends:
            await send_to_all_admins("ℹ️ All trending topics already covered. Try again later when new trends appear.")
            return

        # Process up to 3 per run
        for trend in new_trends[:3]:
            topic = trend["topic"]
            category = trend["category"]
            try:
                logger.info(f"Processing [{category}]: {topic}")
                real_data = ""

                if category == "football":
                    football_data = await fetch_football_data(topic)
                    news_data = await fetch_news_context(topic, category)
                    real_data = "\n\n".join(filter(None, [football_data, news_data]))
                elif category == "finance":
                    finance_data = await fetch_finance_data(topic)
                    news_data = await fetch_news_context(topic, category)
                    real_data = "\n\n".join(filter(None, [finance_data, news_data]))
                else:
                    real_data = await fetch_news_context(topic, category)

                article_data = await generate_article(topic, category, real_data)
                image_url = await fetch_image(article_data.get("image_query", topic), category)

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
                await asyncio.sleep(2)

            except Exception as e:
                logger.error(f"Error on '{topic}': {e}")
                continue

        if generated == 0:
            await send_to_all_admins("⚠️ Pipeline ran but failed to generate articles. Check Railway logs.")
        else:
            logger.info(f"Pipeline done. {generated} articles generated.")

    except Exception as e:
        logger.error(f"Pipeline error: {e}")
        await send_to_all_admins(f"⚠️ Pipeline error: {str(e)[:200]}")

# ── API ROUTES ──
@app.on_event("startup")
async def startup():
    init_db()
    logger.info("NaijaFlash v4 started")

@app.get("/")
async def root():
    return {"status": "NaijaFlash API running", "version": "4.0.0"}

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
    return {"total_published": total, "pending_approval": pending, "total_views": views, "by_category": [dict(r) for r in by_cat], "recent": [dict(r) for r in recent]}

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
        "status": "ok", "version": "4.0.0",
        "db": "connected" if db_ok else "error",
        "groq": "✅" if GROQ_API_KEY else "❌",
        "claude": "✅" if ANTHROPIC_API_KEY else "❌",
        "telegram": "✅" if TELEGRAM_BOT_TOKEN else "❌",
        "gnews": "✅" if GNEWS_API_KEY else "❌",
        "newsapi": "✅" if NEWS_API_KEY else "❌",
        "tavily": "✅" if TAVILY_API_KEY else "❌",
        "api_football": "✅" if API_FOOTBALL_KEY else "❌",
        "unsplash": "✅" if UNSPLASH_ACCESS_KEY else "❌",
        "pexels": "✅" if PEXELS_API_KEY else "❌",
    }

@app.post("/api/telegram/webhook")
async def telegram_webhook(request: Request):
    try:
        data = await request.json()

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
                await client.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery", json={"callback_query_id": cb["id"]})

        elif "message" in data:
            msg = data["message"]
            text = msg.get("text","").strip()
            chat_id = msg["chat"]["id"]
            user_id = msg["from"]["id"]

            if text == "/start":
                await send_telegram(chat_id, "👋 Welcome to <b>NaijaFlash Bot</b>!\n\nSend /help to see all commands.")

            elif text == "/help":
                cmd = (
                    "🤖 <b>NaijaFlash Bot Commands</b>\n\n"
                    "/stats — Blog statistics\n"
                    "/trends — See what's trending in Nigeria now\n"
                    "/generate — Generate articles from trends\n"
                    "/pending — View pending articles\n"
                    "/help — Show this message"
                )
                if is_owner(user_id):
                    cmd += "\n\n👑 <b>Owner Commands</b>\n/addadmin [chat_id]\n/removeadmin [chat_id]\n/listadmins\n/reset — Clear used topics & start fresh"
                await send_telegram(chat_id, cmd)

            elif text == "/trends":
                if not is_admin(user_id):
                    await send_telegram(chat_id, "⛔ Admins only.")
                    return {"ok": True}
                await send_telegram(chat_id, "🔍 Fetching Nigeria trends...")
                trends = await fetch_nigeria_trends()
                if not trends:
                    await send_telegram(chat_id, "No relevant trends found right now.")
                else:
                    lines = "\n".join([f"• [{t['category'].upper()}] {t['topic']}" for t in trends])
                    await send_telegram(chat_id, f"🇳🇬 <b>Trending in Nigeria Now</b>\n\n{lines}")

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
                    f"📊 <b>NaijaFlash Stats</b>\n\n✅ Published: {pub}\n⏳ Pending: {pend}\n👁 Total Views: {views}\n\n<b>By Category:</b>\n{cat_lines}"
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
                    await send_telegram(chat_id, "⛔ Owner only.")
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
                    await send_telegram(chat_id, f"✅ Admin {new_id} added.")
                    await send_telegram(new_id, "🎉 You've been added as a NaijaFlash admin! Send /help to get started.")
                except Exception as e:
                    await send_telegram(chat_id, f"❌ Error: {e}")

            elif text.startswith("/removeadmin"):
                if not is_owner(user_id):
                    await send_telegram(chat_id, "⛔ Owner only.")
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

            elif text == "/reset":
                if not is_owner(user_id):
                    await send_telegram(chat_id, "⛔ Owner only.")
                    return {"ok": True}
                try:
                    conn = get_db()
                    cur = conn.cursor()
                    cur.execute("SELECT COUNT(*) as c FROM used_topics")
                    count = cur.fetchone()["c"]
                    cur.execute("DELETE FROM used_topics")
                    conn.commit()
                    cur.close()
                    conn.close()
                    await send_telegram(chat_id,
                        f"♻️ <b>Reset complete.</b>\n\n"
                        f"Cleared {count} used topics.\n"
                        f"All trends are now fresh — send /generate to start."
                    )
                except Exception as e:
                    await send_telegram(chat_id, f"❌ Reset error: {e}")

    except Exception as e:
        logger.error(f"Webhook error: {e}")

    return {"ok": True}
