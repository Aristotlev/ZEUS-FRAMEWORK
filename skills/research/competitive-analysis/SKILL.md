---
name: "competitive-analysis"
description: "Comprehensive competitive business analysis: market positioning, cost modeling, revenue analysis"
triggers:
  - "competitor analysis"
  - "competitor ads"
  - "ad intelligence"
  - "ad campaigns"
  - "market positioning" 
  - "cost breakdown"
  - "competitive landscape"
  - "business model analysis"
  - "pricing strategy"
  - "market research"
tags: [business, strategy, analysis, costs, competition, market-research]
---

# Competitive Analysis

Comprehensive framework for analyzing competitive landscapes, cost structures, and market positioning.

## When to Use This Skill

- Analyzing competitor strategies and weaknesses
- Building cost models for business operations
- Market positioning and differentiation analysis  
- Revenue model comparisons
- Investment and budget planning
- Strategic business planning

## Critical Cost Modeling Methodology

### ⚠️ AVOID COMMON UNDERESTIMATION ERRORS

**The #1 mistake in cost analysis is missing operational expenses.** See `references/cost-audit-methodology.md` for detailed case study of a 3.3x cost underestimate ($85 → $227/month) and systematic audit framework.

**Most Common Missing Costs:**

1. **Infrastructure Costs** (often 30-50% of budget)
   - Hosting/VPS ($20-50/month typical)
   - Database backups and monitoring ($10-20/month)
   - Domain, SSL, CDN ($5-15/month)
   - Error tracking and analytics ($10-30/month)

2. **Subscription Requirements** (hidden but mandatory)
   - API tiers: many "free" APIs require paid plans for real usage
   - Workspace tools: Google/Microsoft business plans ($6-15/user/month)
   - Publishing platforms: social media management tools ($15-50/month)
   - Premium service tiers: higher limits, priority processing

3. **Realistic Usage Patterns**
   - LLM token consumption: 5-10x higher than initial estimates
   - API call volumes: burst usage, retry logic, error handling
   - Storage and bandwidth: growth over time
   - Support and maintenance overhead

### Cost Analysis Framework

```
TOTAL COST = Direct Generation + Infrastructure + Subscriptions + Operations

Direct Generation:
- LLM/API calls per unit
- Media generation (images, video, audio)
- Processing and transformation costs

Infrastructure (25-40% of total):
- Hosting environment
- Database and storage
- Monitoring and analytics
- Security and backups

Subscriptions (20-35% of total):  
- Required service tiers
- Workspace and collaboration tools
- Publishing and distribution platforms
- Development and deployment tools

Operations (10-20% of total):
- Support and maintenance
- Updates and scaling
- Error handling and retry logic
- Manual oversight and quality control
```

## Competitive Positioning Analysis

### 1. Market Landscape Mapping

For ad intelligence / competitor ad campaign research, see `references/ad-intelligence.md` — covers Meta Ad Library, Google Ads Transparency Center, Reddit, platform quirks, and parallelized data-gathering workflow.

**Identify the 3-5 key players and categorize:**
- **The Giant**: Market leader with highest revenue/users
- **Feature Match**: Closest feature/service comparison
- **Free Alternative**: Loss-leader or different business model
- **Niche Players**: Specialized or emerging competitors

### 2. Weakness Analysis Matrix

For each competitor, identify:
- **Discovery Friction**: Paywalls, registration barriers, poor SEO
- **Platform Gaps**: Missing channels (TikTok, YouTube, Discord, etc.)
- **Content Limitations**: Format restrictions, update frequency
- **Community Weakness**: Poor engagement, no social features
- **Monetization Issues**: Over-reliance on one revenue stream

### 3. Strategic Opportunity Identification

**Gap Analysis:**
- What's the biggest unmet need?
- Which platforms are underserved?
- What content formats are missing?
- Where is pricing disconnected from value?

