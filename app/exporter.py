"""B站收藏夹整理助手 - 导出器

把 AI 分类结果导出为：
- Excel：单文件，每个分类一个工作表（推荐，默认）
- Markdown：按分类分章节的清单
- HTML：单文件自包含，可离线浏览，封面缩略图外链
"""

from __future__ import annotations

import csv
import html
import os
import time
from pathlib import Path


def _safe_filename(name: str) -> str:
    """清理文件名中的非法字符。"""
    for ch in '\\/:*?"<>|':
        name = name.replace(ch, "_")
    return name.strip() or "未分类"


def _fmt_time(ts: int) -> str:
    if not ts:
        return ""
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))
    except (OverflowError, OSError, ValueError):
        return ""


def _fmt_duration(sec: int) -> str:
    if not sec:
        return ""
    sec = int(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def build_folders(categorized: dict[str, list[dict]]) -> list[str]:
    """生成将创建的文件夹清单（分类名），跳过空分类。"""
    return [name for name, videos in categorized.items() if videos]


def export_csv(categorized: dict[str, list[dict]], out_dir: str | os.PathLike) -> list[Path]:
    """每个分类导出一个 CSV，返回生成的文件列表。"""
    paths = []
    for cat, videos in categorized.items():
        if not videos:
            continue
        p = Path(out_dir) / f"{_safe_filename(cat)}.csv"
        with open(p, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(["标题", "分类", "BVID", "UP主", "链接", "收藏时间", "发布时间", "时长", "播放量", "简介"])
            for v in videos:
                writer.writerow([
                    v.get("title", ""),
                    cat,
                    v.get("bvid", ""),
                    v.get("upper_name", ""),
                    v.get("link", ""),
                    _fmt_time(v.get("fav_time", 0)),
                    _fmt_time(v.get("pubtime", 0)),
                    _fmt_duration(v.get("duration", 0)),
                    v.get("play_count", 0),
                    (v.get("intro") or "").replace("\n", " "),
                ])
        paths.append(p)
    return paths


def export_excel(categorized: dict[str, list[dict]], out_dir: str | os.PathLike,
                 filename: str = "收藏夹分类清单.xlsx") -> Path:
    """导出一个 Excel 文件，每个分类一个工作表（分表）。"""
    try:
        import openpyxl
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.utils import get_column_letter
    except ImportError as exc:  # noqa: BLE001
        raise RuntimeError("缺少 openpyxl 依赖，无法导出 Excel。请安装：pip install openpyxl") from exc

    headers = ["标题", "分类", "BVID", "UP主", "链接", "收藏时间", "发布时间", "时长", "播放量", "简介"]
    wb = openpyxl.Workbook()
    default_ws = wb.active
    wb.remove(default_ws)  # 删除默认空表

    header_fill = PatternFill("solid", fgColor="FB7299")
    header_font = Font(color="FFFFFF", bold=True)

    for cat, videos in categorized.items():
        if not videos:
            continue
        ws = wb.create_sheet(title=_safe_sheet_name(cat))
        # 表头
        for col, h in enumerate(headers, start=1):
            cell = ws.cell(row=1, column=col, value=h)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center", vertical="center")
        # 数据
        for r, v in enumerate(videos, start=2):
            row = [
                v.get("title", ""),
                cat,
                v.get("bvid", ""),
                v.get("upper_name", ""),
                v.get("link", ""),
                _fmt_time(v.get("fav_time", 0)),
                _fmt_time(v.get("pubtime", 0)),
                _fmt_duration(v.get("duration", 0)),
                v.get("play_count", 0),
                (v.get("intro") or "").replace("\n", " "),
            ]
            for col, val in enumerate(row, start=1):
                ws.cell(row=r, column=col, value=val)
        # 自动列宽（中文约按 2 字符宽估算）
        for col in range(1, len(headers) + 1):
            letter = get_column_letter(col)
            max_len = len(str(headers[col - 1]))
            for cell in ws[letter]:
                if cell.value is not None:
                    max_len = max(max_len, len(str(cell.value)))
            ws.column_dimensions[letter].width = min(max_len * 2 + 2, 80)
        # 冻结首行、链接列超链接化
        ws.freeze_panes = "A2"
        for r in range(2, len(videos) + 2):
            link = ws.cell(row=r, column=5).value
            if isinstance(link, str) and link.startswith("http"):
                c = ws.cell(row=r, column=5)
                c.hyperlink = link
                c.style = "Hyperlink"
        ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{len(videos) + 1}"

    if not wb.sheetnames:
        ws = wb.create_sheet(title="全部")
        ws.append(headers)

    p = Path(out_dir) / filename
    wb.save(p)
    return p


def _safe_sheet_name(name: str) -> str:
    """Excel 工作表名限制：≤31 字符，不允许 \\ / ? * [ ] : 等字符。"""
    name = _safe_filename(name)
    for ch in "\\/?*[]:":
        name = name.replace(ch, "_")
    return name[:31] or "未分类"


def count_excel_sheets(xlsx_path: str | os.PathLike) -> int:
    """读取 Excel 文件的工作表数量（仅用于日志展示）。"""
    try:
        import openpyxl
        wb = openpyxl.load_workbook(xlsx_path, read_only=True)
        return len(wb.sheetnames)
    except Exception:  # noqa: BLE001
        return 0


def export_markdown(categorized: dict[str, list[dict]], out_dir: str | os.PathLike) -> Path:
    """导出一个总 Markdown 清单文件。"""
    p = Path(out_dir) / "收藏夹分类清单.md"
    lines = ["# B站收藏夹分类清单", ""]
    lines.append(f"- 导出时间：{time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"- 分类数：{len(categorized)}")
    total = sum(len(v) for v in categorized.values())
    lines.append(f"- 视频总数：{total}")
    lines.append("")
    for cat, videos in categorized.items():
        if not videos:
            continue
        lines.append(f"## {cat}（{len(videos)} 条）")
        lines.append("")
        for v in videos:
            title = v.get("title", "")
            link = v.get("link", "")
            up = v.get("upper_name", "")
            lines.append(f"- **[{title}]({link})**" + (f"（UP：{up}）" if up else ""))
            intro = (v.get("intro") or "").strip().replace("\n", " ")
            if intro:
                lines.append(f"  - {intro[:80]}")
        lines.append("")
    with open(p, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return p


def export_html(categorized: dict[str, list[dict]], out_dir: str | os.PathLike,
                title: str = "B站收藏夹分类清单") -> Path:
    """导出一个自包含 HTML 清单文件（封面缩略图为外链）。"""
    now = time.strftime("%Y-%m-%d %H:%M")
    cards = []
    for cat, videos in categorized.items():
        if not videos:
            continue
        for v in videos:
            cover = (v.get("cover") or "").replace("http://", "https://")
            cards.append(
                "<a class='card' href='{link}' target='_blank'>"
                "<div class='cover'>{cover_html}</div>"
                "<div class='body'>"
                "<div class='title'>{title}</div>"
                "<div class='meta'>UP：{up} · {duration} · 收藏于 {fav} · 播放 {play}</div>"
                "<div class='intro'>{intro}</div>"
                "</div></a>".format(
                    link=html.escape(v.get("link", "")),
                    cover_html=f"<img src='{html.escape(cover)}' loading='lazy' alt='' onerror=\"this.style.display='none'\">" if cover else "",
                    title=html.escape(v.get("title", "")),
                    up=html.escape(v.get("upper_name", "") or "未知"),
                    duration=_fmt_duration(v.get("duration", 0)),
                    fav=_fmt_time(v.get("fav_time", 0)),
                    play=v.get("play_count", 0),
                    intro=html.escape((v.get("intro") or "")[:100]),
                )
            )
        cards.append(f"<div class='cat-sep' id='{html.escape(cat)}'>{html.escape(cat)}（{len(videos)}）</div>")

    total = sum(len(v) for v in categorized.values())
    groups = "".join(
        f"<span class='chip'><a href='#{html.escape(cat)}'>{html.escape(cat)}（{len(videos)}）</a></span>"
        for cat, videos in categorized.items() if videos
    )
    doc = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
body {{ font-family: "Microsoft YaHei", "PingFang SC", sans-serif; background:#f5f6f8; color:#222; margin:0; }}
.wrap {{ max-width:1100px; margin:0 auto; padding:24px; }}
h1 {{ font-size:22px; }}
.meta {{ color:#888; font-size:13px; margin-bottom:16px; }}
.chips {{ margin:16px 0; }}
.chip {{ display:inline-block; background:#e8ecf1; border-radius:14px; padding:4px 12px; margin:4px; font-size:13px; }}
.chip a {{ color:#444; text-decoration:none; }}
.cat-sep {{ font-size:16px; font-weight:bold; background:linear-gradient(90deg,#fb7299,#ffa1b5); color:#fff;
  padding:8px 14px; border-radius:8px; margin:28px 0 12px; }}
.card {{ display:flex; background:#fff; border-radius:10px; padding:12px; margin:10px 0;
  text-decoration:none; color:#222; box-shadow:0 1px 3px rgba(0,0,0,.08); transition:box-shadow .15s; }}
.card:hover {{ box-shadow:0 3px 10px rgba(0,0,0,.15); }}
.cover {{ width:160px; min-width:160px; height:96px; border-radius:8px; overflow:hidden; background:#eee; margin-right:14px; }}
.cover img {{ width:100%; height:100%; object-fit:cover; }}
.body {{ flex:1; min-width:0; }}
.title {{ font-size:15px; font-weight:600; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; }}
.meta {{ color:#999; font-size:12px; margin-top:6px; }}
.intro {{ color:#666; font-size:12px; margin-top:4px; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; }}
footer {{ color:#aaa; font-size:12px; text-align:center; padding:20px; }}
</style>
</head>
<body>
<div class="wrap">
<h1>{html.escape(title)}</h1>
<div class="meta">导出时间：{now} ｜ 共 {total} 条视频</div>
<div class="chips">{groups}</div>
{''.join(cards)}
</div>
<footer>由 B站收藏夹整理助手生成</footer>
</body>
</html>"""
    p = Path(out_dir) / "收藏夹分类清单.html"
    with open(p, "w", encoding="utf-8") as f:
        f.write(doc)
    return p