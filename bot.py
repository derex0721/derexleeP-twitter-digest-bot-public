#!/usr/bin/env python3
"""
Twitter Digest Bot
每 2 小時抓取追蹤帳號的最新推文 + 市場數據 + 指定網站，用 AI 濃縮，推送到 Discord + Telegram。
"""

import feedparser
import requests
import time
import os
import re
from datetime import datetime, timezone, timedelta

# ── 設定（憑證請設定在 GitHub Secrets）────────────────────

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"

ACCOUNTS = [
    "TheLongInvest", "jimmyhuli", "LinQingV", "TimmerFidelity",
    "Jason23818126", "cnfinancewatch", "LazaroInvestor", "tradergokux",
    "cantonmeow", "thestockwhale", "Mr_Derivatives", "EchoAnalysis",
    "MMatters22596", "thethirdanalyst", "burrytracker", "Jason60704294",
    "BangXBT", "zip_ck", "xixx_1120", "Dentoshi",
    "EtherRawl", "Xmen__charts", "LSTraderCrypto", "Pure8Nature",
    "Murphychen888", "Trader_XO", "y_cryptoanalyst", "mrblock",
    "derrrrrrrq", "lianyanshe", "KuiGas", "riyuexiaochu",
    "TingHu888", "bocaibocai_", "VincentLogic", "kiki520_eth",
    "supraEVM", "Defi_Scribbler", "AirdropAlchemis", "0xEvieYang",
    "lanhubiji", "bclaobai", "ustyianskyi", "bashorunedward",
    "hongzho93205203", "quang250802", "0x_xifeng", "CryptoLakhan",
    "weingfo", "zaijin338191", "jiamigou", "0xlemoneth",
    "22333D", "cryptosquare_cn", "MrMikeInvesting", "follow_clues",
]

NITTER_INSTANCES = [
    "https://nitter.privacydev.net",
    "https://nitter.poast.org",
    "https://nitter.net",
    "https://nitter.1d4.us",
    "https://nitter.kavin.rocks",
    "https://nitter.unixfox.eu",
]

EXTRA_WEBSITES = [
    "https://connectfarm1.com/",
]

# ── 市場數據 ──────────────────────────────────────────────

def fetch_market_data():
    data = {}

    try:
        r = requests.get("https://api.alternative.me/fng/?limit=1", timeout=10)
        fng = r.json()["data"][0]
        data["fear_greed"] = f"{int(fng['value'])}/100 ({fng['value_classification']})"
    except Exception:
        data["fear_greed"] = "無法取得"

    try:
        r = requests.get("https://www.okx.com/api/v5/market/ticker?instId=BTC-USDT", timeout=10)
        price = float(r.json()["data"][0]["last"])
        data["btc_price"] = f"${price:,.0f}"
        data["_btc_price_float"] = price
    except Exception:
        data["btc_price"] = "N/A"
        data["_btc_price_float"] = 0

    try:
        r = requests.get(
            "https://www.okx.com/api/v5/public/funding-rate?instId=BTC-USD-SWAP", timeout=10)
        rate = float(r.json()["data"][0]["fundingRate"]) * 100
        direction = "偏多（多付空）" if rate > 0 else "偏空（空付多）"
        data["btc_funding"] = f"{rate:+.4f}% {direction}"
    except Exception:
        data["btc_funding"] = "無法取得"

    try:
        r = requests.get(
            "https://www.okx.com/api/v5/public/open-interest?instId=BTC-USDT-SWAP", timeout=10)
        data["btc_oi"] = f"${float(r.json()['data'][0]['oiUsd']) / 1e9:.2f}B"
    except Exception:
        data["btc_oi"] = "無法取得"

    try:
        r = requests.get(
            "https://www.okx.com/api/v5/rubik/stat/contracts/long-short-account-ratio"
            "?ccy=BTC&period=1H&limit=1", timeout=10)
        ratio = float(r.json()["data"][0][1])
        long_pct = ratio / (ratio + 1) * 100
        data["long_short"] = f"多 {long_pct:.1f}% / 空 {100 - long_pct:.1f}%"
    except Exception:
        data["long_short"] = "無法取得"

    try:
        r = requests.get(
            "https://www.okx.com/api/v5/public/liquidation-orders"
            "?instType=SWAP&uly=BTC-USDT&state=filled&limit=100", timeout=10)
        details = r.json()["data"][0]["details"]
        cutoff_ms = (datetime.now(timezone.utc) - timedelta(hours=24)).timestamp() * 1000
        recent = [d for d in details if float(d["ts"]) > cutoff_ms]
        price = data.get("_btc_price_float", 0)
        long_liq = sum(float(d["sz"]) for d in recent if d["side"] == "sell") * price / 1e6
        short_liq = sum(float(d["sz"]) for d in recent if d["side"] == "buy") * price / 1e6
        data["liquidation"] = f"24h ${long_liq + short_liq:.1f}M｜多 ${long_liq:.1f}M｜空 ${short_liq:.1f}M"
    except Exception:
        data["liquidation"] = "無法取得"

    return data

