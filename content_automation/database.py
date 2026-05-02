import asyncpg
import asyncio
import json
from datetime import datetime
from enum import Enum

class ContentStatus(Enum):
    DATA_DETECTED = "data_detected"
    ANALYZED = "analyzed"  
    CONTENT_PLANNED = "content_planned"
    WRITTEN = "written"
    MEDIA_GENERATED = "media_generated"
    FORMATTED = "formatted"
    QA_PASSED = "qa_passed"
    SCHEDULED = "scheduled"
    POSTED = "posted"
    FAILED = "failed"

async def create_tables(pool):
    """Initialize database schema"""
    
    async with pool.acquire() as conn:
        # Daily content plans
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS daily_plans (
                id SERIAL PRIMARY KEY,
                date DATE UNIQUE NOT NULL,
                plan_data JSONB NOT NULL,
                status VARCHAR(20) DEFAULT 'active',
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        
        # Content queue with state machine
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS content_queue (
                id SERIAL PRIMARY KEY,
                content_type VARCHAR(50) NOT NULL, -- 'article', 'carousel', 'video', 'alert'
                title VARCHAR(255) NOT NULL,
                spec_data JSONB NOT NULL,
                status VARCHAR(30) NOT NULL,
                priority INTEGER DEFAULT 5, -- 1-10, higher = more urgent
                budget_allocated DECIMAL(10,2) DEFAULT 0,
                budget_spent DECIMAL(10,2) DEFAULT 0,
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW(),
                published_at TIMESTAMP,
                error_message TEXT,
                retry_count INTEGER DEFAULT 0
            )
        """)
        
        # Generated content storage
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS generated_content (
                id SERIAL PRIMARY KEY,
                queue_id INTEGER REFERENCES content_queue(id),
                content_text TEXT,
                media_urls JSONB, -- Array of image/video URLs
                platform_versions JSONB, -- Platform-specific formats
                metadata JSONB, -- Analytics, costs, etc.
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        
        # Congressional trades data
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS congressional_trades (
                id SERIAL PRIMARY KEY,
                politician VARCHAR(100) NOT NULL,
                ticker VARCHAR(10) NOT NULL,
                action VARCHAR(10) NOT NULL, -- BUY, SELL
                amount_min DECIMAL(15,2),
                amount_max DECIMAL(15,2),
                filed_date DATE NOT NULL,
                transaction_date DATE,
                significance_score DECIMAL(3,1), -- 0.0 - 10.0
                processed BOOLEAN DEFAULT FALSE,
                raw_data JSONB,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        
        # Publishing analytics
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS post_analytics (
                id SERIAL PRIMARY KEY,
                content_id INTEGER REFERENCES generated_content(id),
                platform VARCHAR(30) NOT NULL,
                post_url VARCHAR(500),
                engagement_data JSONB, -- likes, shares, comments
                reach_metrics JSONB, -- impressions, reach
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW()
            )
        """)
        
        # Cost tracking
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS cost_tracking (
                id SERIAL PRIMARY KEY,
                date DATE NOT NULL,
                service VARCHAR(50) NOT NULL, -- 'deepseek', 'fal', 'elevenlabs', 'publer'
                operation VARCHAR(100) NOT NULL, -- 'text_generation', 'image_generation', etc.
                quantity INTEGER NOT NULL,
                cost_usd DECIMAL(10,4) NOT NULL,
                metadata JSONB,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        
        # Create indexes for performance
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_content_queue_status ON content_queue(status);
            CREATE INDEX IF NOT EXISTS idx_content_queue_priority ON content_queue(priority DESC);
            CREATE INDEX IF NOT EXISTS idx_trades_processed ON congressional_trades(processed, significance_score DESC);
            CREATE INDEX IF NOT EXISTS idx_daily_plans_date ON daily_plans(date);
        """)

class DatabaseManager:
    """Database operations for Zeus Framework"""
    
    def __init__(self, pool):
        self.pool = pool
    
    async def add_trade_alert(self, trade_data: dict) -> int:
        """Add new congressional trade for processing"""
        
        async with self.pool.acquire() as conn:
            trade_id = await conn.fetchval("""
                INSERT INTO congressional_trades 
                (politician, ticker, action, amount_min, amount_max, filed_date, 
                 transaction_date, significance_score, raw_data)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                RETURNING id
            """, 
                trade_data['politician'],
                trade_data['ticker'], 
                trade_data['action'],
                trade_data.get('amount_min'),
                trade_data.get('amount_max'),
                trade_data['filed_date'],
                trade_data.get('transaction_date'),
                trade_data.get('significance_score'),
                json.dumps(trade_data)
            )
            
            # Add to content queue if significant enough
            if trade_data.get('significance_score', 0) >= 7.0:
                await conn.execute("""
                    INSERT INTO content_queue 
                    (content_type, title, spec_data, status, priority)
                    VALUES ($1, $2, $3, $4, $5)
                """,
                    'alert',
                    f"{trade_data['politician']} {trade_data['action']} {trade_data['ticker']}",
                    json.dumps(trade_data),
                    ContentStatus.DATA_DETECTED.value,
                    9  # High priority for alerts
                )
            
            return trade_id
    
    async def queue_content(self, content_type: str, title: str, spec_data: dict, 
                           priority: int = 5, budget: float = 0) -> int:
        """Add content to generation queue"""
        
        async with self.pool.acquire() as conn:
            return await conn.fetchval("""
                INSERT INTO content_queue 
                (content_type, title, spec_data, status, priority, budget_allocated)
                VALUES ($1, $2, $3, $4, $5, $6)
                RETURNING id
            """,
                content_type, title, json.dumps(spec_data), 
                ContentStatus.CONTENT_PLANNED.value, priority, budget
            )
    
    async def get_next_content(self) -> dict:
        """Get highest priority content from queue"""
        
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT id, content_type, title, spec_data, status, priority, budget_allocated
                FROM content_queue 
                WHERE status IN ($1, $2, $3)
                ORDER BY priority DESC, created_at ASC
                LIMIT 1
            """, 
                ContentStatus.CONTENT_PLANNED.value,
                ContentStatus.DATA_DETECTED.value,
                ContentStatus.FAILED.value  # Include failed for retry
            )
            
            if row:
                return {
                    'id': row['id'],
                    'content_type': row['content_type'],
                    'title': row['title'], 
                    'spec_data': json.loads(row['spec_data']),
                    'status': row['status'],
                    'priority': row['priority'],
                    'budget_allocated': float(row['budget_allocated'])
                }
            return None
    
    async def update_content_status(self, content_id: int, status: ContentStatus, 
                                  error_message: str = None):
        """Update content status in pipeline"""
        
        async with self.pool.acquire() as conn:
            if error_message:
                await conn.execute("""
                    UPDATE content_queue 
                    SET status = $1, error_message = $2, retry_count = retry_count + 1,
                        updated_at = NOW()
                    WHERE id = $3
                """, status.value, error_message, content_id)
            else:
                await conn.execute("""
                    UPDATE content_queue 
                    SET status = $1, updated_at = NOW()
                    WHERE id = $2
                """, status.value, content_id)
    
    async def store_generated_content(self, queue_id: int, content_text: str,
                                    media_urls: list, platform_versions: dict,
                                    metadata: dict = None) -> int:
        """Store generated content results"""
        
        async with self.pool.acquire() as conn:
            content_id = await conn.fetchval("""
                INSERT INTO generated_content 
                (queue_id, content_text, media_urls, platform_versions, metadata)
                VALUES ($1, $2, $3, $4, $5)
                RETURNING id
            """,
                queue_id, content_text, json.dumps(media_urls),
                json.dumps(platform_versions), json.dumps(metadata or {})
            )
            
            # Update queue status
            await self.update_content_status(queue_id, ContentStatus.FORMATTED)
            
            return content_id
    
    async def log_cost(self, service: str, operation: str, quantity: int, 
                      cost_usd: float, metadata: dict = None):
        """Track API costs for budget monitoring"""
        
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO cost_tracking 
                (date, service, operation, quantity, cost_usd, metadata)
                VALUES ($1, $2, $3, $4, $5, $6)
            """,
                datetime.now().date(), service, operation, quantity, 
                cost_usd, json.dumps(metadata or {})
            )
    
    async def get_daily_costs(self, date=None) -> dict:
        """Get cost breakdown for a specific date"""
        
        if date is None:
            date = datetime.now().date()
            
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT service, SUM(cost_usd) as total_cost, COUNT(*) as operations
                FROM cost_tracking 
                WHERE date = $1
                GROUP BY service
                ORDER BY total_cost DESC
            """, date)
            
            return {
                'date': date,
                'by_service': {row['service']: {
                    'cost': float(row['total_cost']),
                    'operations': row['operations']
                } for row in rows},
                'total': sum(float(row['total_cost']) for row in rows)
            }
    
    async def get_content_analytics(self, days: int = 7) -> dict:
        """Get content performance analytics"""
        
        async with self.pool.acquire() as conn:
            # Content production stats
            production_stats = await conn.fetchrow("""
                SELECT 
                    COUNT(*) as total_content,
                    COUNT(CASE WHEN status = 'posted' THEN 1 END) as published,
                    COUNT(CASE WHEN status = 'failed' THEN 1 END) as failed,
                    AVG(budget_spent) as avg_cost
                FROM content_queue 
                WHERE created_at >= NOW() - INTERVAL '%s days'
            """ % days)
            
            # Content type breakdown
            type_breakdown = await conn.fetch("""
                SELECT content_type, COUNT(*) as count
                FROM content_queue
                WHERE created_at >= NOW() - INTERVAL '%s days'
                GROUP BY content_type
            """ % days)
            
            return {
                'total_content': production_stats['total_content'],
                'published': production_stats['published'],
                'failed': production_stats['failed'], 
                'success_rate': production_stats['published'] / max(production_stats['total_content'], 1),
                'avg_cost': float(production_stats['avg_cost'] or 0),
                'by_type': {row['content_type']: row['count'] for row in type_breakdown}
            }

# Database initialization
async def init_database():
    """Initialize database connection and schema"""
    
    pool = await asyncpg.create_pool(
        host=os.getenv('DB_HOST', 'localhost'),
        port=int(os.getenv('DB_PORT', 5432)),
        user=os.getenv('DB_USER', 'postgres'), 
        password=os.getenv('DB_PASSWORD'),
        database=os.getenv('DB_NAME', 'zeus_content'),
        min_size=5,
        max_size=20
    )
    
    await create_tables(pool)
    return pool

if __name__ == "__main__":
    import os
    asyncio.run(init_database())