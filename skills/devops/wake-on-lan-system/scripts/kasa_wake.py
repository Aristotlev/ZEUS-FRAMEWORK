#!/usr/bin/env python3
"""
Kasa Smart Plug Wake Script — toggles power to boot a desktop.
Requires: pip install python-kasa
BIOS must have: "Restore on AC Power Loss → Power On"

Usage:
    python3 kasa_wake.py discover          # Find plugs on network
    python3 kasa_wake.py off 192.168.1.x   # Cut power (shut down)
    python3 kasa_wake.py on 192.168.1.x    # Restore power (boot)
    python3 kasa_wake.py wake 192.168.1.x  # Cycle: off → wait → on
"""

import asyncio
import sys
import time


async def discover():
    """Find all Kasa smart plugs on the network."""
    from kasa import Discover
    devices = await Discover.discover()
    if not devices:
        print("No Kasa devices found. Check:")
        print("  1. Plug is powered and on same WiFi")
        print("  2. 2.4GHz network is enabled")
        print("  3. Try: pip install python-kasa --upgrade")
        return
    
    for ip, dev in devices.items():
        await dev.update()
        print(f"  {ip} → {dev.alias} [{dev.device_type}] {'ON' if dev.is_on else 'OFF'}")


async def control(host: str, action: str):
    """Turn plug on/off."""
    from kasa import SmartPlug
    plug = SmartPlug(host)
    await plug.update()
    
    if action == "on":
        await plug.turn_on()
        print(f"✅ {host} turned ON — desktop should boot")
    elif action == "off":
        await plug.turn_off()
        print(f"🔴 {host} turned OFF — desktop shutting down (if BIOS ACPI enabled)")
    elif action == "wake":
        print(f"🔴 Turning OFF {host}...")
        await plug.turn_off()
        await asyncio.sleep(3)  # Wait for PSU capacitors to drain
        print(f"✅ Turning ON {host} — desktop should boot now")
        await plug.turn_on()
    else:
        print(f"Unknown action: {action}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    
    cmd = sys.argv[1]
    
    if cmd == "discover":
        asyncio.run(discover())
    elif cmd in ("on", "off", "wake"):
        if len(sys.argv) < 3:
            print(f"Usage: {sys.argv[0]} {cmd} <ip_address>")
            sys.exit(1)
        asyncio.run(control(sys.argv[2], cmd))
    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)