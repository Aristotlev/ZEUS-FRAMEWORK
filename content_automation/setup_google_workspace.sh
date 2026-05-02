#!/bin/bash

# Google Workspace Integration Setup
echo "🏛️ Zeus Framework - Google Workspace Setup"
echo "=========================================="

# Check if gcloud CLI is installed
if ! command -v gcloud &> /dev/null; then
    echo "❌ Google Cloud CLI not found. Installing..."
    
    # Install gcloud CLI
    if [[ "$OSTYPE" == "linux-gnu"* ]]; then
        curl https://sdk.cloud.google.com | bash
        exec -l $SHELL
    elif [[ "$OSTYPE" == "darwin"* ]]; then
        brew install --cask google-cloud-sdk
    else
        echo "Please install Google Cloud CLI manually: https://cloud.google.com/sdk/docs/install"
        exit 1
    fi
fi

echo "🔐 Setting up Google Workspace integration..."

# Create service account (if not exists)
PROJECT_ID="${GOOGLE_CLOUD_PROJECT:-zeus-framework}"
SERVICE_ACCOUNT_NAME="zeus-content-ideas"
SERVICE_ACCOUNT_EMAIL="${SERVICE_ACCOUNT_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

echo "📋 Creating service account..."
gcloud iam service-accounts create $SERVICE_ACCOUNT_NAME \
    --description="Zeus Framework Content Ideas Integration" \
    --display-name="Zeus Content Ideas" || echo "Service account may already exist"

# Grant necessary permissions
echo "🔑 Granting permissions..."
gcloud projects add-iam-policy-binding $PROJECT_ID \
    --member="serviceAccount:$SERVICE_ACCOUNT_EMAIL" \
    --role="roles/drive.file"

# Create and download credentials
echo "📜 Creating service account key..."
gcloud iam service-accounts keys create google_credentials.json \
    --iam-account=$SERVICE_ACCOUNT_EMAIL

echo "✅ Google service account created!"
echo ""
echo "📊 Next Steps:"
echo "1. Enable APIs in Google Cloud Console:"
echo "   - Google Sheets API"
echo "   - Google Docs API" 
echo "   - Google Drive API"
echo ""
echo "2. Create your Zeus Content Ideas Google Sheet:"
echo "   - Run: python -c 'from enhanced_content_ideas import GoogleWorkspaceIntegration; import asyncio; asyncio.run(GoogleWorkspaceIntegration().setup_content_ideas_sheet())'"
echo ""
echo "3. Share your Google Sheet with the service account:"
echo "   - Share with: $SERVICE_ACCOUNT_EMAIL"
echo "   - Permission: Editor"
echo ""
echo "4. Copy the Sheet ID from the URL and update your .env:"
echo "   GOOGLE_SHEET_ID=your_sheet_id_here"

# Create example Google Sheet template (if Python is available)
if command -v python3 &> /dev/null; then
    echo ""
    echo "🔧 Installing Python dependencies..."
    pip install google-auth google-auth-oauthlib google-auth-httplib2 google-api-python-client feedparser beautifulsoup4 aiohttp
    
    echo ""
    echo "📊 Creating Zeus Content Ideas Google Sheet..."
    python3 -c "
import asyncio
import sys
sys.path.append('.')
try:
    from enhanced_content_ideas import GoogleWorkspaceIntegration
    integration = GoogleWorkspaceIntegration()
    sheet_id = asyncio.run(integration.setup_content_ideas_sheet())
    if sheet_id:
        print(f'✅ Google Sheet created: https://docs.google.com/spreadsheets/d/{sheet_id}')
        print(f'📝 Add this to your .env file:')
        print(f'GOOGLE_SHEET_ID={sheet_id}')
    else:
        print('❌ Failed to create Google Sheet - check credentials')
except Exception as e:
    print(f'⚠️ Could not auto-create sheet: {e}')
    print('You can create it manually later.')
"
fi

echo ""
echo "🎉 Setup complete!"
echo ""
echo "📋 Google Workspace Integration Ready:"
echo "✅ Service account created"
echo "✅ Credentials saved to google_credentials.json"  
echo "✅ Ready for Google Sheets integration"
echo ""
echo "🔗 Useful links:"
echo "- Enable APIs: https://console.cloud.google.com/apis/library"
echo "- Manage service accounts: https://console.cloud.google.com/iam-admin/serviceaccounts"