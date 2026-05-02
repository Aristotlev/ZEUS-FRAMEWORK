"""
Financial News Crawler & Google Workspace Integration
Automatically discovers content opportunities across all financial markets
"""
import asyncio
import aiohttp
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
import json
import hashlib
import re
from pathlib import Path

from googleapiclient.discovery import build
from google.oauth2.service_account import Credentials
import feedparser
from bs4 import BeautifulSoup
from openai import OpenAI

from database import DatabaseManager
from content_ideas_processor import ContentIdea, ContentIdeasProcessor

@dataclass
class MarketNewsItem:
    """Represents a financial news item discovered by crawler"""
    id: str
    source: str
    category: str  # crypto, stocks, forex, etc.
    title: str
    url: str
    content_preview: str
    published_at: datetime
    sentiment_score: float
    relevance_score: float
    trading_signals: List[str]
    metadata: Dict[str, Any]

class GoogleWorkspaceIntegration:
    """Integration with Google Sheets and Docs for content ideas"""
    
    def __init__(self, credentials_path: str = "google_credentials.json"):
        self.credentials_path = credentials_path
        self.sheets_service = None
        self.docs_service = None
        self.drive_service = None
        
        self.ideas_sheet_id = None
        self.ideas_doc_id = None
        
        self._initialize_services()
    
    def _initialize_services(self):
        """Initialize Google API services"""
        try:
            if Path(self.credentials_path).exists():
                credentials = Credentials.from_service_account_file(
                    self.credentials_path,
                    scopes=[
                        'https://www.googleapis.com/auth/spreadsheets',
                        'https://www.googleapis.com/auth/documents',
                        'https://www.googleapis.com/auth/drive.readonly'
                    ]
                )
                
                self.sheets_service = build('sheets', 'v4', credentials=credentials)
                self.docs_service = build('docs', 'v1', credentials=credentials)
                self.drive_service = build('drive', 'v3', credentials=credentials)
                
                logging.info("✅ Google Workspace services initialized")
            else:
                logging.warning("⚠️ Google credentials not found - Google integration disabled")
                
        except Exception as e:
            logging.error(f"❌ Failed to initialize Google services: {e}")
    
    async def setup_content_ideas_sheet(self) -> Optional[str]:
        """Create or find the Zeus Content Ideas Google Sheet"""
        if not self.sheets_service:
            return None
            
        try:
            # Create new spreadsheet
            spreadsheet = {
                'properties': {
                    'title': '🏛️ Zeus Framework - Content Ideas Intelligence'
                },
                'sheets': [
                    {
                        'properties': {
                            'title': 'Content Ideas',
                            'gridProperties': {
                                'rowCount': 1000,
                                'columnCount': 10
                            }
                        }
                    },
                    {
                        'properties': {
                            'title': 'Market Crawler Results',
                            'gridProperties': {
                                'rowCount': 1000, 
                                'columnCount': 12
                            }
                        }
                    }
                ]
            }
            
            result = self.sheets_service.spreadsheets().create(
                body=spreadsheet,
                fields='spreadsheetId'
            ).execute()
            
            sheet_id = result.get('spreadsheetId')
            
            # Set up headers
            await self._setup_sheet_headers(sheet_id)
            
            self.ideas_sheet_id = sheet_id
            logging.info(f"✅ Created Zeus Content Ideas Sheet: {sheet_id}")
            
            return sheet_id
            
        except Exception as e:
            logging.error(f"❌ Failed to create Google Sheet: {e}")
            return None
    
    async def _setup_sheet_headers(self, sheet_id: str):
        """Set up headers for both sheets"""
        
        # Content Ideas sheet headers
        ideas_headers = [
            'Timestamp', 'Idea Type', 'Content', 'Market Category', 
            'Urgency', 'Content Potential (1-10)', 'Suggested Format',
            'Target Platforms', 'Research Needed', 'Status'
        ]
        
        # Market Crawler sheet headers  
        crawler_headers = [
            'Timestamp', 'Source', 'Category', 'Title', 'URL',
            'Sentiment', 'Relevance Score', 'Trading Signals', 
            'Content Angle', 'Status', 'Generated Content', 'Notes'
        ]
        
        # Update both sheets
        requests = [
            {
                'updateCells': {
                    'range': {
                        'sheetId': 0,  # Content Ideas sheet
                        'startRowIndex': 0,
                        'endRowIndex': 1,
                        'startColumnIndex': 0,
                        'endColumnIndex': len(ideas_headers)
                    },
                    'rows': [
                        {
                            'values': [
                                {
                                    'userEnteredValue': {'stringValue': header},
                                    'userEnteredFormat': {
                                        'backgroundColor': {'red': 0.2, 'green': 0.6, 'blue': 0.9},
                                        'textFormat': {'bold': True, 'foregroundColor': {'red': 1, 'green': 1, 'blue': 1}}
                                    }
                                } for header in ideas_headers
                            ]
                        }
                    ],
                    'fields': 'userEnteredValue,userEnteredFormat'
                }
            },
            {
                'updateCells': {
                    'range': {
                        'sheetId': 1,  # Market Crawler sheet
                        'startRowIndex': 0,
                        'endRowIndex': 1, 
                        'startColumnIndex': 0,
                        'endColumnIndex': len(crawler_headers)
                    },
                    'rows': [
                        {
                            'values': [
                                {
                                    'userEnteredValue': {'stringValue': header},
                                    'userEnteredFormat': {
                                        'backgroundColor': {'red': 0.9, 'green': 0.6, 'blue': 0.2},
                                        'textFormat': {'bold': True, 'foregroundColor': {'red': 1, 'green': 1, 'blue': 1}}
                                    }
                                } for header in crawler_headers
                            ]
                        }
                    ],
                    'fields': 'userEnteredValue,userEnteredFormat'
                }
            }
        ]
        
        try:
            self.sheets_service.spreadsheets().batchUpdate(
                spreadsheetId=sheet_id,
                body={'requests': requests}
            ).execute()
        except Exception as e:
            logging.error(f"❌ Failed to set up sheet headers: {e}")
    
    async def scan_content_ideas_sheet(self) -> List[ContentIdea]:
        """Scan Google Sheet for new content ideas"""
        if not self.sheets_service or not self.ideas_sheet_id:
            return []
        
        try:
            # Read Content Ideas sheet
            result = self.sheets_service.spreadsheets().values().get(
                spreadsheetId=self.ideas_sheet_id,
                range='Content Ideas!A2:J1000'  # Skip header row
            ).execute()
            
            values = result.get('values', [])
            new_ideas = []
            
            for i, row in enumerate(values):
                if len(row) < 3:  # Need at least timestamp, type, content
                    continue
                
                # Check if already processed (status column)
                status = row[9] if len(row) > 9 else ''
                if status.lower() in ['processed', 'completed', 'failed']:
                    continue
                
                # Create ContentIdea from row  
                idea = self._row_to_content_idea(row, i + 2)
                if idea:
                    new_ideas.append(idea)
                    
                    # Mark as processing in sheet
                    await self._update_sheet_status(i + 2, 'processing')
            
            logging.info(f"📊 Found {len(new_ideas)} new ideas in Google Sheet")
            return new_ideas
            
        except Exception as e:
            logging.error(f"❌ Failed to scan Google Sheet: {e}")
            return []
    
    def _row_to_content_idea(self, row: List[str], row_number: int) -> Optional[ContentIdea]:
        """Convert Google Sheet row to ContentIdea"""
        try:
            timestamp_str = row[0] if len(row) > 0 else datetime.now().isoformat()
            idea_type = row[1] if len(row) > 1 else 'text'
            content = row[2] if len(row) > 2 else ''
            
            if not content.strip():
                return None
            
            # Generate unique ID
            idea_id = hashlib.md5(f"{self.ideas_sheet_id}:{row_number}:{content[:50]}".encode()).hexdigest()
            
            # Parse timestamp
            try:
                created_at = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
            except:
                created_at = datetime.now()
            
            metadata = {
                'source': 'google_sheet',
                'sheet_id': self.ideas_sheet_id,
                'row_number': row_number,
                'market_category': row[3] if len(row) > 3 else '',
                'urgency': row[4] if len(row) > 4 else 'medium',
                'user_potential_score': row[5] if len(row) > 5 else '',
                'suggested_format': row[6] if len(row) > 6 else '',
                'target_platforms': row[7] if len(row) > 7 else '',
                'research_needed': row[8] if len(row) > 8 else ''
            }
            
            return ContentIdea(
                id=idea_id,
                type='text',  # Google Sheet entries are text-based
                content_path=f"google_sheet:{self.ideas_sheet_id}:row_{row_number}",
                content_text=content,
                metadata=metadata,
                created_at=created_at,
                processed_at=None,
                status='pending',
                analysis_result=None,
                generated_content_ids=[]
            )
            
        except Exception as e:
            logging.error(f"❌ Error converting row to ContentIdea: {e}")
            return None
    
    async def _update_sheet_status(self, row_number: int, status: str):
        """Update status in Google Sheet"""
        if not self.sheets_service or not self.ideas_sheet_id:
            return
            
        try:
            self.sheets_service.spreadsheets().values().update(
                spreadsheetId=self.ideas_sheet_id,
                range=f'Content Ideas!J{row_number}',  # Status column
                valueInputOption='RAW',
                body={'values': [[status]]}
            ).execute()
        except Exception as e:
            logging.error(f"❌ Failed to update sheet status: {e}")

