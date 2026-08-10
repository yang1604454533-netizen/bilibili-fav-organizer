"""B站收藏夹整理助手 - CustomTkinter 图形界面"""

from __future__ import annotations

import os
import queue
import threading
import time
from pathlib import Path

import customtkinter as ctk
import qrcode
from PIL import Image, ImageTk

from .ai_classifier import AIClassifier, AIConfig, DEFAULT_CATEGORIES
from .bilibili import (
    QR_EXPIRED,
    QR_NOT_SCANNED,
    QR_OK,
    QR_SCANNED,
    BiliClient,
    BilibiliError,
)
from . import config as appconfig
from . import exporter

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

APP_TITLE = "B站收藏夹整理助手"


class FavOrganizerApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("980x720")
        self.minsize(880, 640)

        # 运行时状态
        self.cfg = appconfig.load_config()
        self.client = BiliClient(request_delay=self._configured_delay(), log_cb=self._log)
        self.ai_config = self._build_ai_config()
        self.qr_key: str = ""
        self.qr_polling = False
        self._ai: AIClassifier | None = None
        self._msg_queue = queue.Queue()
        self._current_folders: list = []          # FavoriteFolder
        self._current_videos: list[dict] = []     # 拉取的收藏视频（dict 形式）
        self._categorized: dict[str, list[dict]] = {}

        self._build_ui()
        # 后台日志轮询
        self.after(200, self._poll_queue)

        self._log(f"欢迎使用 {APP_TITLE}")

    # ------------------------------------------------------------------ #
    # 配置辅助                                                            #
    # ------------------------------------------------------------------ #
    def _configured_delay(self) -> float:
        try:
            return float(self.cfg.get("request_delay", 0.6))
        except (TypeError, ValueError):
            return 0.6

    def _build_ai_config(self) -> AIConfig:
        def _num(key: str, default: float, cast):
            try:
                return cast(self.cfg.get(key, default))
            except (TypeError, ValueError):
                return default
        cats = self.cfg.get("categories")
        categories = list(cats) if isinstance(cats, list) and cats else list(DEFAULT_CATEGORIES)
        return AIConfig(
            model=str(self.cfg.get("model", "deepseek-chat")),
            api_key=str(self.cfg.get("api_key", "")),
            base_url=str(self.cfg.get("base_url", "https://api.deepseek.com/v1")),
            temperature=_num("temperature", 0.2, float),
            max_tokens=_num("max_tokens", 4000, int),
            categories=categories,
        )

    # ------------------------------------------------------------------ #
    # UI 构建                                                            #
    # ------------------------------------------------------------------ #
    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        # ---- 顶栏：登录状态 + 操作按钮 ----
        top = ctk.CTkFrame(self)
        top.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 0))
        top.grid_columnconfigure(0, weight=1)

        self.login_status = ctk.CTkLabel(top, text="未登录", text_color="#888888")
        self.login_status.grid(row=0, column=0, sticky="w", padx=12, pady=8)

        self.btn_qr = ctk.CTkButton(top, text="扫码登录", width=96, command=self._start_qr_login)
        self.btn_qr.grid(row=0, column=1, padx=4, pady=8)
        self.btn_cookie = ctk.CTkButton(top, text="粘贴Cookie", width=96, fg_color="#444444",
                                        hover_color="#555555", command=self._cookie_login_dialog)
        self.btn_cookie.grid(row=0, column=2, padx=4, pady=8)
        self.btn_settings = ctk.CTkButton(top, text="AI设置", width=96, fg_color="#444444",
                                          hover_color="#555555", command=self._open_settings)
        self.btn_settings.grid(row=0, column=3, padx=4, pady=8)

        # ---- 中部：收藏夹列表 + 视频表 ----
        middle = ctk.CTkFrame(self)
        middle.grid(row=1, column=0, sticky="nsew", padx=10, pady=8)
        middle.grid_columnconfigure(0, weight=0)
        middle.grid_columnconfigure(1, weight=1)
        middle.grid_rowconfigure(0, weight=1)

        # 左：收藏夹
        left = ctk.CTkFrame(middle)
        left.grid(row=0, column=0, sticky="nsw", padx=(0, 8))
        left.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(left, text="我的收藏夹").grid(row=0, column=0, sticky="w", padx=8, pady=(8, 2))
        self.folder_scroll = ctk.CTkScrollableFrame(left, width=300, height=380)
        self.folder_scroll.grid(row=1, column=0, sticky="nsew", padx=8)
        self.folder_buttons: list[ctk.CTkButton] = []
        self.selected_folder_idx: int | None = None
        btnrow = ctk.CTkFrame(left)
        btnrow.grid(row=2, column=0, sticky="ew", padx=8, pady=6)
        btnrow.grid_columnconfigure(0, weight=1)
        self.btn_refresh = ctk.CTkButton(btnrow, text="刷新列表", command=self._refresh_folders)
        self.btn_refresh.grid(row=0, column=0, padx=(0, 4))
        self.btn_load = ctk.CTkButton(btnrow, text="加载该夹视频", command=self._load_selected_videos)
        self.btn_load.grid(row=0, column=1)

        # 右：视频
        right = ctk.CTkFrame(middle)
        right.grid(row=0, column=1, sticky="nsew")
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(right, text="收藏的视频（选择后点下方按钮分类导出）").grid(row=0, column=0, sticky="w", padx=8, pady=(8, 2))
        self.video_listbox = ctk.CTkTextbox(right, state="disabled", font=("Microsoft YaHei UI", 13), wrap="none")
        self.video_listbox.grid(row=1, column=0, sticky="nsew", padx=8)
        self.video_listbox.tag_config("sel", background="#fb7299", foreground="#ffffff")

        vbtn = ctk.CTkFrame(right)
        vbtn.grid(row=2, column=0, sticky="ew", padx=8, pady=6)
        vbtn.grid_columnconfigure(0, weight=1)
        self.btn_category = ctk.CTkButton(vbtn, text="AI 分类并导出", width=160,
                                          command=self._run_ai_classify)
        self.btn_category.grid(row=0, column=0, padx=4)
        self.btn_export = ctk.CTkButton(vbtn, text="导出当前结果", width=120,
                                        fg_color="#444444", hover_color="#555555",
                                        command=self._export_only)
        self.btn_export.grid(row=0, column=1, padx=4)
        self.lbl_video_count = ctk.CTkLabel(vbtn, text="共 0 条")
        self.lbl_video_count.grid(row=0, column=2, padx=8)

        # ---- 底部：日志 + 进度 ----
        bottom = ctk.CTkFrame(self)
        bottom.grid(row=2, column=0, sticky="nsew", padx=10, pady=(0, 10))
        bottom.grid_columnconfigure(0, weight=1)
        self.logbox = ctk.CTkTextbox(bottom, height=110, state="disabled",
                                     font=("Consolas", 11), wrap="word")
        self.logbox.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 4))
        progrow = ctk.CTkFrame(bottom)
        progrow.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 8))
        progrow.grid_columnconfigure(0, weight=1)
        self.progress = ctk.CTkProgressBar(progrow)
        self.progress.grid(row=0, column=0, sticky="ew")
        self.progress.set(0)
        self.lbl_progress = ctk.CTkLabel(progrow, text="就绪", width=200)
        self.lbl_progress.grid(row=0, column=1, padx=8)

    # ------------------------------------------------------------------ #
    # 通用小工具                                                          #
    # ------------------------------------------------------------------ #
    def _log(self, msg: str) -> None:
        ts = time.strftime("%H:%M:%S")
        self._msg_queue.put(("log", f"[{ts}] {msg}"))

    def _poll_queue(self) -> None:
        try:
            while True:
                kind, payload = self._msg_queue.get_nowait()
                if kind == "log":
                    self._append_log(payload)
                elif kind == "qr_image":
                    self._show_qr(payload)
                elif kind == "qr_state":
                    self._update_qr_state(payload)
                elif kind == "folders_loaded":
                    self._render_folders(payload)
                elif kind == "videos_loaded":
                    self._render_videos(payload)
                elif kind == "cookie_ok":
                    self._on_cookie_ok(payload)
                elif kind == "progress":
                    cur, total, label = payload
                    self._update_progress(cur, total, label)
                elif kind == "categorized":
                    self._on_categorized(payload)
                elif kind == "error":
                    self._show_error_dialog(payload)
        except queue.Empty:
            pass
        self.after(200, self._poll_queue)

    def _append_log(self, text: str) -> None:
        self.logbox.configure(state="normal")
        self.logbox.insert("end", text + "\n")
        self.logbox.see("end")
        self.logbox.configure(state="disabled")

    def _update_progress(self, cur: int, total: int, label: str | None = None) -> None:
        if total and total > 0:
            self.progress.set(min(1.0, cur / total))
        self.lbl_progress.configure(text=label or f"{cur}/{total}")

    def _show_error_dialog(self, msg: str) -> None:
        dlg = ctk.CTkToplevel(self)
        dlg.title("出错了")
        dlg.geometry("480x160")
        ll = ctk.CTkLabel(dlg, text=msg[:500], wraplength=440, justify="left")
        ll.pack(padx=20, pady=20, fill="both", expand=True)
        ctk.CTkButton(dlg, text="关闭", command=dlg.destroy).pack(pady=(0, 16))
        self._append_log(f"[错误] {msg}")

    # ------------------------------------------------------------------ #
    # 登录                                                               #
    # ------------------------------------------------------------------ #
    def _start_qr_login(self) -> None:
        self.btn_qr.configure(state="disabled", text="二维码生成中…")
        threading.Thread(target=self._qr_login_worker, daemon=True).start()

    def _qr_login_worker(self) -> None:
        try:
            info = self.client.qr_generate()
            self.qr_key = info["qrcode_key"]
            url = info["url"]
            if url.startswith("http://"):
                url = "https://" + url[len("http://"):]
            img = qrcode.make(url)
            img = img.convert("RGB")
            img = img.resize((280, 280))
            # 用系统临时目录里的唯一文件，避免 exe 目录不可写/并发覆盖
            import tempfile
            fd, tmp_path = tempfile.mkstemp(suffix=".png", prefix="bfav_qr_")
            with os.fdopen(fd, "wb") as f:
                img.save(f, format="PNG")
            self._msg_queue.put(("qr_image", tmp_path))
            self._log("二维码已生成，请用 B站 App 扫描。有效 180 秒。")
            self.qr_polling = True
            self._log("正在等待扫码…")
            threading.Thread(target=self._qr_poll_worker, daemon=True).start()
        except Exception as exc:  # noqa: BLE001
            self._msg_queue.put(("error", f"生成二维码失败：{exc}"))
            self._msg_queue.put(("qr_state", "idle"))

    def _qr_poll_worker(self) -> None:
        while self.qr_polling:
            try:
                res = self.client.qr_poll(self.qr_key)
            except Exception as exc:  # noqa: BLE001
                self._log(f"轮询失败：{exc}")
                time.sleep(1.5)
                continue
            code = res["code"]
            if code == QR_OK:
                self.qr_polling = False
                self._msg_queue.put(("qr_state", "success"))
                self._msg_queue.put(("log", "扫码成功！正在刷新收藏夹…"))
                self._refresh_folders_async()
                return
            if code == QR_SCANNED:
                self._msg_queue.put(("qr_state", "scanned"))
            elif code == QR_EXPIRED:
                self.qr_polling = False
                self._msg_queue.put(("qr_state", "expired"))
                self._log("二维码已失效，请重新扫码")
                return
            time.sleep(1.5)
        self._msg_queue.put(("qr_state", "idle"))

    def _show_qr(self, path: str) -> None:
        try:
            img = Image.open(path).convert("RGB")
            tk_img = ImageTk.PhotoImage(img)
        except Exception as exc:  # noqa: BLE001
            self._show_error_dialog(f"二维码图片加载失败：{exc}")
            return
        dlg = ctk.CTkToplevel(self)
        dlg.title("扫码登录 B站")
        dlg.geometry("340x400")
        dlg.resizable(False, False)
        dlg.grab_set()
        lab = ctk.CTkLabel(dlg, text="")
        lab.image = tk_img  # 防止被 GC
        lab.configure(image=tk_img)
        lab.pack(padx=20, pady=(20, 8))
        self.qr_status = ctk.CTkLabel(dlg, text="等待扫码…")
        self.qr_status.pack(pady=4)
        btn = ctk.CTkButton(dlg, text="关闭", command=dlg.destroy)
        btn.pack(pady=12)
        # 对话框关闭时停止轮询 & 恢复按钮
        def _on_close():
            self.qr_polling = False
            self.btn_qr.configure(state="normal", text="扫码登录")
            dlg.destroy()
        dlg.protocol("WM_DELETE_WINDOW", _on_close)

    def _update_qr_state(self, state: str) -> None:
        if hasattr(self, "qr_status") and self.qr_status.winfo_exists():
            mapping = {
                "idle": ("登录已停止", "#aaaaaa"),
                "scanned": ("已扫码，请在手机上确认登录", "#ffb74d"),
                "success": ("登录成功！", "#66bb6a"),
                "expired": ("二维码失效，请重新扫码", "#ef5350"),
            }
            if state in mapping:
                text, color = mapping[state]
                self.qr_status.configure(text=text, text_color=color)
        if state in ("success", "idle", "expired"):
            self.btn_qr.configure(state="normal", text="扫码登录")
        if state == "success":
            self._update_login_status()

    def _cookie_login_dialog(self) -> None:
        dlg = ctk.CTkToplevel(self)
        dlg.title("粘贴 Cookie 登录")
        dlg.geometry("560x300")
        dlg.grab_set()
        tip = ctk.CTkLabel(dlg, text=(
            "请从浏览器登录 bilibili.com 后，复制当前页面的 Cookie 粘贴到下面：\n"
            "（在浏览器按 F12 → 网络 → 刷新页面 → 点击任意请求 → 请求头里复制 Cookie 整行）"),
            justify="left", wraplength=520, text_color="#aaaaaa")
        tip.pack(padx=16, pady=(14, 8), anchor="w")
        self.cookie_entry = ctk.CTkTextbox(dlg, height=130, wrap="word")
        self.cookie_entry.pack(padx=16, pady=6, fill="both", expand=True)

        def _submit():
            cookie = self.cookie_entry.get("1.0", "end").strip()
            if not cookie:
                self._show_error_dialog("Cookie 不能为空")
                return
            try:
                self.client.login_by_cookie(cookie)
            except Exception as exc:  # noqa: BLE001
                self._show_error_dialog(f"Cookie 登录失败：{exc}")
                return
            dlg.destroy()
            self._log("Cookie 登录成功")
            self._update_login_status()
            self._refresh_folders_async()

        row = ctk.CTkFrame(dlg)
        row.pack(padx=16, pady=12, fill="x")
        ctk.CTkButton(row, text="登录", command=_submit).pack(side="left", padx=4)
        ctk.CTkButton(row, text="取消", fg_color="#444444",
                      hover_color="#555555", command=dlg.destroy).pack(side="left", padx=4)

    def _update_login_status(self) -> None:
        if self.client.is_logged_in:
            uid = self.client.uid or "?"
            self.login_status.configure(text=f"已登录（UID {uid}）", text_color="#66bb6a")
            self.btn_qr.configure(state="normal", text="扫码登录")
        else:
            self.login_status.configure(text="未登录", text_color="#888888")

    def _on_cookie_ok(self, uid: str) -> None:
        self.login_status.configure(text=f"已登录（UID {uid}）", text_color="#66bb6a")

    # ------------------------------------------------------------------ #
    # 收藏夹                                                              #
    # ------------------------------------------------------------------ #
    def _refresh_folders(self) -> None:
        self._refresh_folders_async(silent=False)

    def _refresh_folders_async(self, silent: bool = True) -> None:
        if not self.client.is_logged_in and not self.client.uid:
            self._log("尚未登录，无法获取收藏夹。请先扫码或粘贴 Cookie。")
            return
        if not silent:
            self.btn_refresh.configure(state="disabled", text="刷新中…")
        threading.Thread(target=self._folders_worker, daemon=True).start()

    def _folders_worker(self) -> None:
        try:
            folders = self.client.get_favorite_folders()
        except Exception as exc:  # noqa: BLE001
            self._msg_queue.put(("error", f"获取收藏夹失败：{exc}"))
            self._msg_queue.put(("folders_loaded", []))
            self._msg_queue.put(("log", "提示：若收藏夹为私密，需要登录 Cookie；公开收藏夹可直接获取"))
            return
        finally:
            self._msg_queue.put(("log", ""))
        self._current_folders = folders
        self._msg_queue.put(("folders_loaded", folders))
        self._log(f"获取到 {len(folders)} 个收藏夹")

    def _render_folders(self, folders) -> None:
        self.btn_refresh.configure(state="normal", text="刷新列表")
        self.selected_folder_idx = None
        # 清空旧的按钮
        for w in self.folder_scroll.winfo_children():
            w.destroy()
        self.folder_buttons.clear()
        if not folders:
            ctk.CTkLabel(self.folder_scroll, text="（暂无收藏夹或未登录）",
                         text_color="#888888").pack(fill="x", padx=4, pady=8)
            return
        for i, f in enumerate(folders):
            priv = " 🔒" if f.is_private else ""
            line = f"{i + 1:>2}. {f.title}{priv}（{f.media_count}）"
            btn = ctk.CTkButton(
                self.folder_scroll, text=line, anchor="w",
                height=32, fg_color="transparent", hover_color="#2f2f2f",
                text_color="#dddddd", corner_radius=6,
                command=lambda idx=i: self._select_folder(idx),
            )
            btn.pack(fill="x", padx=2, pady=1)
            self.folder_buttons.append(btn)

    def _select_folder(self, idx: int) -> None:
        """选中收藏夹：更新高亮样式。"""
        self.selected_folder_idx = idx
        for i, btn in enumerate(self.folder_buttons):
            if i == idx:
                btn.configure(fg_color="#fb7299", hover_color="#fb7299",
                              text_color="#ffffff")
            else:
                btn.configure(fg_color="transparent", hover_color="#2f2f2f",
                              text_color="#dddddd")

    def _selected_folder_index(self) -> int | None:
        """返回当前选中的收藏夹索引；无选中返回 None。"""
        idx = self.selected_folder_idx
        if idx is None or not (0 <= idx < len(self._current_folders)):
            return None
        return idx

    def _load_selected_videos(self) -> None:
        idx = self._selected_folder_index()
        if idx is None or idx >= len(self._current_folders):
            self._log("请先在左侧选择收藏夹（点击对应行）")
            return
        folder = self._current_folders[idx]
        if folder.is_private and not self.client.is_logged_in:
            self._log("该收藏夹为私密，请先登录")
            return
        self._log(f"开始加载收藏夹「{folder.title}」…")
        self.btn_load.configure(state="disabled", text="加载中…")
        self.lbl_video_count.configure(text="加载中…")
        threading.Thread(target=self._videos_worker, args=(folder,), daemon=True).start()

    def _videos_worker(self, folder) -> None:
        try:
            videos = self.client.get_favorites(
                folder.id,
                progress_cb=lambda cur, total: self._msg_queue.put(("progress", (cur, total, f"拉取中 {cur}/{total}"))),
            )
        except Exception as exc:  # noqa: BLE001
            self._msg_queue.put(("error", f"加载视频失败：{exc}"))
            self._msg_queue.put(("videos_loaded", []))
            return
        self._msg_queue.put(("videos_loaded", videos))
        self._msg_queue.put(("log", f"收藏夹「{folder.title}」共 {len(videos)} 条视频"))

    def _render_videos(self, videos) -> None:
        self.btn_load.configure(state="normal", text="加载该夹视频")
        self.lbl_video_count.configure(text=f"共 {len(videos)} 条")
        self._current_videos = [v.to_dict() for v in videos]
        self._categorized = {}
        self.video_listbox.configure(state="normal")
        self.video_listbox.delete("1.0", "end")
        if not self._current_videos:
            self.video_listbox.insert("end", "（该收藏夹暂无内容）\n")
        for i, v in enumerate(self._current_videos):
            self.video_listbox.insert("end", f"{i + 1:>3}. {v['title']}\n")
        self.video_listbox.configure(state="disabled")
        self.progress.set(0)
        self.lbl_progress.configure(text="就绪")

    # ------------------------------------------------------------------ #
    # AI 分类 + 导出                                                      #
    # ------------------------------------------------------------------ #
    def _run_ai_classify(self) -> None:
        if not self._current_videos:
            self._log("请先加载一个收藏夹的视频")
            return
        self.ai_config = self._build_ai_config()
        if not self.ai_config.is_valid:
            self._open_settings()
            self._log("AI 未配置：请先填写 API Key 与模型")
            return
        self._ai = AIClassifier(self.ai_config, log_cb=self._log,
                                progress_cb=lambda c, t: self._msg_queue.put(
                                    ("progress", (c, t, f"AI 分类中 {c}/{t}"))))
        self.btn_category.configure(state="disabled", text="分类中…")
        threading.Thread(target=self._classify_worker, daemon=True).start()

    def _classify_worker(self) -> None:
        try:
            result = self._ai.classify(self._current_videos)
        except Exception as exc:  # noqa: BLE001
            self._msg_queue.put(("error", f"AI 分类失败：{exc}"))
            self._msg_queue.put(("categorized", None))
            return
        self._msg_queue.put(("categorized", result))

    def _pick_export_dir(self) -> Path | None:
        from tkinter import filedialog
        d = filedialog.askdirectory(title="选择导出文件夹")
        return Path(d) if d else None

    def _on_categorized(self, result) -> None:
        self.btn_category.configure(state="normal", text="AI 分类并导出")
        self.progress.set(0)
        if result is None:
            self.lbl_progress.configure(text="分类失败")
            return
        self._categorized = result
        stats = "、".join(f"{k} {len(v)}条" for k, v in result.items() if v)
        self._log(f"分类完成：{stats}")
        self.lbl_progress.configure(text="分类完成，请选择导出位置")
        out_dir = self._pick_export_dir()
        if not out_dir:
            self._log("已取消导出")
            self.lbl_progress.configure(text="就绪")
            return
        try:
            self._do_export(out_dir)
        except Exception as exc:  # noqa: BLE001
            self._show_error_dialog(f"导出失败：{exc}")

    def _export_only(self) -> None:
        if not self._categorized:
            self._log("还没有分类结果，请先点击「AI 分类并导出」")
            return
        out_dir = self._pick_export_dir()
        if not out_dir:
            return
        try:
            self._do_export(out_dir)
        except Exception as exc:  # noqa: BLE001
            self._show_error_dialog(f"导出失败：{exc}")

    def _do_export(self, out_dir: Path) -> None:
        out_dir.mkdir(parents=True, exist_ok=True)
        xlsx_file = exporter.export_excel(self._categorized, out_dir)
        md_file = exporter.export_markdown(self._categorized, out_dir)
        html_file = exporter.export_html(self._categorized, out_dir)
        n_sheets = exporter.count_excel_sheets(xlsx_file)
        self._log(f"导出完成 → {out_dir}")
        self._log(f"  Excel：{xlsx_file.name}（{n_sheets} 个分类工作表）；Markdown：{md_file.name}；HTML：{html_file.name}")
        self.lbl_progress.configure(text="导出完成")
        # 打开文件夹
        try:
            import os
            os.startfile(str(out_dir))  # noqa: S606 - 用户主动触发的资源管理器打开
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------ #
    # AI 设置                                                            #
    # ------------------------------------------------------------------ #
    def _open_settings(self) -> None:
        dlg = ctk.CTkToplevel(self)
        dlg.title("AI 设置")
        dlg.geometry("620x560")
        dlg.resizable(False, False)
        dlg.grab_set()

        ctk.CTkLabel(dlg, text="接入方式：DeepSeek 官方 / 自定义 OpenAI 兼容 API", text_color="#aaaaaa"
                     ).pack(anchor="w", padx=20, pady=(14, 2))

        def field(parent, label, default, show=None):
            row = ctk.CTkFrame(parent, fg_color="transparent")
            row.pack(fill="x", padx=20, pady=6)
            row.grid_columnconfigure(1, weight=1)
            ctk.CTkLabel(row, text=label, width=120, anchor="w").grid(row=0, column=0, sticky="w")
            ent = ctk.CTkEntry(row, show=show)
            ent.grid(row=0, column=1, sticky="ew")
            if default:
                ent.insert(0, str(default))
            return ent

        e_base = field(dlg, "API 地址 (base_url)", self.ai_config.base_url)
        e_key = field(dlg, "API Key", self.ai_config.api_key, show="*")
        e_model = field(dlg, "模型名", self.ai_config.model)
        e_temp = field(dlg, "temperature (0-1)", self.ai_config.temperature)
        e_token = field(dlg, "max_tokens", self.ai_config.max_tokens)

        ctk.CTkLabel(dlg, text="分类列表（每行一个，可增删改）：", text_color="#aaaaaa"
                     ).pack(anchor="w", padx=20, pady=(10, 2))
        e_cats = ctk.CTkTextbox(dlg, height=160, wrap="word")
        e_cats.pack(fill="x", padx=20)
        e_cats.insert("1.0", "\n".join(self.ai_config.categories))

        def _save():
            try:
                base = e_base.get().strip()
                key = e_key.get().strip()
                model = e_model.get().strip()
                temp = float(e_temp.get().strip() or 0.2)
                token = int(e_token.get().strip() or 2000)
                cats = [c.strip() for c in e_cats.get("1.0", "end").splitlines() if c.strip()]
                if not cats:
                    cats = ["其他"]
                if not base or not model:
                    self._show_error_dialog("API 地址与模型名不能为空")
                    return
            except ValueError as exc:
                self._show_error_dialog(f"设置格式错误：{exc}")
                return
            self.cfg.update({
                "base_url": base, "api_key": key, "model": model,
                "temperature": temp, "max_tokens": token, "categories": cats,
            })
            appconfig.save_config(self.cfg)
            self.ai_config = self._build_ai_config()
            self._log(f"AI 设置已保存（{self.ai_config.provider_name} / {model}）")
            dlg.destroy()

        row = ctk.CTkFrame(dlg, fg_color="transparent")
        row.pack(fill="x", padx=20, pady=14)
        ctk.CTkButton(row, text="保存", command=_save).pack(side="left", padx=4)
        ctk.CTkButton(row, text="取消", fg_color="#444444",
                      hover_color="#555555", command=dlg.destroy).pack(side="left", padx=4)

    # ------------------------------------------------------------------ #
    # 收尾                                                                #
    # ------------------------------------------------------------------ #
    # 清理遗留的二维码临时文件
    def _cleanup_qr_tmp(self) -> None:
        try:
            import tempfile
            tmpdir = tempfile.gettempdir()
            for name in os.listdir(tmpdir):
                if name.startswith("bfav_qr_") and name.endswith(".png"):
                    try:
                        os.remove(os.path.join(tmpdir, name))
                    except OSError:
                        pass
        except OSError:
            pass

    def on_close(self) -> None:
        self._cleanup_qr_tmp()
        self.destroy()


def run() -> None:
    app = FavOrganizerApp()
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    app.mainloop()


if __name__ == "__main__":
    run()