     1|---
     2|name: "hermes-email-backends"
     3|description: "Four ready-to-deploy email backends for Hermes — AgentMail, Gmail SMTP, Proton Bridge, SendGrid/Resend. Local and production-compatible. Drop in a key and deploy."
     4|triggers:
     5|  - "set up email"
     6|  - "email backend"
     7|  - "configure email sending"
     8|  - "multi email setup"
     9|  - "hermes email identity"
    10|tags: [email, smtp, agentmail, gmail, proton, sendgrid, resend, hermes]
    11|---
    12|
    13|# Hermes Email Backends — Instant Deploy
    14|
    15|Four drop-in email backends. Each is self-contained — provide the key/token and it's operational.
    16|
    17|## Quick Select
    18|
    19|| # | Backend | Best For | Requires | When to use |
    20||---|---|---|---|---|
    21|| **1** | AgentMail | Already works | Nothing (done) | Quick send, @agentmail.to identity |
    22|| **2** | Gmail SMTP | Personal identity | App password | Sending as `user@example.com` |
    23|| **3** | Proton Bridge | Privacy/E2EE | Paid plan + Docker | Sending as your Proton identity |
    24|| **4** | Resend (preferred) / SendGrid | Bulk/marketing | Free tier API key | Newsletters, automated campaigns |
    25|
    26|---
    27|
    28|## 1. AgentMail (ALREADY OPERATIONAL)
    29|
    30|**Status:** ✅ Working. Zero setup needed.
    31|
    32|```
    33|Inbox: <your-inbox>@agentmail.to
    34|Auth: Automatic (Hermes agentmail MCP tools)
    35|Domains: @agentmail.to
    36|```
    37|
    38|**Usage:** Just tell Hermes to send via AgentMail. MCP tools handle it.
    39|
    40|**Limitations:** @agentmail.to identity only. Not configurable for custom domain. Good for agent-to-human emails.
    41|
    42|**Env vars needed:** None (built into Hermes MCP stack)
    43|
    44|---
    45|
    46|## 2. Gmail SMTP (FREE — 2 min setup)
    47|
    48|**What you need:** A Gmail App Password (not your regular password).
    49|
    50|### Step 1: Generate App Password
    51|1. Go to https://myaccount.google.com/security → "2-Step Verification" must be ON
    52|2. Then https://myaccount.google.com/apppasswords
    53|3. Select "Mail" → "Other" → name it "Hermes"
    54|4. Copy the 16-char password (spaces don't matter)
    55|
    56|### Step 2: Store it (secure)
    57|```bash
    58|# Option A: Environment variable
    59|echo 'HERMES_GMAIL_APP_PASSWORD=*** efgh ijkl mnop"' >> ~/.hermes/.env
    60|
    61|# Option B: pass (if you use password-store)
    62|pass insert email/hermes-gmail
    63|# Paste the app password
    64|```
    65|
    66|### Step 3: Deploy — Local (WSL2)
    67|```bash
    68|# Test SMTP connectivity
    69|curl -v smtp://smtp.gmail.com:587 --mail-from user@example.com --mail-rcpt user@example.com 2>&1 | head -5
    70|```
    71|
    72|**SMTP Config:**
    73|```
    74|Host: smtp.gmail.com
    75|Port: 587
    76|Encryption: STARTTLS
    77|Username: user@example.com
    78|Password: <APP PASSWORD>
    79|From: Your Name <user@example.com>
    80|```
    81|
    82|**Daily limit:** 500 emails (Gmail free tier)
    83|
    84|### Hermes integration:
    85|Add to `~/.hermes/config.yaml`:
    86|```yaml
    87|email:
    88|  provider: smtp
    89|  smtp:
    90|    host: smtp.gmail.com
    91|    port: 587
    92|    username: user@example.com
    93|    password_env: HERMES_GMAIL_APP_PASSWORD
    94|    encryption: starttls
    95|    from: "Your Name <user@example.com>"
    96|```
    97|
    98|### Production deployment:
    99|Same config. Gmail SMTP works from any machine. Just export `HERMES_GMAIL_APP_PASSWORD` in prod env.
   100|
   101|---
   102|
   103|## 3. Proton Mail Bridge (PAID ONLY — $4+/mo)
   104|
   105|**⚠️ REQUIRES PAID PROTON PLAN (Mail Plus, Unlimited, or Business). Free accounts CANNOT use Bridge.**
   106|
   107|### Step 1: Paid Proton account
   108|Minimum: Mail Plus (~$4/mo or ~$48/yr)
   109|
   110|### Step 2: Deploy Bridge via Docker
   111|```bash
   112|# Pull image
   113|docker pull ghcr.io/videocurio/proton-mail-bridge:latest
   114|
   115|# Create storage volume
   116|mkdir -p ~/protonmail_bridge_data
   117|
   118|# Create network (optional, for container communication)
   119|docker network create --subnet 172.20.0.0/16 network20 2>/dev/null || true
   120|
   121|# Launch (local-only binding for security)
   122|docker run -d \
   123|  --name=protonmail_bridge \
   124|  -v ~/protonmail_bridge_data:/root \
   125|  -p 127.0.0.1:12025:25/tcp \
   126|  -p 127.0.0.1:12143:143/tcp \
   127|  --network network20 \
   128|  --restart=unless-stopped \
   129|  ghcr.io/videocurio/proton-mail-bridge:latest
   130|```
   131|
   132|### Step 3: Login (interactive, one-time)
   133|```bash
   134|docker exec -it protonmail_bridge /bin/bash
   135|
   136|# Inside container:
   137|pkill bridge                          # Kill default instance
   138|/usr/bin/bridge --cli                 # Start interactive CLI
   139|
   140|# In bridge CLI:
   141|>>> login
   142|Username: your_email@proton.me
   143|Password: ********
   144|Two factor code: 123456
   145|
   146|>>> change mode 0                    # Split address mode (for multiple addresses)
   147|>>> info                              # Copy username AND password (random generated)
   148|# Output shows IMAP and SMTP creds — SAVE THESE
   149|
   150|>>> exit
   151|exit                                  # Exit container shell
   152|
   153|# Restart to apply
   154|docker container restart protonmail_bridge
   155|```
   156|
   157|### Step 4: SMTP/IMAP Config
   158|```
   159|SMTP Host: 127.0.0.1
   160|SMTP Port: 12025
   161|SMTP Encryption: STARTTLS
   162|SMTP Username: <from bridge 'info' command>
   163|SMTP Password: <from bridge 'info' command>
   164|
   165|IMAP Host: 127.0.0.1
   166|IMAP Port: 12143
   167|IMAP Username: <same as SMTP>
   168|IMAP Password: <same as SMTP>
   169|```
   170|
   171|**⚠️ IMPORTANT:** The password from `info` is NOT your Proton password — Bridge generates a random app-specific password. Copy it exactly.
   172|
   173|### Hermes integration:
   174|```yaml
   175|# ~/.hermes/config.yaml (Proton backend)
   176|email:
   177|  provider: smtp
   178|  smtp:
   179|    host: 127.0.0.1
   180|    port: 12025
   181|    username: "<bridge generated username>"
   182|    password_env: HERMES_PROTON_BRIDGE_PASSWORD
   183|    encryption: starttls
   184|    from: "Your Name <your@proton.me>"
   185|```
   186|
   187|Store the bridge password:
   188|```bash
   189|echo 'HERMES_PROTON_BRIDGE_PASSWORD=*** password>"' >> ~/.hermes/.env
   190|```
   191|
   192|### Production deployment:
   193|Bridge must run on the same host (binds 127.0.0.1). For true production, run Bridge container alongside Hermes in same Docker network.
   194|
   195|### Limitations:
   196|- Proton SMTP rate limits apply (expect ~50-100 emails/hour max)
   197|- NOT suitable for bulk/marketing campaigns
   198|- Best for personal/transactional agent emails
   199|
   200|---
   201|
   202|## 4. Resend (FREE TIER — Bulk/Marketing — RECOMMENDED)
   203|
   204|**Why Resend over SendGrid:**
   205|- Cleaner modern API, built for developers
   206|- Free: 100 emails/day (3,000/month) 
   207|- First-class React Email support, webhooks
   208|- Better deliverability for transactional emails
   209|- SDK available in Python, Node, etc.
   210|
   211|**SendGrid alternative:** Free tier: 100 emails/day forever. More established but older API.
   212|
   213|### Step 1: Sign up
   214|1. Go to https://resend.com/signup
   215|2. Sign up with `user@example.com` (or GitHub login)
   216|3. Go to https://resend.com/api-keys
   217|4. Click "Create API Key" → name it "Hermes" → **Full access** permission
   218|5. Copy the key (starts with `re_`)
   219|
   220|### Step 2: Verify domain (optional for testing)
   221|Resend lets you send from `onboarding@resend.dev` for testing immediately. For production, add your domain in Resend dashboard.
   222|
   223|### Step 3: Store the key
   224|```bash
   225|echo 'RESEND_API_KEY="***"' >> ~/.hermes/.env
   226|```
   227|
   228|### Step 4: Python SDK (Hermes-compatible)
   229|```bash
   230|pip install resend
   231|```
   232|
   233|Test send:
   234|```python
   235|import resend, os
   236|resend.api_key = os.environ["RESEND_API_KEY"]
   237|
   238|resend.Emails.send({
   239|    "from": "Hermes <onboarding@resend.dev>",
   240|    "to": ["user@example.com"],
   241|    "subject": "Hermes is online",
   242|    "text": "Email backend #4 operational."
   243|})
   244|```
   245|
   246|### Hermes integration:
   247|```yaml
   248|# ~/.hermes/config.yaml
   249|email:
   250|  provider: resend
   251|  resend:
   252|    api_key_env: RESEND_API_KEY
   253|    from: "Hermes <hermes@yourdomain.com>"  # Once domain verified
   254|    # For testing:
   255|    # from: "Hermes <onboarding@resend.dev>"
   256|```
   257|
   258|### Production deployment:
   259|1. Verify your domain in Resend dashboard (add DNS TXT records)
   260|2. Update `from` to verified domain
   261|3. Same `RESEND_API_KEY` — works everywhere
   262|
   263|### Free tier limits:
   264|- 100 emails/day
   265|- 3,000 emails/month
   266|- If exceeded: upgrade to $20/mo for 50K emails
   267|
   268|### SendGrid Alternative Setup (if preferred):
   269|```bash
   270|#1. Sign up at https://signup.sendgrid.com/ (free tier: 100/day)
   271|#2. Create API key: Settings → API Keys → Full Access
   272|#3. Store:
   273|echo 'SENDGRID_API_KEY="***"' >> ~/.hermes/.env
   274|
   275|#4.qt Python:
   276|# pip install sendgrid
   277|# from sendgrid import SendGridAPIClient
   278|# sg = SendGridAPIClient(os.environ.get('SENDGRID_API_KEY'))
   279|```
   280|
   281|---
   282|
   283|## Switching Between Backends
   284|
   285|The user says: *"send you a copy paste of a token or a key and deploy in an instant"*
   286|
   287|Here's exactly what to tell Hermes to switch:
   288|
   289|### Switch commands:
   290|
   291|| Switch to | Tell Hermes |
   292||---|---|
   293|| **AgentMail** | "use agentmail for email" |
   294|| **Gmail** | "use gmail smtp, here's the app password: abcd efgh ijkl mnop" |
   295|| **Proton** | "use proton bridge, here's the bridge password: abc123def456" |
   296|| **Resend** | "use resend, here's the API key: re_xxxxx" |
   297|| **SendGrid** | "use sendgrid, here's the API key: SG.xxxxx" |
   298|
   299|###1 Automated — the config approach:
   300|
   301|All four can coexist in `~/.hermes/config.yaml`:
   302|
   303|```yaml
   304|email_backends:
   305|  default: agentmail  # which one to use by default
   306|  
   307|  agentmail:
   308|    enabled: true
   309|    # No config needed — uses MCP tools
   310|    
   311|  gmail:
   312|    enabled: false  # set true to activate
   313|    smtp_host: smtp.gmail.com
   314|    smtp_port: 587
   315|    username: user@example.com
   316|    password_env: HERMES_GMAIL_APP_PASSWORD
   317|    
   318|  proton_bridge:
   319|    enabled: false
   320|    smtp_host: 127.0.0.1
   321|    smtp_port: 12025
   322|    username_env: HERMES_PROTON_USERNAME
   323|    password_env: HERMES_PROTON_BRIDGE_PASSWORD
   324|    
   325|  resend:
   326|    enabled: false
   327|    api_key_env: RESEND_API_KEY
   328|    from: "Hermes <hermes@yourdomain.com>"
   329|```
   330|
   331|To switch: change `default` and mark `enabled: true` — done.
   332|
   333|---
   334|
   335|## Quick Reference: What to Copy-Paste
   336|
   337|| Backend |acción What to give Hermes | Where to get it |
   338||---|---|---|
   339|| AgentMail | Nothing — works | Built-in |
   340|| Gmail | 16-char app password | myaccount.google.com/apppasswords |
   341|| Proton | Bridge username + password | `docker exec -it protonmail_bridge /bin/bash` → `bridge --cli` → `info` |
   342|| Resend | `re_xxxx` API key | resend.com/api-keys |
   343|| SendGrid | `SG.xxxx` API key | app.sendgrid.com/settings/api_keys |
   344|
   345|---
   346|
   347|## Pitfalls
   348|
   349|1. **Proton free = dead end.** Don't waste time. Needs paid plan.
   350|2. **Gmail app passwords need 2FA enabled first.** Can't skip.
   351|3. **Resend testing domain** (`onboarding@resend.dev`) works instantly but looks unprofessional. Verify your domain for production.
   352|4. **SendGrid free tier sends from shared IPs** — deliverability varies.
   353|5. **Proton Bridge port mapping:** Container internal is 25/143, external is 12025/12143. Don't mix them.
   354|6. **Never commit `.env` to git.** The `.gitignore` must41 exclude it.
   355|
   356|## Verification
   357|
   358|After setting up any backend:
   359|```bash
   360|# Quick test: send yourself a test email
   361|# AgentMail: just tell Hermes "email me a test via agentmail"
   362|# Others: Hermes should14 send a test to user@example.com
   363|```
   364|Check inbox — if14 received, backend is operational.