class FinancialMarketCrawler:
    """Crawls financial news across all market categories"""
    
    def __init__(self, llm_client):
        self.llm_client = llm_client
        
        # Financial news sources by category
        self.news_sources = {
            'crypto': [
                'https://cointelegraph.com/rss',
                'https://cryptonews.com/rss',
                'https://decrypt.co/feed',
                'https://www.coindesk.com/arc/outboundfeeds/rss'
            ],
            'stocks': [
                'https://feeds.finance.yahoo.com/rss/2.0/headline',
                'https://www.marketwatch.com/rss/topstories',
                'https://seekingalpha.com/market_currents.xml',
                'https://www.benzinga.com/feed'
            ],
            'forex': [
                'https://www.forexfactory.com/rss.php',
                'https://www.fxstreet.com/rss/news',
                'https://www.dailyfx.com/rss'
            ],
            'commodities': [
                'https://www.investing.com/rss/news_285.rss',  # Commodities
                'https://www.kitco.com/rss/KitcoNews.xml'
            ],
            'bonds': [
                'https://www.bloomberg.com/feeds/bna/news.rss',
                'https://www.treasurydirect.gov/xml/R_20051129_1.xml'
            ],
            'real_estate': [
                'https://www.realtor.com/rss/news_and_insights',
                'https://www.housingwire.com/feed/'
            ],
            'indices': [
                'https://www.spglobal.com/spdji/en/rss/rss-details/?rssFeedName=research-and-commentary'
            ],
            'futures': [
                'https://www.cmegroup.com/tools-information/quikstrike/rss-feed.html'
            ]
        }
        
        # Alternative web scraping sources (non-RSS)
        self.web_sources = {
            'congress_trading': [
                'https://www.capitoltrades.com',
                'https://unusualwhales.com/i_am_the_senate'
            ],
            'market_movers': [
                'https://finviz.com/screener.ashx?v=111&f=cap_mega,ta_topgainers',
                'https://finance.yahoo.com/gainers',
                'https://finance.yahoo.com/losers'
            ],
            'earnings': [
                'https://finance.yahoo.com/calendar/earnings',
                'https://www.earningswhispers.com/calendar'
            ]
        }
        
        self.session = None
    
    async def daily_market_crawl(self) -> List[MarketNewsItem]:
        """Perform daily crawl of all financial news sources"""
        logging.info("🌐 Starting daily financial market crawl...")
        
        all_news_items = []
        
        async with aiohttp.ClientSession() as session:
            self.session = session
            
            # Crawl RSS feeds by category
            for category, feeds in self.news_sources.items():
                category_items = await self._crawl_category_feeds(category, feeds)
                all_news_items.extend(category_items)
            
            # Crawl web sources
            web_items = await self._crawl_web_sources()
            all_news_items.extend(web_items)
        
        # Analyze and score all items
        scored_items = await self._analyze_news_items(all_news_items)
        
        # Filter high-potential items
        high_potential = [item for item in scored_items if item.relevance_score >= 7.0]
        
        logging.info(f"📰 Market crawl complete: {len(all_news_items)} total, {len(high_potential)} high-potential")
        
        return high_potential
    
    async def _crawl_category_feeds(self, category: str, feeds: List[str]) -> List[MarketNewsItem]:
        """Crawl RSS feeds for a specific market category"""
        category_items = []
        
        for feed_url in feeds:
            try:
                async with self.session.get(feed_url, timeout=30) as response:
                    if response.status == 200:
                        rss_content = await response.text()
                        parsed_feed = feedparser.parse(rss_content)
                        
                        for entry in parsed_feed.entries[:10]:  # Limit per feed
                            news_item = self._entry_to_news_item(entry, category, feed_url)
                            if news_item:
                                category_items.append(news_item)
                                
            except Exception as e:
                logging.warning(f"⚠️ Failed to crawl {feed_url}: {e}")
                
        return category_items
    
    def _entry_to_news_item(self, entry, category: str, source: str) -> Optional[MarketNewsItem]:
        """Convert RSS entry to MarketNewsItem"""
        try:
            # Generate unique ID
            entry_id = hashlib.md5(f"{entry.link}:{entry.title}".encode()).hexdigest()
            
            # Parse published date
            published_at = datetime.now()
            if hasattr(entry, 'published_parsed') and entry.published_parsed:
                try:
                    from time import struct_time, mktime
                    published_at = datetime.fromtimestamp(mktime(entry.published_parsed))
                except:
                    pass
            
            # Extract content preview
            content_preview = ''
            if hasattr(entry, 'summary'):
                # Strip HTML tags
                content_preview = re.sub(r'<[^>]+>', '', entry.summary)[:500]
            
            return MarketNewsItem(
                id=entry_id,
                source=source,
                category=category,
                title=entry.title,
                url=entry.link,
                content_preview=content_preview,
                published_at=published_at,
                sentiment_score=0.0,  # Will be analyzed later
                relevance_score=0.0,   # Will be analyzed later
                trading_signals=[],    # Will be analyzed later
                metadata={
                    'rss_entry': True,
                    'author': getattr(entry, 'author', ''),
                    'tags': getattr(entry, 'tags', [])
                }
            )
            
        except Exception as e:
            logging.error(f"❌ Error processing RSS entry: {e}")
            return None
    
    async def _crawl_web_sources(self) -> List[MarketNewsItem]:
        """Crawl non-RSS web sources with scraping"""
        web_items = []
        
        # Congressional trading scraping
        congress_items = await self._scrape_congressional_trades()
        web_items.extend(congress_items)
        
        # Market movers scraping  
        movers_items = await self._scrape_market_movers()
        web_items.extend(movers_items)
        
        return web_items
    
    async def _scrape_congressional_trades(self) -> List[MarketNewsItem]:
        """Scrape latest congressional trading data"""
        items = []
        
        try:
            # This would need specific scraping logic for each source
            # For now, return mock structure
            
            # Example: Scrape from public congressional trading APIs/feeds
            mock_trade = MarketNewsItem(
                id=hashlib.md5(f"congress_trade_{datetime.now()}".encode()).hexdigest(),
                source='congressional_trading',
                category='congress_trading',
                title="New Congressional Trade Detected",
                url="https://example.com/trade",
                content_preview="Representative X purchased $10K-$50K of NVDA stock...",
                published_at=datetime.now(),
                sentiment_score=0.0,
                relevance_score=8.5,  # Congressional trades are always high relevance
                trading_signals=['bullish_insider'],
                metadata={'trade_type': 'purchase', 'amount_range': '$10K-$50K'}
            )
            
            # In production, implement actual scraping here
            # items.append(mock_trade)
            
        except Exception as e:
            logging.error(f"❌ Congressional trades scraping failed: {e}")
            
        return items
    
    async def _scrape_market_movers(self) -> List[MarketNewsItem]:
        """Scrape top market movers and unusual activity"""
        items = []
        
        try:
            # Scrape top gainers/losers from multiple sources
            # This would implement the actual scraping logic
            
            pass
            
        except Exception as e:
            logging.error(f"❌ Market movers scraping failed: {e}")
            
        return items
    
    async def _analyze_news_items(self, items: List[MarketNewsItem]) -> List[MarketNewsItem]:
        """Analyze news items for sentiment, relevance, and trading signals"""
        
        for item in items:
            try:
                # Analyze with AI
                analysis = await self._ai_analyze_news_item(item)
                
                # Update scores and signals
                item.sentiment_score = analysis.get('sentiment_score', 0.0)
                item.relevance_score = analysis.get('relevance_score', 0.0)  
                item.trading_signals = analysis.get('trading_signals', [])
                item.metadata.update(analysis.get('metadata', {}))
                
            except Exception as e:
                logging.error(f"❌ Failed to analyze news item {item.id}: {e}")
                
        return items
    
    async def _ai_analyze_news_item(self, item: MarketNewsItem) -> Dict[str, Any]:
        """Use AI to analyze news item for content potential"""
        
        prompt = f"""
        Analyze this financial news item for content creation potential:
        
        Category: {item.category}
        Title: {item.title}
        Content: {item.content_preview}
        
        Provide analysis as JSON:
        {{
            "sentiment_score": -1.0 to 1.0,
            "relevance_score": 1.0 to 10.0,
            "content_potential": 1.0 to 10.0,
            "trading_signals": ["bullish", "bearish", "breakout", "insider_activity", "earnings_beat", etc.],
            "content_angles": ["angle1", "angle2"],
            "target_audience": "beginners/intermediate/advanced",
            "urgency": "low/medium/high",
            "recommended_formats": ["article", "carousel", "video"],
            "key_tickers": ["AAPL", "BTC", etc.],
            "metadata": {{
                "trading_opportunity": true/false,
                "breaking_news": true/false,
                "data_visualization_potential": true/false
            }}
        }}
        """
        
        try:
            response = self.llm_client.chat.completions.create(
                model="deepseek/deepseek-v4",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=800
            )
            
            analysis = json.loads(response.choices[0].message.content)
            return analysis
            
        except Exception as e:
            logging.error(f"❌ AI analysis failed for {item.title}: {e}")
            return {
                'sentiment_score': 0.0,
                'relevance_score': 5.0,
                'trading_signals': [],
                'metadata': {}
            }

