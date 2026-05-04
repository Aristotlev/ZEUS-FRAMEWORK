# Competitor Ad Intelligence Gathering

Workflow for researching competitor paid ad campaigns across platforms using public transparency tools.

## Platforms & URLs

### Meta Ad Library (Facebook & Instagram)
- **URL:** `https://www.facebook.com/ads/library/`
- **Key params:** `active_status=all`, `ad_type=all`, `country=US`, `media_type=all`
- **Search types:**
  - `search_type=keyword_exact_phrase` — ads containing exact phrase (catches competitor ads targeting brand keywords too)
  - `search_type=keyword_unordered` — broader keyword match
  - `search_type=page` — ads BY a specific Facebook page (most useful for finding a company's own ads)
- **Data available:** Spend ranges, impression ranges, audience size, active dates, platforms, ad creative/images, multiple versions per ad
- **⚡ QUIRK:** The page is a React SPA — initial snapshot may be empty. Wait 2-3 seconds and re-snapshot to get results.
- **⚡ QUIRK:** The API endpoint (`/ads/library/api/`) is blocked by anti-bot challenges (JS challenge redirect). Use browser UI only.
- **⚡ QUIRK:** Page name search (`search_type=page`) often returns no results even for known companies. Fall back to exact phrase keyword search.
- **⚡ QUIRK:** Keyword searches for fintech/investment brands are dominated by third-party ads, not the brand's own ads. E.g., searching "unusualwhales" or "quiverquantitative" returned mostly Autopilot (a trading automation tool) ads mentioning those brands, zero direct ads from the companies themselves. Always check "Paid for by" attribution — do NOT assume search result count equals competitor ad activity.
- **⚡ WORKAROUND:** When keyword search is noisy with third-party ads, complement with a web search (`site:facebook.com "competitor name" ad`) to find the competitor's actual Facebook page, then try `search_type=page` with the exact page name as listed on Facebook.

### Google Ads Transparency Center
- **URL:** `https://adstransparency.google.com/?region=US`
- **Search:** Type advertiser name in the search box — autocomplete shows verified advertisers with ad counts
- **Data available:** Total ad count, ad versions count, platform distribution, ad creatives (images, iframes), political ad classification
- **⚡ QUIRK:** Interface may render in Greek (el) depending on request origin. Text labels are Greek but data (advertiser names, ad counts) is in English. Use `Αρχική σελίδα` = Home, `Διαφημίσεις` = Ads.
- **⚡ QUIRK:** Ad creatives often render in iframes — detailed text not visible in grid view. Click individual ads for details.
- **⚡ QUIRK:** Google does NOT publicly disclose ad spend amounts (unlike Meta which shows spend ranges).

### YouTube Ads
- Covered under Google Ads Transparency Center (same advertiser profile)
- No separate YouTube ad library exists
- Check if advertiser has dedicated YouTube campaigns vs. bundled Google Ads

### Reddit Ads
- **No public ad library exists** — Reddit does not provide transparency tools
- Check r/advertising for industry discussion (rarely has competitor-specific intel)
- Search for sponsored content mentions (low yield)

### Other Platforms (not researched)
- TikTok Ad Library: `https://ads.tiktok.com/business/creativecenter/`
- Snapchat Political Ads Library: `https://www.snap.com/en-US/political-ads`
- LinkedIn Ad Library: `https://www.linkedin.com/ad-library/`

## Parallelization Workflow

⚠️ **PITFALL:** Don't research platforms sequentially. The user expects fast results — parallelize aggressively:

```
Phase 1 (parallel):
  - browser_navigate → Meta Ad Library (Company A)
  - browser_navigate → Meta Ad Library (Company B)
  - web_search / terminal → Reddit, web research
  - browser_navigate → Google Ads Transparency Center

Phase 2 (after Phase 1 loads):
  - browser_snapshot all tabs
  - Click into advertiser profiles
  - Extract spend/impression data
  - **IMMEDIATELY write report to disk** at ~/.hermes/reports/ad_analysis_<date>.md
  - Verify file saved with read_file before attempting delivery

Phase 3 (only after Phase 2 file confirmed):
  - Email report via AgentMail to recipients
```

## Data Extraction Priorities

For each competitor, extract:
1. **Ad counts** (total unique ads, versions)
2. **Platform distribution** (Search, Display, YouTube, FB, IG)
3. **Spend ranges** (Meta only — Google doesn't disclose)
4. **Impression ranges** (Meta only)
5. **Ad creative themes** (messaging, CTAs, offers)
6. **Active date ranges** (recent vs. historical)
7. **Competitor ads targeting their brand** (keyword search on Meta catches these)
8. **Advertiser verification status**

## Common Deliverables

- Summary comparison table (Company A vs B vs Competitors by platform)
- Strategic insight section (blind spots, opportunities, gaps)
- **⚠️ CRITICAL:** Write report to disk FIRST (`~/.hermes/reports/ad_analysis_<date>.md`) and verify it saved. Sessions consistently truncate at the email step — a saved file survives and can be delivered next session. Only attempt email after file is confirmed on disk.
- Email delivery via AgentMail: `mcp_agentmail_send_message` with inboxId as full email address
