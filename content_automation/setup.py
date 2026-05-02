#!/usr/bin/env python3
"""
Zeus Framework Setup Script
Initialize database and verify API connections
"""

import asyncio
import os
import sys
from pathlib import Path

# Add project root to path
sys.path.append(str(Path(__file__).parent))

from database import init_database, DatabaseManager
from orchestrator import ZeusOrchestrator
import logging

async def verify_api_connections():
    """Test all API connections"""
    
    print("🔧 Verifying API connections...")
    
    # Test DeepSeek API
    try:
        from openai import OpenAI
        client = OpenAI(
            api_key=os.getenv('DEEPSEEK_API_KEY'),
            base_url="https://api.deepseek.com/v1"
        )
        
        response = client.chat.completions.create(
            model="deepseek-v4",
            messages=[{"role": "user", "content": "Hello"}],
            max_tokens=10
        )
        print("✅ DeepSeek API - Connected")
        
    except Exception as e:
        print(f"❌ DeepSeek API - Failed: {e}")
        return False
    
    # Test fal.ai API
    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            headers = {"Authorization": f"Key {os.getenv('FAL_API_KEY')}"}
            async with session.get("https://fal.run/", headers=headers) as response:
                if response.status == 200:
                    print("✅ fal.ai API - Connected")
                else:
                    print(f"❌ fal.ai API - Failed: Status {response.status}")
                    
    except Exception as e:
        print(f"❌ fal.ai API - Failed: {e}")
    
    # Test ElevenLabs API
    try:
        async with aiohttp.ClientSession() as session:
            headers = {"xi-api-key": os.getenv('ELEVENLABS_API_KEY')}
            async with session.get("https://api.elevenlabs.io/v1/voices", headers=headers) as response:
                if response.status == 200:
                    print("✅ ElevenLabs API - Connected")
                else:
                    print(f"❌ ElevenLabs API - Failed: Status {response.status}")
                    
    except Exception as e:
        print(f"❌ ElevenLabs API - Failed: {e}")
    
    # Test Publer API
    try:
        async with aiohttp.ClientSession() as session:
            headers = {"Authorization": f"Bearer {os.getenv('PUBLER_API_KEY')}"}
            async with session.get("https://api.publer.io/v1/accounts", headers=headers) as response:
                if response.status == 200:
                    print("✅ Publer API - Connected")
                else:
                    print(f"❌ Publer API - Failed: Status {response.status}")
                    
    except Exception as e:
        print(f"❌ Publer API - Failed: {e}")
    
    return True

async def setup_database():
    """Initialize database schema"""
    
    print("🗃️ Setting up database...")
    
    try:
        pool = await init_database()
        print("✅ Database schema created successfully")
        
        # Test database operations
        db = DatabaseManager(pool)
        
        # Add sample congressional trade
        trade_data = {
            'politician': 'Test Politician',
            'ticker': 'TSLA',
            'action': 'BUY',
            'amount_min': 1000000,
            'amount_max': 5000000,
            'filed_date': '2024-01-15',
            'significance_score': 8.5
        }
        
        trade_id = await db.add_trade_alert(trade_data)
        print(f"✅ Test trade added (ID: {trade_id})")
        
        await pool.close()
        
    except Exception as e:
        print(f"❌ Database setup failed: {e}")
        return False
        
    return True

def check_environment():
    """Check if all required environment variables are set"""
    
    print("🔍 Checking environment configuration...")
    
    required_vars = [
        'DEEPSEEK_API_KEY',
        'FAL_API_KEY', 
        'ELEVENLABS_API_KEY',
        'PUBLER_API_KEY',
        'DB_HOST',
        'DB_USER',
        'DB_PASSWORD',
        'DB_NAME'
    ]
    
    missing_vars = []
    for var in required_vars:
        if not os.getenv(var):
            missing_vars.append(var)
    
    if missing_vars:
        print(f"❌ Missing environment variables: {', '.join(missing_vars)}")
        print("💡 Copy .env.example to .env and fill in your API keys")
        return False
    
    print("✅ All required environment variables set")
    return True

def create_directories():
    """Create necessary directories"""
    
    print("📁 Creating directories...")
    
    directories = [
        'logs',
        'temp', 
        'media_cache',
        'backups'
    ]
    
    for dir_name in directories:
        dir_path = Path(__file__).parent / dir_name
        dir_path.mkdir(exist_ok=True)
    
    print("✅ Directories created")

async def main():
    """Main setup routine"""
    
    print("🏛️ Zeus Framework Setup")
    print("=" * 50)
    
    # Load environment variables
    from dotenv import load_dotenv
    load_dotenv()
    
    # Step 1: Check environment
    if not check_environment():
        sys.exit(1)
    
    # Step 2: Create directories
    create_directories()
    
    # Step 3: Setup database
    if not await setup_database():
        sys.exit(1)
        
    # Step 4: Verify API connections
    if not await verify_api_connections():
        print("⚠️ Some API connections failed - check your keys")
        
    print("\n" + "=" * 50)
    print("🚀 Zeus Framework setup complete!")
    print("\nNext steps:")
    print("1. Run: python main.py")
    print("2. Monitor logs for content generation")
    print("3. Check Publer for published posts")

if __name__ == "__main__":
    asyncio.run(main())