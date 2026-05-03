---
name: "wake-on-lan-system"
description: "Reliable Wake-on-LAN system for desktops behind WiFi extenders. Three methods ranked by reliability: Smart Plug, SSH-to-extender, Pi relay. With Discord/webhook trigger for remote wake."
triggers:
  - "wake on lan"
  - "wake desktop"
  - "wol setup"
  - "remote power on"
  - "boot desktop remotely"
tags: [wake-on-lan, wol, smart-plug, remote, discord, networking]
---

# Wake-on-LAN System — Reliable Remote Boot

Three methods, ranked by reliability. Primary target: desktop behind TP-Link WiFi extender (Ethernet-connected).

## Why Standard WoL Fails Here

WiFi extenders often don't forward broadcast/magic packets to their Ethernet ports. The extender creates a bridge that drops WoL packets. This is why your current setup is "unreliable."

## Architecture

```
  Remote User (Discord)
       ↓
  Webhook / Cloud Relay (always-on)
       ↓
  [WoW Trigger Device] → Magic Packet → Desktop boots → Hermes starts
```

---

## Method 1: Smart Plug (★★★★★ RECOMMENDED)

**Reliability:** 100%. Works from S5 (full shutdown). No network tricks.

### How it works
1. Desktop plugged into WiFi smart plug
2. BIOS set to "Restore on AC Power Loss → Power On"
3. Smart plug toggled OFF → ON via API
4. Desktop detects power and boots automatically
5. Hermes starts with Windows/WSL

### Hardware needed
- WiFi smart plug: ~$10 (TP-Link Kasa KP115, Sonoff S31, or similar)
- Must support local API or cloud API

### Step 1: BIOS Configuration
```
Enter BIOS (Del/F2 at boot)
→ Advanced → Power Management / ACPI Configuration
→ "Restore on AC Power Loss" → [Power On] / [Always On]
→ Save & Exit
```

**Verify:** Unplug desktop from wall, plug back in. It should boot automatically. If yes, proceed.

### Step 2: Smart Plug Setup

**Option A: TP-Link Kasa (recommended, local API)**
```bash
# Install python-kasa
pip install python-kasa

# Discover plug on network
kasa discover

# Turn OFF (shuts down desktop if on)
kasa --host 192.168.x.x off

# Turn ON (boots desktop)
kasa --host 192.168.x.x on
```

**Option B: Sonoff S31 (flashed with Tasmota)**
```
HTTP API built in:
  Turn ON:  curl http://192.168.x.x/cm?cmnd=Power%20On
  Turn OFF: curl http://192.168.x.x/cm?cmnd=Power%20Off
```

**Option C: Any Tuya-compatible plug**
```bash
pip install tinytuya
# Requires Tuya IoT platform API keys (free dev account)
```

### Step 3: Webhook Relay (always-on trigger)

Since Hermes runs on the desktop (off when needed), we need an always-on relay.

**Free cloud approach — use a webhook service:**

1. **Pipedream.com** (free tier: 10,000 invocations/month)
   - Create a workflow with HTTP trigger
   - When hit, calls smart plug API to toggle power
   
2. **Or use the Raspberry Pi relay** (see Method 3) — more work but fully self-hosted

3. **Or: The extender itself** (see Method 2) if it has SSH

### How people trigger it

**Via Discord DM to Hermes (when Hermes proxy is on Pi/cloud):**
```
User: "wake desktop"
Pi/Cloud relay → toggles smart plug → desktop boots → Hermes comes online
```

**Via simple HTTP (for testing):**
```bash
curl -X POST https://your-webhook-url/wake
```

---

## Method 2: WoL from Extender Itself (★★★★☆)

**Reliability:** High (packet originates on the extender, which has direct Ethernet to desktop).

### Prerequisites
- TP-Link extender with SSH or web admin that can run commands
- WoL tool on extender (etherwake or similar)

### Step 1: Check extender SSH access
```bash
# Try to SSH into extender (find IP from router admin)
ssh admin@192.168.x.x
# Common TP-Link default: admin/admin or check device label
```

