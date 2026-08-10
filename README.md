# B站收藏夹整理助手

用 AI 将 B 站账号收藏夹里的视频自动分类（科技 / 美食 / 音乐 / 体育 / …），导出为分类清单（CSV / Markdown / HTML）。本地运行、图形界面、支持 DeepSeek 与任意 OpenAI 兼容 API。

## 功能

- 🔑 两种登录方式：**扫码登录**（B站 App 扫码，自动保存会话）与 **粘贴 Cookie**
- 📂 拉取账号的**全部收藏夹**（含公开/私密），点击即可加载其中视频
- 🤖 **AI 自动分类**：接入 DeepSeek（默认）或自定义 base_url 的 OpenAI 兼容大模型
- 🗂️ 导出分类清单（**仅链接与元信息，不下载视频本体**）：
  - 每个分类一个 `CSV`（utf-8-sig，Excel 直接打开不乱码）
  - 一个总 `Markdown` 清单
  - 一个自带封面的 `HTML` 页面（分类锚点导航）
- ⚙️ 分类列表可自定义（AI 设置里增删改）

## 快速开始（已打包）

直接运行 `dist/B站收藏夹整理助手.exe`（无需安装 Python）。

### 首次使用流程

1. 打开程序，点 **扫码登录**，用 B站 App 扫二维码（或点 **粘贴Cookie**）。
2. 左侧出现收藏夹列表 → 点选一个 → **加载该夹视频**。
3. 点 **AI设置**：填入 DeepSeek API Key（默认 `deepseek-chat`），也可改自定义 base_url / 模型（如 `https://api.openai.com/v1` + `gpt-4o-mini`）。
4. 点 **AI 分类并导出** → 选择导出文件夹 → 自动生成分类 CSV / Markdown / HTML，并打开文件夹。

> API Key 只保存在程序同目录的 `config.json`，不会上传到任何第三方。

## 从源码运行

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python main.py
```

## 打包为 exe

```bash
# 建议使用 Python 3.12+（原因见下）
py -3.12 -m venv .venv312
.venv312\Scripts\pip install -r requirements.txt pyinstaller
build.bat            # 产物: dist/B站收藏夹整理助手.exe
```

> **为什么建议 3.12+**：Python 3.10.0 自带的 `dis` 模块存在一个字节码解析缺陷（`co_consts` 索引越界的 `IndexError`），会在 PyInstaller 分析阶段扫描新版 Pillow 时崩溃。用 3.12+ 打包可完全避开。程序源码本身兼容 3.10–3.12。

## 项目结构

```
main.py               # 入口
app/
  bilibili.py         # B站 API 封装（扫码登录/收藏夹/视频详情）
  ai_classifier.py    # AI 分类器（DeepSeek / 自定义 OpenAI 兼容 API）
  exporter.py         # CSV / Markdown / HTML 导出
  config.py           # config.json 读写（exe 同目录）
  gui.py              # CustomTkinter 图形界面
smoke_test.py         # 核心逻辑冒烟测试（mock AI 服务，无需联网）
build.bat             # 一键打包脚本
```

## 技术说明

- 接口均取自 B 站公开 Web API（`passport.bilibili.com` 扫码登录、`api.bilibili.com/x/v3/fav/...` 收藏夹、`x/web-interface/view` 视频详情），**2026-01 实测无需 wbi 签名**。
- 请求自带浏览器 UA / Referer，并对收藏夹翻页做间隔限流，降低风控概率。
- 程序**忽略系统代理直连**：实测部分本地代理对 `bilibili.com` 的 TLS 分流有问题，直连更稳定。

## 免责声明

本工具仅用于个人收藏夹内容整理，请遵守 B 站服务条款与相关法律法规。