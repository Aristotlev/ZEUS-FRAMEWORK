import asyncio
import logging
import json
from datetime import datetime, timedelta
import os
from typing import Dict, List

from database import DatabaseManager, init_database, ContentStatus
from orchestrator import ZeusOrchestrator
from content_ideas_processor import ContentIdeasProcessor, daily_content_ideas_processing
from enhanced_content_ideas import EnhancedContentIdeasProcessor, enhanced_daily_content_ideas_processing

logger = logging.getLogger(__name__)

class ContentProcessor:
    """Main content processing engine - handles the pipeline queue"""
    
    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager
        self.orchestrator = ZeusOrchestrator()
        self.running = False
        
    async def start(self):
        """Start the content processing loop"""
        logger.info("🚀 Starting Zeus Framework Content Processor")
        
        await self.orchestrator.init_db()
        self.running = True
        
        # Start concurrent processing tasks
        tasks = [
            asyncio.create_task(self.content_processing_loop()),
            asyncio.create_task(self.daily_planning_scheduler()),
            asyncio.create_task(self.content_ideas_scheduler()),  # NEW: Content ideas processing
            asyncio.create_task(self.trade_monitoring_loop()),
            asyncio.create_task(self.cost_monitoring_loop())
        ]
        
        try:
            await asyncio.gather(*tasks)
        except KeyboardInterrupt:
            logger.info("🛑 Shutting down Content Processor")
            self.running = False
    
    async def content_processing_loop(self):
        """Main content processing loop - processes queue items"""
        
        while self.running:
            try:
                # Get next content item from queue
                content_item = await self.db.get_next_content()
                
                if content_item is None:
                    await asyncio.sleep(10)  # No work, wait 10s
                    continue
                
                logger.info(f"🔄 Processing: {content_item['content_type']} - {content_item['title']}")
                
                # Update status to in-progress
                await self.db.update_content_status(
                    content_item['id'], 
                    ContentStatus.WRITTEN
                )
                
                # Route to appropriate pipeline
                success = await self.process_content_item(content_item)
                
                if success:
                    await self.db.update_content_status(
                        content_item['id'],
                        ContentStatus.POSTED
                    )
                    logger.info(f"✅ Completed: {content_item['title']}")
                else:
                    await self.db.update_content_status(
                        content_item['id'],
                        ContentStatus.FAILED,
                        "Pipeline processing failed"
                    )
                    logger.error(f"❌ Failed: {content_item['title']}")
                
            except Exception as e:
                logger.error(f"Content processing error: {e}")
                await asyncio.sleep(30)  # Wait before retry
    
    async def process_content_item(self, item: Dict) -> bool:
        """Route content item to appropriate pipeline"""
        
        try:
            content_type = item['content_type']
            spec_data = item['spec_data']
            
            if content_type == 'article':
                await self.orchestrator.generate_article(spec_data)
                
            elif content_type == 'carousel':
                await self.orchestrator.generate_carousel(spec_data)
                
            elif content_type == 'video':
                await self.orchestrator.generate_video(spec_data)
                
            elif content_type == 'alert':
                # Fast path for breaking news
                from pipelines.carousel_pipeline import BreakingAlertPipeline
                alert_pipeline = BreakingAlertPipeline(
                    self.orchestrator.deepseek_client,
                    self.orchestrator.fal_api_key,
                    self.orchestrator.publer_key
                )
                await alert_pipeline.generate_alert(spec_data)
            
            else:
                logger.error(f"Unknown content type: {content_type}")
                return False
                
            return True
            
        except Exception as e:
            logger.error(f"Pipeline execution failed: {e}")
            return False
    
    async def content_ideas_scheduler(self):
        """Process content ideas daily at 7 AM EST (after daily planning)"""
        
        while self.running:
            try:
                now = datetime.now()
                
                # Check if it's 7 AM EST and we haven't processed ideas today
                if now.hour == 7 and now.minute < 5:  # 5-minute window
                    
                    # Check if we already processed ideas today
                    last_process = await self.get_last_ideas_process_date()
                    if last_process != now.date():
                        logger.info("🧠 Processing daily content ideas...")
                        
                        # Process content ideas (ENHANCED with Google + Market Crawling)
                        results = await enhanced_daily_content_ideas_processing(
                            self.orchestrator.deepseek_client, 
                            self.db
                        )
                        
                        # Queue high-potential ideas as content
                        await self.queue_idea_content(results)
                        
                        logger.info(f"✅ Content ideas processing complete. {results['new_ideas_found']} new, {results['ideas_processed']} processed")
                
                # Sleep until next check (every 5 minutes)
                await asyncio.sleep(300)
                
            except Exception as e:
                logger.error(f"Content ideas processing error: {e}")
                await asyncio.sleep(3600)  # Wait 1 hour on error

    async def queue_idea_content(self, ideas_results: Dict):
        """Queue high-potential content ideas for creation"""
        
        try:
            for result in ideas_results.get('results', []):
                if not result.get('success'):
                    continue
                    
                analysis = result.get('analysis', {})
                strategy = analysis.get('content_strategy', {})
                
                # Only queue high-potential ideas (7+ out of 10)
                if strategy.get('content_potential', 0) >= 7:
                    
                    # Queue based on recommended formats
                    formats = strategy.get('recommended_formats', [])
                    urgency = strategy.get('urgency', 'low')
                    
                    # Set priority based on urgency
                    priority = 8 if urgency == 'high' else 6 if urgency == 'medium' else 4
                    
                    for format_type in formats[:2]:  # Limit to top 2 formats
                        if format_type == 'article':
                            await self.db.queue_content(
                                'article',
                                f"[IDEA] {strategy.get('content_angle', 'User Idea')}",
                                {
                                    'source': 'user_idea',
                                    'idea_id': result['idea_id'],
                                    'analysis': analysis,
                                    'strategy': strategy
                                },
                                priority=priority,
                                budget=1.2  # Slightly higher budget for idea content
                            )
                        
                        elif format_type == 'carousel':
                            await self.db.queue_content(
                                'carousel',
                                f"[IDEA] Carousel: {strategy.get('content_angle', 'User Idea')}",
                                {
                                    'source': 'user_idea',
                                    'idea_id': result['idea_id'],
                                    'analysis': analysis,
                                    'strategy': strategy
                                },
                                priority=priority,
                                budget=2.5
                            )
                        
                        elif format_type == 'video':
                            # Only create video for high-urgency ideas due to cost
                            if urgency == 'high':
                                await self.db.queue_content(
                                    'video',
                                    f"[IDEA] Video: {strategy.get('content_angle', 'User Idea')}",
                                    {
                                        'source': 'user_idea',
                                        'idea_id': result['idea_id'],
                                        'analysis': analysis,
                                        'strategy': strategy,
                                        'budget': 'standard'  # Use standard budget for ideas
                                    },
                                    priority=priority,
                                    budget=3.0
                                )
            
            logger.info(f"💡 Queued content from {len([r for r in ideas_results.get('results', []) if r.get('success')])} processed ideas")
            
        except Exception as e:
            logger.error(f"Failed to queue idea content: {e}")

    async def get_last_ideas_process_date(self):
        """Check when we last processed content ideas"""
        # Implementation depends on database structure - for now mock
        return datetime.now().date() - timedelta(days=1)

    async def daily_planning_scheduler(self):
        """Runs daily content planning at 6 AM EST"""
        
        while self.running:
            try:
                now = datetime.now()
                
                # Check if it's 6 AM EST and we haven't planned today
                if now.hour == 6 and now.minute < 5:  # 5-minute window
                    
                    # Check if we already planned today
                    last_plan = await self.get_last_plan_date()
                    if last_plan != now.date():
                        logger.info("📅 Running daily content planning...")
                        
                        plan = await self.orchestrator.daily_planning()
                        await self.queue_planned_content(plan)
                        
                        logger.info(f"✅ Daily planning complete. Theme: {plan.get('theme', 'Unknown')}")
                
                # Sleep until next check (every 5 minutes)
                await asyncio.sleep(300)
                
            except Exception as e:
                logger.error(f"Daily planning error: {e}")
                await asyncio.sleep(3600)  # Wait 1 hour on error
    
    async def trade_monitoring_loop(self):
        """Monitor for new congressional trades and trigger alerts"""
        
        while self.running:
            try:
                # Check for new high-significance trades
                new_trades = await self.check_new_trades()
                
                for trade in new_trades:
                    if trade['significance_score'] >= 7.0:
                        # Add to high-priority queue
                        await self.db.queue_content(
                            'alert',
                            f"🚨 {trade['politician']} {trade['action']} {trade['ticker']}",
                            trade,
                            priority=9,  # Highest priority
                            budget=0.01  # $0.01 for alerts
                        )
                        
                        logger.info(f"🚨 Queued breaking alert: {trade['politician']} {trade['ticker']}")
                
                await asyncio.sleep(60)  # Check every minute
                
            except Exception as e:
                logger.error(f"Trade monitoring error: {e}")
                await asyncio.sleep(300)
    
    async def cost_monitoring_loop(self):
        """Monitor daily costs and budget limits"""
        
        while self.running:
            try:
                # Check daily costs every hour
                daily_costs = await self.db.get_daily_costs()
                
                # Alert if approaching daily budget ($50)
                if daily_costs['total'] > 40.0:
                    logger.warning(f"💰 Daily costs high: ${daily_costs['total']:.2f}")
                    
                    # Switch to cheap mode if over budget
                    if daily_costs['total'] > 50.0:
                        logger.warning("🚨 Over daily budget! Switching to emergency cheap mode")
                        await self.enable_cheap_mode()
                
                # Log cost summary
                logger.info(f"💰 Daily costs: ${daily_costs['total']:.2f}")
                
                await asyncio.sleep(3600)  # Check hourly
                
            except Exception as e:
                logger.error(f"Cost monitoring error: {e}")
                await asyncio.sleep(1800)
    
    async def queue_planned_content(self, plan: Dict):
        """Queue all planned content from daily planning"""
        
        try:
            # Queue articles
            for article in plan.get('content_plan', {}).get('articles', []):
                await self.db.queue_content(
                    'article',
                    article['title'],
                    article,
                    priority=5,
                    budget=1.0  # $1 per article
                )
            
            # Queue carousels  
            for carousel in plan.get('content_plan', {}).get('carousels', []):
                await self.db.queue_content(
                    'carousel',
                    f"Carousel: {carousel['theme']}",
                    carousel,
                    priority=6,
                    budget=2.0  # $2 per carousel
                )
            
            # Queue videos
            for video in plan.get('content_plan', {}).get('videos', []):
                budget = 15.0 if video.get('budget') == 'premium' else 2.0
                await self.db.queue_content(
                    'video',
                    f"Video: {video['script_concept']}",
                    video,
                    priority=4,
                    budget=budget
                )
            
            logger.info("📋 All planned content queued successfully")
            
        except Exception as e:
            logger.error(f"Failed to queue planned content: {e}")
    
    async def get_last_plan_date(self):
        """Check when we last ran daily planning"""
        # Implementation depends on database structure
        return datetime.now().date() - timedelta(days=1)  # Mock
    
    async def check_new_trades(self) -> List[Dict]:
        """Check for new congressional trades from external APIs"""
        # Mock implementation - replace with real congressional trade APIs
        return []
    
    async def enable_cheap_mode(self):
        """Switch all pipelines to cheapest possible settings"""
        logger.info("🔧 Enabling emergency cheap mode...")
        
        # This would modify pipeline settings globally
        # - Use only Flux Schnell for images
        # - Disable premium video generation
        # - Reduce content volume
        # - Skip non-essential content

