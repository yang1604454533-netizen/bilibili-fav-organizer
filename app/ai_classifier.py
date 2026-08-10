"""B站收藏夹整理助手 - AI 分类器

支持两种接入方式：
1. DeepSeek 官方 API（默认配置，兼容 OpenAI 协议）
2. 自定义 OpenAI 兼容 API（用户填写 base_url / api_key / model）

分类器对每批视频并行请求、失败自动重试，并从 JSON 中安全解析分类结果。
"""

from __future__ import annotations

import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable, Optional

import requests

DEFAULT_CATEGORIES = ["科技", "美食", "音乐", "体育", "游戏", "影视", "知识", "动漫", "生活", "其他"]


@dataclass
class AIConfig:
    """AI 接入配置。is_valid 仅在两项都非空时成立。"""

    model: str = "deepseek-chat"
    api_key: str = ""
    base_url: str = "https://api.deepseek.com/v1"
    temperature: float = 0.2
    max_tokens: int = 4000
    categories: list = field(default_factory=lambda: list(DEFAULT_CATEGORIES))

    @property
    def is_valid(self) -> bool:
        return bool(self.api_key.strip()) and bool(self.model.strip())

    @property
    def provider_name(self) -> str:
        if "deepseek" in self.base_url.lower():
            return "DeepSeek"
        if self.base_url.strip():
            return "自定义"
        return "未配置"


_CONTEXT_PROMPT = """你是一个视频分类助手。请把下面的 B站视频逐条正确归类到给定的分类之一。

可用分类：{categories}

规则：
- 只输出一个 JSON 对象，不要输出任何其他文字、解释或 Markdown 代码块。
- JSON 格式：{{"items": [{{"bvid": "...", "category": "分类名"}}]}}
- 每条视频必须对应一条结果，bvid 必须原样返回。
- 必须根据标题和简介的实际内容判断，选择最贴切的分类；严禁默认归"其他"。
- 只有确实无法归入任何分类时，才使用"其他"。
- 分类名必须严格来自"可用分类"列表，不要自创分类名。

待分类视频：
{items}
"""


