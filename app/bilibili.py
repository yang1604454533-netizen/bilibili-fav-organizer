"""B站收藏夹整理助手 - B站客户端

封装扫码登录、收藏夹列表、收藏夹内容、视频详情等接口。
所有请求带浏览器 UA 与 Referer 降低风控概率，并对收藏夹内容请求做间隔限流。
"""

from __future__ import annotations

import time
import threading
from dataclasses import dataclass, field
from typing import Callable, Optional

import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# 扫码轮询状态码
QR_OK = 0            # 登录成功（同时 Set-Cookie SESSDATA 等）
QR_NOT_SCANNED = 86101   # 未扫码
QR_SCANNED = 86090       # 已扫码未确认
QR_EXPIRED = 86038       # 二维码失效


class BilibiliError(Exception):
    """B站 API 业务错误。"""

    def __init__(self, message: str, is_risk: bool = False, risk_type: str = "ip"):
        super().__init__(message)
        self.is_risk = is_risk        # True = 风控/限流类错误（412 等）
        self.risk_type = risk_type    # "ip" = IP 级风控，"account" = 账号级风控


@dataclass
class FavoriteFolder:
    id: int          # media_id（用于拉取内容）
    fid: int         # 收藏夹真实 id
    mid: int         # 创建者 uid
    title: str
    media_count: int
    attr: int = 0

    @property
    def is_private(self) -> bool:
        return bool(self.attr & 1)


@dataclass
class FavoriteVideo:
    bvid: str
    aid: int
    title: str
    cover: str
    intro: str = ""
    duration: int = 0
    link: str = ""
    fav_time: int = 0
    pubtime: int = 0
    upper_name: str = ""
    upper_mid: int = 0
    play_count: int = 0

    def to_dict(self) -> dict:
        return {
            "bvid": self.bvid,
            "aid": self.aid,
            "title": self.title,
            "cover": self.cover,
            "intro": self.intro,
            "duration": self.duration,
            "link": self.link or f"https://www.bilibili.com/video/{self.bvid}",
            "fav_time": self.fav_time,
            "pubtime": self.pubtime,
            "upper_name": self.upper_name,
            "upper_mid": self.upper_mid,
            "play_count": self.play_count,
        }


