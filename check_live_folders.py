"""Read the user's REAL Bilibili favourite folders to verify what was written.

This talks to the live B站 API using the same cookie the GUI is using, so it
shows ground truth rather than what the app claims it did.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.bilibili import BiliClient

# The GUI keeps its session cookie in memory only, so re-read from where the
# app persists it; if that is unavailable we fall back to a cookie file.
COOKIE_PATHS = [
    os.path.join(os.environ.get("APPDATA", ""), "BilibiliFavOrganizer", "cookie.txt"),
    os.path.join(os.environ.get("APPDATA", ""), "BilibiliFavOrganizer", "session.json"),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookie.txt"),
]

cookie = None
for p in COOKIE_PATHS:
    if os.path.exists(p):
        cookie = open(p, encoding="utf-8").read().strip()
        print(f"cookie from: {p}")
        break

if not cookie:
    print("no persisted cookie found; set BILI_COOKIE env var instead")
    cookie = os.environ.get("BILI_COOKIE", "")

if not cookie:
    print("FATAL: no cookie available — cannot read live B站 data")
    sys.exit(2)

c = BiliClient(request_delay=0.6)
c.set_cookie(cookie)
print(f"logged in: {c.is_logged_in}  uid={c.uid}")

folders = c.get_favorite_folders()
print(f"\n=== 你的 {len(folders)} 个收藏夹 ===")
for f in sorted(folders, key=lambda x: -x.media_count):
    print(f"  {f.title:<20} {f.media_count:>4} 条   (id={f.id}, 私密={f.is_private})")