class AIClassifier:
    """调用大模型 API 将视频列表分类。线程安全。"""

    def __init__(self, config: AIConfig, session: Optional[requests.Session] = None,
                 progress_cb: Optional[Callable[[int, int], None]] = None,
                 log_cb: Optional[Callable[[str], None]] = None,
                 max_workers: int = 4):
        self.config = config
        self._session = session or requests.Session()
        self._session.trust_env = False  # 忽略系统代理，直连
        self._progress_cb = progress_cb
        self._log_cb = log_cb
        self._max_workers = max_workers
        self._lock = threading.Lock()

    def _log(self, msg: str) -> None:
        if self._log_cb:
            try:
                self._log_cb(msg)
            except Exception:
                pass

    def _bump_progress(self, done: int, total: int) -> None:
        if self._progress_cb:
            try:
                self._progress_cb(done, total)
            except Exception:
                pass

    # ------------------------------------------------------------------ #
    # 对外主入口：输入 [{bvid, title, intro, ...}] 的视频元数据列表          #
    # ------------------------------------------------------------------ #
    def classify(self, videos: list[dict]) -> dict[str, list[dict]]:
        """把视频列表分类，返回 {分类名: [视频dict]}。"""
        if not videos:
            return {}
        if not self.config.is_valid:
            raise ValueError("AI 配置无效：请先填写 API Key 并确认模型名")

        cat_map: dict[str, str] = {}          # bvid -> 分类名
        # 单批视频数：8 条一批（推理模型的 reasoning 会占 completion tokens，
        # 批量过大会导致 JSON 输出被 max_tokens 截断 → 解析失败）
        batch_size = max(1, min(8, self._max_workers * 2))
        batches = [videos[i:i + batch_size] for i in range(0, len(videos), batch_size)]
        total = len(batches)

        self._log(f"开始 AI 分类：共 {len(videos)} 条视频，分 {total} 批，模型={self.config.model}")
        done = 0
        with ThreadPoolExecutor(max_workers=self._max_workers) as pool:
            futures = [pool.submit(self._classify_batch, b) for b in batches]
            for fut in as_completed(futures):
                try:
                    result = fut.result()
                    cat_map.update(result)
                except Exception as exc:
                    self._log(f"批次分类失败：{exc}，该批视频归入'其他'")
                    # 批次内容拿到的是原始视频列表，需要兜底
                    # （在 _classify_batch 内部已做兜底，这里仅记录日志）
                done += 1
                self._bump_progress(done, total)

        result: dict[str, list[dict]] = {}
        for v in videos:
            cat = cat_map.get(v["bvid"], "其他")
            result.setdefault(cat, []).append(v)
        # 保持分类顺序与配置一致
        ordered = {}
        for c in self.config.categories:
            if c in result:
                ordered[c] = result[c]
        for c, items in result.items():
            if c not in ordered:
                ordered[c] = items
        return ordered

    # ------------------------------------------------------------------ #
    # VM
    # ------------------------------------------------------------------ #
    def _build_prompt(self, batch: list[dict]) -> str:
        def _clean(s: str, limit: int = 120) -> str:
            """清洗文本：去换行/控制字符、压缩空白、截断，避免干扰 JSON 输出。"""
            s = (s or "").replace("\r", " ").replace("\n", " ").replace("\t", " ")
            s = re.sub(r"\s+", " ", s).strip()
            return s[:limit]
        items_text = []
        for v in batch:
            line = (f"- bvid: {v.get('bvid', '')} | 标题: {_clean(v.get('title', ''), 80)}"
                    f" | 简介: {_clean(v.get('intro', ''), 120)}")
            items_text.append(line)
        return _CONTEXT_PROMPT.format(
            categories="、".join(self.config.categories),
            items="\n".join(items_text),
        )

    def _classify_batch(self, batch: list[dict], retries: int = 2) -> dict[str, str]:
        """单批分类（带重试），返回 {bvid: category}。"""
        prompt = self._build_prompt(batch)
        content = self._chat(prompt, retries=retries)
        mapping: dict[str, str] = {}
        if content:
            try:
                mapping = self._parse_response(content)
            except ValueError as exc:
                self._log(f"解析 AI 返回失败：{exc}")
        if not mapping:
            self._log("AI 结果无法使用，改用本地关键词启发式分类")
            mapping = self._heuristic_classify(batch)
        # 兜底：仍缺失的 bvid 归入"其他"
        for v in batch:
            mapping.setdefault(v["bvid"], "其他")
        return mapping

    # ------------------------------------------------------------------ #
    # 本地启发式兜底：AI 结果不可用时，按标题/简介关键词粗分类            #
    # ------------------------------------------------------------------ #
    def _heuristic_classify(self, batch: list[dict]) -> dict[str, str]:
        mapping = {}
        rules = [
            ("科技", ["科技", "手机", "电脑", "数码", "评测", "芯片", "AI", "人工智能", "软件", "硬件", "编程", "代码", "开源", "机器", "新能源", "电动车", "汽车", "科普"]),
            ("美食", ["美食", "吃", "菜谱", "做饭", "烹饪", "料理", "探店", "小吃", "餐厅", "零食", "烘焙", "厨房", "做法", "食谱"]),
            ("音乐", ["音乐", "歌", "吉他", "钢琴", "乐器", "演唱", "翻唱", "现场", "乐队", "专辑", "音综", "乐评"]),
            ("体育", ["体育", "足球", "篮球", "球", "比赛", "赛事", "健身", "跑步", "运动", "nba", "cba", "世界杯", "奥运"]),
            ("游戏", ["游戏", "手游", "网游", "主机", "ps5", "switch", "steam", "电玩", "开黑", "电竞", "攻略", "吃鸡", "王者", "原神", "我的世界", "minecraft"]),
            ("影视", ["电影", "影评", "电视剧", "剧集", "美剧", "日剧", "韩剧", "番外", "预告", "影视", "解说", "悬疑"]),
            ("知识", ["知识", "学习", "课程", "教程", "讲解", "历史", "地理", "物理", "数学", "化学", "生物", "心理", "经济", "哲学", "纪录片", "干货", "方法论"]),
            ("动漫", ["动漫", "动画", "二次元", "番剧", "漫画", "手办", "cos", "cv", "声优", "op", "ed"]),
            ("生活", ["生活", "日常", "vlog", "探店", "旅行", "穿搭", "美妆", "家居", "宠物", "收纳", "手工", "记录"]),
        ]
        valid = set(self.config.categories)
        for v in batch:
            text = f"{v.get('title', '')} {v.get('intro', '')}".lower()
            cat = "其他"
            for c, kws in rules:
                if c not in valid:
                    continue
                if any(kw in text for kw in kws):
                    cat = c
                    break
            mapping[v["bvid"]] = cat
        return mapping

    def _chat(self, prompt: str, retries: int = 2) -> str:
        """调用 OpenAI 兼容的 /chat/completions 接口。

        对截断（返回内容未闭合）自动以更大的 max_tokens 重试一次，
        应对推理模型 reasoning 占 token 导致 JSON 被截断的情况。
        """
        url = self.config.base_url.rstrip("/") + "/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }
        last_exc: Optional[Exception] = None
        max_tokens = self.config.max_tokens
        for attempt in range(retries + 1):
            payload = {
                "model": self.config.model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": self.config.temperature,
                "max_tokens": max_tokens,
                "stream": False,
            }
            try:
                resp = self._session.post(url, json=payload, headers=headers, timeout=60)
                if resp.status_code == 429:
                    raise RuntimeError(f"触发限流(429)：{resp.text[:200]}")
                resp.raise_for_status()
                data = resp.json()
                content = (data["choices"][0]["message"]["content"] or "").strip()
                if not content:
                    # 推理模型偶发：reasoning 占满输出导致 content 为空，属可自愈情况
                    raise RuntimeError("模型偶发未输出正文，自动重试")
                # 截断检测：期待 JSON 但输出未闭合 → 增大 max_tokens 重试
                if self._looks_truncated(content):
                    self._log(f"返回内容疑似被 max_tokens 截断（长度 {len(content)}），增大到 {max_tokens * 2} 重试")
                    max_tokens = max_tokens * 2
                    raise RuntimeError("content 被截断")
                return content
            except Exception as exc:  # noqa: BLE001 - 需要捕获一切以重试
                last_exc = exc
                if attempt < retries:
                    time_wait = 1.5 * (attempt + 1)
                    self._log(f"请求未成功（{exc}），{time_wait:.1f}s 后自动重试...")
                    threading.Event().wait(time_wait)
        raise RuntimeError(f"AI 请求多次失败：{last_exc}")

    @staticmethod
    def _looks_truncated(content: str) -> bool:
        """判断 JSON 内容是否被截断：有 '{' 但缺少配对的 '}' 结尾。"""
        stripped = content.strip()
        if not stripped:
            return False
        # 去掉可能的 markdown 围栏后再判断
        if stripped.startswith("```"):
            stripped = re.sub(r"^```(?:json)?\s*", "", stripped).strip()
            stripped = re.sub(r"```\s*$", "", stripped).strip()
        open_cnt = stripped.count("{")
        close_cnt = stripped.count("}")
        return open_cnt > close_cnt

    # ------------------------------------------------------------------ #
    # 解析 AI 返回内容（容忍代码块/多余文字/缺失字段）                       #
    # ------------------------------------------------------------------ #
    def _parse_response(self, content: str) -> dict[str, str]:
        text = content.strip()
        # 去掉 Markdown 代码块围栏
        fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
        if fenced:
            text = fenced.group(1).strip()
        data = None
        # 1) 先尝试整体 JSON 解析（容忍首尾杂文本）
        candidates = [text]
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            candidates.append(text[start:end + 1])
        for cand in candidates:
            try:
                data = json.loads(cand)
                break
            except json.JSONDecodeError:
                continue
        if isinstance(data, dict):
            items = data.get("items")
            if isinstance(items, list):
                mapping = self._extract_mapping(items)
                if mapping:
                    return mapping
        # 2) 整体解析失败 → 逐条用正则提取 "bvid": "...", "category": "..."
        mapping = self._extract_via_regex(text)
        if mapping:
            return mapping
        raise ValueError("返回内容无法解析为分类结果")

    def _extract_mapping(self, items: list) -> dict[str, str]:
        """从 items 列表提取 {bvid: category}，容忍缺失/非法分类。"""
        mapping = {}
        valid_cats = set(self.config.categories)
        for it in items:
            if not isinstance(it, dict):
                continue
            bvid = str(it.get("bvid", "")).strip()
            cat = str(it.get("category", "")).strip()
            if not bvid or not cat:
                continue
            if cat not in valid_cats:
                cat = "其他"
            mapping[bvid] = cat
        return mapping

    def _extract_via_regex(self, text: str) -> dict[str, str]:
        """正则逐条提取 bvid 与 category，容忍单引号/中文引号/尾逗号等 AI 输出缺陷。"""
        mapping = {}
        valid_cats = set(self.config.categories)
        # 匹配 "bvid": "BVxxx", "category": "xxx" （键/值均可带引号变体）
        pat = re.compile(
            r'''["']bvid["']\s*:\s*["']([A-Za-z0-9]+)["']\s*,\s*["']category["']\s*:\s*["']([^"']+)["']'''
        )
        for m in pat.finditer(text):
            bvid, cat = m.group(1), m.group(2).strip()
            if bvid and cat:
                mapping[bvid] = cat if cat in valid_cats else "其他"
        return mapping