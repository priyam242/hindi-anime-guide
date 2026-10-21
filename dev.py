#!/usr/bin/env python3
"""
Hindi Anime Guide - One-command development server runner.
Usage:
    python dev.py
"""

import os
import socket
import sys
import uvicorn
from app.config import get_settings

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def get_lan_ip() -> str:
    """Detects primary local network (LAN) IP address."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


if __name__ == "__main__":
    settings = get_settings()

    # Read host and port from HOST / PORT
    host = os.getenv("HOST", settings.host)
    try:
        port = int(os.getenv("PORT", str(settings.port)))
    except ValueError:
        port = 8000

    lan_ip = get_lan_ip()

    print("=" * 65)
    print(f"🚀 {settings.app_name} - {settings.app_description}")
    print("=" * 65)
    print(f"💻 लोकल URL (Local PC):    http://127.0.0.1:{port}")
    
    if host == "0.0.0.0":
        print(f"📱 फ़ोन / LAN URL (Phone):  http://{lan_ip}:{port}")
        print("   ↳ (फ़ोन और पीसी एक ही Wi-Fi नेटवर्क से जुड़े होने चाहिए)")
    else:
        print(f"📡 बाउंड होस्ट (Host):     {host}")
        print("💡 फ़ोन पर देखने के लिए .env में HOST=0.0.0.0 सेट करें।")

    print(f"🛠️  डेवलपमेंट मोड:         {settings.debug} (ऑटो-रीलोड सक्रिय)")
    print("=" * 65)

    # Run uvicorn server
    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        reload=True
    )
