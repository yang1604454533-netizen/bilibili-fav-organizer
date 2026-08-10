"""写操作 API 冒烟测试：mock B站服务器验证请求参数与流程。"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from app.bilibili import BiliClient

RECEIVED: list[dict] = []


class MockBiliHandler(BaseHTTPRequestHandler):
    def _read_form(self):
        from urllib.parse import parse_qs, unquote
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode()
        data = {k: v[0] for k, v in parse_qs(body).items()}
        return data

    def do_GET(self):
        if "folder/created/list-all" in self.path:
            resp = {"code": 0, "data": {"list": [
                {"id": 100, "fid": 100, "mid": 1, "title": "默认收藏夹", "media_count": 2, "attr": 0},
            ]}}
        else:
            resp = {"code": 0, "data": {}}
        self._send(resp)

    def do_POST(self):
        data = self._read_form()
        RECEIVED.append({"path": self.path, "data": data})
        if "folder/add" in self.path:
            resp = {"code": 0, "data": {"id": 999}}
        elif "resource/deal" in self.path:
            resp = {"code": 0, "data": {}}
        else:
            resp = {"code": 0, "data": {}}
        self._send(resp)

    def _send(self, payload: dict):
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def main():
    server = HTTPServer(("127.0.0.1", 0), MockBiliHandler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"[mock] B站服务器 :{port}")

    base = f"http://127.0.0.1:{port}"
    client = BiliClient(request_delay=0)
    # 模拟扫码登录后的完整 cookie（含 bili_jct）
    client.set_cookie("SESSDATA=abc; bili_jct=csrf123; DedeUserID=686127")
    assert client.csrf == "csrf123"
    print("[1] csrf 提取 OK")

    # 1) 创建收藏夹（走 mock）
    # 覆盖 URL：BiliClient 里是硬编码 api.bilibili.com，需要替换 session 指向 mock
    import app.bilibili as bb
    orig_get, orig_post = bb.BiliClient._get_raw, bb.BiliClient._post
    # 用 monkeypatch 把请求转发到 mock 服务器
    def fake_raw(self, url, params=None, accept_error_code=False, **kw):
        url = url.replace("https://api.bilibili.com", base)
        return orig_get(self, url, params=params, accept_error_code=accept_error_code, **kw)

    def fake_post(self, url, data=None, **kw):
        url = url.replace("https://api.bilibili.com", base)
        return orig_post(self, url, data=data, **kw)

    bb.BiliClient._get_raw = fake_raw
    bb.BiliClient._post = fake_post
    try:
        # 创建收藏夹（不存在的分类）
        mid = client.create_folder("测试分类")
        assert mid == 999, f"create_folder 返回 {mid}"
        print("[2] create_folder OK, 请求:", RECEIVED[-1]["data"])
        req = RECEIVED[-1]["data"]
        assert req["title"] == "测试分类" and req["privacy"] == "0" and req["csrf"] == "csrf123", req
        assert "folder/add" in RECEIVED[-1]["path"]

        # 2) 移动视频
        client.move_video_to_folder(aid=12345, add_media_id=999, del_media_id=100)
        req2 = RECEIVED[-1]["data"]
        print("[3] move OK, 请求:", req2)
        assert req2["rid"] == "12345" and req2["type"] == "2", req2
        assert req2["add_media_ids"] == "999" and req2["del_media_ids"] == "100", req2
        assert req2["csrf"] == "csrf123"
        assert "resource/deal" in RECEIVED[-1]["path"]

        # 3) apply_classification 全流程（含查重复用）
        videos = [
            {"bvid": "BV1a", "aid": 1, "title": "手机评测"},
            {"bvid": "BV1b", "aid": 2, "title": "美食教程"},
        ]
        categorized = {"科技": [videos[0]], "美食": [videos[1]]}
        moved = client.apply_classification(videos, categorized, source_media_id=100)
        print("[4] apply OK:", moved)
        assert moved == {"科技": 1, "美食": 1}, moved
        # 应发出：2 个 create + 2 个 deal
        creates = [r for r in RECEIVED if "folder/add" in r["path"]]
        deals = [r for r in RECEIVED if "resource/deal" in r["path"]]
        print(f"[5] 请求统计: create={len(creates)} deal={len(deals)}")
        # 手动 move 1 次 + apply 2 次 = 3 次 deal；create 手动 1 次 + apply 2 次 = 3 次
        assert len(deals) == 3, f"deal 数 {len(deals)}"
        assert len(creates) == 3, f"create 数 {len(creates)}"
    finally:
        bb.BiliClient._get_raw = orig_get
        bb.BiliClient._post = orig_post
    server.shutdown()
    print("\n✅ 写操作冒烟测试通过")


if __name__ == "__main__":
    main()