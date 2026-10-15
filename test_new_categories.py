"""Verify the NEW 17-category config actually drives classification correctly.

Reads the real config.json the GUI reads, then runs the project's own
AIClassifier against the live 7910 forwarder with a sample of titles drawn
from the user's actual export — covering each new category.

A pass means: the config is valid, the GUI will pick it up, and the model
can actually distinguish the new (finer) categories, including the
游戏开发 vs 游戏娱乐 split the user specifically asked for.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.ai_classifier import AIClassifier, AIConfig
from app.config import CONFIG_PATH, load_config

print(f"config path : {CONFIG_PATH}")
cfg = load_config()
cats = cfg.get("categories", [])
print(f"categories  : {len(cats)}")
for c in cats:
    print(f"   - {c}")
assert len(cats) == 17, f"expected 17 categories, got {len(cats)}"

# Real titles sampled from the user's export, chosen to probe the tricky splits.
samples = [
    ("AI工具与模型", "LM Studio 全面指南：本地跑大模型，从下载到 OpenAI 兼容 API"),
    ("编程与开发", "彻底弄懂C/C++内存布局/分段 | 堆 栈 静态内存"),
    ("游戏开发", "【从零打造 C++ 2D 游戏引擎: Scion2D 】中文配音【01】"),
    ("游戏娱乐", "【maimai】[右]The wheel to the right【宴】 ALL PERFECT"),
    ("开源与效率工具", "一个开源软件顶掉电脑里 8 个小工具！ToolKnit Desktop 实测"),
    ("数码硬件外设", "锐评酷态科销量前15的充电宝从夯到拉！"),
    ("健身与体态", "成年后脊柱侧弯从26°到17°，00后自救指南"),
    ("穿搭颜值与好物", "细！软！塌！发质的打理教学！超有料！"),
    ("宿舍与校园生活", "准大学生必看大学宿舍忠告，尤其第三条"),
    ("动漫与番剧", "7月新番首集锐评速递"),
    ("影视与剧集解说", "【墨菲】一口气看完《风骚律师》1-6季解读（全剧终）"),
    ("音乐", "19分钟 学会乐理！"),
    ("学习与考试", "【Anki+欧路词典】这是我见过最高效的背单词工具组合"),
    ("二次元亚文化", "锐评男娘从夯到拉2"),
    ("ASMR助眠", "【ASMR/KU100】点进来就停不下来了..深度掏耳，解压又助眠"),
    ("生活其他", "最爱牛肉面之人"),
]

videos = [
    {"bvid": f"BV{i:03d}", "aid": i + 1, "title": title, "intro": ""}
    for i, (_, title) in enumerate(samples)
]

ai = AIConfig(
    api_key=cfg.get("api_key") or "x",
    model=cfg.get("model"),
    base_url=cfg.get("base_url"),
    temperature=cfg.get("temperature", 0.2),
    max_tokens=cfg.get("max_tokens", 4000),
    categories=cats,
)
clf = AIClassifier(ai, log_cb=lambda m: print("   [log]", m))

print(f"\n=== classifying {len(videos)} sample titles via {ai.base_url} ===")
result = clf.classify(videos)

by_title = {}
for cat, items in result.items():
    for it in items:
        by_title[it.get("title")] = cat

print("\n=== expected vs actual ===")
hits = 0
for expected, title in samples:
    actual = by_title.get(title, "<not returned>")
    ok = actual == expected
    hits += ok
    print(f"  {'OK  ' if ok else 'DIFF'} {expected:<16} -> {actual:<16} | {title[:34]}")

print(f"\n{hits}/{len(samples)} matched the expected category")

# Every title must be returned exactly once.
missing = [t for _, t in samples if t not in by_title]
print(f"titles not returned: {len(missing)}")
for m in missing:
    print("   !", m)

# The categories actually used must all be from the configured list.
bad = [c for c in result if c not in cats]
print(f"invented categories (should be 0): {len(bad)} {bad}")

print("\n=== category distribution ===")
for c in cats:
    n = len(result.get(c, []))
    if n:
        print(f"  {c:<16} {n}")

sys.exit(0 if not bad and not missing else 1)
