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
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")

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
    "pension","saving","opay","palmpay","kuda","piggyvest","cowrywise",
    # Nigerian listed companies and energy sector
    "seplat","mtn nigeria","airtel africa","nnpc","oando","total energies",
    "lafarge","nestle nigeria","guinness nigeria","cadbury nigeria","unilever nigeria",
    "stanbic ibtc","first bank","wema bank","sterling bank","jaiz bank","ecobank",
    "dividend","share price","stock market","ngx","securities exchange","market cap",
    "oil price","crude oil","barrel","petroleum","refinery","nnpcl","nlng",
    "bonds","treasury bills","mutual fund","etf","portfolio","capital market"
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
    "immigration","visa","passport","foreign affairs","united nations","african union",
    # Nigerian states, cities, politicians
    "kwankwaso","obiano","okowa","makinde","sanwoolu","ganduje","zulum","fayemi",
    "abuja","kano","ibadan","port harcourt","kaduna","enugu","benin city",
    "uyo","calabar","warri","owerri","abeokuta","maiduguri","sokoto","yola",
    "anambra","imo","delta","rivers","bayelsa","edo","ogun","oyo","osun","ekiti",
    "kwara","kogi","benue","plateau","nassarawa","taraba","adamawa","bauchi",
    "gombe","yobe","borno","zamfara","kebbi","katsina","jigawa",
    "senate president","speaker","chief justice","attorney general","minister of",
    "national assembly","presidency","aso rock","state house","federal government"
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
        CREATE TABLE IF NOT EXISTS ad_slots (
            id SERIAL PRIMARY KEY,
            slot_key VARCHAR(50) UNIQUE NOT NULL,
            headline TEXT,
            subtext TEXT,
            link TEXT,
            button_label VARCHAR(100),
            is_active BOOLEAN DEFAULT TRUE,
            updated_at TIMESTAMP DEFAULT NOW()
        );
        INSERT INTO ad_slots (slot_key, headline, subtext, link, button_label)
        VALUES ('slot1', 'Convert Crypto to Naira Instantly — Best Rates Guaranteed',
                'UnrugX Exchange · USDT, BTC, ETH · Instant bank transfer · No hidden fees',
                'https://unrugx.com', 'Start Now →')
        ON CONFLICT (slot_key) DO NOTHING;
        CREATE TABLE IF NOT EXISTS article_views (
            id SERIAL PRIMARY KEY,
            article_id INTEGER NOT NULL,
            viewed_at TIMESTAMP DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS idx_views_article ON article_views(article_id);
        CREATE INDEX IF NOT EXISTS idx_views_time ON article_views(viewed_at DESC);
        CREATE TABLE IF NOT EXISTS sponsored_sessions (
            id SERIAL PRIMARY KEY,
            chat_id BIGINT NOT NULL,
            step VARCHAR(50) DEFAULT 'title',
            title TEXT,
            category VARCHAR(50),
            body TEXT,
            link TEXT,
            image_url TEXT,
            excerpt TEXT,
            expires_days INTEGER,
            created_at TIMESTAMP DEFAULT NOW()
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

# Known football countries — single word trending = football
FOOTBALL_COUNTRIES = [
    "netherlands","france","england","germany","spain","italy","portugal","brazil",
    "argentina","nigeria","ghana","senegal","cameroon","egypt","morocco","algeria",
    "croatia","belgium","switzerland","denmark","sweden","norway","scotland","wales",
    "japan","korea","australia","usa","mexico","colombia","uruguay","chile","ecuador"
]

def classify_topic(topic: str) -> Optional[str]:
    """
    Classify a trending topic into one of our 7 categories.
    Returns None if not confident enough — topic will be skipped.
    """
    t = topic.lower().strip()

    # Rule 1: "X vs Y" or "X v Y" pattern
    # Only classify as football if at least one entity is a known football team/country/league
    if re.search(r'\bvs?\b', t) or ' v ' in t:
        has_football_entity = False
        for team in KNOWN_TEAMS:
            if team in t:
                has_football_entity = True
                break
        if not has_football_entity:
            for country in FOOTBALL_COUNTRIES:
                if country in t:
                    has_football_entity = True
                    break
        if not has_football_entity:
            football_league_words = ["epl","ucl","champions league","la liga","serie a",
                                     "bundesliga","premier league","afcon","fifa","ligue 1",
                                     "world cup","nations league","euro","copa"]
            for lw in football_league_words:
                if lw in t:
                    has_football_entity = True
                    break
        if has_football_entity:
            return "football"
        else:
            return None  # Not a football match — skip (e.g. Knicks vs Spurs, Tyson vs Paul)

    # Rule 2: Check for known footballer names
    for name in KNOWN_FOOTBALLERS:
        if name in t:
            return "football"

    # Rule 3: Check for known team names
    for team in KNOWN_TEAMS:
        if team in t:
            return "football"

    # Rule 4: Single football country name trending = football
    words = t.split()
    if len(words) <= 2:
        for country in FOOTBALL_COUNTRIES:
            if country in t:
                return "football"

    # Rule 5: Score each category by keyword matches
    scores = {}
    for cat, keywords in CAT_KEYWORD_MAP.items():
        score = 0
        for kw in keywords:
            if kw in t:
                score += len(kw.split())
        if score > 0:
            scores[cat] = score

    if not scores:
        return None  # No category matched — skip this topic entirely

    best_cat = max(scores, key=scores.get)
    if scores[best_cat] < 1:
        return None

    # Rule 6: If two categories tie, prefer more specific one
    sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    if len(sorted_scores) > 1 and sorted_scores[0][1] == sorted_scores[1][1]:
        for cat, score in sorted_scores:
            if cat != "news":
                return cat

    # Rule 7: Never return football unless there's a real football keyword match
    if best_cat == "football" and scores.get("football", 0) < 1:
        return None

    return best_cat

# ── TAVILY TREND DISCOVERY ──
# More specific queries to get actual news stories, not website homepages
CATEGORY_TREND_QUERIES = {
    "football": [
        "Super Eagles Nigeria football match result June 2026",
        "Premier League Champions League match result today",
        "Africa Cup Nations World Cup football Nigeria player news",
    ],
    "finance": [
        "naira dollar exchange rate black market CBN today 2026",
        "Nigeria economy inflation CBN interest rate news June 2026",
        "bitcoin crypto USDT naira price Nigeria today",
    ],
    "entertainment": [
        "Davido Burna Boy Wizkid Asake new song album 2026",
        "Nollywood movie award celebrity Nigeria entertainment news",
        "BBNaija Afrobeats Nigerian music concert tour 2026",
    ],
    "tech": [
        "smartphone phone price Nigeria Tecno Samsung iPhone 2026",
        "Nigeria tech startup internet data plan MTN Airtel 2026",
        "Nigeria technology innovation app launch news June 2026",
    ],
    "health": [
        "Nigeria disease outbreak NCDC health alert June 2026",
        "Nigeria hospital health ministry doctor medical news 2026",
        "malaria typhoid cholera Lassa fever Nigeria treatment 2026",
    ],
    "education": [
        "JAMB UTME Post-UTME result 2026 university admission Nigeria",
        "WAEC NECO result timetable exam 2026 Nigeria students",
        "Nigeria scholarship university admission NYSC update 2026",
    ],
    "news": [
        "Tinubu government policy Nigeria news June 2026",
        "Nigeria senate court EFCC police army attack news today",
        "Nigeria state governor politics election news June 2026",
    ],
}

# ── WEBSITE NAME DETECTION ──
WEBSITE_NAME_PATTERNS = [
    r'(\.ng|\.com|\.org|\.net)\s*[-–|]',
    r'[-–|]\s*(news|gist|blog|updates|latest|today|ng|media|online|tv|fm|wire|post|nigeria)$',
    r'^(myschool|soccernet|owngoal|nff official|legit\.ng|vanguard|punch|guardian|thisday|channels|arise|sahara)',
    r'(nairaland|naijaloaded|linda ikeji|bellanaija|pulse\.ng|nairametrics|premiumtimes)',
    r'^(google news|yahoo news|bing news|breaking news|latest news|top stories)',
    r'(schools and exams news|celebrity gist|breaking news, latest stories)',
    r'(nigeria football news|nigerian football|all nigeria soccer)',
    r'^\w[\w\s]{2,25}\s*[-–|:]\s*(news|updates|gist|latest|today|ng|nigeria)$',
    r'(the world\'s no\.|#\d+ source|no\. ?1 source)',
    r'(archives?|category|tag|page \d|section)\s*$',
    r'^(nigeria|nigerian)\s+(politics|finance|health|education|entertainment|tech|sports|football)\s+news',
    r'(state house|aso rock|presidency)\s*,?\s*abuja\s*$',
    r'^headline stories?\s*[·•\-–]',
    r';\s*(the punch|vanguard|channels|guardian|thisday|daily post|leadership|thisday)',
    r'latest\s*;\s*\w',
    r'^\d+:\d+\s+(mon|tue|wed|thu|fri|sat|sun)',
    r'\|\s*latest and breaking',
    r'news in nigeria\s*[-–|]',
    r'^(phone apps|mobile phones|smartphones)\s*\|',
    r'(entertainment today|entertainment news)\s*\|',
    r'^(nff official website|the nigeria football federation)',
    # Long messy strings with semicolons/bullets = RSS feed snippets
    r'.{30,}[;·•].{10,}[;·•]',
    # Strings that start with a news item then add more — likely aggregator
    r'\w+\s+\d+:\d+\s+(mon|tue|wed|thu|fri|sat|sun)',
    # Markdown headers
    r'^#{1,6}\s+',
    # Spotify/music playlists
    r'playlist by \w+',
    r'\d+ tracks?',
    r'spotify|apple music|audiomack',
    # Data table titles
    r'rates?\s*\(₦/us\$\)',
    r'nfem\s+rates?',
    r'exchange rates?\s+table',
    # Presidency/government page titles
    r'^seal of the president',
    r'^office of the (president|vice president)',
    # Source + date formats
    r'leadership,\s+nigeria\s+\d+(d|h|m)\.',
    r'\|\s+\d+(d|h|m)\s*$',
    # "Vision for" type titles — too vague
    r"^[A-Za-z']+['s]+\s+vision for",
]

def is_website_name(title: str) -> bool:
    t = title.lower().strip()
    # Too long — likely a multi-story RSS snippet not a headline
    if len(t) > 200:
        return True
    for pattern in WEBSITE_NAME_PATTERNS:
        if re.search(pattern, t):
            return True
    words = t.split()
    generic = {"news","latest","updates","gist","today","breaking","stories","headlines","nigeria","nigerian"}
    if len(words) <= 4 and len(set(words) - generic) <= 1:
        return True
    return False

def extract_best_headline(page_title: str, content: str, category: str) -> Optional[str]:
    """Extract the best real news headline from a Tavily result."""

    # Try page title first — only if it's a real headline
    if page_title and not is_website_name(page_title) and len(page_title) > 20:
        # Remove source suffix: "Kebbi Extends Retirement Age - Daily Post Nigeria" -> "Kebbi Extends..."
        clean = re.sub(r'\s*[-–|]\s*[A-Z][A-Za-z\s\.&]{2,35}$', '', page_title).strip()
        clean = re.sub(r'^\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4}\s*[-–]\s*', '', clean).strip()
        # Remove "·" and everything after (common in RSS aggregators)
        clean = re.sub(r'\s*[·•]\s*.+$', '', clean).strip()
        if len(clean) >= 20 and not is_website_name(clean):
            return clean[:150]

    # Extract from content — find first sentence that reads like a news story
    if not content:
        return None

    # Split on sentence boundaries and newlines
    sentences = re.split(r'(?<=[.!?])\s+|\n+', content)
    news_words = {
        'announce','reveal','confirm','launch','release','approve','win','lose',
        'beat','score','sign','hire','fire','arrest','accuse','deny','warn','urge',
        'hit','rise','fall','drop','increase','decrease','reach','extend','expand',
        'naira','dollar','jamb','waec','neco','police','court','government',
        'president','minister','governor','senate','million','billion','₦','$','%',
        'vs','defeat','victory','draw','qualify','dead','kill','attack','bomb',
        'crash','flood','outbreak','ban','suspend','resign','appoint','sack','probe',
        'approve','reject','pass','sign','bill','law','policy','reform','invest',
    }

    for sent in sentences:
        sent = sent.strip()
        # Must be a good length for a headline
        if len(sent) < 20 or len(sent) > 200:
            continue
        if is_website_name(sent):
            continue
        sent_lower = sent.lower()
        # Must contain at least one news action word
        if sum(1 for w in news_words if w in sent_lower) >= 1:
            # Clean it up
            result = re.sub(r'\s*[-–|]\s*[A-Z][A-Za-z\s\.&]{2,35}$', '', sent).strip()
            if len(result) >= 20:
                return result[:150]

    return None

def deduplicate_topics(topics: List[dict]) -> List[dict]:
    """Remove duplicate topics — keep only one per similar story."""
    unique = []
    seen_keywords = []
    stopwords = {"nigeria","nigerian","latest","news","today","breaking","update",
                 "about","with","from","that","this","will","have","been","their",
                 "would","could","should","after","before","during","while","says",
                 "said","also","just","over","more","into","than","then","when"}

    for topic in topics:
        t = topic["topic"].lower()
        keywords = set(w for w in re.findall(r'\b\w{5,}\b', t) if w not in stopwords)
        if not keywords:
            continue
        is_dup = any(len(keywords & seen) >= 2 for seen in seen_keywords)
        if not is_dup:
            unique.append(topic)
            seen_keywords.append(keywords)

    return unique

async def fetch_topics_from_tavily(category: str, queries) -> List[dict]:
    """
    Discover real trending topics using Tavily.
    Accepts single query string or list of queries.
    Extracts actual news headlines from content — not website page titles.
    """
    if not TAVILY_API_KEY:
        return []

    query_list = [queries] if isinstance(queries, str) else queries[:2]
    topics = []
    seen = set()

    for query in query_list:
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.post(
                    "https://api.tavily.com/search",
                    json={
                        "api_key": TAVILY_API_KEY,
                        "query": query,
                        "search_depth": "basic",
                        "max_results": 6,
                        "include_answer": False,
                        "sort_by": "date",
                    }
                )
                if r.status_code != 200:
                    continue

                for res in r.json().get("results", []):
                    page_title = res.get("title", "").strip()
                    content = res.get("content", "").strip()
                    url = res.get("url", "")
                    pub_date = res.get("published_date", "") or ""

                    # Skip results older than 14 days
                    if pub_date:
                        try:
                            pub_dt = datetime.fromisoformat(pub_date.replace("Z", "+00:00"))
                            age_days = (datetime.now(timezone.utc) - pub_dt).days
                            if age_days > 14:
                                continue
                        except Exception:
                            pass

                    skip_domains = ["google.com","bing.com","yahoo.com","reddit.com",
                                    "wikipedia.org","facebook.com","twitter.com","x.com",
                                    "youtube.com","instagram.com","tiktok.com","spotify.com",
                                    "apple.com","audiomack.com"]
                    if any(d in url for d in skip_domains):
                        continue

                    headline = extract_best_headline(page_title, content, category)
                    if not headline:
                        continue

                    h_lower = headline.lower()
                    if h_lower in seen:
                        continue
                    seen.add(h_lower)

                    # Classify — if result clearly belongs to different category, use that
                    detected_cat = classify_topic(headline)
                    # Only use detected_cat if it's confident, otherwise use search category
                    final_cat = detected_cat if detected_cat else category

                    # Skip if misclassified as football with no football keywords
                    if final_cat == "football" and category != "football":
                        if not any(kw in headline.lower() for kw in ["football","soccer","goal","match","premier","league","afcon","fifa","ucl"]):
                            final_cat = category

                    topics.append({
                        "topic": headline,
                        "category": final_cat,
                        "source": "tavily_discovery",
                        "context": content[:600]
                    })

        except Exception as e:
            logger.error(f"Tavily error [{category}] '{query[:30]}': {e}")

    logger.info(f"Tavily [{category}]: {len(topics)} headlines from {len(query_list)} queries")
    return topics

async def fetch_nigeria_trends() -> List[dict]:
    """
    Discover trending Nigerian topics using Tavily.
    Searches across all 7 categories concurrently.
    Deduplicates similar stories before returning.
    """
    all_topics = []
    seen_topics = set()

    def add_topic(topic_dict: dict):
        t = topic_dict["topic"].strip()
        t_lower = t.lower()
        if t_lower in seen_topics or len(t) < 15:
            return
        seen_topics.add(t_lower)
        all_topics.append(topic_dict)

    # Run all category searches concurrently
    tasks = [
        fetch_topics_from_tavily(cat, query)
        for cat, query in CATEGORY_TREND_QUERIES.items()
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for cat_topics in results:
        if isinstance(cat_topics, list):
            for t in cat_topics:
                add_topic(t)

    # Bonus: Google Trends RSS if available
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(
                "https://trends.google.com/trending/rss?geo=NG",
                headers={"User-Agent": "Mozilla/5.0 (compatible; NaijaFlash/1.0)"}
            )
            if r.status_code == 200:
                root = ET.fromstring(r.text)
                now = datetime.now(timezone.utc)
                for item in root.findall(".//item"):
                    title_el = item.find("title")
                    pubdate_el = item.find("pubDate")
                    if title_el is None or not title_el.text:
                        continue
                    topic = title_el.text.strip()
                    if pubdate_el is not None and pubdate_el.text:
                        try:
                            pub_dt = parsedate_to_datetime(pubdate_el.text)
                            if (now - pub_dt).total_seconds() / 3600 > 24:
                                continue
                        except Exception:
                            pass
                    cat = classify_topic(topic)
                    if cat and not is_website_name(topic):
                        add_topic({"topic": topic, "category": cat, "source": "google_trends"})
    except Exception as e:
        logger.info(f"Google Trends RSS unavailable: {e}")

    # Deduplicate similar stories
    all_topics = deduplicate_topics(all_topics)

    # Log breakdown
    cat_counts = {}
    for t in all_topics:
        cat_counts[t["category"]] = cat_counts.get(t["category"], 0) + 1
    logger.info(f"Trends after dedup: {len(all_topics)} topics | {cat_counts}")

    return all_topics

# ── TAVILY SEARCH ──

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
        # Include Nigerian-specific fintech context for remittance/transfer topics
        if any(w in t for w in ["send money","transfer","remit","abroad","overseas"]):
            return f"{topic} LemFi Wise Remitly Nigeria {year}"
        elif any(w in t for w in ["bitcoin","btc","crypto","usdt","ethereum"]):
            return f"{topic} Nigeria naira price {year}"
        elif any(w in t for w in ["dollar","naira","rate","exchange","cbn"]):
            return f"{topic} black market official rate Nigeria {year}"
        else:
            return f"{topic} Nigeria {year}"
    elif category == "education":
        return f"{topic} Nigeria {year}"
    elif category == "health":
        return f"{topic} Nigeria treatment medication {year}"
    elif category == "entertainment":
        # For entertainment, search for latest news only — avoid training knowledge
        return f"{topic} latest news {year}"
    elif category == "news":
        return f"{topic} Nigeria latest {year}"
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
                        "sort_by": "date",  # Most recent first
                    }
                )
                if r.status_code == 200:
                    data = r.json()
                    parts = []

                    # Include Tavily's auto-generated answer summary
                    answer = data.get("answer", "")
                    if answer:
                        parts.append(f"CURRENT SUMMARY (most accurate): {answer}")

                    # Include individual search results with publication dates
                    results = data.get("results", [])
                    for res in results[:5]:
                        title = res.get("title", "")
                        url = res.get("url", "")
                        content = res.get("content", "")[:400]
                        pub_date = res.get("published_date", "")
                        if title:
                            date_note = f"Published: {pub_date}" if pub_date else "Date: unknown"
                            parts.append(f"Source: {title}\n{date_note}\nURL: {url}\nContent: {content}")

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
async def fetch_player_stats(player_name: str) -> str:
    """Fetch season stats for a specific player from API-Football."""
    if not API_FOOTBALL_KEY:
        return ""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            # Search for player
            r = await client.get(
                "https://v3.football.api-sports.io/players",
                params={"search": player_name, "season": datetime.now().year},
                headers={"x-apisports-key": API_FOOTBALL_KEY}
            )
            if r.status_code != 200:
                return ""
            players = r.json().get("response", [])
            if not players:
                # Try previous season
                r = await client.get(
                    "https://v3.football.api-sports.io/players",
                    params={"search": player_name, "season": datetime.now().year - 1},
                    headers={"x-apisports-key": API_FOOTBALL_KEY}
                )
                if r.status_code == 200:
                    players = r.json().get("response", [])

            if not players:
                return ""

            # Take the first/best match
            p = players[0]
            player = p.get("player", {})
            stats_list = p.get("statistics", [])

            if not stats_list:
                return ""

            # Aggregate stats across all competitions
            total_goals = sum(s.get("goals", {}).get("total", 0) or 0 for s in stats_list)
            total_assists = sum(s.get("goals", {}).get("assists", 0) or 0 for s in stats_list)
            total_apps = sum(s.get("games", {}).get("appearences", 0) or 0 for s in stats_list)
            total_mins = sum(s.get("games", {}).get("minutes", 0) or 0 for s in stats_list)
            total_yellow = sum(s.get("cards", {}).get("yellow", 0) or 0 for s in stats_list)
            total_red = sum(s.get("cards", {}).get("red", 0) or 0 for s in stats_list)
            avg_rating = None
            ratings = [float(s.get("games", {}).get("rating", 0) or 0) for s in stats_list if s.get("games", {}).get("rating")]
            if ratings:
                avg_rating = sum(ratings) / len(ratings)

            # Best competition (most appearances)
            best_comp = max(stats_list, key=lambda s: s.get("games", {}).get("appearences", 0) or 0)
            comp_name = best_comp.get("league", {}).get("name", "Unknown League")
            club_name = best_comp.get("team", {}).get("name", "Unknown Club")

            season_year = datetime.now().year
            lines = [
                f"Player: {player.get('name', player_name)}",
                f"Age: {player.get('age', 'N/A')} | Nationality: {player.get('nationality', 'N/A')}",
                f"Club: {club_name} | Competition: {comp_name}",
                f"Season: {season_year-1}/{season_year}",
                f"Appearances: {total_apps} | Minutes: {total_mins}",
                f"Goals: {total_goals} | Assists: {total_assists}",
                f"Yellow Cards: {total_yellow} | Red Cards: {total_red}",
            ]
            if avg_rating:
                lines.append(f"Average Rating: {avg_rating:.1f}/10")

            # Add shot stats from best competition
            shots_total = best_comp.get("shots", {}).get("total", 0) or 0
            shots_on = best_comp.get("shots", {}).get("on", 0) or 0
            if shots_total:
                lines.append(f"Shots: {shots_total} total, {shots_on} on target")

            # Pass stats
            pass_acc = best_comp.get("passes", {}).get("accuracy", 0) or 0
            if pass_acc:
                lines.append(f"Pass Accuracy: {pass_acc}%")

            logger.info(f"API-Football player stats: found {player.get('name')} with {total_goals} goals")
            return "\n".join(lines)

    except Exception as e:
        logger.error(f"Player stats error: {e}")
    return ""

async def fetch_match_player_stats(fixture_id: int) -> str:
    """Fetch per-player stats for a specific match."""
    if not API_FOOTBALL_KEY:
        return ""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"https://v3.football.api-sports.io/fixtures/players",
                params={"fixture": fixture_id},
                headers={"x-apisports-key": API_FOOTBALL_KEY}
            )
            if r.status_code != 200:
                return ""
            teams_data = r.json().get("response", [])
            if not teams_data:
                return ""

            lines = ["Player Ratings & Stats From This Match:"]
            for team_data in teams_data[:2]:  # Both teams
                team_name = team_data.get("team", {}).get("name", "")
                players = team_data.get("players", [])
                team_lines = [f"\n{team_name}:"]
                for p in players[:11]:  # Starting 11
                    pname = p.get("player", {}).get("name", "")
                    stats = p.get("statistics", [{}])[0] if p.get("statistics") else {}
                    rating = stats.get("games", {}).get("rating", "N/A")
                    goals = stats.get("goals", {}).get("total", 0) or 0
                    assists = stats.get("goals", {}).get("assists", 0) or 0
                    minutes = stats.get("games", {}).get("minutes", 0) or 0
                    line = f"  {pname} — Rating: {rating}/10, {minutes}' played"
                    if goals:
                        line += f", {goals} goal{'s' if goals > 1 else ''}"
                    if assists:
                        line += f", {assists} assist{'s' if assists > 1 else ''}"
                    team_lines.append(line)
                lines.extend(team_lines)
            return "\n".join(lines)
    except Exception as e:
        logger.error(f"Match player stats error: {e}")
    return ""