### Step 2: Install etherwake on extender
Some TP-Link extenders run OpenWRT or similar. If accessible:
```bash
# On extender:
opkg update && opkg install etherwake
# Send WoL (replace with desktop MAC)
etherwake -b AA:BB:CC:DD:EE:FF
```

### Step 3: Create trigger script on extender
```bash
#!/bin/sh
# /usr/bin/wake-desktop.sh on extender
WOL_MAC="AA:BB:CC:DD:EE:FF"
/usr/bin/etherwake -b "$WOL_MAC"
```

### Step 4: Trigger from cloud
Use a webhook → SSH into extender → run script. Same cloud approach as Method 1.

---

## Method 3: Raspberry Pi / ESP32 Relay (★★★☆☆)

**Reliability:** Medium. Pi is always-on, but magic packet must traverse extender.

### Hardware
- Raspberry Pi Zero 2 W (~$15) or any Pi
- Connected directly to main router (NOT through extender)
- Always-on (draws ~2W)

### Setup

### Step 1: OS Install
```bash
# Flash Raspberry Pi OS Lite to SD card
# Enable SSH: touch /boot/ssh on the SD card
# Enable WiFi: create /boot/wpa_supplicant.conf
```

### Step 2: Install Tailscale on Pi
```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

### Step 3: WoL relay server on Pi
```python
# /home/pi/wol_relay.py
from flask import Flask, request
import subprocess, os

app = Flask(__name__)
DESKTOP_MAC = "AA:BB:CC:DD:EE:FF"  # ← Replace with actual MAC
API_TOKEN = os.environ.get("WOL_TOKEN", "change-me")

@app.route("/wake", methods=["POST"])
def wake():
    if request.headers.get("Authorization") != f"Bearer {API_TOKEN}":
        return "Unauthorized", 401
    
    # Try unicast first (might work if ARP cached)
    subprocess.run(["wakeonlan", DESKTOP_MAC], timeout=5)
    # Also try broadcast
    subprocess.run(["wakeonlan", "-i", "192.168.1.255", DESKTOP_MAC], timeout=5)
    
    return "Magic packet sent", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
```

### Step 4: Install and run
```bash
# On Pi:
sudo apt install wakeonlan python3-flask -y
pip3 install flask

# Run (use systemd for persistence):
sudo tee /etc/systemd/system/wol-relay.service << 'EOF'
[Unit]
Description=WoL Relay Server
After=network.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi
Environment=WOL_TOKEN=your-secret-token
ExecStart=/usr/bin/python3 /home/pi/wol_relay.py
Restart=always

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl enable --now wol-relay
```

### Trigger from anywhere
```bash
curl -X POST \
  -H "Authorization: Bearer your-secret-token" \
  http://100.x.x.x:8080/wake     # Pi's Tailscale IP
```

---

## Method 4: True WoL (BIOS/NIC) — Test First

Before spending money, test if classic WoL can work.

### Step 1: BIOS WoL setup
```
BIOS → Advanced → Power Management
→ "Wake on LAN" / "Wake on PCI-E" → [Enabled]
→ "Wake on PME" → [Enabled]
→ Disable "ErP Ready" / "Deep Sleep" (keeps NIC powered in S5)
```

### Step 2: Windows NIC settings
```
Device Manager → Network Adapter → Your Ethernet NIC → Properties
→ Advanced tab:
  "Wake on Magic Packet" → Enabled
  "Wake on Pattern Match" → Enabled  
  "Wake on Link Settings" → Enabled (optional)
→ Power Management tab:
  ✓ "Allow this device to wake the computer"
  ✓ "Only allow a magic packet to wake the computer"
```

### Step 3: Get MAC address
```bash
# From WSL or PowerShell:
ipconfig /all | findstr "Physical"
# Or: Get-NetAdapter | Select Name, MacAddress
```

### Step 4: Test WoL from another device
```bash
# Put desktop to sleep (not shutdown)
# From WSL or any Linux device on same network:
sudo apt install wakeonlan -y
wakeonlan AA:BB:CC:DD:EE:FF