class BiliClient:
    def __init__(self,
                 request_delay: float = 1.0,
                 session: Optional[requests.Session] = None,
                 log_cb: Optional[Callable[[str], None]] = None):
        self.request_delay = request_delay
        self._session = session or requests.Session()
        self._session.trust_env = False  # 忽略系统代理，直连（系统代理会干扰 bilibili TLS）
        self._session.headers.update({
            "User-Agent": UA,
            "Referer": "https://www.bilibili.com/",
            "Origin": "https://www.bilibili.com",
        })
        self._log_cb = log_cb
        # Cookie 存储（扫码登录后写入）
        self.cookies: dict[str, str] = {}
        # 简单限流锁：收藏夹内容请求之间 sleep request_delay 秒
        self._throttle = threading.Lock()
        self._last_request = 0.0

    def _log(self, msg: str) -> None:
        if self._log_cb:
            try:
                self._log_cb(msg)
            except Exception:
                pass

    def set_cookie(self, cookie_str: str) -> None:
        """设置 Cookie。支持 'k=v; k2=v2' 格式，或完整 'Cookie: k=v' 头格式。"""
        cookie_str = (cookie_str or "").strip()
        if cookie_str.lower().startswith("cookie:"):
            cookie_str = cookie_str[len("cookie:"):].strip()
        self.cookies.clear()
        for part in cookie_str.split(";"):
            part = part.strip()
            if not part or "=" not in part:
                continue
            k, v = part.split("=", 1)
            self.cookies[k.strip()] = v.strip()
        # 同步到 session
        self._session.cookies.clear()
        for k, v in self.cookies.items():
            self._session.cookies.set(k, v, domain=".bilibili.com")

    @property
    def is_logged_in(self) -> bool:
        return bool(self.cookies.get("SESSDATA"))

    @property
    def uid(self) -> str:
        return self.cookies.get("DedeUserID", "")

    @property
    def csrf(self) -> str:
        """bili_jct，写接口（csrf）认证用。"""
        return self.cookies.get("bili_jct", "")

    def _post(self, url: str, data: Optional[dict] = None, **kwargs) -> dict:
        """带限流与 csrf 的 POST，返回响应中的 data 字段。

        限流间隔在 [base, base*2] 内随机，模拟真人操作节奏降低风控概率。
        """
        kwargs.setdefault("timeout", 20)
        if self.request_delay > 0:
            with self._throttle:
                gap = time.time() - self._last_request
                import random as _rnd
                wait = self.request_delay * _rnd.uniform(1.0, 2.0)  # 随机化：模拟真人
                if gap < wait:
                    time.sleep(wait - gap)
                self._last_request = time.time()
        body = dict(data or {})
        if "csrf" not in body and self.csrf:
            body["csrf"] = self.csrf
        # 写接口需要视频页 Referer（降低风控概率）
        headers = dict(kwargs.pop("headers", {}))
        headers.setdefault("Referer", "https://www.bilibili.com/video/")
        resp = self._session.post(url, data=body, headers=headers, **kwargs)
        resp_text = resp.text[:300]
        try:
            resp_data = resp.json()
        except ValueError as exc:
            if resp.status_code == 412:
                raise BilibiliError(
                    f"B站风控拦截(412)：操作过于频繁，账号可能已进入风控冷却期。"
                    f"建议先暂停批量操作，稍后重试。响应详情：{resp_text[:120]}",
                    is_risk=True, risk_type="ip") from exc
            raise BilibiliError(f"接口返回非 JSON：{url} ({resp.status_code})") from exc
        if resp_data.get("code", 0) != 0:
            raise BilibiliError(
                f"写操作失败 code={resp_data.get('code')}：{resp_data.get('message', '')} ({url})")
        return resp_data.get("data") or {}

    def _get(self, url: str, params: Optional[dict] = None, **kwargs) -> dict:
        """带限流与统一错误处理的 GET，返回响应中的 data 字段（内层业务数据）。"""
        raw = self._get_raw(url, params=params, **kwargs)
        return raw.get("data") or {}

    def _get_raw(self, url: str, params: Optional[dict] = None,
                 accept_error_code: bool = False, **kwargs) -> dict:
        """原始 GET：返回整个响应字典（含 code / message）。

        accept_error_code=True 时即使 code != 0 也不抛异常，
        用于扫码轮询等需要区分状态码的场景。
        """
        kwargs.setdefault("timeout", 20)
        if self.request_delay > 0:
            with self._throttle:
                gap = time.time() - self._last_request
                if gap < self.request_delay:
                    time.sleep(self.request_delay - gap)
                self._last_request = time.time()
        resp = self._session.get(url, params=params, **kwargs)
        # 若接口要求 Cookie 而 response 返回了 Set-Cookie (扫码登录成功)，自动保存
        setc = resp.headers.get("set-cookie")
        if setc:
            self._absorb_set_cookie(setc)
        try:
            data = resp.json()
        except ValueError as exc:
            raise BilibiliError(f"接口返回非 JSON：{url} ({resp.status_code})") from exc
        if data.get("code", 0) != 0 and not accept_error_code:
            raise BilibiliError(f"接口返回错误 code={data.get('code')}：{data.get('message', '')} ({url})")
        return data

    def _absorb_set_cookie(self, setc: str) -> None:
        """从 Set-Cookie 头中提取键值对，用于扫码登录后保存会话。

        Set-Cookie 头形如：
        "SESSDATA=xxx; Path=/; HttpOnly, bili_jct=yyy; Path=/; ..., DedeUserID=zzz; Path=/; ..."
        按 ", " 切分每个 cookie，取每段第一对 k=v（忽略 Path/HttpOnly/Expires 等属性）。
        """
        for seg in setc.replace(", ", "\x00").split("\x00"):
            seg = seg.strip()
            if not seg:
                continue
            first = seg.split(";", 1)[0].strip()
            if "=" in first:
                k, v = first.split("=", 1)
                k, v = k.strip(), v.strip()
                if k and v:
                    self.cookies[k] = v
                    self._session.cookies.set(k, v, domain=".bilibili.com")

    # ------------------------------------------------------------------ #
    # 扫码登录                                                            #
    # ------------------------------------------------------------------ #
    def qr_generate(self) -> dict:
        """获取登录二维码，返回 {url, qrcode_key}。"""
        data = self._get("https://passport.bilibili.com/x/passport-login/web/qrcode/generate")
        return {"url": data["url"], "qrcode_key": data["qrcode_key"]}

    def qr_poll(self, qrcode_key: str) -> dict:
        """轮询扫码状态，返回 {code, message}。

        B站响应结构：外层 code 恒为 0（请求成功），内层 data.code 才是
        扫码状态（0=成功 / 86101=未扫码 / 86090=已扫码未确认 / 86038=已失效）。
        扫码成功后 Set-Cookie 自动被会话吸收。
        """
        raw = self._get_raw(
            "https://passport.bilibili.com/x/passport-login/web/qrcode/poll",
            params={"qrcode_key": qrcode_key},
            accept_error_code=True,
        )
        data = raw.get("data") or {}
        code = data.get("code", QR_EXPIRED)  # 内层 data.code 才是扫码状态
        msg = data.get("message") or raw.get("message") or ""
        return {"code": code, "message": msg}

    def login_by_cookie(self, cookie_str: str) -> bool:
        """手动粘贴 Cookie 登录。返回是否成功（以 SESSDATA 为准）。"""
        self.set_cookie(cookie_str)
        if not self.is_logged_in:
            raise BilibiliError("Cookie 中缺少 SESSDATA，无法登录（请确认粘贴的是完整 Cookie）")
        self._log(f"已通过 Cookie 登录，账号 UID={self.uid or '未知'}")
        return True

    # ------------------------------------------------------------------ #
    # 收藏夹                                                              #
    # ------------------------------------------------------------------ #
    def get_favorite_folders(self, uid: str = "") -> list[FavoriteFolder]:
        """获取某用户创建的公开收藏夹；传空 uid 时用当前登录用户的 uid。"""
        uid = uid or self.uid
        if not uid:
            raise BilibiliError("未登录且未指定 uid")
        data = self._get("https://api.bilibili.com/x/v3/fav/folder/created/list-all",
                         params={"up_mid": uid})
        folders = []
        for item in data.get("list") or []:
            folders.append(FavoriteFolder(
                id=item.get("id", 0),
                fid=item.get("fid", 0),
                mid=item.get("mid", 0),
                title=item.get("title", ""),
                media_count=item.get("media_count", 0),
                attr=item.get("attr", 0),
            ))
        return folders

    def get_favorites(self, media_id: int, limit: Optional[int] = None,
                      progress_cb: Optional[Callable[[int, int], None]] = None,
                      cancel_event: Optional[threading.Event] = None) -> list[FavoriteVideo]:
        """分页拉取一个收藏夹的全部视频（media_count + has_more 双条件翻页）。

        私有收藏夹需要登录 Cookie。返回按收藏时间倒序的视频列表。
        """
        videos: list[FavoriteVideo] = []
        ps = 20
        pn = 1
        first = True
        total = 0
        while True:
            if cancel_event and cancel_event.is_set():
                self._log("收藏夹拉取已取消")
                break
            data = self._get("https://api.bilibili.com/x/v3/fav/resource/list", params={
                "media_id": media_id,
                "pn": pn,
                "ps": ps,
                "platform": "web",
                "order": "mtime",
                "type": 0,
                "tid": 0,
                "keyword": "",
            })
            if first:
                info = data.get("info") or {}
                total = info.get("media_count") or 0
                if limit is not None:
                    total = min(total, limit)
                first = False
            medias = data.get("medias") or []
            if not medias:
                break
            for m in medias:
                videos.append(self._parse_media(m))
                if limit is not None and len(videos) >= limit:
                    break
            if progress_cb:
                progress_cb(len(videos), total)
            has_more = data.get("has_more", 0)
            if not has_more or (limit is not None and len(videos) >= limit):
                break
            pn += 1
        return videos

    def _parse_media(self, m: dict) -> FavoriteVideo:
        upper = m.get("upper") or {}
        cnt = m.get("cnt_info") or {}
        bvid = m.get("bvid") or ""
        return FavoriteVideo(
            bvid=bvid,
            aid=m.get("id", 0),
            title=m.get("title", ""),
            cover=m.get("cover", ""),
            intro=m.get("intro", ""),
            duration=m.get("duration", 0),
            link=m.get("link", ""),
            fav_time=m.get("fav_time", 0),
            pubtime=m.get("pubtime", 0) or m.get("ctime", 0),
            upper_name=upper.get("name", ""),
            upper_mid=upper.get("mid", 0),
            play_count=cnt.get("play", 0) or 0,
        )

    # ------------------------------------------------------------------ #
    # 视频详情（用于补充简介/UP主等；当前清单导出不强制）                     #
    # ------------------------------------------------------------------ #
    def get_video_detail(self, bvid: str) -> dict:
        data = self._get("https://api.bilibili.com/x/web-interface/view", params={"bvid": bvid})
        return {
            "title": data.get("title", ""),
            "desc": data.get("desc", ""),
            "pic": data.get("pic", ""),
            "pubdate": data.get("pubdate", 0),
            "owner_name": (data.get("owner") or {}).get("name", ""),
        }

    # ------------------------------------------------------------------ #
    # 风控检测：区分 IP 风控 vs 账号风控                                     #
    # ------------------------------------------------------------------ #
    def _detect_risk_type(self) -> str:
        """触发 412 风控后，等几秒再试一个轻量读接口判断风控级别。

        直接用底层 session.get 绕过限流与通用错误处理，精确判断 HTTP 状态码：
        - HTTP 200 / 非 412 的其他码 → 读接口正常 → IP 级风控（冷却短）
        - HTTP 412 → 读接口也被限 → 账号级风控（冷却长）
        - 探测请求只发一次，避免加重风控。
        """
        self._log("正在检测风控级别（等待几秒后探测）…")
        time.sleep(3)
        try:
            self._log("发送探测请求：获取收藏夹列表（只读）…")
            resp = self._session.get(
                "https://api.bilibili.com/x/v3/fav/folder/created/list-all",
                params={"up_mid": self.uid or 0},
                timeout=15,
            )
            if resp.status_code == 412:
                self._log("探测也触发 412 → 账号级风控（冷却较长）")
                return "account"
            # 能正常收到 HTTP 200 或其他非 412 码，说明读操作层已恢复
            self._log(f"探测返回 HTTP {resp.status_code} → IP 级风控（冷却较短）")
            return "ip"
        except requests.RequestException as exc:
            self._log(f"探测请求网络异常：{exc} → 保守判定账号风控")
            return "account"
        except Exception as exc:  # noqa: BLE001
            self._log(f"探测请求异常：{exc} → 保守判定账号风控")
            return "account"

    # ------------------------------------------------------------------ #
    # 写操作：按 AI 分类结果整理收藏夹                                     #
    # 注意：写接口一次只能操作 1 个视频（1 个 rid），且 B站对批量操作有风控，   #
    # 必须慢速限流（request_delay）与分批。                                  #
    # ------------------------------------------------------------------ #
    def create_folder(self, title: str, privacy: int = 0) -> int:
        """创建收藏夹，返回 media_id。重名时返回现有 id。"""
        # 先查重：已存在同名收藏夹则直接返回其 id
        for f in self.get_favorite_folders():
            if f.title == title:
                self._log(f"收藏夹「{title}」已存在，复用 id={f.id}")
                return f.id
        if not self.csrf:
            raise BilibiliError("缺少 bili_jct，无法执行写操作（请重新扫码登录）")
        data = self._post("https://api.bilibili.com/x/v3/fav/folder/add",
                          data={"title": title, "privacy": privacy})
        media_id = data.get("id") or 0
        if not media_id:
            raise BilibiliError(f"创建收藏夹「{title}」失败：返回无 id")
        self._log(f"已创建收藏夹「{title}」（id={media_id}）")
        return media_id

    def move_video_to_folder(self, aid: int, add_media_id: int,
                             del_media_id: Optional[int] = None) -> None:
        """把单个视频加入目标收藏夹（可选：从原收藏夹移除）。

        采用两步策略降低风控：先 add（不带 del），成功后再单独 del。
        :param aid: 视频 aid
        :param add_media_id: 目标收藏夹 media_id
        :param del_media_id: 可选，原收藏夹 media_id（移动时传）
        """
        if not self.csrf:
            raise BilibiliError("缺少 bili_jct，无法执行写操作（请重新扫码登录）")
        # 第一步：加入目标收藏夹
        self._post("https://api.bilibili.com/x/v3/fav/resource/deal",
                   data={"rid": aid, "type": 2, "add_media_ids": add_media_id})
        # 第二步：从原收藏夹移除（间隔限流由 request_delay 保证）
        if del_media_id:
            try:
                self._post("https://api.bilibili.com/x/v3/fav/resource/deal",
                           data={"rid": aid, "type": 2, "del_media_ids": del_media_id})
            except BilibiliError as exc:
                # 加入成功但删除失败：明确提示（否则表现为"复制"）
                raise BilibiliError(
                    f"已加入目标夹但未能从原夹移除（aid={aid}）：{exc}",
                    is_risk=getattr(exc, "is_risk", False)) from exc

    def apply_classification(self, videos: list[dict],
                             categorized: dict[str, list[dict]],
                             source_media_id: int,
                             progress_cb: Optional[Callable[[int, int], None]] = None,
                             cancel_event: Optional[threading.Event] = None) -> dict:
        """按 AI 分类结果整理收藏夹：为每个分类创建收藏夹并把视频移入。

        :param videos: 已加载的原始视频列表 [{bvid, aid, title, ...}]
        :param categorized: AI 分类结果 {分类名: [视频dict]}
        :param source_media_id: 原收藏夹 media_id（移动后从中移除）
        :return: {分类名: 移动成功数}
        """
        if not self.csrf:
            raise BilibiliError("缺少 bili_jct，无法执行写操作（请重新扫码登录）")
        # bvid -> aid 映射
        aid_map = {v.get("bvid"): v.get("aid") for v in videos if v.get("aid")}

        # 1) 为每个非空分类创建/复用收藏夹
        folder_ids: dict[str, int] = {}
        for cat, items in categorized.items():
            if not items:
                continue
            try:
                folder_ids[cat] = self.create_folder(cat)
            except BilibiliError as exc:
                self._log(f"创建分类夹「{cat}」失败：{exc}，跳过该分类")

        # 2) 分批移动视频：每批 batch 条后休息，模拟真人节奏降低风控
        #    IP 级风控自动等待后重试剩余，账号级风控才停止
        batch = 15                       # 每批条数（超过易触发窗口计数）
        batch_pause = 45                 # 批间休息秒数
        ip_cooldown = 300                # IP 风控冷却秒数（5 分钟）
        total = sum(len(items) for items in categorized.values())
        done = 0
        moved: dict[str, int] = {}
        risk_hit = False
        # 展平所有待移动项（保持分类归属）
        pending: list[tuple[str, dict]] = []  # (cat, video)
        for cat, items in categorized.items():
            if not items or cat not in folder_ids:
                continue
            for v in items:
                pending.append((cat, v))
        in_batch = 0
        for cat, v in pending:
            if risk_hit:
                break
            if cancel_event and cancel_event.is_set():
                self._log("已取消移动")
                break
            aid = aid_map.get(v.get("bvid"))
            if not aid:
                done += 1
                if progress_cb:
                    progress_cb(done, total)
                continue
            # 批间休息：每完成 batch 条暂停，让风控窗口计数回落
            if in_batch >= batch:
                self._log(f"已完成 {batch} 条，休息 {batch_pause} 秒降低风控…")
                if cancel_event and cancel_event.wait(batch_pause):
                    self._log("已取消移动")
                    break
                in_batch = 0
            try:
                self.move_video_to_folder(aid, folder_ids[cat], source_media_id)
                moved[cat] = moved.get(cat, 0) + 1
                in_batch += 1
            except BilibiliError as exc:
                self._log(f"移动 {v.get('title', '')[:20]} 失败：{exc}")
                if getattr(exc, "is_risk", False):
                    risk_type = self._detect_risk_type()
                    if risk_type == "ip":
                        # IP 级：等待冷却后自动重试剩余，不中断
                        self._log(f"IP 级风控，等待 {ip_cooldown // 60} 分钟冷却后自动重试剩余…")
                        if cancel_event and cancel_event.wait(ip_cooldown):
                            self._log("已取消移动")
                            break
                        self._log("冷却结束，继续移动剩余视频…")
                        in_batch = 0
                        continue
                    # 账号级：必须停止
                    self._log("检测到账号级风控，已停止后续移动。"
                              "请等待约 1 小时冷却后再试，期间避免任何批量写操作。")
                    exc.risk_type = "account"
                    risk_hit = True
                    break
            done += 1
            if progress_cb:
                progress_cb(done, total)
        return moved