async def fetch_head_to_head(team1_id: int, team2_id: int) -> str:
    """Fetch accurate head-to-head record between two teams."""
    if not API_FOOTBALL_KEY:
        return ""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                "https://v3.football.api-sports.io/fixtures/headtohead",
                params={"h2h": f"{team1_id}-{team2_id}", "last": 10},
                headers={"x-apisports-key": API_FOOTBALL_KEY}
            )
            if r.status_code == 200:
                fixtures = r.json().get("response", [])
                if not fixtures:
                    return ""
                t1_wins = t2_wins = draws = 0
                lines = []
                for f in fixtures[:5]:
                    home = f["teams"]["home"]["name"]
                    away = f["teams"]["away"]["name"]
                    h_sc = f["goals"]["home"]
                    a_sc = f["goals"]["away"]
                    date = f["fixture"]["date"][:10]
                    if h_sc is not None and a_sc is not None:
                        if h_sc > a_sc:
                            res = f"{home} won"; t1_wins += (1 if f["teams"]["home"]["id"]==team1_id else 0); t2_wins += (1 if f["teams"]["home"]["id"]==team2_id else 0)
                        elif a_sc > h_sc:
                            res = f"{away} won"; t1_wins += (1 if f["teams"]["away"]["id"]==team1_id else 0); t2_wins += (1 if f["teams"]["away"]["id"]==team2_id else 0)
                        else:
                            res = "Draw"; draws += 1
                        lines.append(f"  {date}: {home} {h_sc}-{a_sc} {away} ({res})")
                summary = f"Head-to-Head (last {len(lines)} meetings):\n" + "\n".join(lines)
                return summary
    except Exception as e:
        logger.error(f"H2H error: {e}")
    return ""

