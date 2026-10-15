"""End-to-end check: does this project's AI classifier work against the
local 7910 free-flash forwarder?

It exercises the project's REAL AIClassifier._chat() path (not a mock), so a
pass means the concrete integration works, including the `stream: false` +
resp.json() contract that the forwarder had to be fixed to honour.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.ai_classifier import AIClassifier, AIConfig

LOGS = []
def log(msg):
    LOGS.append(str(msg))
    print("   [log]", msg)

cfg = AIConfig(
    api_key="workbuddy",
    model="deepseek-v4.1-flash",
    base_url="http://127.0.0.1:7910/v1",
)
print(f"base_url = {cfg.base_url}")
print(f"model    = {cfg.model}")
print(f"is_valid = {cfg.is_valid}\n")

clf = AIClassifier(cfg, log_cb=log)

videos = [
    {"bvid": "BV1a", "aid": 1, "title": "从零实现一个 RISC-V 处理器"},
    {"bvid": "BV1b", "aid": 2, "title": "10分钟搞定红烧肉"},
    {"bvid": "BV1c", "aid": 3, "title": "周杰伦 - 晴天 现场版"},
    {"bvid": "BV1d", "aid": 4, "title": "NBA 总决赛集锦"},
    {"bvid": "BV1e", "aid": 5, "title": "Rust 所有权机制详解"},
]

print("=== calling classify() against the live 7910 forwarder ===")
try:
    result = clf.classify(videos)
    print("\n=== RESULT ===")
    for k, v in result.items():
        titles = [x.get("title", "?") for x in v]
        print(f"  {k}: {len(v)} -> {titles}")
    ok = any(len(v) > 0 for v in result.values())
    print("\n" + ("PASS: live 7910 classification worked" if ok else "FAIL: empty classification"))
    sys.exit(0 if ok else 1)
except Exception as e:
    print(f"\nFAIL: {type(e).__name__}: {e}")
    print("\n--- classifier logs ---")
    for l in LOGS:
        print("  ", l)
    sys.exit(1)
