import asyncio
import json
import logging
from typing import Dict, List
import aiohttp
import os
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

class VideoPipeline:
    """Short-form video generation for TikTok/YouTube Shorts/Instagram Reels"""
    
    def __init__(self, llm_client, fal_api_key: str, fish_audio_key: str, vidnoz_key: str, publer_key: str):
        self.llm_client = llm_client
        self.fal_api_key = fal_api_key
        self.fish_audio_key = fish_audio_key
        self.vidnoz_key = vidnoz_key
        self.publer_key = publer_key
        
    async def generate(self, video_spec: Dict):
        """Full video pipeline: script → visuals → audio → edit → publish"""
        logger.info(f"🎬 Starting video: {video_spec['script_concept']}")
        
        try:
            # Step 1: Generate video script
            script = await self.write_video_script(video_spec)
            
            # Step 2: Choose video type based on budget
            if video_spec['budget'] == 'premium':
                video_url = await self.generate_premium_video(script)
            else:
                video_url = await self.generate_cheap_video(script)
                
            # Step 3: Generate voiceover
            audio_url = await self.generate_voiceover(script['narration'])
            
            # Step 4: Combine video + audio
            final_video = await self.compose_video(video_url, audio_url, script)
            
            # Step 5: Format for platforms  
            platform_videos = await self.format_for_platforms(final_video, script)
            
            # Step 6: Publish via Publer
            await self.publish_videos(platform_videos)
            
            logger.info(f"✅ Video published: {video_spec['script_concept']}")
            
        except Exception as e:
            logger.error(f"❌ Video pipeline failed: {e}")
    
    async def write_video_script(self, spec: Dict) -> Dict:
        """Generate 60-second video script"""
        
        script_prompt = f"""
Write a 60-second TikTok/YouTube Shorts script for: {spec['script_concept']}

Type: {spec['type']} (breaking/educational)
Budget: {spec['budget']} (cheap/premium)

Requirements:
- Hook in first 3 seconds
- 150-180 words total (natural speaking pace)
- Include visual cues for editing
- Strong CTA at end
- Viral potential

OUTPUT as JSON:
{{
  "hook": "First 3 seconds text",
  "narration": "Full 60-second script with natural pauses...",
  "visual_cues": [
    {{"timestamp": "0-3s", "visual": "Hook graphic"}},
    {{"timestamp": "3-10s", "visual": "Chart animation"}},
    {{"timestamp": "50-60s", "visual": "CTA screen"}}
  ],
  "captions": ["Key", "phrases", "for", "text", "overlay"],
  "music_mood": "upbeat/dramatic/calm"
}}
"""
        # Script generation using LLM client
        response = await self.llm_client.chat.completions.create(
            model="deepseek-v4",
            messages=[{"role": "user", "content": script_prompt}],
            temperature=0.6,
            max_tokens=1000
        )
        
        return json.loads(response.choices[0].message.content)
    
    async def generate_premium_video(self, script: Dict) -> str:
        """Generate video using Kling 1.6 (expensive but high quality)"""
        
        # Create video prompt from script visual cues
        video_prompt = f"""
Create a 60-second video about congressional trading:

Visual sequence:
{json.dumps(script['visual_cues'], indent=2)}

Style: Professional financial news, clean graphics, smooth transitions
Mood: {script['music_mood']}
Format: Vertical 9:16 for mobile
"""

        # Script generation using LLM client
        response = await self.llm_client.chat.completions.create(
            model="deepseek-v4",
            messages=[{"role": "user", "content": f"""
Convert this video concept into a Kling 1.6 prompt:
{video_prompt}

Output optimized prompt for AI video generation:
"""}],
            temperature=0.3,
            max_tokens=300
        )
        
        optimized_prompt = prompt_response.choices[0].message.content.strip()
        
        # Generate video via fal.ai
        async with aiohttp.ClientSession() as session:
            payload = {
                "prompt": optimized_prompt,
                "aspect_ratio": "9:16",  # Vertical for TikTok
                "duration": "5",  # 5-second clips
                "cfg_scale": 0.5
            }
            
            headers = {"Authorization": f"Key {self.fal_api_key}"}
            
            # Generate multiple 5s clips and stitch together
            clips = []
            for i in range(12):  # 12 x 5s = 60s total
                async with session.post(
                    "https://fal.run/fal-ai/kling-video/v1/standard",
                    json={**payload, "prompt": f"{optimized_prompt} - segment {i+1}"},
                    headers=headers
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        clips.append(result['video']['url'])
            
            # Stitch clips together (implementation needed)
            return await self.stitch_video_clips(clips)
    
    async def generate_cheap_video(self, script: Dict) -> str:
        """Generate video using static images + transitions (cheap alternative)"""
        
        # Generate key frame images
        frame_images = []
        
        for cue in script['visual_cues']:
            # Create image prompt from visual cue
            image_prompt = f"Professional financial graphic: {cue['visual']}, clean design, dark theme, suitable for video frame"
            
            # Generate via Flux Schnell (cheap)
            async with aiohttp.ClientSession() as session:
                payload = {
                    "prompt": image_prompt,
                    "image_size": "portrait_9_16", 
                    "num_images": 1,
                    "num_inference_steps": 4
                }
                
                headers = {"Authorization": f"Key {self.fal_api_key}"}
                
                async with session.post(
                    "https://fal.run/fal-ai/flux/schnell",
                    json=payload,
                    headers=headers
                ) as response:
                    result = await response.json()
                    frame_images.append(result['images'][0]['url'])
        
        # Convert static images to video with transitions
        return await self.images_to_video(frame_images, script)
    
    async def generate_voiceover(self, narration: str) -> str:
        """Generate AI voiceover using Fish Audio API"""
        
        async with aiohttp.ClientSession() as session:
            payload = {
                "text": narration,
                "voice": "rachel",  # Professional female voice
                "format": "mp3",
                "sample_rate": 44100,
                "speed": 1.0
            }
            
            headers = {
                "Authorization": f"Bearer {self.fish_audio_key}",
                "Content-Type": "application/json"
            }
            
            async with session.post(
                "https://api.fish.audio/v1/tts",
                json=payload,
                headers=headers
            ) as response:
                if response.status == 200:
                    # Save audio to temp file
                    audio_data = await response.read()
                    
                    with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as f:
                        f.write(audio_data)
                        return f.name
                else:
                    logger.error(f"Fish Audio API error: {response.status}")
                    return None
    
    async def compose_video(self, video_url: str, audio_path: str, script: Dict) -> str:
        """Combine video + audio + captions using FFmpeg"""
        
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            
            # Download video
            video_path = temp_path / "video.mp4"
            async with aiohttp.ClientSession() as session:
                async with session.get(video_url) as response:
                    with open(video_path, 'wb') as f:
                        f.write(await response.read())
            
            # Generate captions file (SRT format)
            captions_path = await self.generate_captions_file(script['captions'], temp_path)
            
            # Combine video + audio + captions
            output_path = temp_path / "final_video.mp4"
            
            ffmpeg_cmd = [
                'ffmpeg', '-y',
                '-i', str(video_path),
                '-i', audio_path,
                '-vf', f"subtitles={captions_path}:force_style='FontSize=24,PrimaryColour=&Hffffff,BackColour=&H80000000'",
                '-c:v', 'libx264',
                '-c:a', 'aac',
                '-shortest',
                str(output_path)
            ]
            
            process = await asyncio.create_subprocess_exec(
                *ffmpeg_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            stdout, stderr = await process.communicate()
            
            if process.returncode == 0:
                # Upload final video to storage and return URL
                return await self.upload_video(output_path)
            else:
                logger.error(f"FFmpeg error: {stderr.decode()}")
                return None
    
    async def format_for_platforms(self, video_url: str, script: Dict) -> Dict:
        """Create platform-specific versions with different captions"""
        
        caption_prompt = f"""
Create platform-specific captions for this video:
Hook: {script['hook']}
Content: {script['narration'][:200]}...

Generate captions for:
1. TIKTOK: Trending hashtags, casual tone, hook for algorithm
2. YOUTUBE_SHORTS: SEO-optimized title + description  
3. INSTAGRAM_REELS: Engaging caption with story elements

OUTPUT JSON:
{{
  "tiktok": {{"caption": "...", "hashtags": [...]}},
  "youtube": {{"title": "...", "description": "..."}},
  "instagram": {{"caption": "...", "hashtags": [...]}}
}}
"""
        # Script generation using LLM client
        response = await self.llm_client.chat.completions.create(
            model="deepseek-v4",
            messages=[{"role": "user", "content": caption_prompt}],
            temperature=0.5,
            max_tokens=1000
        )
        
        captions = json.loads(response.choices[0].message.content)
        
        return {
            "tiktok": {
                "video_url": video_url,
                "caption": captions['tiktok']['caption'],
                "hashtags": captions['tiktok']['hashtags']
            },
            "youtube": {
                "video_url": video_url,
                "title": captions['youtube']['title'],
                "description": captions['youtube']['description']
            },
            "instagram": {
                "video_url": video_url, 
                "caption": captions['instagram']['caption'],
                "hashtags": captions['instagram']['hashtags']
            }
        }
    
    async def publish_videos(self, platform_videos: Dict):
        """Publish videos via Publer API"""
        
        async with aiohttp.ClientSession() as session:
            headers = {"Authorization": f"Bearer {self.publer_key}"}
            
            for platform, video_data in platform_videos.items():
                account_mapping = {
                    "tiktok": "tiktok_account_id",
                    "youtube": "youtube_account_id", 
                    "instagram": "instagram_account_id"
                }
                
                caption_text = video_data['caption']
                if 'hashtags' in video_data:
                    hashtag_str = ' '.join([f"#{tag}" for tag in video_data['hashtags']])
                    caption_text += f"\n\n{hashtag_str}"
                
                post_data = {
                    "text": caption_text,
                    "social_accounts": [account_mapping[platform]],
                    "media_urls": [video_data['video_url']],
                    "scheduled_at": None
                }
                
                async with session.post(
                    "https://api.publer.io/v1/posts",
                    json=post_data,
                    headers=headers
                ) as response:
                    if response.status == 200:
                        logger.info(f"✅ Video published to {platform}")
                    else:
                        logger.error(f"❌ Publer error for {platform}: {response.status}")
    
    # Helper methods
    async def stitch_video_clips(self, clips: List[str]) -> str:
        """Stitch multiple video clips together"""
        # Implementation needed for video concatenation
        pass
    
    async def images_to_video(self, images: List[str], script: Dict) -> str:
        """Convert static images to video with transitions"""
        # Implementation needed for slideshow-style video creation
        pass
        
    async def generate_captions_file(self, captions: List[str], temp_path: Path) -> str:
        """Generate SRT captions file"""
        # Implementation needed for subtitle generation
        pass
        
    async def upload_video(self, video_path: Path) -> str:
        """Upload video to storage and return URL"""
        # Implementation needed for video upload to CDN/storage
        pass


class AvatarPipeline:
    """Premium avatar video generation using HeyGen"""
    
    def __init__(self, deepseek_client, heygen_key: str, publer_key: str):
        self.deepseek_client = deepseek_client
        self.heygen_key = heygen_key
        self.publer_key = publer_key
        
    async def generate(self, video_spec: Dict):
        """Generate avatar video using HeyGen API"""
        logger.info(f"👤 Starting avatar video: {video_spec['script_concept']}")
        
        try:
            # Step 1: Write presenter script  
            script = await self.write_presenter_script(video_spec)
            
            # Step 2: Generate background slides
            backgrounds = await self.generate_backgrounds(script)
            
            # Step 3: Create HeyGen avatar video
            avatar_video = await self.generate_vidnoz_avatar_video(script, backgrounds)
            
            # Step 4: Format and publish
            await self.publish_avatar_video(avatar_video, script)
            
            logger.info(f"✅ Avatar video published")
            
        except Exception as e:
            logger.error(f"❌ Avatar pipeline failed: {e}")
    
    async def write_presenter_script(self, spec: Dict) -> Dict:
        """Write script optimized for avatar presentation"""
        
        script_prompt = f"""
Write a 90-second presenter script for: {spec['script_concept']}

Requirements:
- Professional news anchor style
- Clear, confident delivery  
- Include natural pauses and emphasis
- Break into 5-6 segments for background changes
- Strong opening and closing

OUTPUT JSON:
{{
  "segments": [
    {{"text": "Opening segment...", "background_theme": "news studio"}},
    {{"text": "Data segment...", "background_theme": "charts and graphs"}}
  ],
  "full_script": "Complete script...",
  "title": "Video title"
}}
"""
        # Script generation using LLM client
        response = await self.llm_client.chat.completions.create(
            model="deepseek-v4",
            messages=[{"role": "user", "content": script_prompt}],
            temperature=0.4,
            max_tokens=1500
        )
        
        return json.loads(response.choices[0].message.content)
    
    async def generate_vidnoz_avatar_video(self, script: Dict, backgrounds: List[str]) -> str:
        """Generate avatar video via Vidnoz API - 60 FREE minutes per month!"""
        
        async with aiohttp.ClientSession() as session:
            payload = {
                "avatar_id": "f004_business_woman_casual",  # Professional female avatar
                "text": script['full_script'],
                "voice_id": "en_us_female_professional",  # Professional voice
                "background": {
                    "type": "image" if backgrounds else "template",
                    "value": backgrounds[0] if backgrounds else "office_modern"
                },
                "aspect_ratio": "9:16",  # Vertical for TikTok/Instagram
                "quality": "hd",
                "speed": 1.0
            }
            
            headers = {
                "Authorization": f"Bearer {self.vidnoz_key}",
                "Content-Type": "application/json"
            }
            
            # Submit video generation job
            async with session.post(
                "https://api.vidnoz.com/v1/avatar/generate",
                json=payload,
                headers=headers
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    job_id = result['job_id']
                    
                    # Poll for completion (Vidnoz is usually faster than HeyGen)
                    return await self.poll_vidnoz_status(job_id)
                else:
                    logger.error(f"Vidnoz API error: {response.status}")
                    return None
    
    async def poll_vidnoz_status(self, job_id: str) -> str:
        """Poll Vidnoz API until avatar video is ready"""
        
        async with aiohttp.ClientSession() as session:
            headers = {"Authorization": f"Bearer {self.vidnoz_key}"}
            
            for _ in range(30):  # Max 5 minutes (Vidnoz is faster)
                await asyncio.sleep(10)
                
                async with session.get(
                    f"https://api.vidnoz.com/v1/avatar/status/{job_id}",
                    headers=headers
                ) as response:
                    result = await response.json()
                    
                    if result['status'] == 'completed':
                        return result['video_url']
                    elif result['status'] == 'failed':
                        logger.error(f"Vidnoz generation failed: {result['error']}")
                        return None
            
            logger.error("Vidnoz video generation timeout")
            return None