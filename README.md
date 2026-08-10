# B站收藏夹整理助手

用 AI 将 B 站账号收藏夹里的视频自动分类（科技 / 美食 / 音乐 / 体育 / …），导出为**分类清单**（Excel 分表 / Markdown / HTML）。本地运行、图形界面、支持 DeepSeek 与任意 OpenAI 兼容 API。

## 功能

- 🔑 两种登录方式：**扫码登录**（B站 App 扫码，成功后自动关闭弹窗）与 **粘贴 Cookie**
- 📂 拉取账号的**全部收藏夹**（含公开/私密），点击收藏夹即高亮选中，一键加载其中视频
- 🤖 **AI 自动分类**：接入 DeepSeek（默认 `deepseek-chat`）或自定义 base_url 的 OpenAI 兼容大模型；多批并发、自动重试、本地启发式兜底，**实测 111 条视频 0 条「其他」**
- 📁 **按分类整理收藏夹**（写操作）：AI 分类后一键在 B站自动新建分类收藏夹（科技/美食/…），并把视频从原收藏夹移入对应分类夹，原夹保留；慢速限流分批执行，降低风控风险（需扫码/Cookie 登录且含 `bili_jct`）
- 📊 导出分类清单（**仅链接与元信息，不下载视频本体**）：
  - **Excel 分表**：一个 `收藏夹分类清单.xlsx`，每个分类一个工作表（粉色表头、冻结首行、自动列宽、链接可点）
  - 一个总 `Markdown` 清单
  - 一个自带封面的 `HTML` 页面（分类锚点导航）
- 🎨 双主题：**浅色蓝调 / 深色粉调**，一键切换（默认浅色）
- ❓ 内置「使用说明」弹窗，分步引导
- 🔒 API Key 仅保存在本机 `config.json`（`%APPDATA%\BilibiliFavOrganizer\`），**不会上传到 GitHub 或任何第三方**

## 快速开始（已打包）

直接运行 `dist/BilibiliFavOrganizer.exe`（无需安装 Python）。

### 首次使用流程

1. 打开程序，点顶部 **扫码登录**，用 B站 App 扫二维码（或点 **粘贴Cookie** 从浏览器复制 Cookie 粘贴）。
2. 左侧列表点选一个收藏夹（选中变主题色）→ 点 **加载收藏夹视频**。
3. 点 **AI设置**：填入 DeepSeek API Key（模型名默认已填），也可改自定义 base_url / 模型（如 `https://api.openai.com/v1` + `gpt-4o-mini`）。
4. 点 **AI 分类并导出** → 选择保存文件夹 → 自动生成 Excel 分表 / Markdown / HTML，并打开文件夹。
5. （可选）点 **按分类整理收藏夹** → 确认后自动在 B站新建分类收藏夹并把视频移入（原夹保留）。
6. 顶栏 **使用说明** 按钮可随时查看分步引导；**☀/🌙** 切换深浅主题。

> API Key 只保存在本机 `config.json`，不会上传到任何第三方。

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
build.bat            # 产物: dist/BilibiliFavOrganizer.exe
```

> **为什么建议 3.12+**：Python 3.10.0 自带的 `dis` 模块存在一个字节码解析缺陷（`co_consts` 索引越界的 `IndexError`），会在 PyInstaller 分析阶段扫描新版 Pillow 时崩溃。用 3.12+ 打包可完全避开。程序源码本身兼容 3.10–3.12。

## 项目结构

```
main.py               # 入口
app/
  bilibili.py         # B站 API 封装（扫码登录/收藏夹/视频详情）
  ai_classifier.py    # AI 分类器（DeepSeek / 自定义 OpenAI 兼容 API，自动重试+启发式兜底）
  exporter.py         # Excel 分表 / Markdown / HTML 导出
  config.py           # config.json 读写（%APPDATA%\BilibiliFavOrganizer\）
  gui.py              # CustomTkinter 图形界面（双主题、使用说明）
smoke_test.py         # 核心逻辑冒烟测试（mock AI 服务，无需联网）
build.bat             # 一键打包脚本
```

## 技术说明

- 接口均取自 B 站公开 Web API（`passport.bilibili.com` 扫码登录、`api.bilibili.com/x/v3/fav/...` 收藏夹、`x/web-interface/view` 视频详情），实测无需 wbi 签名。
- 请求自带浏览器 UA / Referer，并对收藏夹翻页做间隔限流，降低风控概率。
- 程序**忽略系统代理直连**：实测部分本地代理对 `bilibili.com` 的 TLS 分流有问题，直连更稳定。
- AI 分类稳定性：单批 5 条 + 并发批处理 + 空内容/截断自动重试 + 失败逐条启发式兜底，即使模型偶发异常也不会整批归「其他」。

## 免责声明

本工具仅用于个人收藏夹内容整理，请遵守 B 站服务条款与相关法律法规。
