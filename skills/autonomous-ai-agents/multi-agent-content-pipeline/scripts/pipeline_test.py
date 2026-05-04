#!/usr/bin/env python3
"""
Zeus Content Pipeline — On-Demand Test Script
Generates: text (OpenRouter) + image (Replicate Flux) + social post → Publer
Tests the full pipeline with a single run. Requires .env with keys.

Usage:
    export $(grep -v '^#' ~/.hermes/.env | xargs)
    python3 pipeline_test.py --topic "Bitcoin surges past 100K"

Required env vars:
    OPENROUTER_API_KEY — LLM text generation
    REPLICATE_API_KEY  — Image/video generation
    PUBLER_API_KEY     — Social media distribution

Publer API reference: ~/.hermes/skills/.../references/publer-api-reference.md
"""

import os, sys, json, requests, logging, argparse, time
from datetime import datetime, timedelta

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger("zeus-test")

OPENROUTER_KEY = os.getenv("OPENROUTER_API_KEY", "")
REPLICATE_KEY = os.getenv("REPLICATE_API_KEY", "")
PUBLER_KEY = os.getenv("PUBLER_API_KEY", "")

# Use Gemini Flash — DeepSeek V4 Pro returns null via OpenRouter
ORCHESTRATOR_MODEL = "google/gemini-2.5-flash"

# Publer API (correct as of May 2026)
PUBLER_BASE = "https://app.publer.com/api/v1"
PUBLER_AUTH_HEADER = f"Bearer-API {PUBLER_KEY}"
WORKSPACE_ID = "your-workspace-id"  # ContentPipeline workspace


class ReplicateClient:
    """Unified Replicate client for all media generation."""
    BASE_URL = "https://api.replicate.com/v1"
    
    def __init__(self, api_key=None):
        self.api_key = api_key or REPLICATE_KEY
        self.headers = {
            "Authorization": f"Token {self.api_key}",
            "Content-Type": "application/json"
        }
    
    def _predict(self, model: str, input_data: dict, timeout=120):
        url = f"{self.BASE_URL}/models/{model}/predictions"
        r = requests.post(url, json={"input": input_data}, headers=self.headers)
        if r.status_code != 201:
            log.error(f"Prediction failed: {r.status_code} {r.text[:300]}")
            return None
        prediction = r.json()
        while prediction["status"] not in ("succeeded", "failed", "canceled"):
            time.sleep(2)
            r = requests.get(prediction["urls"]["get"], headers=self.headers)
            prediction = r.json()
            if prediction["status"] == "processing":
                log.info(f"  ⏳ {prediction.get('logs', '')[-80:]}")
        if prediction["status"] == "succeeded":
            return prediction["output"]
        log.error(f"Prediction failed: {prediction.get('error', 'unknown')}")
        return None
    
    def generate_image(self, prompt: str, width=1024, height=1024, model="flux-schnell"):
        models = {
            "flux-schnell": "black-forest-labs/flux-schnell",
            "flux-pro": "black-forest-labs/flux-pro",
        }
        log.info(f"🎨 Generating image: {prompt[:80]}...")
        output = self._predict(models.get(model, models["flux-schnell"]), {
            "prompt": prompt, "width": width, "height": height,
            "num_outputs": 1, "aspect_ratio": "1:1", "output_format": "png",
        })
        return output[0] if output and isinstance(output, list) else output


