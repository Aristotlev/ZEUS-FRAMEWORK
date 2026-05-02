#!/usr/bin/env python3
"""
Zeus Framework Real-time Monitor
Dashboard for tracking content generation, costs, and system health
"""

import asyncio
import json
import os
from datetime import datetime, timedelta
from typing import Dict, List
import sys
from pathlib import Path

# Add project root to path
sys.path.append(str(Path(__file__).parent))

from database import init_database, DatabaseManager
from main import HealthMonitor

class ZeusMonitor:
    """Real-time monitoring dashboard"""
    
    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager
        self.health_monitor = HealthMonitor(db_manager)
        
    async def start_dashboard(self):
        """Start the monitoring dashboard"""
        
        print("🔍 Zeus Framework Monitor")
        print("=" * 50)
        
        while True:
            try:
                # Clear screen  
                os.system('clear' if os.name == 'posix' else 'cls')
                
                print("🏛️ ZEUS FRAMEWORK - LIVE DASHBOARD")
                print(f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
                print("=" * 60)
                
                # System health
                health = await self.health_monitor.check_system_health()
                status_color = "🟢" if health['status'] == 'healthy' else "🟡"
                print(f"System Status: {status_color} {health['status'].upper()}")
                
                if health['issues']:
                    for issue in health['issues']:
                        print(f"  ⚠️ {issue}")
                
                print()
                
                # Daily analytics
                analytics = await self.db.get_content_analytics(days=1)
                print("📊 TODAY'S CONTENT PRODUCTION")
                print("-" * 30)
                print(f"Published: {analytics['published']}")
                print(f"Failed: {analytics['failed']}")
                print(f"Success Rate: {analytics['success_rate']:.1%}")
                
                print("\nBy Content Type:")
                for content_type, count in analytics['by_type'].items():
                    print(f"  {content_type}: {count}")
                
                print()
                
                # Cost tracking
                costs = await self.db.get_daily_costs()
                print("💰 TODAY'S COSTS")
                print("-" * 15)
                print(f"Total: ${costs['total']:.2f}")
                
                for service, data in costs['by_service'].items():
                    print(f"  {service}: ${data['cost']:.2f} ({data['operations']} ops)")
                
                print()
                
                # Queue status
                queue_status = await self.get_queue_status()
                print("📋 CONTENT QUEUE")
                print("-" * 16)
                print(f"Pending: {queue_status['pending']}")
                print(f"In Progress: {queue_status['in_progress']}")
                print(f"Failed (retry): {queue_status['failed']}")
                
                print()
                
                # Recent activity
                recent = await self.get_recent_activity()
                print("🕐 RECENT ACTIVITY")
                print("-" * 17)
                for activity in recent[:5]:
                    status_emoji = "✅" if activity['status'] == 'posted' else "⏳" if activity['status'] in ['written', 'formatted'] else "❌"
                    print(f"{status_emoji} {activity['title'][:40]}... ({activity['status']})")
                
                print()
                print("Press Ctrl+C to exit")
                
                # Refresh every 30 seconds
                await asyncio.sleep(30)
                
            except KeyboardInterrupt:
                print("\n👋 Monitor stopped")
                break
            except Exception as e:
                print(f"Monitor error: {e}")
                await asyncio.sleep(10)
    
    async def get_queue_status(self) -> Dict:
        """Get current queue status"""
        
        async with self.db.pool.acquire() as conn:
            result = await conn.fetchrow("""
                SELECT 
                    COUNT(CASE WHEN status IN ('content_planned', 'data_detected') THEN 1 END) as pending,
                    COUNT(CASE WHEN status IN ('written', 'media_generated', 'formatted') THEN 1 END) as in_progress,
                    COUNT(CASE WHEN status = 'failed' AND retry_count < 3 THEN 1 END) as failed
                FROM content_queue
                WHERE created_at >= NOW() - INTERVAL '1 day'
            """)
            
            return {
                'pending': result['pending'],
                'in_progress': result['in_progress'], 
                'failed': result['failed']
            }
    
    async def get_recent_activity(self) -> List[Dict]:
        """Get recent content activity"""
        
        async with self.db.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT title, status, updated_at, content_type
                FROM content_queue
                ORDER BY updated_at DESC
                LIMIT 10
            """)
            
            return [
                {
                    'title': row['title'],
                    'status': row['status'],
                    'updated_at': row['updated_at'],
                    'content_type': row['content_type']
                }
                for row in rows
            ]

class AlertSystem:
    """Alert system for critical events"""
    
    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager
        
    async def check_alerts(self):
        """Check for alert conditions"""
        
        alerts = []
        
        # Check cost overruns
        costs = await self.db.get_daily_costs()
        if costs['total'] > 60.0:
            alerts.append({
                'type': 'cost_overrun',
                'message': f"Daily costs exceeded budget: ${costs['total']:.2f}",
                'severity': 'high'
            })
        
        # Check failure rates
        analytics = await self.db.get_content_analytics(days=1)
        if analytics['success_rate'] < 0.7:
            alerts.append({
                'type': 'high_failure_rate', 
                'message': f"Low success rate: {analytics['success_rate']:.1%}",
                'severity': 'medium'
            })
        
        # Check queue backup
        queue_status = await self.get_queue_backup()
        if queue_status > 20:
            alerts.append({
                'type': 'queue_backup',
                'message': f"Queue backup: {queue_status} items pending",
                'severity': 'medium'
            })
        
        return alerts
    
    async def get_queue_backup(self) -> int:
        """Check if queue is backing up"""
        
        async with self.db.pool.acquire() as conn:
            result = await conn.fetchval("""
                SELECT COUNT(*) 
                FROM content_queue
                WHERE status IN ('content_planned', 'data_detected')
                AND created_at < NOW() - INTERVAL '2 hours'
            """)
            
            return result or 0

async def main():
    """Main monitoring entry point"""
    
    # Load environment
    from dotenv import load_dotenv
    load_dotenv()
    
    try:
        # Initialize database
        db_pool = await init_database()
        db_manager = DatabaseManager(db_pool)
        
        # Start monitor
        monitor = ZeusMonitor(db_manager)
        await monitor.start_dashboard()
        
    except KeyboardInterrupt:
        print("👋 Monitor shutdown")
    except Exception as e:
        print(f"💥 Monitor error: {e}")

if __name__ == "__main__":
    asyncio.run(main())