# Also try directed broadcast:
wakeonlan -i 192.168.1.255 AA:BB:CC:DD:EE:FF
```

If this WORKS, great — use Method 3 (Pi relay). If it FAILS (likely with extender), use Method 1 (smart plug).

---

## Discord Integration

### Option A: Hermes itself handles it (when running)
```
User in Discord: "@Hermes wake my desktop"
Hermes: "Desktop already online — I'm running on it!"
```

When Hermes is ON, no wake needed. The problem is when it's OFF.

### Option B: Separate Discord bot on Pi
```python
# Simple Discord bot that only does WoL
import discord
import subprocess

bot = discord.Bot()
DESKTOP_MAC = "AA:BB:CC:DD:EE:FF"

@bot.slash_command(name="wake", description="Wake the desktop")
async def wake(ctx):
    await ctx.defer()
    subprocess.run(["wakeonlan", "-i", "192.168.1.255", DESKTOP_MAC])
    await ctx.respond("⚡ Magic packet sent! Desktop should boot in ~30s.")

bot.run("YOUR_DISCORD_BOT_TOKEN")
```

### Option C: Cloud webhook + Discord integration
1. Set up Pipedream/Zapier webhook that calls smart plug API
2. Create a Discord bot (or use an existing always-on Hermes proxy) that triggers it
3. Users type `/wake` or DM "wake desktop"

---

## Complete Deployment Checklist

- [ ] **BIOS:** AC Power Loss → Power On (for Method 1)
- [ ] **BIOS:** Wake on LAN → Enabled (for Methods 2-4)
- [ ] **Windows NIC:** Wake on Magic Packet → Enabled
- [ ] **MAC address:** Recorded from `ipconfig /all`
- [ ] **Smart plug:** Purchased and configured (Method 1)
- [ ] **Pi/relay device:** Set up and tested (Method 2 or 3)
- [ ] **Webhook/cloud relay:** Deployed for external triggers
- [ ] **Discord bot token:** Created at discord.com/developers
- [ ] **Test:** Sleep desktop → trigger WoL → verify it wakes
- [ ] **Test:** Shut down desktop → trigger smart plug → verify it boots

---

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---|---|---|
| WoL from same network fails | Extender blocking broadcasts | Use Method 1 (smart plug) |
| WoL works from sleep but not S5 | ErP/Deep Sleep enabled, NIC unpowered | Disable ErP in BIOS |
| Smart plug turns on but no boot | BIOS "AC Power Loss" not set to Power On | Check BIOS setting |
| Pi can't reach desktop | Extender NATs its Ethernet ports | Use Method 1 or connect Pi to extender directly |
| Webhook unreachable | Cloud relay down | Use a proper cloud platform (Pipedream free tier) |

---

## Pitfalls

1. **ErP Ready / Deep Sleep in BIOS kills WoL** — NIC gets no power in S5. Must disable.
2. **Some BIOS have "AC Power Loss" default to "Last State"** — if desktop was OFF, power restore keeps it OFF. Set to "Power On."
3. **WiFi extenders create isolated networks** — devices behind the extender may not be on the same broadcast domain. Test with `arp -a`.
4. **Smart plugs need 2.4GHz WiFi** — most are 2.4GHz only. Your router must have 2.4GHz enabled.
5. **WoL MAC must be WITHOUT colons for some tools** — `AABBCCDDEEFF` vs `AA:BB:CC:DD:EE:FF`.

---

## Recommendation for Your Setup

Given: TP-Link extender + gaming desktop + already have Tailscale:

**Best path: Smart Plug ($10) + Pipedream webhook (free)**
1. Buy TP-Link Kasa KP115
2. Set BIOS: AC Power Loss → Power On
3. Create Pipedream webhook that calls Kasa API
4. 100% reliable, works from anywhere, no extra hardware to maintain

**For Discord:**
Once desktop is up and Hermes runs, Hermes can document the webhook URL. Users can bookmark it. Or I'll build a tiny Discord bot that pings the webhook.