async def fetch_team_form(team_id: int, team_name: str) -> str:
    """Fetch last 5 match results for a team — for pre-match previews."""
    if not API_FOOTBALL_KEY:
        return ""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                "https://v3.football.api-sports.io/fixtures",
                params={"team": team_id, "last": 5, "timezone": "Africa/Lagos"},
                headers={"x-apisports-key": API_FOOTBALL_KEY}
            )
            if r.status_code == 200:
                fixtures = r.json().get("response", [])
                if not fixtures:
                    return ""
                form_str = ""
                result_lines = []
                for f in fixtures:
                    home_id = f["teams"]["home"]["id"]
                    h_sc = f["goals"]["home"]
                    a_sc = f["goals"]["away"]
                    if h_sc is not None and a_sc is not None:
                        is_home = home_id == team_id
                        my_score = h_sc if is_home else a_sc
                        opp_score = a_sc if is_home else h_sc
                        opponent = f["teams"]["away"]["name"] if is_home else f["teams"]["home"]["name"]
                        if my_score > opp_score:
                            form_str += "W"; result_lines.append(f"  W {my_score}-{opp_score} vs {opponent}")
                        elif my_score < opp_score:
                            form_str += "L"; result_lines.append(f"  L {my_score}-{opp_score} vs {opponent}")
                        else:
                            form_str += "D"; result_lines.append(f"  D {my_score}-{opp_score} vs {opponent}")
                if result_lines:
                    return f"{team_name} Recent Form: {form_str}\n" + "\n".join(result_lines)
    except Exception as e:
        logger.error(f"Team form error: {e}")
    return ""