**Competitive Angles:**
- **Out-free**: Remove paywall friction vs premium competitors
- **Out-community**: Build stronger social/Discord vs data-only competitors  
- **Out-content**: Dominate video/multimedia vs text-only competitors
- **Out-scale**: Higher volume at lower cost vs boutique competitors

## Cost-Competitive Analysis

### 1. Competitor Cost Estimation

**Research Methods:**
- Public pricing pages and plan limits
- Job postings (team size, salary ranges)  
- Tool/service requirements from their features
- Content volume × estimated per-unit costs
- Infrastructure requirements for their scale

**Conservative Estimation:**
- Start with minimum viable costs
- Add 50-100% buffer for hidden expenses
- Research actual enterprise pricing (not marketing prices)
- Factor in team/manual costs (often biggest expense)

### 2. Your Cost Reality Check

**Step 1: List EVERY operational component**
- Don't assume "free" APIs will stay free at scale
- Include setup, maintenance, and support time
- Factor in error handling and redundancy
- Plan for growth and peak usage scenarios

**Step 2: Get real quotes**
- Contact sales for actual pricing beyond free tiers
- Test APIs at realistic volumes to understand costs
- Research hosting requirements for your architecture
- Price out monitoring, backup, and security needs

**Step 3: Multiply initial estimates by 2-3x**
- Operational costs always exceed projections
- Usage patterns are bursty and unpredictable
- Hidden requirements emerge during implementation
- Growth requires infrastructure investment

### 3. Competitive Math

```
Your Cost Per Unit vs Competitor Cost Per Unit = Competitive Advantage

Sustainable only if:
- Your total monthly costs < 20% of competitor estimated costs
- You can achieve 80%+ of their feature value  
- Your business model can monetize the cost advantage
- You have path to profitability at realistic user acquisition costs
```

## Revenue Model Analysis

### 1. Revenue Stream Identification

**Common Models:**
- **Subscription**: Monthly/annual recurring (predictable but high churn risk)
- **Freemium**: Free tier + premium features (high volume, low conversion)
- **Usage-based**: Pay per API call/content piece (scales with value)
- **Community**: Discord/memberships + exclusive content
- **Affiliate**: Revenue share on referrals/transactions
- **Sponsored**: Brand partnerships and promoted content
- **Data/Insights**: Sell aggregated analytics or reports

### 2. Break-Even Mathematics

**Critical Metrics:**
```
Monthly Costs ÷ Average Revenue Per User (ARPU) = Users Needed to Break Even

Example:
$227/month costs ÷ $0.005 ARPU = 45,400 users needed
$227/month costs ÷ $0.020 ARPU = 11,350 users needed  
$227/month costs ÷ $0.050 ARPU = 4,540 users needed
```

**Revenue Per Follower Benchmarks:**
- Social media: $0.001-0.005/follower/month
- Newsletter: $0.01-0.05/subscriber/month  
- Premium community: $0.50-2.00/member/month
- B2B SaaS: $5-50/user/month

## Ad Library Research

For researching competitor advertising campaigns on Meta, Google, and Reddit, see `references/ad-library-research.md`. Covers platform-specific pitfalls: Meta anti-bot challenges, Google region quirks, Reddit's lack of public ad library, and how to discover keyword-hijacking competitors.

## Pitfalls to Avoid

### Ad Intelligence Delivery

- **Save locally FIRST, deliver second.** Sessions frequently truncate at the email-send step. The research is lost when the report was never written to disk. Mandatory sequence: research → write report file to disk → verify file exists → THEN attempt email delivery. Do NOT inline the report text in the email tool call without first saving it.
- **Independent proof of work.** A saved report file at `~/.hermes/reports/ad_analysis_<date>.md` survives session truncation and can be delivered in a follow-up session. Skip this and you're burning the whole research run.
- **Free Tier Assumptions**: Most "free" services have practical limits requiring paid plans
- **Linear Scaling Myths**: Costs often have step functions and minimum commitments
- **Infrastructure Blindness**: Missing hosting, monitoring, security, backup costs
- **Token Underestimation**: LLM usage is always 5-10x initial projections
- **Manual Labor Ignorance**: Support, content moderation, quality control time

