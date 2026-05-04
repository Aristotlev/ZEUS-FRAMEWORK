# Ad Library Research — Platform Pitfalls & Techniques
## Meta Ad Library, Google Ads Transparency Center, Reddit

Session date: May 3, 2026. Researching ExampleCompany + ExampleCompetitor ad campaigns.

---

## Meta Ad Library

### Pitfalls

1. **Anti-bot challenge wall** — The Meta Ad Library API (`/ads/library/api/`) returns a JS challenge page when hit via curl/headless. Must use a real browser (Browserbase with stealth works, but still fragile).

2. **Search type matters** — `search_type=keyword_exact_phrase` returns ads that MENTION the keyword (including competitor ads). `search_type=page` returns ads BY that page/advertiser. Searched both — ExampleCompany had zero ads as an advertiser but ~53 competitor ads (Autopilot) targeting their keywords.

3. **Empty page = still loading** — The Meta Ad Library is a React SPA. Initial snapshot often shows just the search bar. Wait ~5-10 seconds and re-snapshot for results to render.

4. **Spend data is ranges, not exact** — Meta shows "$4K-$4.5K" not "$4,231." Impressions are also ranges ("300K-350K").

5. **Ad status indicators:**
   - "Inactive" — ad stopped running
   - "This content was removed because it didn't follow our Advertising Standards" — ad rejected
   - Active ads show "Active" with dates
   - "This ad has multiple versions" → expand dropdown to see creative variants

6. **Country filter** — Default is US. Change via combobox for international campaigns.

### Effective Workflow
```
1. Search by advertiser name (search_type=page) first
2. Then search by keyword (search_type=keyword_exact_phrase) to find competitors targeting their brand
3. Sort by total_impressions descending
4. Click "See ad details" for creative text + spend data
5. Note: spend data only shows for some ads (political/social issue ads in EU always show it)
```

---

## Google Ads Transparency Center

### Pitfalls

1. **Region/language rendering** — The page renders in Greek when accessed from this WSL2 environment. Search box labels and ad details are in Greek. Functionally works but need to read through translated UI.

2. **Search requires interaction** — URL parameter `?advertiser_name=...` alone doesn't trigger search. Must type into the search box and select the autocomplete suggestion.

3. **Verified advertisers** — Look for "Επαληθευμένος" / "Verified" badge. Both ExampleCompany Inc and ExampleCompetitor Inc are verified.

4. **Ad count in grid vs. total** — Shows "45 ads" / "~46 ads" but the grid shows ad VERSIONS (80 versions for UW). The dashboard count is unique ads; the grid count is all creative variants.

5. **Ad types:**
   - Regular ads → just images/text
   - Political ads → show additional disclosure info (who paid, targeting)
   - App install ads → rendered in iframes, hard to extract text from

6. **No spend data** — Google does NOT show spend or impression data in the transparency center (unlike Meta). You only get creative content and dates.

7. **Filter by platform** — "All platforms" includes YouTube, Search, Display, Gmail, Discover. Can filter to isolate YouTube-only campaigns.

### Effective Workflow
```
1. Navigate to adstransparency.google.com
2. Set region to US (critical — advertiser data is region-specific)
3. Type advertiser name in search box → select autocomplete suggestion
4. Note: "# ads" in search dropdown is approximate ("~46")
5. Click advertiser to see full grid
6. Click individual ads to view creative detail
```

---

## Reddit Ads

### Reality
- **No public ad library exists.** Reddit does not provide a transparency tool for advertiser campaigns.
- r/advertising subreddit occasionally has ad-related discussions but not targeted per-advertiser data.
- Search via Reddit API (`old.reddit.com/search.json`) returned no relevant results for either company.

### What you CAN do
- Monitor r/advertising, r/PPC, r/marketing for industry discussions
- Use third-party ad intelligence tools (AdSpy, AdPlexity, etc.) — all paid
- Twitter/X Ad Library exists but only for political ads

---

## Competitor Discovery (bonus finding)

When searching "unusual whales" on Meta Ad Library, the majority of results were NOT from ExampleCompany — they were from **Autopilot** (joinautopilot.com), a competing politician trade-tracking service. Autopilot is:
- Spending $25K–$40K+ lifetime on Meta targeting UW's audience
- Running video ads + static image carousels
- Using copy like "Copy politicians' trades" and political angle ("Rep Khanna anti-corruption bill")
- This is a **keyword hijacking** strategy — they bid on "unusual whales" as a keyword

**Actionable:** Check your own brand keywords on Meta Ad Library to see if competitors are targeting them.