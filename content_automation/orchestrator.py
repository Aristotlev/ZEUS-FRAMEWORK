import os
from datetime import datetime, timedelta
import asyncio
import logging
from typing import Dict, List, Optional
import json

import asyncpg
from openai import OpenAI
import requests
from PIL import Image
import aiohttp

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class ZeusOrchestrator:
    """Daily content planning and pipeline coordination"""
    
    def __init__(self):
        self.db_pool = None
        # Use OpenRouter with DeepSeek V4
        self.llm_client = OpenAI(
            api_key=os.getenv('OPENROUTER_API_KEY'),
            base_url="https://openrouter.ai/api/v1"
        )
        self.fal_api_key = os.getenv('FAL_API_KEY')
        self.fish_audio_key = os.getenv('FISH_AUDIO_API_KEY')
        self.vidnoz_key = os.getenv('VIDNOZ_API_KEY')
        self.publer_key = os.getenv('PUBLER_API_KEY')
        
    async def init_db(self):
        """Initialize database connection pool"""
        self.db_pool = await asyncpg.create_pool(
            host=os.getenv('DB_HOST', 'localhost'),
            port=int(os.getenv('DB_PORT', 5432)),
            user=os.getenv('DB_USER', 'postgres'),
            password=os.getenv('DB_PASSWORD'),
            database=os.getenv('DB_NAME', 'zeus_content'),
            min_size=5,
            max_size=20
        )
        
    async def daily_planning(self) -> Dict:
        """Main orchestrator - runs once daily at 6 AM EST"""
        logger.info("🧠 Starting daily content planning...")
        
        # Get market context
        market_data = await self.get_market_context()
        congressional_trades = await self.get_congressional_trades()
        
        planning_prompt = f"""
You are the Zeus Framework content orchestrator. Plan today's content strategy.

MARKET CONTEXT:
{json.dumps(market_data, indent=2)}

CONGRESSIONAL TRADES (last 24h):
{json.dumps(congressional_trades, indent=2)}

DAILY BUDGET: $50 media generation
CONTENT TARGETS:
- 1-2 Articles (long-form analysis) 
- 2-3 Carousels (data visualizations)
- 3-5 Short videos (TikTok/YouTube Shorts)
- 10+ Breaking alerts (real-time)

OUTPUT REQUIRED (JSON):
{{
  "theme": "daily narrative theme",
  "priority_platforms": ["tiktok", "twitter", "youtube"],  
  "content_plan": {{
    "articles": [
      {{"title": "...", "angle": "...", "urgency": "high/medium/low"}}
    ],
    "carousels": [
      {{"theme": "...", "data_focus": "...", "platform": "instagram"}}  
    ],
    "videos": [
      {{"script_concept": "...", "type": "breaking/educational", "budget": "cheap/premium"}}
    ]
  }},
  "budget_allocation": {{
    "articles": 10,
    "carousels": 60, 
    "videos": 30
  }}
}}
"""

        response = self.llm_client.chat.completions.create(
            model="deepseek/deepseek-v4",  # OpenRouter format
            messages=[{"role": "user", "content": planning_prompt}],
            temperature=0.3,
            max_tokens=2000
        )
        
        plan = json.loads(response.choices[0].message.content)
        
        # Store plan in database
        await self.store_daily_plan(plan)
        
        # Trigger content generation pipelines
        await self.trigger_pipelines(plan)
        
        logger.info(f"✅ Daily plan created: {plan['theme']}")
        return plan
    
    async def get_market_context(self) -> Dict:
        """Fetch current market data and sentiment"""
        # Mock implementation - replace with real APIs
        return {
            "volatility": 0.65,
            "trending_tickers": ["NVDA", "TSLA", "AAPL"],
            "market_sentiment": "cautiously_bullish",
            "earnings_today": ["META", "GOOGL"],
            "fed_events": []
        }
    
    async def get_congressional_trades(self) -> List[Dict]:
        """Fetch latest congressional trading data"""
        # Mock implementation - replace with real APIs
        return [
            {
                "politician": "Nancy Pelosi",
                "ticker": "NVDA", 
                "action": "BUY",
                "amount_range": "$1M-$5M",
                "filed_date": "2024-01-15",
                "significance": 8.5
            },
            {
                "politician": "Chuck Schumer", 
                "ticker": "TSLA",
                "action": "SELL",
                "amount_range": "$500K-$1M", 
                "filed_date": "2024-01-14",
                "significance": 6.2
            }
        ]
    
    async def store_daily_plan(self, plan: Dict):
        """Store content plan in database"""
        async with self.db_pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO daily_plans (date, plan_data, status)
                VALUES ($1, $2, 'active')
            """, datetime.now().date(), json.dumps(plan))
    
    async def trigger_pipelines(self, plan: Dict):
        """Launch all content generation pipelines"""
        tasks = []
        
        # Article pipeline
        for article in plan['content_plan']['articles']:
            tasks.append(self.generate_article(article))
            
        # Carousel pipeline  
        for carousel in plan['content_plan']['carousels']:
            tasks.append(self.generate_carousel(carousel))
            
        # Video pipeline
        for video in plan['content_plan']['videos']:
            tasks.append(self.generate_video(video))
            
        # Execute all pipelines concurrently
        await asyncio.gather(*tasks)
    
    async def generate_article(self, article_spec: Dict):
        """Article + thumbnail generation pipeline"""
        from pipelines.article_pipeline import ArticlePipeline
        pipeline = ArticlePipeline(self.deepseek_client, self.fal_api_key, self.publer_key)
        await pipeline.generate(article_spec)
    
    async def generate_carousel(self, carousel_spec: Dict):
        """Carousel slide generation pipeline"""  
        from pipelines.carousel_pipeline import CarouselPipeline
        pipeline = CarouselPipeline(self.deepseek_client, self.fal_api_key, self.publer_key)
        await pipeline.generate(carousel_spec)
    
    async def generate_video(self, video_spec: Dict):
        """Video content generation pipeline"""
        from pipelines.video_pipeline import VideoPipeline  
        pipeline = VideoPipeline(self.llm_client, self.fal_api_key, self.fish_audio_key, self.vidnoz_key, self.publer_key)
        await pipeline.generate(video_spec)

async def main():
    """Daily orchestrator entry point"""
    orchestrator = ZeusOrchestrator()
    await orchestrator.init_db()
    
    # Run daily planning
    plan = await orchestrator.daily_planning()
    logger.info(f"🚀 Zeus Framework launched with theme: {plan['theme']}")

if __name__ == "__main__":
    asyncio.run(main())