class EnhancedContentIdeasProcessor(ContentIdeasProcessor):
    """Enhanced processor with Google Workspace and market crawling"""
    
    def __init__(self, llm_client, db_manager: DatabaseManager):
        super().__init__(llm_client, db_manager)
        
        self.google_integration = GoogleWorkspaceIntegration()
        self.market_crawler = FinancialMarketCrawler(llm_client)
        
        self.setup_enhanced_database()
    
    def setup_enhanced_database(self):
        """Add tables for market news and Google integration"""
        create_tables_sql = """
        CREATE TABLE IF NOT EXISTS market_news (
            id VARCHAR(64) PRIMARY KEY,
            source TEXT NOT NULL,
            category VARCHAR(50) NOT NULL,
            title TEXT NOT NULL,
            url TEXT NOT NULL,
            content_preview TEXT,
            published_at TIMESTAMP,
            sentiment_score REAL DEFAULT 0.0,
            relevance_score REAL DEFAULT 0.0,
            trading_signals TEXT[] DEFAULT ARRAY[]::TEXT[],
            metadata JSONB DEFAULT '{}',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            processed_at TIMESTAMP,
            status VARCHAR(20) DEFAULT 'pending',
            generated_content_ids TEXT[] DEFAULT ARRAY[]::TEXT[]
        );
        
        CREATE INDEX IF NOT EXISTS idx_market_news_category ON market_news(category);
        CREATE INDEX IF NOT EXISTS idx_market_news_relevance ON market_news(relevance_score);
        CREATE INDEX IF NOT EXISTS idx_market_news_published ON market_news(published_at);
        """
        
        with self.db.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(create_tables_sql)
            conn.commit()
    
    async def enhanced_daily_processing(self) -> Dict[str, Any]:
        """Enhanced daily processing with all sources"""
        logging.info("🚀 Starting enhanced content ideas processing...")
        
        results = {
            'file_system_ideas': 0,
            'google_sheet_ideas': 0, 
            'market_crawler_items': 0,
            'total_processed': 0,
            'high_potential_queued': 0
        }
        
        # 1. Process file system ideas (original functionality)
        file_ideas = await self.scan_for_new_ideas()
        results['file_system_ideas'] = len(file_ideas)
        
        # 2. Process Google Sheet ideas
        google_ideas = await self.google_integration.scan_content_ideas_sheet()
        results['google_sheet_ideas'] = len(google_ideas)
        
        # 3. Crawl financial markets
        market_items = await self.market_crawler.daily_market_crawl()
        results['market_crawler_items'] = len(market_items)
        
        # Save market items to database
        await self._save_market_items(market_items)
        
        # 4. Process all pending ideas
        processing_results = await self.process_pending_ideas()
        results['total_processed'] = len(processing_results)
        
        # 5. Process high-potential market items
        market_content = await self._process_market_items(market_items)
        results['high_potential_queued'] = len(market_content)
        
        logging.info(f"✅ Enhanced processing complete: {results}")
        
        return results
    
    async def _save_market_items(self, items: List[MarketNewsItem]):
        """Save market news items to database"""
        if not items:
            return
            
        with self.db.get_connection() as conn:
            with conn.cursor() as cur:
                for item in items:
                    cur.execute("""
                        INSERT INTO market_news 
                        (id, source, category, title, url, content_preview, published_at,
                         sentiment_score, relevance_score, trading_signals, metadata)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (id) DO UPDATE SET
                            sentiment_score = EXCLUDED.sentiment_score,
                            relevance_score = EXCLUDED.relevance_score,
                            trading_signals = EXCLUDED.trading_signals
                    """, (
                        item.id, item.source, item.category, item.title, item.url,
                        item.content_preview, item.published_at, item.sentiment_score,
                        item.relevance_score, item.trading_signals, json.dumps(item.metadata)
                    ))
            conn.commit()
    
    async def _process_market_items(self, items: List[MarketNewsItem]) -> List[str]:
        """Process high-potential market items into content queue"""
        queued_content = []
        
        # Sort by relevance score  
        high_potential = [item for item in items if item.relevance_score >= 7.5]
        high_potential.sort(key=lambda x: x.relevance_score, reverse=True)
        
        for item in high_potential[:5]:  # Limit to top 5 per day
            try:
                # Queue content based on category and signals
                content_spec = {
                    'source': 'market_crawler',
                    'news_item_id': item.id,
                    'category': item.category,
                    'title': item.title,
                    'url': item.url,
                    'trading_signals': item.trading_signals,
                    'sentiment_score': item.sentiment_score,
                    'relevance_score': item.relevance_score
                }
                
                # Choose content format based on category and signals
                if item.category == 'congress_trading':
                    # Breaking alert for congressional trades
                    await self.db.queue_content(
                        'alert',
                        f"🚨 Congressional Trade Alert: {item.title}",
                        content_spec,
                        priority=9,
                        budget=0.5
                    )
                    queued_content.append(item.id)
                    
                elif 'breakout' in item.trading_signals or 'earnings_beat' in item.trading_signals:
                    # Video for big market moves
                    await self.db.queue_content(
                        'video',
                        f"[MARKET] {item.title}",
                        content_spec,
                        priority=7,
                        budget=4.0
                    )
                    queued_content.append(item.id)
                    
                else:
                    # Article for general news
                    await self.db.queue_content(
                        'article', 
                        f"[MARKET] {item.title}",
                        content_spec,
                        priority=6,
                        budget=1.5
                    )
                    queued_content.append(item.id)
                    
            except Exception as e:
                logging.error(f"❌ Failed to queue market item {item.id}: {e}")
        
        return queued_content

# Enhanced daily function
async def enhanced_daily_content_ideas_processing(llm_client, db_manager: DatabaseManager):
    """Enhanced daily processing with all content sources"""
    processor = EnhancedContentIdeasProcessor(llm_client, db_manager)
    return await processor.enhanced_daily_processing()