async def fetch_football_data(topic: str) -> str:
    """
    Fetch real football data:
    - UPCOMING match: team form + head-to-head + key players preview
    - COMPLETED match: result + goalscorers + player ratings
    - Player/team topic: WHY trending + season stats
    """
    if not API_FOOTBALL_KEY:
        return ""

    topic_lower = topic.lower()
    stopwords = {"versus","match","game","news","latest","today","result","score","update",
                 "vs","the","and","for","stats","statistics","preview","prediction"}
    topic_words = [w for w in re.split(r'\W+', topic_lower) if len(w) >= 3 and w not in stopwords]
    is_match_topic = bool(re.search(r'\bvs?\b', topic_lower) or ' v ' in topic_lower)

    try:
        async with httpx.AsyncClient(timeout=12) as client:
            # ── MATCH TOPIC ──
            if is_match_topic:
                results = []
                for params in [{"last": 15}, {"next": 10}]:
                    r = await client.get(
                        "https://v3.football.api-sports.io/fixtures",
                        params={**params, "timezone": "Africa/Lagos"},
                        headers={"x-apisports-key": API_FOOTBALL_KEY}
                    )
                    if r.status_code == 200:
                        results.extend(r.json().get("response", []))

                # Sort by date desc — most recent first
                results.sort(key=lambda x: x["fixture"]["date"], reverse=True)

                relevant = []
                best_fixture_id = None
                upcoming_fixture = None

                for f in results:
                    home = f["teams"]["home"]["name"].lower()
                    away = f["teams"]["away"]["name"].lower()
                    league = f["league"]["name"].lower()
                    home_words = home.split()
                    away_words = away.split()
                    matched = any(
                        any(tw in hw for hw in home_words) or
                        any(tw in aw for aw in away_words) or
                        tw in league
                        for tw in topic_words
                    )

                    if matched:
                        h_score = f["goals"]["home"]
                        a_score = f["goals"]["away"]
                        status = f["fixture"]["status"]["long"]
                        status_short = f["fixture"]["status"]["short"]
                        date = f["fixture"]["date"][:10]
                        fixture_id = f["fixture"]["id"]
                        home_name = f["teams"]["home"]["name"]
                        away_name = f["teams"]["away"]["name"]
                        home_id = f["teams"]["home"]["id"]
                        away_id = f["teams"]["away"]["id"]

                        is_upcoming = status_short in ["NS","TBD","SUSP","PST"]

                        if is_upcoming:
                            match_info = [
                                "UPCOMING MATCH — Write a preview article:",
                                f"Match: {home_name} vs {away_name}",
                                f"Date: {date}",
                                f"Status: {status}",
                                f"League: {f['league']['name']}",
                                f"Round: {f['league'].get('round','')}"
                            ]
                            relevant.append("\n".join(match_info))
                            if not upcoming_fixture:
                                upcoming_fixture = (home_id, away_id, home_name, away_name)
                        else:
                            # Determine winner explicitly
                            if h_score is not None and a_score is not None:
                                if h_score > a_score:
                                    winner_line = f"WINNER: {home_name} won {h_score}-{a_score}"
                                elif a_score > h_score:
                                    winner_line = f"WINNER: {away_name} won {a_score}-{h_score}"
                                else:
                                    winner_line = f"RESULT: Draw {h_score}-{h_score}"
                            else:
                                winner_line = "Score: Not available"

                            match_info = [
                                "COMPLETED MATCH — Write a match report:",
                                f"Home Team: {home_name} | Away Team: {away_name}",
                                f"Home Goals: {h_score} | Away Goals: {a_score}",
                                winner_line,
                                f"Date: {date}",
                                f"League: {f['league']['name']}",
                                f"Round: {f['league'].get('round','')}"
                            ]

                            events = f.get("events", [])
                            goals = [e for e in events if e.get("type") == "Goal"]
                            if goals:
                                goal_lines = [
                                    f"{g.get('player',{}).get('name','?')} ({g.get('team',{}).get('name','?')}) {g.get('time',{}).get('elapsed','?')}'"
                                    for g in goals
                                ]
                                match_info.append("Goalscorers: " + ", ".join(goal_lines))
                            else:
                                match_info.append("Goalscorers: Not available from API — do NOT fabricate names")

                            relevant.append("\n".join(match_info))
                            if h_score is not None and best_fixture_id is None:
                                best_fixture_id = fixture_id

                result_text = ""
                if relevant:
                    result_text = "\n\n".join(relevant[:2])

                    if best_fixture_id:
                        # Post-match player ratings
                        player_stats = await fetch_match_player_stats(best_fixture_id)
                        if player_stats:
                            result_text += f"\n\n{player_stats}"
                    elif upcoming_fixture:
                        # Pre-match: team form + head to head
                        h_id, a_id, h_name, a_name = upcoming_fixture
                        home_form = await fetch_team_form(h_id, h_name)
                        away_form = await fetch_team_form(a_id, a_name)
                        h2h = await fetch_head_to_head(h_id, a_id)
                        for extra in [home_form, away_form, h2h]:
                            if extra:
                                result_text += f"\n\n{extra}"

                    logger.info(f"API-Football match data fetched for: {topic}")
                    return result_text

            # ── PLAYER / TEAM TOPIC ──
            else:
                parts = []

                # Step 1: Find out WHY the player/team is trending using Tavily
                why_trending = await fetch_news_context(topic, "football")
                if why_trending:
                    parts.append(f"WHY THIS IS TRENDING (use this as the main story angle):\n{why_trending}")

                # Step 2: Get their season stats from API-Football for supporting data
                for name in topic_words:
                    if len(name) >= 4:
                        stats = await fetch_player_stats(name)
                        if stats:
                            parts.append(f"PLAYER SEASON STATS (use as supporting data):\n{stats}")
                            break
                # Fallback: try full topic as player name
                if len(parts) < 2:
                    stats = await fetch_player_stats(topic)
                    if stats:
                        parts.append(f"PLAYER SEASON STATS (use as supporting data):\n{stats}")

                if parts:
                    logger.info(f"API-Football player topic data fetched for: {topic}")
                    return "\n\n".join(parts)

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
async def fetch_image(query: str, category: str, article_title: str = "") -> str:
    """
    Fetch image for article.
    Primary: Pollinations AI — generates relevant image from article title (free, no copyright)
    Fallback: Unsplash → Pexels → category default
    """
    # Use article title for Pollinations if available, otherwise use query
    image_prompt = article_title if article_title else query
    # Clean for URL
    clean_prompt = re.sub(r'[^a-zA-Z0-9\s]', '', image_prompt)[:100].strip()
    clean_prompt = clean_prompt.replace(' ', '%20')

    # Primary: Pollinations AI (free, copyright-free, topic-relevant)
    try:
        pollinations_url = f"https://image.pollinations.ai/prompt/{clean_prompt}%20Nigeria%20news%20photo%20realistic?width=900&height=500&nologo=true&seed={random.randint(1,9999)}"
        # Verify it returns an image
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.head(pollinations_url)
            if r.status_code == 200:
                logger.info(f"Pollinations image generated for: {image_prompt[:50]}")
                return pollinations_url
    except Exception as e:
        logger.info(f"Pollinations failed: {e}")

    # Fallback: Unsplash
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
    except Exception as e:
        logger.info(f"Unsplash failed: {e}")

    # Fallback: Pexels
    try:
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
        logger.info(f"Pexels failed: {e}")

    # Final fallback: category default
    fallbacks = {
        "entertainment": "https://images.unsplash.com/photo-1493225457124-a3eb161ffa5f?w=900&q=80",
        "finance": "https://images.unsplash.com/photo-1611974789855-9c2a0a7236a3?w=900&q=80",
        "tech": "https://images.unsplash.com/photo-1574944985070-8f3ebc6b79d2?w=900&q=80",
        "health": "https://images.unsplash.com/photo-1559757148-5c350d0d3c56?w=900&q=80",
        "education": "https://images.unsplash.com/photo-1523050854058-8df90110c9f1?w=900&q=80",
        "news": "https://images.unsplash.com/photo-1508921912186-1d1a45ebb3c1?w=900&q=80",
        "football": "https://images.unsplash.com/photo-1574629810360-7efbbe195018?w=900&q=80",
    }
    return fallbacks.get(category, fallbacks["news"])

# ── SLUG ──
def slugify(text: str) -> str:
    text = text.lower()
    text = re.sub(r'[^a-z0-9\s-]', '', text)
    text = re.sub(r'\s+', '-', text.strip())
    text = re.sub(r'-+', '-', text)
    return text[:80] + '-' + str(int(datetime.now().timestamp()))[-6:]

# ── AI WRITER ──
def is_article_quality_ok(article: dict) -> tuple:
    """
    Basic quality check before sending to Telegram.
    Returns (ok: bool, reason: str)
    """
    title = article.get("title", "")
    excerpt = article.get("excerpt", "")
    body = article.get("body", "")

    # Title too short
    if len(title) < 15:
        return False, f"Title too short: '{title}'"

    # Suspicious phrases indicating AI had no real data and fabricated
    bad_phrases = [
        "not mentioned", "not reported", "no data", "not available",
        "i cannot", "i don't have", "as of my knowledge",
        "i'm not sure", "it is unclear", "cannot confirm",
        "no information", "not confirmed", "unverified"
    ]
    combined = (title + " " + excerpt + " " + body).lower()
    for phrase in bad_phrases:
        if phrase in combined:
            return False, f"AI admitted uncertainty: '{phrase}' found in content"

    # Body too short
    if len(body) < 200:
        return False, "Article body too short"

    return True, "ok"

