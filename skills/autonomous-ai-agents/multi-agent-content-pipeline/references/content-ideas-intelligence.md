# Content Ideas Intelligence System

Multi-source content discovery and automatic queue feeding system that prevents content pipeline starvation.

## Architecture

**3-Source Discovery Pattern:**
```
File System Drops → ContentIdeasProcessor → Content Queue
Google Sheets API → EnhancedProcessor → Auto-Analysis  
Market Crawlers → AI Scoring → High-Potential Filter
```

## Source Integration Details

### File System Drop Zones
```python
content_ideas/
├── images/          # Screenshots, charts, infographics  
├── screenshots/     # Trading alerts, social media caps
├── links/           # URLs as .txt files
├── notes/           # Raw text ideas
├── files/           # PDFs, documents
├── processed/       # Successfully handled items
└── failed/          # Processing failures
```

**Processing Flow:**
1. Daily scan at 7 AM EST
2. Vision AI analysis for images (GPT-4V)
3. Web content extraction for URLs
4. AI scoring 1-10 for content potential
5. Auto-queue 7+/10 items as content

### Google Sheets Integration

**Service Account Setup:**
```bash
./setup_google_workspace.sh
# Creates service account + credentials
# Enables Sheets/Docs/Drive APIs
# Auto-generates structured sheet
```

**Sheet Structure:**
```
Timestamp | Idea Type | Content | Market Category | Urgency | 
Content Potential | Suggested Format | Target Platforms | 
Research Needed | Status
```

**Real-time Sync:**
- Team members add rows
- Daily processor scans for new entries
- Status automatically updated (processing/completed/failed)
- High-scoring ideas become content immediately

### Financial Market Crawling

**8 Market Categories:**
```python
news_sources = {
    'crypto': ['cointelegraph.com/rss', 'coindesk.com/arc/outboundfeeds/rss'],
    'stocks': ['feeds.finance.yahoo.com/rss', 'marketwatch.com/rss'],
    'forex': ['forexfactory.com/rss.php', 'fxstreet.com/rss'],
    'commodities': ['investing.com/rss/news_285.rss'],
    'bonds': ['bloomberg.com/feeds/bna/news.rss'], 
    'real_estate': ['realtor.com/rss/news_and_insights'],
    'indices': ['spglobal.com/spdji/en/rss'],
    'futures': ['cmegroup.com/tools-information/quikstrike/rss-feed.html']
}
```

**Congressional Trading Detection:**
- Scrapes ExampleCompetitor, ExampleCompany
- Detects insider activity patterns
- Auto-scores 8.5+/10 relevance
- Immediate alert + content generation

**AI Analysis Pipeline:**
```python
async def _ai_analyze_news_item(item):
    # Sentiment: -1.0 to 1.0
    # Relevance: 1.0 to 10.0 
    # Trading signals: ["bullish", "breakout", "insider_activity"]
    # Content formats: ["article", "carousel", "video"]
    # Urgency: "low/medium/high"
```

## Database Schema Extensions

```sql
CREATE TABLE content_ideas (
    id VARCHAR(64) PRIMARY KEY,
    type VARCHAR(20) NOT NULL,  -- 'image', 'url', 'text', 'file'
    content_path TEXT NOT NULL,
    content_text TEXT,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    processed_at TIMESTAMP,
    status VARCHAR(20) DEFAULT 'pending',
    analysis_result JSONB,
    generated_content_ids TEXT[] DEFAULT ARRAY[]::TEXT[]
);

CREATE TABLE market_news (
    id VARCHAR(64) PRIMARY KEY,
    source TEXT NOT NULL,
    category VARCHAR(50) NOT NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    sentiment_score REAL DEFAULT 0.0,
    relevance_score REAL DEFAULT 0.0,
    trading_signals TEXT[] DEFAULT ARRAY[]::TEXT[],
    metadata JSONB DEFAULT '{}'
);
```

## Auto-Queue Decision Logic

**Congressional Trading (9+/10):**
- Instant alert ($0.50)
- Analysis video ($4.00) 
- Data carousel ($2.50)
- High priority (9)

**Market Breakouts (8+/10):**  
- Analysis video ($4.00)
- Twitter thread (carousel format)
- Priority 7

**General Financial News (7+/10):**
- Long-form article ($1.50)
- Instagram carousel ($2.50) 
- Priority 6

**User Ideas (7+/10):**
- Format based on AI recommendation
- Slightly higher budget (+20%)
- Priority based on urgency flag

## Implementation Integration

**Enhanced Main Loop:**
```python
async def content_ideas_scheduler(self):
    # Run at 7 AM EST (after daily planning at 6 AM)
    if now.hour == 7 and now.minute < 5:
        results = await enhanced_daily_content_ideas_processing(
            self.orchestrator.llm_client, 
            self.db
        )
        await self.queue_idea_content(results)
```

**Processing Results Structure:**
```python
{
    'file_system_ideas': 5,      # Dropped files processed
    'google_sheet_ideas': 12,    # New sheet rows
    'market_crawler_items': 34,  # RSS + web scraping
    'total_processed': 51,       # All sources combined
    'high_potential_queued': 8   # 7+/10 items → content queue
}
```

## Success Metrics

**Content Discovery Rates:**
- Congressional trades: 95% → content creation
- Market breakouts: 80% → content creation  
- User screenshots: 70% → content creation
- RSS financial news: 15% → content creation

**Processing Performance:**
- File system scan: ~30 items/day typical
- Google Sheet sync: ~10-50 items/day
- Market crawler: ~100-200 items/day
- High-potential rate: ~10-15% across all sources

## Pitfalls & Solutions

### Pitfall: Google API quota exhaustion
**Solution**: Implement exponential backoff, cache sheet data, batch read operations

### Pitfall: Market crawler rate limiting
**Solution**: Stagger RSS feed requests, respect robots.txt, implement delays

### Pitfall: False positive content scoring  
**Solution**: Track generated content performance, retrain scoring prompts based on engagement data

### Pitfall: Duplicate detection across sources
**Solution**: Content fingerprinting with URL + title hashing, cross-source deduplication

### Pitfall: Processing lag during high-volume news events
**Solution**: Priority queue with congressional trading > market breakouts > general news > user ideas

## Docker Integration

**Volume Mounts:**
```yaml
volumes:
  - ./content_ideas:/app/content_ideas  # File system drops
  - ./google_credentials.json:/app/google_credentials.json  # Service account
```

**Environment Variables:**
```bash
GOOGLE_SHEET_ID=your_sheet_id_here
MARKET_CRAWL_ENABLED=true
MARKET_CRAWL_CATEGORIES=crypto,stocks,forex,commodities,bonds,real_estate,indices,futures
CONGRESSIONAL_TRADING_ENABLED=true
```

This system ensures the content pipeline never runs dry by continuously discovering opportunities across multiple intelligent sources.