class PublerClient:
    """Publer social media distribution — correct API as of May 2026."""
    
    def __init__(self):
        self.headers = {
            "Authorization": PUBLER_AUTH_HEADER,
            "Publer-Workspace-Id": WORKSPACE_ID,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
    
    def upload_media(self, image_url: str) -> str:
        """Upload external image to Publer, return media_id."""
        log.info(f"📤 Uploading image to Publer...")
        img_data = requests.get(image_url, timeout=30).content
        r = requests.post(f"{PUBLER_BASE}/media",
            headers={"Authorization": PUBLER_AUTH_HEADER, "Publer-Workspace-Id": WORKSPACE_ID},
            files={"file": ("image.png", img_data, "image/png")},
            timeout=15)
        if r.status_code == 200:
            media_id = r.json()["id"]
            log.info(f"  ✅ Media ID: {media_id}")
            return media_id
        log.error(f"  ❌ Upload failed: {r.status_code} {r.text[:200]}")
        return None
    
    def schedule_post(self, provider: str, account_id: str, text: str, 
                      media_id: str = None, schedule_minutes: int = 2) -> dict:
        """Schedule a post to one social account. Returns job result."""
        schedule_time = (datetime.now() + timedelta(minutes=schedule_minutes)).strftime("%Y-%m-%dT%H:%M:%S")
        
        network = {"type": "photo" if media_id else "status", "text": text}
        if media_id:
            network["media"] = [{"id": media_id}]
        
        payload = {"bulk": {"state": "scheduled", "posts": [{
            "networks": {provider: network},
            "accounts": [{"id": account_id, "scheduled_at": schedule_time}]
        }]}}
        
        r = requests.post(f"{PUBLER_BASE}/posts/schedule", headers=self.headers, json=payload, timeout=15)
        if r.status_code != 200:
            log.error(f"Schedule failed: {r.status_code} {r.text[:300]}")
            return None
        
        job_id = r.json()["job_id"]
        # Poll for completion
        for _ in range(10):
            time.sleep(3)
            r2 = requests.get(f"{PUBLER_BASE}/job_status/{job_id}",
                headers={"Authorization": PUBLER_AUTH_HEADER, "Publer-Workspace-Id": WORKSPACE_ID},
                timeout=10)
            if r2.status_code == 200:
                data = r2.json()
                failures = data.get("payload", {}).get("failures", {})
                if failures:
                    log.error(f"  ❌ Job failed: {failures}")
                    return None
                return data
        
        log.error("  ⚠️ Job timeout")
        return None
    
    def get_accounts(self) -> list:
        """Get connected social accounts."""
        r = requests.get(f"{PUBLER_BASE}/accounts", headers=self.headers, timeout=10)
        return r.json() if r.status_code == 200 else []


def generate_text(prompt: str, max_tokens=500) -> str:
    log.info(f"📝 Generating text ({max_tokens} tokens)...")
    r = requests.post("https://openrouter.ai/api/v1/chat/completions", headers={
        "Authorization": f"Bearer {OPENROUTER_KEY}",
        "Content-Type": "application/json"
    }, json={
        "model": ORCHESTRATOR_MODEL,
        "messages": [
            {"role": "system", "content": "You are a financial content writer. Write concise, data-driven content for social media. Vary phrasing to avoid duplicate-content flags."},
            {"role": "user", "content": prompt}
        ], "max_tokens": max_tokens, "temperature": 0.7
    }, timeout=30)
    if r.status_code == 200:
        text = r.json()["choices"][0]["message"]["content"]
        if text is None:
            log.error("❌ Model returned null content — try google/gemini-2.5-flash instead of deepseek")
            return None
        log.info(f"✅ Generated {len(text)} chars")
        return text
    log.error(f"❌ OpenRouter failed: {r.status_code}")
    return None


def run_pipeline(topic: str = None):
    if not topic:
        topic = "Bitcoin breaks 100K resistance after ETF inflows surge"
    log.info("=" * 60)
    log.info(f"⚡ ZEUS PIPELINE TEST — {datetime.now().strftime('%H:%M:%S')}")
    log.info(f"   Topic: {topic}")
    log.info("=" * 60)
    
    missing = [k for k in ["OPENROUTER_API_KEY","REPLICATE_API_KEY","PUBLER_API_KEY"] 
               if not os.getenv(k)]
    if missing:
        log.error(f"❌ Missing: {', '.join(missing)}")
        return False
    
    # Stage 1: Text (vary to avoid Twitter duplicate detection)
    text = generate_text(f"Write a 3-sentence financial news update about: {topic}", 200)
    if not text: return False
    social = generate_text(f"Turn into a tweet (max 280 chars, vary wording): {text}", 100) or text[:280]
    
    # Stage 2: Image
    replicate = ReplicateClient()
    img_url = replicate.generate_image(f"Professional financial visualization: {topic[:100]}")
    if not img_url:
        log.error("❌ Image generation failed")
        return False
    
    # Stage 3: Upload to Publer + schedule
    publer = PublerClient()
    
    # Upload media
    media_id = publer.upload_media(img_url)
    if not media_id:
        log.error("❌ Media upload failed")
        return False
    
    # Post to Twitter
    result = publer.schedule_post("twitter", "69f783d1afc106b8869cf50b", 
                                  social[:280], media_id=media_id)
    
    log.info(f"\n✅ DONE — Text:{len(text)}c | Image:✅ | Posted:{'✅' if result else '❌'}")
    return bool(result)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--topic", "-t", type=str, help="Custom content topic")
    sys.exit(0 if run_pipeline(parser.parse_args().topic) else 1)