async def generate_article(topic: str, category: str, real_data: str = "", is_evergreen: bool = False) -> dict:
    cat_context = CAT_PROMPTS.get(category, "Nigerian news")

    if real_data:
        data_section = f"\n\nVERIFIED REAL DATA — USE THIS:\n{real_data}\n\nIMPORTANT: Base your article on this data only. Do not add statistics or facts not in this data."
    else:
        data_section = "\n\nNO REAL-TIME DATA AVAILABLE. Write a helpful, accurate general guide on this topic based on your knowledge. Be clear this is general information, not breaking news."

    extra_instructions = ""
    if category == "football":
        extra_instructions = (
            "\nFOOTBALL RULES:"
            "\n- SCORES RULE: Use ONLY the score from 'AUTHORITATIVE MATCH DATA'. NEVER fabricate a scoreline."
            "\n- WINNER RULE: Data clearly states WINNER — use that exactly, never swap teams"
            "\n- GOALSCORERS RULE: Only name goalscorers if they appear in the data. If data says 'Not available — do NOT fabricate names', do NOT name any scorers"
            "\n- COMPLETED MATCH: Write PAST TENSE match report — scoreline, goalscorers (only if in data), player ratings, key moments, Nigerian angle"
            "\n- UPCOMING MATCH: Write FUTURE TENSE preview — team form (use the W/L/D data provided), head-to-head record, key players to watch, prediction, Nigerian angle"
            "\n- PRE-MATCH article structure: Introduction → Team Form → Head to Head → Key Players → Prediction → Nigerian Angle"
            "\n- POST-MATCH article structure: Introduction with result → Match Report → Player Ratings → Key Moments → Nigerian Angle"
            "\n- TRANSFER RULE: Any unconfirmed transfer must use 'reportedly' or 'according to reports'"
            "\n- Write a descriptive headline — not just team names. Example: 'Nigeria vs Portugal Preview: Super Eagles Form, H2H & Prediction'"
            "\n- For player topics: WHY TRENDING is the main story angle, stats are supporting data"
            "\n- Always include Nigerian angle — Super Eagles, Nigerian players, what it means for Nigerian fans"
            "\n- VENUE RULE: Always use the exact venue from the data — never guess or substitute a different city"
            "\n- OPPONENT RULE: Use the exact team name from data — 'Northern Ireland' and 'Republic of Ireland' are different teams"
            "\n- FUTURE MATCH RULE: The 2026 FIFA World Cup starts June 11, 2026. NEVER write a result for any World Cup match unless data confirms it has been played"
        )
    elif category == "tech":
        extra_instructions = (
            "\nTECH RULES:"
            "\n- PHONE PRICES: Always give price RANGES in NGN, never a single figure"
            "\n- iPhone 18 has NOT been released yet (expected October 2026) — never give an iPhone 18 price"
            "\n- Current iPhone prices in Nigeria (June 2026): iPhone 16e ~₦326,250, iPhone 17 ~₦359,550, iPhone 17 Pro Max ~₦539,550"
            "\n- Never say 'official price' for phones — Apple doesn't publish NGN prices. Say 'current market price'"
            "\n- Tecno, Infinix, Itel prices are much lower (₦80,000–₦300,000 range)"
            "\n- Always mention where to buy: Slot, Jumia, Konga, Computer Village"
        )
    elif category == "finance":
        extra_instructions = (
            "\nFINANCE RULES:"
            "\n- Use ONLY the exact rates in the real data — never guess or round up figures"
            "\n- If no live rate is provided, say 'rates vary — check your exchange platform'"
            "\n- Explain what the rate means for Nigerians practically"
            "\n- Never state a specific naira rate as fact unless it appears in the real data provided"
        )
    elif category == "tech":
        extra_instructions = (
            "\nTECH RULES:"
            "\n- iPhone 18 has NOT been released yet — expected October 2026. Never state an iPhone 18 price as fact"
            "\n- Current iPhone prices in Nigeria (June 2026): iPhone 16e from ₦326,250, iPhone 17 from ₦359,550, iPhone 17 Pro Max from ₦539,550"
            "\n- Never state any phone price below ₦100,000 for a new iPhone — minimum real price is ₦300,000+"
            "\n- Always present phone prices as ranges and note they vary by retailer and exchange rate"
            "\n- For unreleased phones: say 'expected to launch' and 'estimated price' — never state as confirmed"
        )
    elif category == "health":
        extra_instructions = (
            "\nHEALTH RULES:"
            "\n- Always include a disclaimer: 'Consult a doctor before taking any medication'"
            "\n- Drug prices must be stated as approximate and subject to change"
            "\n- Never recommend specific dosages — direct readers to a pharmacist or doctor"
        )

    source_note = "This is an evergreen guide on a topic Nigerians frequently search." if is_evergreen else "This topic is currently trending in Nigeria."

    prompt = f"""You are a senior Nigerian journalist writing for NaijaFlash, Nigeria's most trusted news blog.

Topic: {topic}
Category: {cat_context}
Context: {source_note}{data_section}{extra_instructions}

STRICT RULES — FOLLOW EXACTLY:
1. ONLY use facts, figures, names, and statistics from the VERIFIED REAL DATA above
2. If a fact is NOT in the real data, DO NOT write it — leave it out completely
3. NEVER add song titles, album names, award nominations, film titles, or player names from your own training knowledge unless in the real data
4. NEVER present government promises or pledges as confirmed facts — always say "the government says" or "according to officials"
5. For government/economic articles: always include both positive indicators AND challenges/criticism — never one-sided reporting
6. For health articles: always add disclaimer "Consult a doctor before taking any medication"
7. For finance/tech articles: present prices as RANGES not single figures. Never use "official price" for products without NGN official pricing — say "current market price" instead
8. DATE RULE: Check the Published date on each source in the real data. ALWAYS use the most recent source when facts conflict. NEVER present a 2024 event as a new 2026 development
9. TRANSFER/RUMOUR RULE: Any unconfirmed transfer, signing, departure, or speculation must ALWAYS use "reportedly", "according to reports", or "sources claim"
10. SOURCE NAMING RULE: Use the exact source name from the data — if data says "Times Higher Education ranking", write that, not "NUC ranking"
11. MATCH DATE RULE: If real data shows multiple matches between same teams, use the one with the MOST RECENT date
12. NIGERIA WORLD CUP RULE: Nigeria did NOT qualify for the 2026 FIFA World Cup. NEVER write articles about Nigeria preparing for or participating in the 2026 World Cup
13. SINGLE STORY RULE: Write about ONE story only. Do not combine unrelated stories (e.g. economy + Ebola) into one article. Pick the main story from the data and focus on it
14. Write in clear simple English Nigerians understand — no grammar competition
15. Minimum 400 words, 2-3 subheadings using <h2> tags
16. Nigerian context throughout — naira prices, local brands, Nigerian cities where relevant
17. NEVER write phrases like "as of my knowledge" — if you don't have data, write general guidance

Return ONLY this JSON — no markdown, no explanation, no preamble:
{{
  "title": "Compelling SEO headline, max 80 chars, includes main keyword",
  "excerpt": "2 clear sentences summarising the article for SEO and social sharing",
  "body": "Complete article HTML using only <p> <h2> <h3> <ul> <li> <strong> tags",
  "image_query": "3-word image search query"
}}"""

    # Try Groq first
    if GROQ_API_KEY:
        try:
            client = groq.Groq(api_key=GROQ_API_KEY)
            response = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.4,
                max_tokens=2500
            )
            text = response.choices[0].message.content.strip()
            text = text.replace("```json","").replace("```","").strip()
            # Find JSON in response in case there's extra text
            match = re.search(r'\{.*\}', text, re.DOTALL)
            if match:
                text = match.group(0)
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
                match = re.search(r'\{.*\}', text, re.DOTALL)
                if match:
                    text = match.group(0)
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

# ── SPONSORED ARTICLE SESSION HELPERS ──
def get_sponsored_session(chat_id: int) -> Optional[dict]:
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM sponsored_sessions WHERE chat_id=%s ORDER BY created_at DESC LIMIT 1", (chat_id,))
        row = cur.fetchone()
        cur.close()
        conn.close()
        return dict(row) if row else None
    except:
        return None

def update_sponsored_session(chat_id: int, **kwargs):
    try:
        conn = get_db()
        cur = conn.cursor()
        existing = get_sponsored_session(chat_id)
        if not existing:
            cur.execute("INSERT INTO sponsored_sessions (chat_id) VALUES (%s)", (chat_id,))
            conn.commit()
        sets = ", ".join([f"{k}=%s" for k in kwargs.keys()])
        cur.execute(f"UPDATE sponsored_sessions SET {sets} WHERE chat_id=%s",
                    list(kwargs.values()) + [chat_id])
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        logger.error(f"Session update error: {e}")

def clear_sponsored_session(chat_id: int):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("DELETE FROM sponsored_sessions WHERE chat_id=%s", (chat_id,))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        logger.error(f"Session clear error: {e}")

CATEGORIES_DISPLAY = {
    "entertainment": "🎭 Entertainment",
    "finance": "💰 Finance",
    "tech": "📱 Tech",
    "health": "🏥 Health",
    "education": "📚 Education",
    "news": "📰 News",
    "football": "⚽ Football"
}
# Specific, high-search-volume topics Nigerians look for daily
# These are used when a category has nothing trending in Google RSS
EVERGREEN_TOPICS = {
    "finance": [
        "Dollar to Naira exchange rate today black market",
        "USDT to Naira rate today Binance P2P 2026",
        "Bitcoin price in Naira today June 2026",
        "How to make money online in Nigeria 2026",
        "Best investment apps in Nigeria 2026",
        "CBN official dollar rate today 2026",
        "How to send money abroad from Nigeria cheaply",
        "Best fintech apps Nigeria 2026 Opay Palmpay",
        "Ethereum price naira today 2026",
        "How to convert USDT to Naira in Nigeria",
    ],
    "education": [
        "JAMB portal mop-up registration 2026 how to apply",
        "WAEC 2026 timetable subjects and exam dates",
        "How to check JAMB UTME result 2026 portal",
        "Post UTME screening dates and requirements 2026",
        "Scholarship opportunities for Nigerian students 2026",
        "NYSC 2026 batch mobilization update",
        "How to gain university admission in Nigeria 2026",
        "NECO 2026 examination timetable release",
        "Best universities in Nigeria 2026 NUC ranking",
        "Remote jobs for Nigerian graduates 2026",
    ],
    "health": [
        "Malaria symptoms causes and treatment in Nigeria",
        "Typhoid fever symptoms treatment and drugs Nigeria",
        "Best medication for stomach ulcer in Nigeria prices",
        "High blood pressure causes symptoms treatment Nigeria",
        "Signs and symptoms of diabetes in Nigerians",
        "Best hospitals in Lagos Nigeria and their contacts",
        "How to boost immune system naturally Nigeria",
        "Causes of frequent headache and dizziness treatment",
        "How to lose belly fat fast Nigeria diet tips",
        "Symptoms of kidney disease and treatment Nigeria",
    ],
    "entertainment": [
        "Davido latest news music and shows 2026",
        "Burna Boy latest album tour and news 2026",
        "Best Nollywood movies to watch on Netflix 2026",
        "Wizkid new song album and tour news 2026",
        "BBNaija Season 9 housemates and updates 2026",
        "Top Nigerian Afrobeats songs June 2026",
        "Asake latest music video and concert 2026",
        "Rema latest song news and tour 2026",
        "Nigerian movies winning international awards 2026",
        "Adekunle Gold latest music and news 2026",
    ],
    "tech": [
        "Best Android smartphones under 200000 naira 2026",
        "iPhone 16 official price in Nigeria 2026",
        "Tecno Camon 40 full specs and price Nigeria",
        "Best MTN Airtel Glo data plan Nigeria June 2026",
        "Samsung Galaxy S24 price Nigeria 2026",
        "How to make money with your smartphone Nigeria",
        "Best laptops under 400000 naira Nigeria 2026",
        "Infinix Hot 50 price specs and review Nigeria",
        "Best free VPN for Nigeria that actually works 2026",
        "How to start an online business in Nigeria 2026",
    ],
    "news": [
        "Nigeria fuel petrol price per liter today 2026",
        "Tinubu government economic policy latest news",
        "Nigeria inflation rate and cost of living 2026",
        "NERC electricity tariff increase Nigeria 2026",
        "Nigeria insecurity news bandits attack latest",
        "EFCC latest arrest corruption news Nigeria",
        "Nigeria minimum wage implementation update 2026",
        "Lagos state government infrastructure project 2026",
        "Nigeria immigration new passport policy 2026",
        "CBN new banking policy Nigerians must know",
    ],
    "football": [
        "Super Eagles 2026 FIFA World Cup squad players",
        "Victor Osimhen latest goals news and transfer",
        "Premier League top scorer golden boot race 2026",
        "Champions League final result and highlights",
        "Nigeria World Cup 2026 fixtures and schedule",
        "Ademola Lookman Atalanta latest goals news",
        "AFCON 2025 Nigeria qualification latest update",
        "Premier League results and table standings 2026",
        "Real Madrid vs Barcelona El Clasico latest",
        "Nigerian players in Europe latest performance news",
    ],
}

def get_fallback_topic(category: str) -> Optional[dict]:
    """Get an unused evergreen topic for a category."""
    topics = EVERGREEN_TOPICS.get(category, [])
    if not topics:
        return None
    # Shuffle to avoid always picking same topic
    random.shuffle(topics)
    for topic in topics:
        if not is_topic_used(topic):
            return {"topic": topic, "category": category, "source": "evergreen"}
    # All used — reset evergreen topics for this category
    try:
        conn = get_db()
        cur = conn.cursor()
        for topic in topics:
            cur.execute("DELETE FROM used_topics WHERE topic=%s", (topic,))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        logger.error(f"Evergreen reset error: {e}")
    return {"topic": random.choice(topics), "category": category, "source": "evergreen"}

