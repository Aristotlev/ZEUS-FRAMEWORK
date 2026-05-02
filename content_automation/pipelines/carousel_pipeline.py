import asyncio
import json
import logging
from typing import Dict, List
import aiohttp
import os

logger = logging.getLogger(__name__)

class CarouselPipeline:
    """Instagram/LinkedIn carousel generation pipeline"""
    
    def __init__(self, deepseek_client, fal_api_key: str, publer_key: str):
        self.deepseek_client = deepseek_client
        self.fal_api_key = fal_api_key
        self.publer_key = publer_key
        
    async def generate(self, carousel_spec: Dict):
        """Full carousel pipeline: plan → design → publish"""
        logger.info(f"🎠 Starting carousel: {carousel_spec['theme']}")
        
        try:
            # Step 1: Plan carousel structure
            carousel_plan = await self.plan_carousel(carousel_spec)
            
            # Step 2: Generate slide images in parallel
            slide_urls = await self.generate_slides(carousel_plan)
            
            # Step 3: Create captions for each platform
            captions = await self.generate_captions(carousel_plan, carousel_spec['platform'])
            
            # Step 4: Publish via Publer
            await self.publish_carousel(slide_urls, captions, carousel_spec['platform'])
            
            # Step 5: Store metadata
            await self.store_carousel({
                **carousel_spec,
                'plan': carousel_plan,
                'slide_urls': slide_urls,
                'captions': captions,
                'status': 'published'
            })
            
            logger.info(f"✅ Carousel published: {carousel_spec['theme']}")
            
        except Exception as e:
            logger.error(f"❌ Carousel pipeline failed: {e}")
    
    async def plan_carousel(self, spec: Dict) -> Dict:
        """Plan carousel structure and content"""
        
        planning_prompt = f"""
Plan an Instagram/LinkedIn carousel about: {spec['theme']}
Data focus: {spec['data_focus']}

Create a 10-slide carousel with:
- Slide 1: Hook/title slide
- Slides 2-8: Data points/insights  
- Slide 9: Key takeaway
- Slide 10: CTA/follow prompt

For each slide, provide:
1. Slide number
2. Main message (5-7 words max)
3. Supporting data/stat
4. Visual description for image generation

OUTPUT as JSON:
{{
  "title": "carousel title",
  "slides": [
    {{
      "number": 1,
      "message": "Title Hook",
      "data": "Supporting statistic", 
      "visual_prompt": "Clean title slide with bold text..."
    }}
  ]
}}
"""

        response = self.deepseek_client.chat.completions.create(
            model="deepseek-v4",
            messages=[{"role": "user", "content": planning_prompt}],
            temperature=0.4,
            max_tokens=2000
        )
        
        return json.loads(response.choices[0].message.content)
    
    async def generate_slides(self, carousel_plan: Dict) -> List[str]:
        """Generate all slide images using Ideogram 2.0"""
        
        slide_urls = []
        
        async with aiohttp.ClientSession() as session:
            # Generate slides in parallel (but limit concurrency)
            semaphore = asyncio.Semaphore(3)  # Max 3 concurrent requests
            
            async def generate_single_slide(slide):
                async with semaphore:
                    # Optimize prompt for data visualization
                    optimized_prompt = f"""
Clean infographic slide: {slide['visual_prompt']}
                    
Requirements:
- Square 1080x1080 format
- Modern, minimalist design
- High contrast text
- Financial/business theme  
- Bold typography
- Professional color scheme (dark blue/white/gold)
- Include data: {slide['data']}
- Main text: {slide['message']}
"""
                    
                    payload = {
                        "prompt": optimized_prompt,
                        "aspect_ratio": "ASPECT_1_1",  # Square for Instagram
                        "model": "V_2",
                        "magic_prompt_option": "ON"  # Auto-enhance prompt
                    }
                    
                    headers = {"Authorization": f"Bearer {self.fal_api_key}"}
                    
                    async with session.post(
                        "https://fal.run/fal-ai/ideogram-2",
                        json=payload,
                        headers=headers
                    ) as response:
                        if response.status == 200:
                            result = await response.json()
                            return result['data'][0]['url']
                        else:
                            logger.error(f"Ideogram API error: {response.status}")
                            return None
            
            # Generate all slides
            tasks = [generate_single_slide(slide) for slide in carousel_plan['slides']]
            slide_urls = await asyncio.gather(*tasks)
            
        return [url for url in slide_urls if url is not None]
    
    async def generate_captions(self, carousel_plan: Dict, platform: str) -> Dict:
        """Generate platform-specific captions"""
        
        caption_prompt = f"""
Create engaging captions for this carousel: {carousel_plan['title']}

Platform: {platform}

CAROUSEL CONTENT:
{json.dumps([slide['message'] for slide in carousel_plan['slides']], indent=2)}

Generate captions for:
1. INSTAGRAM: Engaging caption with emojis, hashtags, call-to-action
2. LINKEDIN: Professional tone, thought leadership angle  
3. TWITTER: Thread-style breakdown (if applicable)

Requirements:
- Hook in first line
- Encourage swipe-through 
- Include relevant hashtags (10-15 for IG, 3-5 for LinkedIn)
- Strong CTA at the end

OUTPUT as JSON:
{{
  "instagram": {{"caption": "...", "hashtags": [...]}},
  "linkedin": {{"caption": "...", "hashtags": [...]}},
  "twitter": {{"caption": "...", "hashtags": [...]}}
}}
"""

        response = self.deepseek_client.chat.completions.create(
            model="deepseek-v4", 
            messages=[{"role": "user", "content": caption_prompt}],
            temperature=0.5,
            max_tokens=1500
        )
        
        return json.loads(response.choices[0].message.content)
    
    async def publish_carousel(self, slide_urls: List[str], captions: Dict, platform: str):
        """Publish carousel via Publer API"""
        
        platform_mapping = {
            "instagram": "instagram_account_id",
            "linkedin": "linkedin_account_id", 
            "facebook": "facebook_account_id"
        }
        
        account_id = platform_mapping.get(platform, "instagram_account_id")
        caption_data = captions.get(platform, captions['instagram'])
        
        # Publer carousel post structure
        post_data = {
            "text": f"{caption_data['caption']} {' '.join(['#' + tag for tag in caption_data['hashtags']])}",
            "social_accounts": [account_id],
            "media_urls": slide_urls[:10],  # Max 10 slides
            "scheduled_at": None  # Post immediately
        }
        
        async with aiohttp.ClientSession() as session:
            headers = {"Authorization": f"Bearer {self.publer_key}"}
            
            async with session.post(
                "https://api.publer.io/v1/posts",
                json=post_data,
                headers=headers
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    logger.info(f"✅ Carousel published to {platform}")
                    return result
                else:
                    error = await response.text()
                    logger.error(f"❌ Publer API error ({response.status}): {error}")
                    
    async def store_carousel(self, carousel_data: Dict):
        """Store carousel metadata"""
        # Database storage implementation
        pass


class BreakingAlertPipeline:
    """Ultra-fast breaking news alerts (30 seconds end-to-end)"""
    
    def __init__(self, deepseek_client, fal_api_key: str, publer_key: str):
        self.deepseek_client = deepseek_client
        self.fal_api_key = fal_api_key  
        self.publer_key = publer_key
        
    async def generate_alert(self, trade_data: Dict):
        """Generate and publish breaking alert in <30 seconds"""
        logger.info(f"🚨 Breaking alert: {trade_data['politician']} {trade_data['action']} {trade_data['ticker']}")
        
        start_time = asyncio.get_event_loop().time()
        
        try:
            # Step 1: Analyze significance (2 seconds)
            significance = await self.analyze_trade_significance(trade_data)
            
            if significance['score'] < 7.0:
                logger.info(f"⏭️ Skipping low-impact trade (score: {significance['score']})")
                return
            
            # Step 2: Generate content & chart in parallel (10 seconds)
            content_task = self.generate_alert_content(trade_data, significance)
            chart_task = self.generate_quick_chart(trade_data)
            
            alert_content, chart_url = await asyncio.gather(content_task, chart_task)
            
            # Step 3: Format for platforms (3 seconds)
            formatted = await self.format_alert(alert_content, chart_url)
            
            # Step 4: Publish immediately (5 seconds)
            await self.publish_alert(formatted, chart_url)
            
            elapsed = asyncio.get_event_loop().time() - start_time
            logger.info(f"⚡ Alert published in {elapsed:.1f}s")
            
        except Exception as e:
            logger.error(f"❌ Alert pipeline failed: {e}")
    
    async def analyze_trade_significance(self, trade_data: Dict) -> Dict:
        """Quick significance analysis"""
        
        analysis_prompt = f"""
Analyze this congressional trade for news significance:

Politician: {trade_data['politician']} 
Stock: {trade_data['ticker']}
Action: {trade_data['action']}  
Amount: {trade_data['amount_range']}
Date: {trade_data['filed_date']}

Score 0-10 based on:
- Politician prominence
- Stock popularity 
- Trade timing (earnings, news events)
- Amount size
- Recent patterns

OUTPUT JSON:
{{
  "score": 8.5,
  "reasons": ["Large amount", "Tech stock", "Before earnings"],
  "urgency": "HIGH"
}}
"""

        response = self.deepseek_client.chat.completions.create(
            model="deepseek-v4",
            messages=[{"role": "user", "content": analysis_prompt}],
            temperature=0.2,
            max_tokens=300
        )
        
        return json.loads(response.choices[0].message.content)
    
    async def generate_alert_content(self, trade_data: Dict, significance: Dict) -> str:
        """Generate alert text content"""
        
        content_prompt = f"""
Write a breaking news alert (Twitter style):

{trade_data['politician']} {trade_data['action']} {trade_data['ticker']} 
Amount: {trade_data['amount_range']}
Significance: {significance['score']}/10

Requirements:
- Start with "🚨 BREAKING"
- Under 280 characters
- Include key details
- Create urgency
- Add relevant emojis

Output just the alert text, nothing else.
"""

        response = self.deepseek_client.chat.completions.create(
            model="deepseek-v4",
            messages=[{"role": "user", "content": content_prompt}],
            temperature=0.4,
            max_tokens=100
        )
        
        return response.choices[0].message.content.strip()
    
    async def generate_quick_chart(self, trade_data: Dict) -> str:
        """Generate simple chart using Flux Schnell (fastest)"""
        
        chart_prompt = f"Simple stock chart for {trade_data['ticker']}, showing recent price action with a red arrow marking congressional trade date, clean financial design, dark theme"
        
        async with aiohttp.ClientSession() as session:
            payload = {
                "prompt": chart_prompt,
                "image_size": "square",
                "num_images": 1,
                "num_inference_steps": 1  # Ultra-fast
            }
            
            headers = {"Authorization": f"Key {self.fal_api_key}"}
            
            async with session.post(
                "https://fal.run/fal-ai/flux/schnell",
                json=payload,
                headers=headers
            ) as response:
                result = await response.json()
                return result['images'][0]['url']
    
    async def format_alert(self, content: str, chart_url: str) -> Dict:
        """Format alert for all platforms"""
        
        return {
            "twitter": {"text": content, "media": chart_url},
            "discord": {"text": f"{content}\n\n{chart_url}"},
            "telegram": {"text": content, "photo": chart_url}
        }
    
    async def publish_alert(self, formatted: Dict, chart_url: str):
        """Publish to all platforms immediately"""
        
        async with aiohttp.ClientSession() as session:
            headers = {"Authorization": f"Bearer {self.publer_key}"}
            
            # Twitter
            twitter_post = {
                "text": formatted['twitter']['text'],
                "social_accounts": ["twitter_account_id"],
                "media_urls": [chart_url],
                "scheduled_at": None
            }
            
            async with session.post(
                "https://api.publer.io/v1/posts", 
                json=twitter_post,
                headers=headers
            ) as response:
                if response.status == 200:
                    logger.info("✅ Alert posted to Twitter")
                    
            # Add Discord webhook, Telegram bot, etc.
            # await self.post_to_discord(formatted['discord'])
            # await self.post_to_telegram(formatted['telegram'])