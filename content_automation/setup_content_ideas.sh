#!/bin/bash

# Content Ideas Setup Script
# Creates directory structure for dropping content ideas

echo "📂 Setting up Zeus Framework Content Ideas Intelligence..."

# Create main content ideas directory structure
mkdir -p content_ideas/{images,screenshots,links,notes,files,processed,failed}

# Create README with instructions
cat > content_ideas/README.md << 'EOF'
# 🧠 Zeus Framework Content Ideas Intelligence

Drop your content ideas here and Zeus will analyze them daily at 7 AM EST!

## 📁 Directory Structure

### 📸 `images/`
Drop any images that could become content:
- Market charts or screenshots  
- News headlines captures
- Social media posts screenshots
- Infographics or data visualizations
- **Supported:** .jpg, .jpeg, .png, .webp, .gif

### 🖼️ `screenshots/`  
Drop screenshots from trading platforms, news sites, social media:
- Congressional trading alerts
- Market data dashboards
- Twitter/X viral posts about finance
- News article screenshots

### 🔗 `links/`
Drop links you want analyzed:
- **Method 1:** Create a .txt file with just the URL
- **Method 2:** Save browser bookmarks as .url files (Windows) or .webloc (macOS)
- **Method 3:** Paste URL in a note with description

### 📝 `notes/`
Drop your raw ideas as text files:
- Trading strategies to explain
- Market observations  
- Content concepts
- Questions from followers
- **Supported:** .txt, .md files

### 📄 `files/`
Drop any other files:
- PDFs of earnings reports
- Spreadsheets with data
- Documents with research

## 🤖 How It Works

1. **7 AM EST Daily Processing** - Zeus scans all directories
2. **AI Analysis** - Each item gets analyzed for content potential  
3. **Vision AI** - Images/screenshots get analyzed with GPT-4 Vision
4. **Web Scraping** - Links get content extracted and analyzed
5. **Content Strategy** - High-potential items (7/10+) get queued for creation
6. **Auto-Creation** - Ideas become articles, carousels, or videos automatically

## 📊 Content Potential Scoring

- **9-10/10:** Immediate viral potential, high urgency
- **7-8/10:** Strong content, gets queued for creation  
- **5-6/10:** Good backup content, saved for slow news days
- **1-4/10:** Low potential, archived for reference

## 🎯 What Makes Great Content Ideas

### ✅ High-Scoring Ideas:
- Fresh congressional trading data
- Market breaking news with unique angle  
- Viral financial social media posts
- Data visualizations with clear insights
- Contrarian takes on popular narratives

### ❌ Low-Scoring Ideas:
- Old news already covered everywhere
- Generic market commentary  
- Complex topics without clear angle
- Low engagement potential
- No actionable insight

## 📋 Best Practices

### For Images:
- High resolution screenshots
- Clear, readable text/charts
- Include context in filename (e.g., "pelosi_nvidia_trade_alert_20241201.png")

### For Links:
- Include brief note about why it's interesting
- Focus on timely, breaking, or unique content
- Financial/political trading focus performs best

### For Notes:
- Be specific about the angle or hook
- Include target audience (beginners vs advanced)
- Note any required research or data sources

## 🔄 Processing Status

Check `/monitor` dashboard to see:
- Ideas processing status
- Content potential scores
- Generated content from ideas
- Success rates and analytics

## 📁 File Management

- **Processed successfully** → Moved to `processed/` directory
- **Failed processing** → Moved to `failed/` directory  
- **Original files** → Keep a backup if important!

## 💡 Pro Tips

1. **Batch similar ideas** - Drop multiple related screenshots at once
2. **Add context** - Rename files to include context/dates
3. **Mix formats** - Combine image + link + note for comprehensive analysis
4. **Timing matters** - Breaking news ideas get higher priority
5. **Quality over quantity** - 1 great idea > 10 mediocre ones

---

**Need help?** Check the logs or monitoring dashboard for processing status and errors.
EOF

# Set permissions
chmod 755 content_ideas
chmod 644 content_ideas/README.md

# Create example files to show structure
echo "https://example.com/some-interesting-financial-article" > content_ideas/links/example_link.txt

echo "Idea: Create a breakdown of why [politician] buying [stock] before [event] was perfectly timed.

Research needed:
- Exact trade dates
- Stock performance after
- Any insider information available
- Timeline of public announcements

Angle: Connect the dots for retail investors" > content_ideas/notes/example_idea.md

echo "🎯 Content Ideas Intelligence is ready!"
echo ""
echo "📂 Directory: $(pwd)/content_ideas/"
echo ""  
echo "📖 Next Steps:"
echo "1. Read content_ideas/README.md for full instructions"
echo "2. Start dropping your ideas in the appropriate folders"
echo "3. Zeus will process them daily at 7 AM EST"
echo "4. Monitor progress at http://localhost:8080/monitor"
echo ""
echo "💡 Example files created to show the structure!"