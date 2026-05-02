import asyncio
import json
import logging
from typing import Dict
import aiohttp
import os
from datetime import datetime
import asyncpg

logger = logging.getLogger(__name__)

class ArticlePipeline:
    """Long-form article generation with hero images and thumbnails"""
    
    def __init__(self, llm_client, fal_api_key: str, publer_key: str):
        self.llm_client = llm_client  # OpenRouter client
        self.fal_api_key = fal_api_key
        self.publer_key = publer_key
        
    async def generate(self, article_spec: Dict):
        """Full article pipeline: write → images → publish"""
        logger.info(f"📝 Starting article: {article_spec['title']}")
        
        try:
            # Step 1: Generate article content
            article_content = await self.write_article(article_spec)
            
            # Step 2: Generate images in parallel
            image_tasks = [
                self.generate_hero_image(article_spec['title']),
                self.generate_thumbnail(article_spec['title']), 
                self.generate_chart_images(article_content)
            ]
            hero_url, thumb_url, chart_urls = await asyncio.gather(*image_tasks)
            
            # Step 3: Format for platforms
            formatted_content = await self.format_for_platforms(
                article_content, hero_url, thumb_url, chart_urls
            )
            
            # Step 4: Publish via Publer
            await self.publish_content(formatted_content)
            
            # Step 5: Store in database
            await self.store_article({
                **article_spec,
                'content': article_content,
                'hero_image': hero_url,
                'thumbnail': thumb_url,
                'charts': chart_urls,
                'status': 'published'
            })
            
            logger.info(f"✅ Article published: {article_spec['title']}")
            
        except Exception as e:
            logger.error(f"❌ Article pipeline failed: {e}")
            await self.mark_failed(article_spec, str(e))
    
    async def write_article(self, spec: Dict) -> str:
        """Generate 2000-word article using DeepSeek V4"""
        
        writing_prompt = f"""
Write a comprehensive 2000-word article about: {spec['title']}

ANGLE: {spec['angle']}
URGENCY: {spec['urgency']}

REQUIREMENTS:
- Hook readers in first paragraph
- Include data points and statistics  
- Explain market implications
- Add actionable insights for readers
- SEO-optimized with natural keyword usage
- Professional but accessible tone
- Include 3-4 subheadings for chart placement

TARGET AUDIENCE: Retail investors interested in congressional trading patterns

OUTPUT FORMAT:
# {spec['title']}

[Article content with clear section breaks for image insertion]

META_DESCRIPTION: [150 char SEO description]
TAGS: [5 relevant tags]
"""

        response = self.llm_client.chat.completions.create(
            model="deepseek/deepseek-v4",
            messages=[{"role": "user", "content": writing_prompt}],
            temperature=0.4,
            max_tokens=4000
        )
        
        return response.choices[0].message.content
    
    async def generate_hero_image(self, title: str) -> str:
        """Generate hero image using Flux Pro via fal.ai"""
        
        # First, create optimized prompt
        prompt_response = self.deepseek_client.chat.completions.create(
            model="deepseek-v4", 
            messages=[{"role": "user", "content": f"""
Create a Flux Pro image prompt for article: "{title}"

Requirements:
- Professional financial/business setting
- Dark, modern aesthetic matching Bloomberg/Reuters
- Clear, uncluttered composition  
- Suitable for article hero image
- No text overlays

Output only the optimized prompt, nothing else.
"""}],
            temperature=0.3,
            max_tokens=200
        )
        
        optimized_prompt = prompt_response.choices[0].message.content.strip()
        
        # Generate image via fal.ai
        async with aiohttp.ClientSession() as session:
            payload = {
                "prompt": optimized_prompt,
                "image_size": "landscape_16_9", 
                "num_images": 1,
                "enable_safety_checker": True
            }
            
            headers = {"Authorization": f"Key {self.fal_api_key}"}
            
            async with session.post(
                "https://fal.run/fal-ai/flux-pro",
                json=payload,
                headers=headers
            ) as response:
                result = await response.json()
                return result['images'][0]['url']
    
    async def generate_thumbnail(self, title: str) -> str:
        """Generate YouTube/social thumbnail using Flux Schnell"""
        
        # Optimized for thumbnails - bold, eye-catching
        prompt_response = self.deepseek_client.chat.completions.create(
            model="deepseek-v4",
            messages=[{"role": "user", "content": f"""
Create a thumbnail image prompt for: "{title}"

Requirements:
- Bold, high-contrast design
- Eye-catching for social media
- Clear focal point
- Dramatic lighting
- Suitable for small display sizes  
- Financial/trading theme

Output only the optimized prompt.
"""}],
            temperature=0.3,
            max_tokens=150
        )
        
        optimized_prompt = prompt_response.choices[0].message.content.strip()
        
        async with aiohttp.ClientSession() as session:
            payload = {
                "prompt": optimized_prompt,
                "image_size": "landscape_16_9",
                "num_images": 1,
                "num_inference_steps": 4  # Schnell = fast + cheap
            }
            
            headers = {"Authorization": f"Key {self.fal_api_key}"}
            
            async with session.post(
                "https://fal.run/fal-ai/flux/schnell", 
                json=payload,
                headers=headers
            ) as response:
                result = await response.json()
                return result['images'][0]['url']
    
    async def generate_chart_images(self, article_content: str) -> list:
        """Generate 3 chart images for article sections"""
        
        # Extract chart needs from article
        chart_prompt = f"""
Based on this article content, suggest 3 data visualization charts that would enhance reader understanding:

{article_content[:1000]}...

For each chart, provide:
1. Chart type (bar, line, pie, etc.)
2. Data focus (what should be visualized)  
3. Visual description for image generation

Output as JSON array:
[{{"type": "line", "focus": "stock performance", "prompt": "..."}}]
"""

        response = self.llm_client.chat.completions.create(
            model="deepseek/deepseek-v4",
            messages=[{"role": "user", "content": chart_prompt}],
            temperature=0.3,
            max_tokens=800
        )
        
        chart_specs = json.loads(response.choices[0].message.content)
        
        # Generate each chart image
        chart_urls = []
        async with aiohttp.ClientSession() as session:
            for chart in chart_specs[:3]:  # Limit to 3 charts
                payload = {
                    "prompt": f"Clean financial data chart: {chart['prompt']}, professional design, dark theme, clear labels",
                    "image_size": "square",
                    "num_images": 1,
                    "num_inference_steps": 4
                }
                
                headers = {"Authorization": f"Key {self.fal_api_key}"}
                
                async with session.post(
                    "https://fal.run/fal-ai/flux/schnell",
                    json=payload, 
                    headers=headers
                ) as resp:
                    result = await resp.json()
                    chart_urls.append(result['images'][0]['url'])
                    
        return chart_urls
    
    async def format_for_platforms(self, content: str, hero_url: str, 
                                 thumb_url: str, chart_urls: list) -> Dict:
        """Format article for different platforms"""
        
        formatting_prompt = f"""
Format this article for multiple platforms:

CONTENT: {content[:1500]}...

Generate platform-specific versions:

1. BLOG POST (full article with image placements)
2. LINKEDIN POST (summary + link)  
3. TWITTER THREAD (key points in tweet format)
4. FACEBOOK POST (engaging summary)

OUTPUT as JSON:
{{
  "blog": {{"title": "...", "content": "...", "meta_description": "..."}},
  "linkedin": {{"post": "...", "hashtags": [...]}},
  "twitter": {{"thread": [...], "hashtags": [...]}}, 
  "facebook": {{"post": "...", "hashtags": [...]}}
}}
"""

        response = self.llm_client.chat.completions.create(
            model="deepseek/deepseek-v4",
            messages=[{"role": "user", "content": formatting_prompt}],
            temperature=0.3,
            max_tokens=2000
        )
        
        formatted = json.loads(response.choices[0].message.content)
        
        # Add image URLs to formatted content
        formatted['images'] = {
            'hero': hero_url,
            'thumbnail': thumb_url, 
            'charts': chart_urls
        }
        
        return formatted
    
    async def publish_content(self, formatted_content: Dict):
        """Publish to platforms via Publer API"""
        
        publer_posts = []
        
        # LinkedIn post
        publer_posts.append({
            "text": formatted_content['linkedin']['post'],
            "social_accounts": ["linkedin_account_id"],
            "media_urls": [formatted_content['images']['hero']],
            "scheduled_at": None  # Immediate
        })
        
        # Twitter thread
        for i, tweet in enumerate(formatted_content['twitter']['thread']):
            publer_posts.append({
                "text": tweet,
                "social_accounts": ["twitter_account_id"], 
                "media_urls": [formatted_content['images']['thumbnail']] if i == 0 else [],
                "scheduled_at": None
            })
        
        # Facebook post  
        publer_posts.append({
            "text": formatted_content['facebook']['post'],
            "social_accounts": ["facebook_account_id"],
            "media_urls": [formatted_content['images']['hero']], 
            "scheduled_at": None
        })
        
        # Submit to Publer
        async with aiohttp.ClientSession() as session:
            headers = {"Authorization": f"Bearer {self.publer_key}"}
            
            for post in publer_posts:
                async with session.post(
                    "https://api.publer.io/v1/posts",
                    json=post,
                    headers=headers
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        logger.info(f"✅ Published to {post['social_accounts']}")
                    else:
                        logger.error(f"❌ Publer API error: {response.status}")
    
    async def store_article(self, article_data: Dict):
        """Store article metadata in database"""
        # Implementation depends on your database setup
        pass
    
    async def mark_failed(self, spec: Dict, error: str):
        """Mark article as failed in database"""
        logger.error(f"Article failed: {spec['title']} - {error}")
        # Store failure in database for retry logic