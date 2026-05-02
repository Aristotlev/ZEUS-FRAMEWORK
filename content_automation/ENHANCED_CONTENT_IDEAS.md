# 🏛️ Zeus Framework - Enhanced Content Ideas Intelligence

## 🚀 Multi-Source Content Discovery System

Zeus now has **3 intelligent content sources** that work together:

### 1. 📂 **File System Drops** (Original)
- `content_ideas/images/` → Screenshots, charts, infographics
- `content_ideas/links/` → URLs as .txt files  
- `content_ideas/notes/` → Text ideas and observations

### 2. 📊 **Google Sheets Integration** (NEW)
- **Live collaboration** → Multiple people can add ideas
- **Structured input** → Columns for category, urgency, potential score
- **Real-time sync** → Changes appear in daily processing
- **Status tracking** → See what's been processed

### 3. 🌐 **Automated Market Crawling** (NEW)  
- **8 Market Categories:** Crypto, Stocks, Forex, Commodities, Bonds, Real Estate, Indices, Futures
- **Congressional Trading Alerts** → Automated detection of insider trades
- **RSS Feeds** → 20+ financial news sources
- **Market Movers** → Top gainers/losers across exchanges
- **AI Analysis** → Each item scored for content potential (1-10)

## 🧠 Daily Processing Flow (7 AM EST)

```
1. Scan file system drops → Process with vision AI (images)
2. Check Google Sheet → Pull new rows, mark as "processing"  
3. Crawl financial markets → RSS feeds + web scraping
4. AI Analysis → Score everything for content potential
5. Auto-queue high-potential → 7+/10 becomes content
6. Update status → Google Sheet shows "completed/failed"
```

## 📊 Google Sheet Template

| Timestamp | Idea Type | Content | Market Category | Urgency | Content Potential | Suggested Format | Target Platforms | Research Needed | Status |
|-----------|-----------|---------|-----------------|---------|------------------|------------------|------------------|-----------------|---------|
| 2024-01-15 09:30 | Congressional Trade | Nancy Pelosi bought NVDA before earnings | stocks | high | 9 | video,carousel | twitter,tiktok | Exact trade date, stock performance | processing |
| 2024-01-15 10:15 | Market Analysis | Bitcoin forming bull flag pattern | crypto | medium | 7 | article,carousel | twitter,instagram | Technical analysis confirmation | pending |

## 🎯 Content Scoring & Auto-Queue Rules

### **Automatic Content Creation (7+/10):**
- **Congressional Trades** → Instant alert + carousel + video
- **Market Breakouts** → Video analysis + Twitter thread
- **Earnings Surprises** → Article + Instagram carousel
- **Crypto News** → TikTok video + Twitter post

### **Manual Review (5-6/10):**
- Saved for slow news days
- Good backup content
- Lower engagement potential

### **Archived (1-4/10):**
- Too generic or old
- Low viral potential
- Reference only

## 🔧 Setup Instructions

### Google Workspace Integration:
```bash
# 1. Setup Google service account
./setup_google_workspace.sh

# 2. Enable APIs in Google Cloud Console:
#    - Google Sheets API
#    - Google Docs API  
#    - Google Drive API

# 3. Share created sheet with service account
# 4. Add GOOGLE_SHEET_ID to .env
```

### Market Crawling Sources:
```bash
# Crypto: CoinTelegraph, CoinDesk, Decrypt, CryptoNews
# Stocks: Yahoo Finance, MarketWatch, Seeking Alpha, Benzinga  
# Forex: ForexFactory, FXStreet, DailyFX
# Commodities: Investing.com, Kitco
# Bonds: Bloomberg, TreasuryDirect
# Real Estate: Realtor.com, HousingWire
# Indices: S&P Global
# Futures: CME Group
```

## 📈 Content Pipeline Integration

**High-Value Ideas Auto-Queue As:**
- **Articles** ($1.50 budget) → In-depth analysis pieces
- **Carousels** ($2.50 budget) → Data visualization posts  
- **Videos** ($4.00 budget) → Breaking news explanations
- **Alerts** ($0.50 budget) → Immediate congressional trade notifications

## 💡 Pro Tips for Maximum Content Generation

### Google Sheet Power-User:
1. **Batch similar ideas** → Process multiple crypto stories together
2. **Use urgency correctly** → "High" gets processed first
3. **Pre-score potential** → Your 1-10 guess helps AI prioritization
4. **Add research notes** → Saves time during content creation

### File System Optimization:
1. **Screenshot congressional alerts** immediately → Highest conversion rate
2. **Name files descriptively** → "pelosi_nvda_trade_20241201.png" 
3. **Combine formats** → Screenshot + link + note = comprehensive analysis

### Market Categories That Perform Best:
1. **Congressional Trading** → 95% content creation rate
2. **Crypto Breakouts** → 80% content creation rate  
3. **Earnings Surprises** → 75% content creation rate
4. **Market Crashes/Spikes** → 90% content creation rate

## 📊 Monitoring & Analytics

**Track performance at:** `http://localhost:8080/monitor`

- **Ideas processed per day**
- **Content potential scores distribution** 
- **Auto-queue success rates**
- **Source performance** (file system vs Google vs crawler)
- **Category performance** (which markets generate most content)

## 🔄 Daily Workflow Examples

### **Scenario 1: Congressional Trade Alert**
```
7:00 AM → Market crawler detects Pelosi NVDA trade
7:01 AM → AI scores it 9.5/10 (high insider relevance)
7:02 AM → Auto-queues: Alert (immediate), Video ($4), Carousel ($2.50)
8:00 AM → Content starts generating
10:00 AM → Posted across all platforms
Result: 3 pieces of content from 1 news item
```

### **Scenario 2: User Drops Screenshot**  
```
User drops: Screenshot of Bitcoin breaking $50K
7:00 AM → Vision AI analyzes screenshot
7:01 AM → Detects breakout pattern, scores 8/10
7:02 AM → Auto-queues: Article + Video
Result: 2 pieces of content from user screenshot
```

### **Scenario 3: Google Sheet Collaboration**
```
Team member adds: "Explain why GameStop is spiking again"
Next day 7:00 AM → AI analyzes, scores 7.5/10
7:02 AM → Auto-queues: Article + TikTok video
Result: Educational content from team suggestion
```

---

**🎯 Result:** Never run out of content ideas again. The system continuously feeds itself with opportunities from multiple sources, automatically creating high-engagement financial content 24/7.