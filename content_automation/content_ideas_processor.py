"""
Content Ideas Intelligence System
Monitors user-dropped content (images, links, text) and processes them into content pipeline
"""
import os
import json
import hashlib
import asyncio
import aiohttp
import logging
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from openai import OpenAI
import psycopg2
from psycopg2.extras import RealDictCursor

from database import DatabaseManager

@dataclass
class ContentIdea:
    """Represents a user-submitted content idea"""
    id: str
    type: str  # 'image', 'url', 'text', 'file'
    content_path: str
    content_text: Optional[str]
    metadata: Dict[str, Any]
    created_at: datetime
    processed_at: Optional[datetime]
    status: str  # 'pending', 'processing', 'completed', 'failed', 'skipped'
    analysis_result: Optional[Dict[str, Any]]
    generated_content_ids: List[str]  # Links to generated content

class ContentIdeasProcessor:
    """Processes user-dropped content ideas into the content pipeline"""
    
    def __init__(self, llm_client, db_manager: DatabaseManager):
        self.llm_client = llm_client
        self.db = db_manager
        self.ideas_dir = Path("content_ideas")
        self.processed_dir = Path("content_ideas/processed") 
        self.failed_dir = Path("content_ideas/failed")
        
        # Create directories
        self.ideas_dir.mkdir(exist_ok=True)
        self.processed_dir.mkdir(exist_ok=True)
        self.failed_dir.mkdir(exist_ok=True)
        
        # Subdirectories for organization
        (self.ideas_dir / "images").mkdir(exist_ok=True)
        (self.ideas_dir / "screenshots").mkdir(exist_ok=True) 
        (self.ideas_dir / "links").mkdir(exist_ok=True)
        (self.ideas_dir / "notes").mkdir(exist_ok=True)
        (self.ideas_dir / "files").mkdir(exist_ok=True)
        
        self.setup_database()
        
    def setup_database(self):
        """Create content_ideas table"""
        create_table_sql = """
        CREATE TABLE IF NOT EXISTS content_ideas (
            id VARCHAR(64) PRIMARY KEY,
            type VARCHAR(20) NOT NULL,
            content_path TEXT NOT NULL,
            content_text TEXT,
            metadata JSONB DEFAULT '{}',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            processed_at TIMESTAMP,
            status VARCHAR(20) DEFAULT 'pending',
            analysis_result JSONB,
            generated_content_ids TEXT[] DEFAULT ARRAY[]::TEXT[]
        );
        
        CREATE INDEX IF NOT EXISTS idx_content_ideas_status ON content_ideas(status);
        CREATE INDEX IF NOT EXISTS idx_content_ideas_created_at ON content_ideas(created_at);
        CREATE INDEX IF NOT EXISTS idx_content_ideas_type ON content_ideas(type);
        """
        
        with self.db.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(create_table_sql)
            conn.commit()
    
    async def scan_for_new_ideas(self) -> List[ContentIdea]:
        """Scan ideas directory for new content"""
        logging.info("🔍 Scanning for new content ideas...")
        new_ideas = []
        
        # Scan all subdirectories
        for subdir in ['images', 'screenshots', 'links', 'notes', 'files']:
            subdir_path = self.ideas_dir / subdir
            
            for file_path in subdir_path.iterdir():
                if file_path.is_file() and not file_path.name.startswith('.'):
                    # Generate unique ID based on file path and modification time
                    file_stat = file_path.stat()
                    content_hash = hashlib.md5(
                        f"{file_path}:{file_stat.st_mtime}".encode()
                    ).hexdigest()
                    
                    # Check if already processed
                    if not self._is_already_processed(content_hash):
                        idea = await self._create_content_idea(file_path, content_hash)
                        if idea:
                            new_ideas.append(idea)
                            self._save_content_idea(idea)
        
        logging.info(f"📝 Found {len(new_ideas)} new content ideas")
        return new_ideas
    
    def _is_already_processed(self, idea_id: str) -> bool:
        """Check if content idea was already processed"""
        with self.db.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM content_ideas WHERE id = %s", 
                    (idea_id,)
                )
                return cur.fetchone() is not None
    
    async def _create_content_idea(self, file_path: Path, idea_id: str) -> Optional[ContentIdea]:
        """Create ContentIdea from file"""
        try:
            file_stat = file_path.stat()
            subdir = file_path.parent.name
            
            # Determine content type
            content_type = self._determine_content_type(file_path, subdir)
            
            # Extract content text based on type
            content_text = None
            metadata = {
                "file_size": file_stat.st_size,
                "file_ext": file_path.suffix.lower(),
                "original_name": file_path.name
            }
            
            if content_type == 'text' or file_path.suffix.lower() in ['.txt', '.md']:
                content_text = file_path.read_text(encoding='utf-8')
            elif content_type == 'url':
                content_text = await self._extract_url_content(file_path)
            # Images will be processed later with vision analysis
            
            return ContentIdea(
                id=idea_id,
                type=content_type,
                content_path=str(file_path),
                content_text=content_text,
                metadata=metadata,
                created_at=datetime.fromtimestamp(file_stat.st_ctime),
                processed_at=None,
                status='pending',
                analysis_result=None,
                generated_content_ids=[]
            )
            
        except Exception as e:
            logging.error(f"❌ Error creating content idea for {file_path}: {e}")
            return None
    
    def _determine_content_type(self, file_path: Path, subdir: str) -> str:
        """Determine content type from file and location"""
        ext = file_path.suffix.lower()
        
        if subdir in ['images', 'screenshots']:
            return 'image'
        elif subdir == 'links':
            return 'url'
        elif subdir == 'notes' or ext in ['.txt', '.md']:
            return 'text'
        elif ext in ['.jpg', '.jpeg', '.png', '.webp', '.gif']:
            return 'image'
        elif ext in ['.url', '.webloc']:
            return 'url'
        else:
            return 'file'
    
    async def _extract_url_content(self, file_path: Path) -> Optional[str]:
        """Extract URL from various file formats"""
        try:
            content = file_path.read_text(encoding='utf-8')
            
            # Handle .url files (Windows)
            if '[InternetShortcut]' in content:
                for line in content.split('\n'):
                    if line.startswith('URL='):
                        return line.split('URL=', 1)[1].strip()
            
            # Handle .webloc files (macOS) - would need plistlib for full support
            # For now, just treat as text and extract URLs
            import re
            urls = re.findall(r'https?://[^\s<>"]+', content)
            if urls:
                return urls[0]
            
            # Plain text file with URL
            lines = [line.strip() for line in content.split('\n') if line.strip()]
            if lines and (lines[0].startswith('http') or lines[0].startswith('www')):
                return lines[0]
                
            return content.strip()
            
        except Exception as e:
            logging.error(f"❌ Error extracting URL from {file_path}: {e}")
            return None
    
    def _save_content_idea(self, idea: ContentIdea):
        """Save content idea to database"""
        with self.db.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO content_ideas 
                    (id, type, content_path, content_text, metadata, created_at, status)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                """, (
                    idea.id, idea.type, idea.content_path, idea.content_text,
                    json.dumps(idea.metadata), idea.created_at, idea.status
                ))
            conn.commit()
    
    async def process_pending_ideas(self) -> List[Dict[str, Any]]:
        """Process all pending content ideas"""
        logging.info("🧠 Processing pending content ideas...")
        
        # Get all pending ideas
        with self.db.get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT * FROM content_ideas 
                    WHERE status = 'pending' 
                    ORDER BY created_at ASC
                """)
                ideas_data = cur.fetchall()
        
        results = []
        for idea_data in ideas_data:
            try:
                # Update status to processing
                self._update_idea_status(idea_data['id'], 'processing')
                
                # Process the idea
                result = await self._process_single_idea(idea_data)
                results.append(result)
                
                # Update status based on result
                if result['success']:
                    self._update_idea_status(
                        idea_data['id'], 
                        'completed', 
                        analysis_result=result['analysis'],
                        generated_content_ids=result.get('generated_content_ids', [])
                    )
                    # Move file to processed directory
                    await self._move_processed_file(idea_data['content_path'])
                else:
                    self._update_idea_status(idea_data['id'], 'failed')
                    await self._move_failed_file(idea_data['content_path'])
                    
            except Exception as e:
                logging.error(f"❌ Error processing idea {idea_data['id']}: {e}")
                self._update_idea_status(idea_data['id'], 'failed')
                results.append({
                    'idea_id': idea_data['id'],
                    'success': False,
                    'error': str(e)
                })
        
        logging.info(f"✅ Processed {len(results)} content ideas")
        return results
    
    async def _process_single_idea(self, idea_data: Dict) -> Dict[str, Any]:
        """Process a single content idea"""
        idea_type = idea_data['type']
        
        if idea_type == 'image':
            return await self._process_image_idea(idea_data)
        elif idea_type == 'url':
            return await self._process_url_idea(idea_data)
        elif idea_type == 'text':
            return await self._process_text_idea(idea_data)
        else:
            return {
                'idea_id': idea_data['id'],
                'success': False,
                'error': f"Unsupported content type: {idea_type}"
            }
    
    async def _process_image_idea(self, idea_data: Dict) -> Dict[str, Any]:
        """Process image/screenshot using vision analysis"""
        try:
            from vision_analyze import vision_analyze
            
            # Analyze image with AI vision
            analysis_result = vision_analyze(
                image_url=idea_data['content_path'],
                question="Analyze this image for potential financial/trading content. What trading opportunities, market insights, or financial news could this become? Is there any congressional trading data, market charts, or financial information visible?"
            )
            
            # Generate content strategy based on analysis
            strategy = await self._generate_content_strategy(
                f"Image Analysis: {analysis_result}",
                idea_data['metadata']
            )
            
            return {
                'idea_id': idea_data['id'],
                'success': True,
                'analysis': {
                    'type': 'image',
                    'vision_result': analysis_result,
                    'content_strategy': strategy
                },
                'generated_content_ids': []  # Will be populated when content is actually generated
            }
            
        except Exception as e:
            return {
                'idea_id': idea_data['id'],
                'success': False,
                'error': f"Image processing error: {str(e)}"
            }
    
    async def _process_url_idea(self, idea_data: Dict) -> Dict[str, Any]:
        """Process URL by extracting content and analyzing"""
        try:
            url = idea_data['content_text']
            if not url:
                return {
                    'idea_id': idea_data['id'],
                    'success': False,
                    'error': "No URL found in content"
                }
            
            # Extract web content
            web_content = await self._extract_web_content(url)
            if not web_content:
                return {
                    'idea_id': idea_data['id'],
                    'success': False,
                    'error': "Failed to extract web content"
                }
            
            # Analyze for trading/financial relevance
            analysis = await self._analyze_web_content(web_content, url)
            
            # Generate content strategy
            strategy = await self._generate_content_strategy(
                f"Web Content Analysis: {analysis}",
                {**idea_data['metadata'], 'source_url': url}
            )
            
            return {
                'idea_id': idea_data['id'],
                'success': True,
                'analysis': {
                    'type': 'url',
                    'url': url,
                    'content_analysis': analysis,
                    'content_strategy': strategy
                },
                'generated_content_ids': []
            }
            
        except Exception as e:
            return {
                'idea_id': idea_data['id'],
                'success': False,
                'error': f"URL processing error: {str(e)}"
            }
    
    async def _process_text_idea(self, idea_data: Dict) -> Dict[str, Any]:
        """Process text note/idea"""
        try:
            text_content = idea_data['content_text']
            if not text_content or len(text_content.strip()) < 10:
                return {
                    'idea_id': idea_data['id'],
                    'success': False,
                    'error': "Text content too short or empty"
                }
            
            # Analyze text for trading opportunities
            analysis = await self._analyze_text_content(text_content)
            
            # Generate content strategy
            strategy = await self._generate_content_strategy(
                f"Text Idea Analysis: {analysis}",
                idea_data['metadata']
            )
            
            return {
                'idea_id': idea_data['id'],
                'success': True,
                'analysis': {
                    'type': 'text',
                    'content_analysis': analysis,
                    'content_strategy': strategy
                },
                'generated_content_ids': []
            }
            
        except Exception as e:
            return {
                'idea_id': idea_data['id'],
                'success': False,
                'error': f"Text processing error: {str(e)}"
            }
    
    async def _extract_web_content(self, url: str) -> Optional[str]:
        """Extract content from URL"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=30) as response:
                    if response.status == 200:
                        html = await response.text()
                        
                        # Basic content extraction (would be better with BeautifulSoup)
                        import re
                        # Remove scripts and styles
                        clean_html = re.sub(r'<script.*?</script>', '', html, flags=re.DOTALL)
                        clean_html = re.sub(r'<style.*?</style>', '', clean_html, flags=re.DOTALL)
                        # Extract text content
                        text_content = re.sub(r'<[^>]+>', ' ', clean_html)
                        # Clean whitespace
                        text_content = re.sub(r'\s+', ' ', text_content).strip()
                        
                        return text_content[:5000]  # Limit to first 5000 chars
                        
        except Exception as e:
            logging.error(f"❌ Error extracting web content from {url}: {e}")
            return None
    
    async def _analyze_web_content(self, content: str, url: str) -> str:
        """Analyze web content for trading relevance"""
        prompt = f"""
        Analyze this web content for financial/trading opportunities:
        
        URL: {url}
        Content: {content[:2000]}...
        
        Provide analysis on:
        1. Trading opportunities mentioned
        2. Market insights or trends
        3. Congressional trading relevance
        4. Potential content angles
        5. Target audience appeal
        
        Focus on actionable financial information.
        """
        
        response = self.llm_client.chat.completions.create(
            model="deepseek/deepseek-v4",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=1000
        )
        
        return response.choices[0].message.content
    
    async def _analyze_text_content(self, text: str) -> str:
        """Analyze text idea for trading relevance"""
        prompt = f"""
        Analyze this user idea/note for financial content potential:
        
        Content: {text}
        
        Evaluate:
        1. Trading opportunity potential
        2. Market relevance 
        3. Content format suggestions (article/video/carousel)
        4. Target platform recommendations
        5. Research needed to develop this fully
        
        Rate the content potential (1-10) and explain why.
        """
        
        response = self.llm_client.chat.completions.create(
            model="deepseek/deepseek-v4", 
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=800
        )
        
        return response.choices[0].message.content
    
    async def _generate_content_strategy(self, analysis: str, metadata: Dict) -> Dict[str, Any]:
        """Generate content creation strategy based on analysis"""
        prompt = f"""
        Based on this content analysis, create a content strategy:
        
        Analysis: {analysis}
        Metadata: {json.dumps(metadata)}
        
        Provide a JSON response with:
        {{
            "content_potential": 1-10,
            "recommended_formats": ["article", "carousel", "video"],
            "target_platforms": ["twitter", "instagram", "tiktok", "youtube"],
            "content_angle": "specific angle to take",
            "urgency": "high/medium/low",
            "research_needed": ["data points to research"],
            "key_hooks": ["attention-grabbing elements"],
            "estimated_engagement": "high/medium/low"
        }}
        """
        
        response = self.llm_client.chat.completions.create(
            model="deepseek/deepseek-v4",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.4,
            max_tokens=600
        )
        
        try:
            return json.loads(response.choices[0].message.content)
        except:
            return {"error": "Failed to parse strategy JSON"}
    
    def _update_idea_status(self, idea_id: str, status: str, 
                          analysis_result: Optional[Dict] = None,
                          generated_content_ids: Optional[List[str]] = None):
        """Update content idea status in database"""
        with self.db.get_connection() as conn:
            with conn.cursor() as cur:
                update_sql = """
                    UPDATE content_ideas 
                    SET status = %s, processed_at = %s
                """
                params = [status, datetime.utcnow()]
                
                if analysis_result:
                    update_sql += ", analysis_result = %s"
                    params.append(json.dumps(analysis_result))
                
                if generated_content_ids:
                    update_sql += ", generated_content_ids = %s"
                    params.append(generated_content_ids)
                
                update_sql += " WHERE id = %s"
                params.append(idea_id)
                
                cur.execute(update_sql, params)
            conn.commit()
    
    async def _move_processed_file(self, file_path: str):
        """Move processed file to processed directory"""
        try:
            source = Path(file_path)
            if source.exists():
                dest = self.processed_dir / f"{datetime.now().strftime('%Y%m%d')}_{source.name}"
                source.rename(dest)
        except Exception as e:
            logging.error(f"❌ Error moving processed file {file_path}: {e}")
    
    async def _move_failed_file(self, file_path: str):
        """Move failed file to failed directory"""
        try:
            source = Path(file_path)
            if source.exists():
                dest = self.failed_dir / f"{datetime.now().strftime('%Y%m%d')}_{source.name}"
                source.rename(dest)
        except Exception as e:
            logging.error(f"❌ Error moving failed file {file_path}: {e}")
    
    async def get_ideas_summary(self) -> Dict[str, Any]:
        """Get summary of content ideas processing"""
        with self.db.get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # Get counts by status
                cur.execute("""
                    SELECT status, COUNT(*) as count 
                    FROM content_ideas 
                    GROUP BY status
                """)
                status_counts = dict(cur.fetchall())
                
                # Get recent successful ideas
                cur.execute("""
                    SELECT id, type, created_at, analysis_result
                    FROM content_ideas 
                    WHERE status = 'completed' 
                    ORDER BY processed_at DESC 
                    LIMIT 5
                """)
                recent_successes = cur.fetchall()
        
        return {
            'total_ideas': sum(status_counts.values()),
            'status_breakdown': status_counts,
            'recent_successes': [dict(row) for row in recent_successes],
            'processing_rate': status_counts.get('completed', 0) / max(sum(status_counts.values()), 1)
        }

# Daily processor function for cron job
async def daily_content_ideas_processing(llm_client, db_manager: DatabaseManager):
    """Daily processing of content ideas - to be called by scheduler"""
    processor = ContentIdeasProcessor(llm_client, db_manager)
    
    # Scan for new ideas
    new_ideas = await processor.scan_for_new_ideas()
    
    # Process pending ideas
    results = await processor.process_pending_ideas()
    
    # Get summary
    summary = await processor.get_ideas_summary()
    
    logging.info(f"📊 Content Ideas Daily Summary: {summary}")
    
    return {
        'new_ideas_found': len(new_ideas),
        'ideas_processed': len(results),
        'processing_summary': summary,
        'results': results
    }