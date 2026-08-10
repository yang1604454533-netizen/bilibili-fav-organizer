"""核心库冒烟测试：mock AI API + 真实导出。

不依赖网络/B站账号，验证：
1. AIClassifier 调用 OpenAI 兼容接口并正确解析分类
2. exporter 导出 CSV / Markdown / HTML
3. BiliClient 的 Cookie 解析与 URL 拼接逻辑
"""
from __future__ import annotations

import json
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from app.ai_classifier import AIClassifier, AIConfig
from app.bilibili import BiliClient
from app import exporter


class MockAIHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        # 提取 batch 里的 bvid 与标题，做简单位于标题关键词的分类
        prompt = body["messages"][0]["content"]
        items_block = prompt.split("待分类视频：", 1)[-1]
        # 逐行解析 "- bvid: BV... | 标题: xxx | 简介: ..."
        items = []
        for line in items_block.splitlines():
            line = line.strip()
            if not line.startswith("- bvid:"):
                continue
            meta = dict(
                part.split(": ", 1)
                for part in line[2:].split(" | ")
                if ": " in part
            )
            items.append({"bvid": meta.get("bvid", ""), "title": meta.get("标题", ""), "intro": meta.get("简介", "")})
        result = []
        for it in items:
            cat = "其他"
            kw = (it["title"] + it["intro"]).lower()
            if "美食" in kw:
                cat = "美食"
            elif "音乐" in kw or "吉他" in kw:
                cat = "音乐"
            elif "科技" in kw or "手机" in kw:
                cat = "科技"
            elif "体育" in kw or "篮球" in kw or "nba" in kw:
                cat = "体育"
            result.append({"bvid": it["bvid"], "category": cat})
        resp = json.dumps({"choices": [{"message": {"content": json.dumps({"items": result})}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(resp)))
        self.end_headers()
        self.wfile.write(resp)

    def log_message(self, *args):
        pass


def main():
    server = HTTPServer(("127.0.0.1", 0), MockAIHandler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"[mock] AI 服务已启动 :{port}")

    cfg = AIConfig(
        model="mock-model",
        api_key="sk-mock",
        base_url=f"http://127.0.0.1:{port}/v1",
        categories=["科技", "美食", "音乐", "体育", "游戏", "影视", "知识", "动漫", "生活", "其他"],
    )
    videos = [
        {"bvid": "BV1aaaa", "title": "新一代手机芯片深度评测", "intro": "科技", "upper_name": "UP1", "link": "https://www.bilibili.com/video/BV1aaaa", "fav_time": 1700000000, "pubtime": 1690000000, "duration": 660, "play_count": 1234},
        {"bvid": "BV1bbbb", "title": "家常红烧肉做法", "intro": "美食", "upper_name": "UP2", "link": "https://www.bilibili.com/video/BV1bbbb", "fav_time": 1700000001, "pubtime": 1690000001, "duration": 420, "play_count": 56},
        {"bvid": "BV1cccc", "title": "吉他独奏《晴天》", "intro": "音乐", "upper_name": "UP3", "link": "https://www.bilibili.com/video/BV1cccc", "fav_time": 1700000002, "pubtime": 1690000002, "duration": 300, "play_count": 789},
        {"bvid": "BV1dddd", "title": "NBA 总决赛精彩集锦", "intro": "体育", "upper_name": "UP4", "link": "https://www.bilibili.com/video/BV1dddd", "fav_time": 1700000003, "pubtime": 1690000003, "duration": 900, "play_count": 99999},
        {"bvid": "BV1eeee", "title": "某不知名杂谈", "intro": "", "upper_name": "UP5", "link": "https://www.bilibili.com/video/BV1eeee", "fav_time": 1700000004, "pubtime": 1690000004, "duration": 120, "play_count": 1},
    ]
    logs = []

    clf = AIClassifier(cfg, log_cb=logs.append)
    result = clf.classify(videos)
    print(f"[分类结果] { {k: len(v) for k, v in result.items()} }")
    assert result.get("科技") and result["科技"][0]["bvid"] == "BV1aaaa"
    assert result.get("美食") and result["美食"][0]["bvid"] == "BV1bbbb"
    assert result.get("音乐") and result["音乐"][0]["bvid"] == "BV1cccc"
    assert result.get("体育") and result["体育"][0]["bvid"] == "BV1dddd"
    assert result.get("其他") and result["其他"][0]["bvid"] == "BV1eeee"

    out = Path(tempfile.mkdtemp())
    csv_files = exporter.export_csv(result, out)
    md = exporter.export_markdown(result, out)
    html = exporter.export_html(result, out)
    xlsx = exporter.export_excel(result, out)
    print(f"[导出] CSV: {[p.name for p in csv_files]}")
    print(f"[导出] MD: {md.name} ({md.stat().st_size}B), HTML: {html.name} ({html.stat().st_size}B), XLSX: {xlsx.name} ({xlsx.stat().st_size}B)")
    assert len(csv_files) == 5
    assert md.exists() and html.exists() and xlsx.exists()
    assert "美食（1 条）" in md.read_text(encoding="utf-8")
    assert "BV1bbbb" in html.read_text(encoding="utf-8")
    for p in csv_files:
        assert p.stat().st_size > 0
    # Excel 分表验证：每个分类一个工作表
    import openpyxl
    wb = openpyxl.load_workbook(xlsx, read_only=True)
    print(f"[导出] XLSX 工作表: {wb.sheetnames}")
    assert set(wb.sheetnames) == {"科技", "美食", "音乐", "体育", "其他"}, f"工作表不符: {wb.sheetnames}"
    ws = wb["美食"]
    rows = list(ws.iter_rows(values_only=True))
    assert rows[0][0] == "标题" and rows[1][1] == "美食" and rows[1][2] == "BV1bbbb", f"美食表内容错: {rows[:2]}"
    assert exporter.count_excel_sheets(xlsx) == 5

    # Cookie 解析
    client = BiliClient(request_delay=0)
    client.login_by_cookie("SESSDATA=abc123; bili_jct=xyz; DedeUserID=686127; other=1")
    assert client.is_logged_in and client.uid == "686127"

    # 扫码轮询：验证 _get_raw 对非零 code 的透传
    import app.bilibili as bb

    class FakeResp:
        def __init__(self, payload, headers=None):
            self._payload = payload
            self._headers = headers or {}

        def json(self):
            return self._payload

        @property
        def status_code(self):
            return 200

        @property
        def headers(self):
            return self._headers

    calls = {"n": 0}

    def fake_get(url, params=None, **kwargs):
        calls["n"] += 1
        # 第一个响应：未扫码；如果带了 accept 则透传
        if url.endswith("qrcode/generate"):
            return FakeResp({"code": 0, "data": {"url": "https://passport.bilibili.com/x/passport-login/web/login?code=test", "qrcode_key": "key123"}})
        # poll：真实结构为外层 code=0 + 内层 data.code=扫码状态
        return FakeResp({"code": 0, "message": "OK", "data": {"code": 86101, "message": "未扫码"}})

    client._session.get = fake_get
    gen = client.qr_generate()
    assert gen["qrcode_key"] == "key123", f"qr_generate 解析错误: {gen}"
    poll = client.qr_poll("key123")
    assert poll["code"] == 86101, f"qr_poll 应透传 86101 而非抛异常/默认: {poll}"
    # 已扫码、已失效、成功（内层 data.code 才是状态码）
    states = [86090, 86038, 0]

    def fake_get2(url, params=None, **kwargs):
        code = states.pop(0)
        return FakeResp({"code": 0, "message": "OK", "data": {"code": code, "message": "x"}})

    client._session.get = fake_get2
    assert client.qr_poll("key").get("code") == 86090
    assert client.qr_poll("key").get("code") == 86038
    assert client.qr_poll("key").get("code") == 0
    # Set-Cookie 吸收（含 Path/HttpOnly 属性、逗号分隔多 cookie）
    cc = bb.BiliClient(request_delay=0)
    cc._absorb_set_cookie(
        "SESSDATA=abc; Path=/; HttpOnly, bili_jct=ct0; Path=/; Expires=Fri, 01 Jan 2030 00:00:00 GMT, DedeUserID=42; Path=/"
    )
    assert cc.cookies.get("SESSDATA") == "abc"
    assert cc.cookies.get("bili_jct") == "ct0"
    assert cc.cookies.get("DedeUserID") == "42"

    # 容错解析：坏 JSON（模型被简介中的引号干扰）→ 正则降级逐条提取
    from app.ai_classifier import AIClassifier as _AC
    clf2 = _AC(AIConfig(api_key="k", model="m", base_url="http://x/v1",
                        categories=["科技", "美食", "音乐", "体育", "游戏", "影视", "知识", "动漫", "生活", "其他"]))
    bad_json = ('{"items": [{"bvid": "BV1x1", "category": "科技"}, '
                '{"bvid": "BV1x2", "category": "美食"}], extra: broken ,,, }')
    m = clf2._parse_response(bad_json)
    assert m.get("BV1x1") == "科技" and m.get("BV1x2") == "美食", f"坏JSON正则降级失败: {m}"
    # 单引号变体
    m2 = clf2._parse_response("{'items': [{'bvid': 'BV1y1', 'category': '音乐'}]}")
    assert m2.get("BV1y1") == "音乐", f"单引号解析失败: {m2}"
    # 非法分类名 → 归"其他"
    m3 = clf2._parse_response('{"items": [{"bvid": "BV1z1", "category": "随便写的"}]}')
    assert m3.get("BV1z1") == "其他", f"非法分类兜底失败: {m3}"
    # 完全无法解析 → 抛 ValueError（由上层走启发式）
    try:
        clf2._parse_response("完全不是JSON")
        raise AssertionError("应抛 ValueError")
    except ValueError:
        pass
    # 启发式兜底：按标题关键词粗分类
    heu = clf2._heuristic_classify([
        {"bvid": "BV1h1", "title": "最新显卡评测 性能炸裂", "intro": ""},
        {"bvid": "BV1h2", "title": "水煮鱼的做法 一学就会", "intro": ""},
        {"bvid": "BV1h3", "title": "某日常流水账", "intro": ""},
    ])
    assert heu.get("BV1h1") == "科技", f"启发式科技失败: {heu}"
    assert heu.get("BV1h2") == "美食", f"启发式美食失败: {heu}"
    assert heu.get("BV1h3") == "生活", f"启发式生活失败: {heu}"

    server.shutdown()
    print("\n✅ 全部冒烟测试通过（含扫码状态码、Set-Cookie、容错解析与启发式兜底）")


if __name__ == "__main__":
    main()