class HealthMonitor:
    """System health monitoring and alerts"""
    
    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager
        
    async def generate_daily_report(self) -> Dict:
        """Generate daily performance report"""
        
        # Get analytics
        analytics = await self.db.get_content_analytics(days=1)
        costs = await self.db.get_daily_costs()
        
        report = {
            'date': datetime.now().date().isoformat(),
            'content_stats': analytics,
            'cost_breakdown': costs,
            'performance_metrics': {
                'success_rate': analytics['success_rate'],
                'cost_per_content': costs['total'] / max(analytics['published'], 1),
                'total_published': analytics['published']
            }
        }
        
        logger.info(f"📊 Daily Report: {analytics['published']} published, ${costs['total']:.2f} spent")
        
        return report
        
    async def check_system_health(self) -> Dict:
        """Check overall system health"""
        
        health_status = {
            'timestamp': datetime.now().isoformat(),
            'status': 'healthy',
            'issues': []
        }
        
        # Check for failed content
        analytics = await self.db.get_content_analytics(days=1)
        if analytics['success_rate'] < 0.8:
            health_status['issues'].append(f"Low success rate: {analytics['success_rate']:.1%}")
            health_status['status'] = 'warning'
        
        # Check cost overruns  
        costs = await self.db.get_daily_costs()
        if costs['total'] > 60.0:
            health_status['issues'].append(f"High daily costs: ${costs['total']:.2f}")
            health_status['status'] = 'warning'
        
        return health_status

async def main():
    """Main entry point for Zeus Framework"""
    
    # Initialize logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    try:
        # Initialize database
        db_pool = await init_database()
        db_manager = DatabaseManager(db_pool)
        
        # Start content processor
        processor = ContentProcessor(db_manager)
        await processor.start()
        
    except KeyboardInterrupt:
        logger.info("👋 Zeus Framework shutdown complete")
    except Exception as e:
        logger.error(f"💥 Fatal error: {e}")

if __name__ == "__main__":
    asyncio.run(main())