def format_market_block(data):
    return (
        f"**【市場數據 — BTC】**\n"
        f"💰 現價：{data.get('btc_price', 'N/A')}\n"
        f"😱 恐懼貪婪：{data.get('fear_greed', 'N/A')}\n"
        f"💸 資金費率：{data.get('btc_funding', 'N/A')}\n"
        f"📊 未平倉量：{data.get('btc_oi', 'N/A')}\n"
        f"⚖️ 多空比：{data.get('long_short', 'N/A')}\n"
        f"💥 爆倉（24h）：{data.get('liquidation', 'N/A')}"
    )

# ── 網站抓取 ──────────────────────────────────────────────

def fetch_website_content(url):
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; RSSReader/1.0)"}
        r = requests.get(url, headers=headers, timeout=15)
        r.raise_for_status()
        text = re.sub(r'<script[^>]*>.*?</script>', ' ', r.text, flags=re.DOTALL)
        text = re.sub(r'<style[^>]*>.*?</style>', ' ', text, flags=re.DOTALL)
        text = re.sub(r'<[^>]+>', ' ', text)
        text = re.sub(r'\s+', ' ', text)
        return url, text.strip()[:2000]
    except Exception:
        return url, ""

def collect_website_content():
    results = {}
    for url in EXTRA_WEBSITES:
        _, content = fetch_website_content(url)
        if content:
            results[url] = content
            print(f"  + 網站 {url}: 已抓取")
        else:
            print(f"  - 網站 {url}: 無法取得")
    return results

# ── 推文抓取 ──────────────────────────────────────────────

def clean_html(text):
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

def fetch_recent_tweets(username, hours=2):
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    headers = {"User-Agent": "Mozilla/5.0 (compatible; RSSReader/1.0)"}
    for instance in NITTER_INSTANCES:
        try:
            resp = requests.get(f"{instance}/{username}/rss", headers=headers, timeout=12)
            if resp.status_code != 200:
                continue
            feed = feedparser.parse(resp.text)
            if not feed.entries:
                continue
            tweets = []
            for entry in feed.entries[:15]:
                try:
                    pub = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                    if pub < cutoff:
                        continue
                except Exception:
                    pass
                raw = entry.get("title", "") or entry.get("summary", "")
                text = clean_html(raw)
                if text and len(text) > 10:
                    tweets.append(text[:400])
            return username, tweets
        except Exception:
            continue
    return username, []

def collect_all_tweets():
    all_data = {}
    print(f"[{ts()}] 開始抓取 {len(ACCOUNTS)} 個帳號推文...")
    for account in ACCOUNTS:
        username, tweets = fetch_recent_tweets(account)
        if tweets:
            all_data[username] = tweets
            print(f"  + @{username}: {len(tweets)} 則")
        else:
            print(f"  - @{username}: 無新推文")
        time.sleep(0.4)
    return all_data

# ── AI 摘要 ───────────────────────────────────────────────