## Ad Library Intelligence Research

When researching competitor advertising spend and strategy, use ad transparency libraries:

### Meta Ad Library
```
https://www.facebook.com/ads/library/?active_status=all&ad_type=all&country=US&q=<keyword>&search_type=keyword_exact_phrase
```
- Shows ad creatives, spend ranges, impressions, audience estimates
- Search by keyword or advertiser page name
- Pitfall: Meta API has anti-bot challenges — curl won't work. Use browser.
- Pitfall: Results include ads that MENTION the keyword, not just ads BY the advertiser. Check "Paid for by" to distinguish.

### Google Ads Transparency Center
```
https://adstransparency.google.com/?region=US
```
- Search by advertiser name — returns verified advertisers with ad counts
- Shows ad creatives in grid view (may need scroll/click for details)
- Does NOT show spend data (unlike Meta)
- Pitfall: May render in a language based on region settings (Greek for `?region=US` in our case). Use visual snapshots if text isn't extractable.

### What you can extract
| Data | Meta | Google |
|---|---|---|
| Ad count | ✅ | ✅ |
| Spend (ranges) | ✅ | ❌ |
| Impressions | ✅ | ❌ |
| Audience size | ✅ | ❌ |
| Active dates | ✅ | ✅ |
| Ad creative (text/image) | ✅ | Grid view only |
| Platform breakdown | ✅ | Filterable |
| Competitor ads targeting keywords | ✅ | ❌ |

### Research workflow
1. Search Meta Ad Library for competitor name → get spend, impressions, competitor targeting
2. Search Google Ads Transparency for competitor name → get ad count and creatives
3. Cross-reference: Meta shows who's targeting your keywords, Google shows overall ad volume
4. Reddit has no public ad library — skip it
5. TikTok/Snap/LinkedIn require additional separate research

### Pitfalls
- **Meta API blocked by bot detection** — use browser navigation, not curl
- **Google renders in unexpected languages** — use screenshots for visual extraction
- **Search type matters** — "keyword_exact_phrase" vs "page" vs "keyword_unordered" return different result sets
- **Competitor keyword targeting** — ads where competitor names appear as TARGETED KEYWORDS are different from ads BY the competitor. Look for "Paid for by" attribution.
- **Sequential Research**: Gathering data one platform at a time is too slow. Parallelize aggressively — launch all browser tabs and terminal searches simultaneously in Phase 1, then extract data in Phase 2. The user expects speed; each sequential round-trip adds 15-30 seconds of dead time.

### Business Model Traps
- **Revenue Fantasy**: Assuming conversion rates without testing
- **Growth Assumptions**: Linear growth models vs realistic adoption curves
- **Retention Blindness**: Not factoring in churn rates and customer lifecycle
- **Unit Economics Ignorance**: Scaling a loss-making model loses more money

## Verification Steps

1. **Cost Model Validation**
   - Get actual quotes from all major service providers
   - Test realistic usage volumes in development
   - Build 3 scenarios: conservative, realistic, aggressive
   - Factor in 6-12 months of runway costs

2. **Competitive Research Verification**
   - Interview users of competitor products
   - Test competitor free tiers and analyze limitations  
   - Research competitor team sizes and funding
   - Monitor competitor pricing changes and feature releases

3. **Business Model Testing**
   - Launch MVP with real users and measure conversion
   - Test multiple monetization approaches simultaneously
   - Track unit economics from day 1
   - Build scenario models for different growth rates

---

## Success Metrics

- **Cost accuracy**: Final costs within 25% of projections
- **Competitive advantage**: 70%+ cost advantage with 90%+ feature parity
- **Path to profitability**: Clear revenue model reaching break-even within 12 months
- **Strategic differentiation**: Unique value proposition not easily copied

---

*Remember: The most dangerous business analysis is one that confirms what you want to believe. Always challenge your assumptions with real data.*