# ── MAIN PIPELINE ──
async def run_pipeline(force_category: str = None):
    """
    Pipeline with optional category filter.
    Falls back to evergreen topics when nothing is trending for a category.
    """
    label = f"[{force_category.upper()}]" if force_category else "[ALL CATEGORIES]"
    logger.info(f"Pipeline {label} started")
    generated = 0

    try:
        trends = await fetch_nigeria_trends()

        # Filter by forced category if specified
        if force_category:
            cat_trends = [t for t in trends if t["category"] == force_category]
            new_cat_trends = [t for t in cat_trends if not is_topic_used(t["topic"])]

            if new_cat_trends:
                selected = new_cat_trends[:2]  # Max 2 per category per run
                logger.info(f"Using {len(selected)} trending topics for {force_category}")
            else:
                fallback = get_fallback_topic(force_category)
                if not fallback:
                    await send_to_all_admins(f"⚠️ No topics available for <b>{force_category.upper()}</b>.")
                    return
                selected = [fallback]
                logger.info(f"Using evergreen fallback for {force_category}: {fallback['topic']}")
                await send_to_all_admins(
                    f"ℹ️ No trending topics for <b>{force_category.upper()}</b> right now.\n"
                    f"Using evergreen topic: <i>{fallback['topic']}</i>"
                )
        else:
            # All categories — pick max 1 topic per category, diverse coverage
            new_trends = [t for t in trends if not is_topic_used(t["topic"])]

            if new_trends:
                # Pick best topic per category (max 1 each, max 5 total)
                seen_cats = {}
                selected = []
                for t in new_trends:
                    cat = t["category"]
                    if cat not in seen_cats:
                        seen_cats[cat] = 0
                    if seen_cats[cat] < 1 and len(selected) < 5:
                        selected.append(t)
                        seen_cats[cat] += 1
            else:
                cat = random.choice(CATEGORIES)
                fallback = get_fallback_topic(cat)
                if not fallback:
                    await send_to_all_admins("ℹ️ All topics already covered. Try again later.")
                    return
                selected = [fallback]
                await send_to_all_admins(
                    f"ℹ️ No new trending topics right now.\n"
                    f"Using evergreen topic: <i>{fallback['topic']}</i>"
                )

            # Send ONE combined status message for all categories
            if selected:
                cats_found = list({t["category"] for t in selected})
                cat_labels = " · ".join([c.upper() for c in cats_found])
                await send_to_all_admins(
                    f"⚙️ Generating {len(selected)} articles across: {cat_labels}"
                )

        # Process selected topics
        for trend in selected:
            topic = trend["topic"]
            category = trend["category"]
            is_evergreen = trend.get("source") == "evergreen"
            # Context from Tavily discovery — pre-fetched news snippet
            tavily_context = trend.get("context", "")

            try:
                logger.info(f"Processing [{category}]{'[evergreen]' if is_evergreen else '[trending]'}: {topic[:60]}")
                real_data = ""

                if category == "football":
                    football_data = await fetch_football_data(topic)
                    is_match = bool(re.search(r'\bvs?\b', topic.lower()) or ' v ' in topic.lower())
                    if is_match:
                        news_data = await fetch_news_context(topic, category)
                        if football_data:
                            real_data = f"AUTHORITATIVE MATCH DATA (use this for scores/results):\n{football_data}"
                            if news_data:
                                real_data += f"\n\nNEWS CONTEXT (for quotes/background only — never use for scores):\n{news_data}"
                        else:
                            real_data = news_data
                    else:
                        real_data = football_data
                elif category == "finance":
                    finance_data = await fetch_finance_data(topic)
                    news_data = await fetch_news_context(topic, category)
                    real_data = "\n\n".join(filter(None, [finance_data, news_data]))
                else:
                    real_data = await fetch_news_context(topic, category)

                # Add Tavily discovery context if we have it and it's not already in real_data
                if tavily_context and tavily_context not in real_data:
                    real_data = f"DISCOVERY CONTEXT:\n{tavily_context}\n\n{real_data}".strip()

                article_data = await generate_article(topic, category, real_data, is_evergreen)

                # Quality check before saving
                ok, reason = is_article_quality_ok(article_data)
                if not ok:
                    logger.warning(f"Article quality failed for '{topic}': {reason}")
                    await send_to_all_admins(f"⚠️ Article for <b>{topic}</b> failed quality check: {reason}\nSkipping — try again later.")
                    continue

                image_url = await fetch_image(article_data.get("image_query", topic), category, article_data.get("title",""))

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
                await send_article_for_approval(
                    article_id, article_data["title"],
                    article_data.get("excerpt",""), category, topic
                )
                generated += 1
                await asyncio.sleep(2)

            except Exception as e:
                logger.error(f"Error on '{topic}': {e}")
                continue

        if generated == 0:
            await send_to_all_admins("⚠️ Pipeline ran but failed to generate articles. Check Railway logs.")
        else:
            logger.info(f"Pipeline {label} done. {generated} articles generated.")

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
    # Increment views counter and log to article_views for stats tracking
    cur.execute("UPDATE articles SET views=views+1 WHERE slug=%s", (slug,))
    cur.execute("INSERT INTO article_views (article_id) VALUES (%s)", (article["id"],))
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
    # Total published
    cur.execute("SELECT COUNT(*) as t FROM articles WHERE status='published'")
    total = cur.fetchone()["t"]
    cur.execute("SELECT COUNT(*) as p FROM articles WHERE status='pending'")
    pending = cur.fetchone()["p"]
    # Daily stats
    cur.execute("SELECT COUNT(*) as c FROM article_views WHERE viewed_at >= NOW() - INTERVAL '1 day'")
    views_today = cur.fetchone()["c"]
    cur.execute("SELECT COUNT(*) as c FROM articles WHERE status='published' AND published_at >= NOW() - INTERVAL '1 day'")
    published_today = cur.fetchone()["c"]
    # Weekly stats
    cur.execute("SELECT COUNT(*) as c FROM article_views WHERE viewed_at >= NOW() - INTERVAL '7 days'")
    views_week = cur.fetchone()["c"]
    cur.execute("SELECT COUNT(*) as c FROM articles WHERE status='published' AND published_at >= NOW() - INTERVAL '7 days'")
    published_week = cur.fetchone()["c"]
    # Monthly stats
    cur.execute("SELECT COUNT(*) as c FROM article_views WHERE viewed_at >= NOW() - INTERVAL '30 days'")
    views_month = cur.fetchone()["c"]
    cur.execute("SELECT COUNT(*) as c FROM articles WHERE status='published' AND published_at >= NOW() - INTERVAL '30 days'")
    published_month = cur.fetchone()["c"]
    # By category
    cur.execute("SELECT category, COUNT(*) as count FROM articles WHERE status='published' GROUP BY category ORDER BY count DESC")
    by_cat = cur.fetchall()
    # Top articles this month
    cur.execute("""
        SELECT a.id, a.title, a.category, COUNT(v.id) as month_views
        FROM articles a LEFT JOIN article_views v ON a.id=v.article_id
        AND v.viewed_at >= NOW() - INTERVAL '30 days'
        WHERE a.status='published'
        GROUP BY a.id, a.title, a.category
        ORDER BY month_views DESC LIMIT 5
    """)
    top_articles = cur.fetchall()
    cur.close()
    conn.close()
    return {
        "total_published": total,
        "pending_approval": pending,
        "daily": {"views": views_today, "published": published_today},
        "weekly": {"views": views_week, "published": published_week},
        "monthly": {"views": views_month, "published": published_month},
        "by_category": [dict(r) for r in by_cat],
        "top_articles_this_month": [dict(r) for r in top_articles]
    }

@app.post("/api/pipeline/run")
async def trigger_pipeline(background_tasks: BackgroundTasks):
    background_tasks.add_task(run_pipeline)
    return {"status": "Pipeline started"}

@app.get("/api/ads/slot1")
async def get_ad_slot1():
    """Get the active ad for slot 1 — used by the frontend."""
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM ad_slots WHERE slot_key='slot1' AND is_active=TRUE")
        ad = cur.fetchone()
        cur.close()
        conn.close()
        if not ad:
            return {"active": False}
        return {
            "active": True,
            "headline": ad["headline"],
            "subtext": ad["subtext"],
            "link": ad["link"],
            "button_label": ad["button_label"],
        }
    except Exception as e:
        logger.error(f"Ad fetch error: {e}")
        return {"active": False}