def summarize(tweets_data, website_data=None):
    lines = []
    for username, tweets in tweets_data.items():
        lines.append(f"@{username}:")
        for t in tweets:
            lines.append(f"  · {t}")
    tweets_text = "\n".join(lines)

    web_text = ""
    if website_data:
        web_lines = ["\n=== 網站內容 ==="]
        for url, content in website_data.items():
            web_lines.append(f"來源：{url}\n{content}")
        web_text = "\n".join(web_lines)

    prompt = f"""你是一位財經分析師。以下是過去 2 小時內，多位財經/加密貨幣 Twitter 帳號的最新推文，以及追蹤網站的最新內容。

請整理成一份**繁體中文快訊**，格式如下：

**【重點訊號】**
（列出 2-4 個最關鍵觀點，每點用加粗標題，若有多個子項目請用 1. 2. 3. 數字排列，每點充分說明背景與潛在影響）

**【宏觀 / 股市】**
（整理宏觀經濟、美股、Fed、利率、黃金等觀點；若有多個子項目請用 1. 2. 3. 數字排列；無則省略）

**【加密貨幣】**
（BTC/ETH 走勢、山寨幣、資金流向、關鍵技術位；若有多個子項目請用 1. 2. 3. 數字排列；無則省略）

**【個股 / 項目】**
（具體個股、DeFi 項目、代幣分析；若有多個子項目請用 1. 2. 3. 數字排列；無則省略）

**【網站精選】**
（若有來自追蹤網站的值得關注內容，請在此整理；若無特別新內容則省略）

**【市場情緒總結】**
（3-5 句話綜合當前市場情緒與短線需注意事項）

要求：每個子項目充分說明，不超過 80 字；總長度目標 1500-2500 字；直接輸出快訊，不要前言。

推文資料：
{tweets_text[:8500]}
{web_text[:1000]}"""

    payload = {
        "model": GROQ_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 2800,
        "temperature": 0.4,
    }
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    resp = requests.post(GROQ_API_URL, headers=headers, json=payload, timeout=60)
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]

# ── 推送 ─────────────────────────────────────────────────

def send_discord(digest, market_block, tweet_count, account_count):
    now_str = datetime.now().strftime("%Y/%m/%d %H:%M")
    full_content = f"{market_block}\n\n{digest}"
    embed = {
        "title": f"Twitter 財經快訊 — {now_str}",
        "description": full_content[:4000],
        "color": 0x1DA1F2,
        "footer": {"text": f"來源：{account_count} 個帳號 | {tweet_count} 則新推文 | 每 2 小時更新"},
    }
    requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [embed]}, timeout=15).raise_for_status()
    print(f"[{ts()}] 已推送到 Discord")

def send_discord_error(msg):
    requests.post(DISCORD_WEBHOOK_URL, json={"content": f"Bot 錯誤：{msg}"}, timeout=10)

def send_telegram(digest, market_block):
    now_str = datetime.now().strftime("%Y/%m/%d %H:%M")
    text = f"📊 <b>Twitter 財經快訊 — {now_str}</b>\n\n{market_block}\n\n{digest}"
    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        json={"chat_id": TELEGRAM_CHAT_ID, "text": text[:4096], "parse_mode": "HTML"},
        timeout=15,
    ).raise_for_status()
    print(f"[{ts()}] 已推送到 Telegram")

# ── 主流程 ────────────────────────────────────────────────

def ts():
    return datetime.now().strftime("%H:%M:%S")

def run_digest():
    print(f"\n{'='*55}")
    print(f"[{ts()}] 快訊彙整開始 — {datetime.now().strftime('%Y/%m/%d %H:%M')}")

    market_data = fetch_market_data()
    market_block = format_market_block(market_data)
    print(market_block)

    website_data = collect_website_content()
    tweets_data = collect_all_tweets()

    if not tweets_data:
        msg = "所有 Nitter 實例均無法取得推文"
        print(f"[{ts()}] {msg}")
        send_discord_error(msg)
        return

    total = sum(len(v) for v in tweets_data.values())
    print(f"[{ts()}] 共取得 {len(tweets_data)} 個帳號、{total} 則推文，AI 摘要中...")

    try:
        digest = summarize(tweets_data, website_data)
    except Exception as e:
        print(f"[{ts()}] AI 摘要失敗：{e}")
        send_discord_error(f"AI 摘要失敗：{e}")
        return

    disclaimer = "\n\n---\n🌀本快訊所有內容僅單純分享，不構成投資、金融建議，要資金的請謹慎評估，若造成損失請自行負責。"
    digest += disclaimer

    try:
        send_discord(digest, market_block, total, len(tweets_data))
    except Exception as e:
        print(f"[{ts()}] Discord 推送失敗：{e}")

    try:
        send_telegram(digest, market_block)
    except Exception as e:
        print(f"[{ts()}] Telegram 推送失敗：{e}")

    print(f"[{ts()}] 完成！")

if __name__ == "__main__":
    run_digest()