@app.delete("/api/articles/{article_id}")
async def delete_article(article_id: int):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM articles WHERE id=%s RETURNING id, title", (article_id,))
    row = cur.fetchone()
    conn.commit()
    cur.close()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Article not found")
    return {"success": True, "deleted": dict(row)}

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

            elif cb_data.startswith("pub_sponsored_"):
                session_chat_id = int(cb_data.split("_")[2])
                s = get_sponsored_session(session_chat_id)
                if not s:
                    await send_telegram(cb["from"]["id"], "❌ Session expired. Start again with /sponsored.")
                else:
                    try:
                        category = s.get("category", "news")
                        title = s.get("title", "Sponsored Article")
                        body_text = s.get("body", "")
                        link = s.get("link", "")
                        image_url = s.get("image_url", "")
                        expires_days = s.get("expires_days")

                        # Add sponsored badge and link to body
                        full_body = f'<p><strong>⚠️ Sponsored Content</strong></p>{body_text}'
                        if link:
                            full_body += f'<p><a href="{link}" target="_blank">👉 Learn More / Visit Website</a></p>'

                        # Use category fallback image if no image provided
                        fallbacks = {
                            "entertainment": "https://images.unsplash.com/photo-1493225457124-a3eb161ffa5f?w=900&q=80",
                            "finance": "https://images.unsplash.com/photo-1611974789855-9c2a0a7236a3?w=900&q=80",
                            "tech": "https://images.unsplash.com/photo-1574944985070-8f3ebc6b79d2?w=900&q=80",
                            "health": "https://images.unsplash.com/photo-1559757148-5c350d0d3c56?w=900&q=80",
                            "education": "https://images.unsplash.com/photo-1523050854058-8df90110c9f1?w=900&q=80",
                            "news": "https://images.unsplash.com/photo-1508921912186-1d1a45ebb3c1?w=900&q=80",
                            "football": "https://images.unsplash.com/photo-1574629810360-7efbbe195018?w=900&q=80",
                        }
                        if not image_url:
                            image_url = fallbacks.get(category, fallbacks["news"])

                        slug = slugify(title)
                        excerpt = f"[SPONSORED] {body_text[:150]}..."
                        published_at = "NOW()"
                        expires_at = f"NOW() + INTERVAL '{expires_days} days'" if expires_days else "NULL"

                        conn = get_db()
                        cur = conn.cursor()
                        # Add expires_at column if not exists
                        try:
                            cur.execute("ALTER TABLE articles ADD COLUMN IF NOT EXISTS expires_at TIMESTAMP")
                            conn.commit()
                        except:
                            conn.rollback()

                        cur.execute(f"""
                            INSERT INTO articles (slug, title, category, excerpt, body, image_url, status, published_at, expires_at)
                            VALUES (%s, %s, %s, %s, %s, %s, 'published', NOW(), {expires_at})
                            RETURNING id
                        """, (slug, title, category, excerpt, full_body, image_url))
                        article_id = cur.fetchone()["id"]
                        conn.commit()
                        cur.close()
                        conn.close()

                        clear_sponsored_session(session_chat_id)
                        await send_to_all_admins(
                            f"✅ <b>Sponsored Article Published</b>\n\n"
                            f"ID: #{article_id}\n"
                            f"Title: {title}\n"
                            f"Category: {category.upper()}\n"
                            f"Expiry: {'In ' + str(expires_days) + ' days' if expires_days else 'No expiry'}"
                        )
                    except Exception as e:
                        await send_telegram(cb["from"]["id"], f"❌ Error publishing: {e}")

            elif cb_data.startswith("cancel_sponsored_"):
                session_chat_id = int(cb_data.split("_")[2])
                clear_sponsored_session(session_chat_id)
                await send_telegram(cb["from"]["id"], "✅ Sponsored article cancelled.")
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
                    "/stats — Blog statistics (daily/weekly/monthly)\n"
                    "/trends — See what's trending in Nigeria now\n"
                    "/generate — Generate articles (add category name for specific e.g. /generate football)\n\n"
                    "<b>Category Shortcuts:</b>\n"
                    "/football ⚽ /finance 💰 /entertainment 🎭\n"
                    "/tech 📱 /health 🏥 /education 📚 /news 📰\n\n"
                    "/pending — View pending articles\n"
                    "/delete [id] — Delete an article by ID\n\n"
                    "<b>Sponsored Articles:</b>\n"
                    "/sponsored — Create a sponsored article\n"
                    "/listsponsored — View all sponsored articles\n"
                    "/deletesponsored [id] — Remove a sponsored article\n\n"
                    "<b>Ad Management:</b>\n"
                    "/viewad — See current ad slot\n"
                    "/setad Headline | Subtext | Link | Button — Update ad\n"
                    "/clearad — Remove current ad\n\n"
                    "/help — Show this message"
                )
                if is_owner(user_id):
                    cmd += "\n\n👑 <b>Owner Commands</b>\n/addadmin [chat_id]\n/removeadmin [chat_id]\n/listadmins\n/reset — Clear used topics & start fresh"
                await send_telegram(chat_id, cmd)

            elif text == "/trends":
                if not is_admin(user_id):
                    await send_telegram(chat_id, "⛔ Admins only.")
                    return {"ok": True}
                await send_telegram(chat_id, "🔍 Fetching Nigeria trends across all categories...")
                trends = await fetch_nigeria_trends()
                if not trends:
                    await send_telegram(chat_id, "No relevant trends found right now.")
                else:
                    # Group by category
                    by_cat = {}
                    for t in trends:
                        cat = t["category"]
                        if cat not in by_cat:
                            by_cat[cat] = []
                        by_cat[cat].append(t["topic"])

                    lines = []
                    for cat, topics in sorted(by_cat.items()):
                        lines.append(f"\n<b>{cat.upper()}</b>")
                        for topic in topics[:3]:
                            lines.append(f"  • {topic[:60]}")

                    await send_telegram(chat_id,
                        f"🇳🇬 <b>Trending in Nigeria Now</b>\n"
                        f"({len(trends)} topics across {len(by_cat)} categories)"
                        + "\n".join(lines)
                    )

            elif text == "/stats":
                if not is_admin(user_id):
                    await send_telegram(chat_id, "⛔ Admins only.")
                    return {"ok": True}
                conn = get_db()
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) as t FROM articles WHERE status='published'")
                total = cur.fetchone()["t"]
                cur.execute("SELECT COUNT(*) as p FROM articles WHERE status='pending'")
                pend = cur.fetchone()["p"]
                # Daily
                cur.execute("SELECT COUNT(*) as c FROM article_views WHERE viewed_at >= NOW() - INTERVAL '1 day'")
                views_today = cur.fetchone()["c"]
                cur.execute("SELECT COUNT(*) as c FROM articles WHERE status='published' AND published_at >= NOW() - INTERVAL '1 day'")
                pub_today = cur.fetchone()["c"]
                # Weekly
                cur.execute("SELECT COUNT(*) as c FROM article_views WHERE viewed_at >= NOW() - INTERVAL '7 days'")
                views_week = cur.fetchone()["c"]
                cur.execute("SELECT COUNT(*) as c FROM articles WHERE status='published' AND published_at >= NOW() - INTERVAL '7 days'")
                pub_week = cur.fetchone()["c"]
                # Monthly
                cur.execute("SELECT COUNT(*) as c FROM article_views WHERE viewed_at >= NOW() - INTERVAL '30 days'")
                views_month = cur.fetchone()["c"]
                cur.execute("SELECT COUNT(*) as c FROM articles WHERE status='published' AND published_at >= NOW() - INTERVAL '30 days'")
                pub_month = cur.fetchone()["c"]
                # By category
                cur.execute("SELECT category, COUNT(*) as c FROM articles WHERE status='published' GROUP BY category ORDER BY c DESC")
                cats = cur.fetchall()
                # Top article this month
                cur.execute("""
                    SELECT a.title, COUNT(v.id) as mv FROM articles a
                    LEFT JOIN article_views v ON a.id=v.article_id
                    AND v.viewed_at >= NOW() - INTERVAL '30 days'
                    WHERE a.status='published'
                    GROUP BY a.id, a.title ORDER BY mv DESC LIMIT 1
                """)
                top = cur.fetchone()
                cur.close()
                conn.close()
                cat_lines = "\n".join([f"  • {r['category']}: {r['c']}" for r in cats]) or "  None yet"
                top_line = f"\n\n🏆 <b>Top Article This Month:</b>\n{top['title'][:60]}... ({top['mv']} views)" if top and top['mv'] > 0 else ""
                await send_telegram(chat_id,
                    f"📊 <b>NaijaFlash Stats</b>\n\n"
                    f"📰 Total Published: {total}\n"
                    f"⏳ Pending Approval: {pend}\n\n"
                    f"<b>Today</b>\n"
                    f"  👁 Views: {views_today}\n"
                    f"  📝 Published: {pub_today}\n\n"
                    f"<b>This Week</b>\n"
                    f"  👁 Views: {views_week}\n"
                    f"  📝 Published: {pub_week}\n\n"
                    f"<b>This Month</b>\n"
                    f"  👁 Views: {views_month}\n"
                    f"  📝 Published: {pub_month}\n\n"
                    f"<b>By Category:</b>\n{cat_lines}"
                    f"{top_line}"
                )

            elif text == "/generate" or text.startswith("/generate "):
                if not is_admin(user_id):
                    await send_telegram(chat_id, "⛔ Admins only.")
                    return {"ok": True}
                parts = text.split()
                force_cat = parts[1].lower() if len(parts) > 1 else None
                if force_cat and force_cat not in CATEGORIES:
                    await send_telegram(chat_id,
                        f"⛔ Unknown category: <b>{force_cat}</b>\n\n"
                        f"Valid categories:\n" + "\n".join([f"• {c}" for c in CATEGORIES])
                    )
                    return {"ok": True}
                if force_cat:
                    await send_telegram(chat_id, f"⚙️ Generating <b>{force_cat.upper()}</b> articles...")
                else:
                    await send_telegram(chat_id, "⚙️ Fetching Nigeria trends across all categories...")
                asyncio.create_task(run_pipeline(force_cat))

            # Category shortcut commands — /football, /finance, etc.
            elif text in [f"/{c}" for c in CATEGORIES]:
                if not is_admin(user_id):
                    await send_telegram(chat_id, "⛔ Admins only.")
                    return {"ok": True}
                cat = text[1:]
                await send_telegram(chat_id, f"⚙️ Generating <b>{cat.upper()}</b> articles...")
                asyncio.create_task(run_pipeline(cat))

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

            elif text.startswith("/delete"):
                if not is_admin(user_id):
                    await send_telegram(chat_id, "⛔ Admins only.")
                    return {"ok": True}
                parts = text.split()
                if len(parts) < 2:
                    await send_telegram(chat_id, "Usage: /delete [article_id]")
                    return {"ok": True}
                try:
                    article_id = int(parts[1])
                    conn = get_db()
                    cur = conn.cursor()
                    cur.execute("SELECT title, status FROM articles WHERE id=%s", (article_id,))
                    row = cur.fetchone()
                    if not row:
                        await send_telegram(chat_id, f"❌ Article #{article_id} not found.")
                        cur.close()
                        conn.close()
                        return {"ok": True}
                    cur.execute("DELETE FROM articles WHERE id=%s", (article_id,))
                    conn.commit()
                    cur.close()
                    conn.close()
                    await send_to_all_admins(
                        f"🗑️ <b>Article Deleted</b>\n\n"
                        f"ID: #{article_id}\n"
                        f"Title: {row['title']}\n"
                        f"Deleted by: {user_id}"
                    )
                except ValueError:
                    await send_telegram(chat_id, "❌ Invalid article ID. Usage: /delete [number]")
                except Exception as e:
                    await send_telegram(chat_id, f"❌ Error: {e}")

            elif text.startswith("/setad"):
                if not is_admin(user_id):
                    await send_telegram(chat_id, "⛔ Admins only.")
                    return {"ok": True}
                # Format: /setad Headline | Subtext | Link | Button Label
                content = text[len("/setad"):].strip()
                if "|" not in content:
                    await send_telegram(chat_id,
                        "Usage: /setad Headline | Subtext | Link | Button Label\n\n"
                        "Example:\n/setad Get 60% Off Phones | Best deals on Jumia | https://jumia.com.ng | Shop Now →"
                    )
                    return {"ok": True}
                try:
                    parts_ad = [p.strip() for p in content.split("|")]
                    headline = parts_ad[0] if len(parts_ad) > 0 else ""
                    subtext = parts_ad[1] if len(parts_ad) > 1 else ""
                    link = parts_ad[2] if len(parts_ad) > 2 else ""
                    button_label = parts_ad[3] if len(parts_ad) > 3 else "Learn More"
                    conn = get_db()
                    cur = conn.cursor()
                    cur.execute("""
                        UPDATE ad_slots SET headline=%s, subtext=%s, link=%s, button_label=%s,
                        is_active=TRUE, updated_at=NOW() WHERE slot_key='slot1'
                    """, (headline, subtext, link, button_label))
                    conn.commit()
                    cur.close()
                    conn.close()
                    await send_telegram(chat_id,
                        f"✅ <b>Ad Slot 1 Updated</b>\n\n"
                        f"<b>Headline:</b> {headline}\n"
                        f"<b>Subtext:</b> {subtext}\n"
                        f"<b>Link:</b> {link}\n"
                        f"<b>Button:</b> {button_label}"
                    )
                except Exception as e:
                    await send_telegram(chat_id, f"❌ Error updating ad: {e}")

            elif text == "/clearad":
                if not is_admin(user_id):
                    await send_telegram(chat_id, "⛔ Admins only.")
                    return {"ok": True}
                try:
                    conn = get_db()
                    cur = conn.cursor()
                    cur.execute("UPDATE ad_slots SET is_active=FALSE, updated_at=NOW() WHERE slot_key='slot1'")
                    conn.commit()
                    cur.close()
                    conn.close()
                    await send_telegram(chat_id, "✅ Ad Slot 1 cleared. No ad will show until you set a new one.")
                except Exception as e:
                    await send_telegram(chat_id, f"❌ Error: {e}")

            elif text == "/viewad":
                if not is_admin(user_id):
                    await send_telegram(chat_id, "⛔ Admins only.")
                    return {"ok": True}
                try:
                    conn = get_db()
                    cur = conn.cursor()
                    cur.execute("SELECT * FROM ad_slots WHERE slot_key='slot1'")
                    ad = cur.fetchone()
                    cur.close()
                    conn.close()
                    if not ad:
                        await send_telegram(chat_id, "No ad configured.")
                    elif not ad["is_active"]:
                        await send_telegram(chat_id, "Ad Slot 1 is currently <b>inactive</b>.\nUse /setad to activate.")
                    else:
                        await send_telegram(chat_id,
                            f"📢 <b>Current Ad Slot 1</b>\n\n"
                            f"<b>Headline:</b> {ad['headline']}\n"
                            f"<b>Subtext:</b> {ad['subtext']}\n"
                            f"<b>Link:</b> {ad['link']}\n"
                            f"<b>Button:</b> {ad['button_label']}\n"
                            f"<b>Status:</b> ✅ Active\n"
                            f"<b>Updated:</b> {str(ad['updated_at'])[:16]}"
                        )
                except Exception as e:
                    await send_telegram(chat_id, f"❌ Error: {e}")

            elif text == "/sponsored":
                if not is_admin(user_id):
                    await send_telegram(chat_id, "⛔ Admins only.")
                    return {"ok": True}
                clear_sponsored_session(chat_id)
                update_sponsored_session(chat_id, step="title")
                await send_telegram(chat_id,
                    "📢 <b>Create Sponsored Article</b>\n\n"
                    "Step 1/6 — Send the <b>article title</b>:\n\n"
                    "Example: <i>Jumia Black Friday — Up To 70% Off Phones</i>\n\n"
                    "Send /cancel to stop."
                )

            elif text == "/listsponsored":
                if not is_admin(user_id):
                    await send_telegram(chat_id, "⛔ Admins only.")
                    return {"ok": True}
                conn = get_db()
                cur = conn.cursor()
                cur.execute("""
                    SELECT id, title, category, published_at
                    FROM articles WHERE status='published' AND excerpt LIKE '%[SPONSORED]%'
                    ORDER BY published_at DESC LIMIT 10
                """)
                rows = cur.fetchall()
                cur.close()
                conn.close()
                if not rows:
                    await send_telegram(chat_id, "No sponsored articles published yet.")
                else:
                    lines = "\n".join([f"#{r['id']} [{r['category'].upper()}] {r['title'][:45]}..." for r in rows])
                    await send_telegram(chat_id, f"📢 <b>Sponsored Articles</b>\n\n{lines}\n\nUse /deletesponsored [id] to remove.")

            elif text.startswith("/deletesponsored"):
                if not is_admin(user_id):
                    await send_telegram(chat_id, "⛔ Admins only.")
                    return {"ok": True}
                parts = text.split()
                if len(parts) < 2:
                    await send_telegram(chat_id, "Usage: /deletesponsored [article_id]")
                    return {"ok": True}
                try:
                    article_id = int(parts[1])
                    conn = get_db()
                    cur = conn.cursor()
                    cur.execute("SELECT title FROM articles WHERE id=%s", (article_id,))
                    row = cur.fetchone()
                    if not row:
                        await send_telegram(chat_id, f"❌ Article #{article_id} not found.")
                        cur.close()
                        conn.close()
                        return {"ok": True}
                    cur.execute("DELETE FROM articles WHERE id=%s", (article_id,))
                    conn.commit()
                    cur.close()
                    conn.close()
                    await send_to_all_admins(f"🗑️ Sponsored article #{article_id} deleted: {row['title'][:50]}")
                except ValueError:
                    await send_telegram(chat_id, "❌ Invalid ID. Usage: /deletesponsored [number]")
                except Exception as e:
                    await send_telegram(chat_id, f"❌ Error: {e}")

            elif text == "/cancel":
                session = get_sponsored_session(chat_id)
                if session:
                    clear_sponsored_session(chat_id)
                    await send_telegram(chat_id, "✅ Cancelled.")
                else:
                    await send_telegram(chat_id, "Nothing to cancel.")

            # ── SPONSORED MULTI-STEP FLOW ──
            else:
                session = get_sponsored_session(chat_id)
                if session and is_admin(user_id):
                    step = session.get("step")

                    if step == "title":
                        update_sponsored_session(chat_id, title=text, step="category")
                        cat_lines = "\n".join([f"{i+1}. {v}" for i,(k,v) in enumerate(CATEGORIES_DISPLAY.items())])
                        await send_telegram(chat_id,
                            f"✅ Title saved.\n\n"
                            f"Step 2/6 — Choose a <b>category</b>:\n\n{cat_lines}\n\nReply with the number (1-7)."
                        )

                    elif step == "category":
                        cat_keys = list(CATEGORIES_DISPLAY.keys())
                        try:
                            idx = int(text.strip()) - 1
                            if 0 <= idx < len(cat_keys):
                                chosen_cat = cat_keys[idx]
                                update_sponsored_session(chat_id, category=chosen_cat, step="body")
                                await send_telegram(chat_id,
                                    f"✅ Category: <b>{CATEGORIES_DISPLAY[chosen_cat]}</b>\n\n"
                                    f"Step 3/6 — Send the <b>article body</b> (full content):\n\nMinimum 100 words recommended."
                                )
                            else:
                                await send_telegram(chat_id, "❌ Invalid. Reply with 1-7.")
                        except ValueError:
                            await send_telegram(chat_id, "❌ Reply with a number 1-7.")

                    elif step == "body":
                        update_sponsored_session(chat_id, body=text, step="link")
                        await send_telegram(chat_id,
                            f"✅ Body saved.\n\n"
                            f"Step 4/6 — Send the <b>brand link</b> (URL):\n\n"
                            f"Example: https://jumia.com.ng\n\nSend /skip to leave blank."
                        )

                    elif step == "link":
                        link = "" if text == "/skip" else text.strip()
                        update_sponsored_session(chat_id, link=link, step="image")
                        await send_telegram(chat_id,
                            f"✅ Link saved.\n\n"
                            f"Step 5/6 — Send the <b>image URL</b>:\n\n"
                            f"📱 <b>Have your own image? Upload it free at:</b>\n"
                            f"• <b>imgbb.com</b> — best option, no account needed, instant link\n"
                            f"• postimages.org — simple and free\n"
                            f"• imgur.com — popular and reliable\n\n"
                            f"Upload image → copy <b>Direct link</b> → paste here.\n\n"
                            f"Or send /skip to use a default category image."
                        )

                    elif step == "image":
                        image_url = "" if text == "/skip" else text.strip()
                        update_sponsored_session(chat_id, image_url=image_url, step="expiry")
                        await send_telegram(chat_id,
                            f"✅ Image saved.\n\n"
                            f"Step 6/6 — How many <b>days</b> should this stay live?\n\n"
                            f"Reply with a number e.g. <b>30</b>\n"
                            f"Or send /skip for no expiry (stays permanently)."
                        )

                    elif step == "expiry":
                        expires_days = None
                        if text != "/skip":
                            try:
                                expires_days = int(text.strip())
                            except ValueError:
                                await send_telegram(chat_id, "❌ Reply with a number or /skip.")
                                return {"ok": True}
                        update_sponsored_session(chat_id, expires_days=expires_days, step="preview")
                        s = get_sponsored_session(chat_id)
                        expiry_text = f"Expires in {expires_days} days" if expires_days else "No expiry"
                        preview = (
                            f"👁 <b>PREVIEW</b>\n\n"
                            f"<b>Title:</b> {s.get('title','')}\n"
                            f"<b>Category:</b> {CATEGORIES_DISPLAY.get(s.get('category',''),'')}\n"
                            f"<b>Link:</b> {s.get('link') or 'None'}\n"
                            f"<b>Image:</b> {'Custom' if s.get('image_url') else 'Default'}\n"
                            f"<b>Expiry:</b> {expiry_text}\n\n"
                            f"<b>Body preview:</b>\n{str(s.get('body',''))[:300]}...\n\n"
                            f"Publish this article?"
                        )
                        markup = {"inline_keyboard": [[
                            {"text": "✅ Publish Now", "callback_data": f"pub_sponsored_{chat_id}"},
                            {"text": "❌ Cancel", "callback_data": f"cancel_sponsored_{chat_id}"}
                        ]]}
                        await send_telegram(chat_id, preview, reply_markup=markup)

    except Exception as e:
        logger.error(f"Webhook error: {e}")

    return {"ok": True}
