# 微信 AI 润色助手（macOS）

一个 **macOS 桌面助手**：在微信输入框打字 → 按全局热键 → 自动读取当前聊天上下文 →
调用大模型生成多个润色版本 → 你选一个 → 自动回填到微信输入框。

**为什么不是「微信插件」**：个人微信没有官方开放 API，逆向协议（WeChaty / iPad 协议）
违反微信 ToS、极易封号。本方案只用 macOS 自带的公开能力模拟用户自己的操作，
合规、零封号风险。**全程不联网发送任何数据到作者服务器**，只调用你自己配置的大模型接口。

> **本程序仅支持 macOS。** 所有系统调用都收敛在 `macos/` 包内，业务模块不直接依赖任何系统框架。

## 功能

- 全局热键唤起（默认 `Ctrl+Alt+P`；可在 `config.json` 改 `hotkey`）
- **点击右下角悬浮图标也能触发**，与按热键完全等价
- **设置页**（提示词 / 模型与 Key / 软件作者 / 使用说明 四个页签）：
  **右键点击右下角悬浮图标 →「打开设置…」**（有中键的鼠标也可按鼠标中键全局打开）
  - **提示词页签**：查看 / 新增 / 编辑 / 保存 / 删除提示词，内置项可**恢复默认**，
    点「设为生效」即时切换（无需重启）
  - **模型与 Key 页签**：**多套模型配置分别管理**（名称 / API 地址 / 模型名 / Key），
    可新增、删除、切换生效；Key 默认**打码显示**，可一键**测试连接**
  - **软件作者页签**：使用交流群 / 作者公众号两张二维码，可放大、随窗口自适应重绘
  - **使用说明页签**：左目录 + 右只读全文，正文来自 `使用说明-给朋友.md`（改文档即改界面）
- 捕获当前输入框草稿（不破坏原内容）
- 输入框为空 → 自动切换为「依据聊天上下文草拟回复」模式
- 读取当前聊天最近 N 条消息作为上下文（读不到自动降级，不影响润色）
  - 用 **`screencapture -l <窗口ID>` 按窗口截图 + Vision 框架中文 OCR**
  - 消息区靠**分隔线 + 固定比例**动态定位；按**气泡几何**判「我 / 对方」（深浅主题都成立）
  - **被遮挡、不在前台也能截**，不抢焦点、不打断你打字；最小化会自动还原
  - 中文识别模型由 macOS 自带，**无需安装语言包**
- 调用 DeepSeek（OpenAI 兼容接口）生成多个版本话术
- 浮动窗展示，点「发送」回填，点「复制」仅复制
- **回填不走剪贴板**：优先用辅助功能（AX）写值并**读回确认**，失败再走键盘逐字输入。
  这样不会污染你的剪贴板，也**永不发送回车**（避免误发消息）
- **右下角悬浮图标**：空闲 / 处理中两种动图状态；**可拖动**并记住位置；
  **悬停**时右上角浮现红色 × 可退出
- **优雅的启动自检**：缺依赖、缺权限、Tk 版本过低都会在终端与日志里明确说明原因，
  而不是让你面对「程序在跑但按了没反应」

## 运行环境

- **macOS 13 或更高**
- **微信 Mac 版**（已登录、打开聊天窗口）
- **Python 3.10+**
  - 建议用 python.org 或 Homebrew 安装的 **3.11 / 3.12**
  - 系统自带的 Python 3.9 配的是旧版 Tk，**界面画不出来**；`run-mac.command` 会自动优先挑一个
    带 Tk 8.6 的 Python

## 安装

**普通用户（推荐）**：双击 DMG 里的 `安装 话术润色助手.command`。
安装包自带 Python 运行环境，**不需要你预先安装任何东西** —— 详见「打包与分发」。

**开发者 / 从源码运行**：

```bash
python3 -m pip install -r requirements.txt
```

或直接双击 **`run-mac.command`**：它会自动创建 `.venv`、安装依赖并启动
（首次需要几分钟；之后每次双击直接启动）。

## 授权（首次必须做，否则「按了没反应」）

| 权限 | 用途 | 位置 | 生效时机 |
|---|---|---|---|
| **辅助功能** | 读输入框草稿、回填、监听全局热键 | 系统设置 › 隐私与安全性 › 辅助功能 | **立即生效** |
| **屏幕录制** | 截图读取聊天上下文 | 系统设置 › 隐私与安全性 › 录屏与系统录音 | **必须退出重开程序** |

> 从终端启动时权限记在**终端 App** 上；双击 `run-mac.command` 则记在该脚本上。
> 程序启动时会自检并打印缺哪一项。

## 配置 API Key（二选一）

1. 环境变量（推荐，避免把 Key 写进文件）：
   ```bash
   export AI_API_KEY=sk-你的deepseek_key
   ```
2. 或复制 `config.example.json` 为 `config.json` 并填入 `api_key`；
   也可以直接启动程序，首次运行会弹出引导窗口让你填。

> 已适配任意 OpenAI 兼容接口：改 `api_base` / `model` 即可切到豆包、GPT 等。

## 运行

```bash
python3 main.py
```

或双击 `run-mac.command`。启动后保持后台运行，在微信输入框打字，按热键即可。

## 使用流程

1. 在微信输入框写好草稿（不用发）。
2. 按 `Ctrl+Alt+P`。
3. 助手读取草稿 + 最近聊天上下文，调用 AI 生成 3 个版本。
4. 弹出小窗：选「发送」即回填微信输入框（你再按微信的发送键）；
   选「复制」则只复制文本自己处理。

## 说明与边界

- **获取微信内容有等待宽限期**：触发后会持续轮询等待微信窗口（默认最长 **10 秒**，可在
  `config.json` 改 `grab_timeout`）。期间你随时可以打开微信、进入聊天窗口、写好草稿，
  一旦检测到就**立即**抓取；只有超过 10 秒仍拿不到内容才弹提示框。
- **上下文读取只有「截图 + OCR」一条路**：微信 Mac 版不向系统开放消息控件树，
  所以窗口定位走 Quartz 窗口元数据，内容靠 `screencapture -l` + Vision OCR。
  `ocr_context`（默认 `true`）设为 `false` 则完全不读上下文，只用草稿润色。
- **识别误差与前置条件**：基于图像识别，**存在固有误差**（如「消息」识别成「消，」）。
  上下文只用于让模型了解语境，不必逐字精确；需要精确文本时可手动把对方的话粘进输入框。
- **回填依赖焦点**：触发热键时光标应在微信输入框内。程序会把焦点还给微信再写入；
  写入方式优先 AX 直写 + 读回确认，失败退键盘逐字输入，**不使用剪贴板**。
- **失败可见**：AI 调用失败（401 / 网络错误）会弹出置顶错误提示窗，并把原因写进 `polish.log`。
- **热键不要用 Space**：空格是中文输入法确认候选字的高频键，容易误触发。
  默认是 `Ctrl+Alt+P`；想更省事可用功能键（把 `hotkey` 改成 `f8`）。
- **热键格式**：`config.json` 里普通字符必须裸写（如 `p`、`k`、`f8`），**不要**写成 `<p>`；
  只有修饰键（`ctrl` / `alt` / `shift` / `cmd`）需要 `<>`。
- **Option(alt) 的坑**：macOS 上 `Option + 字母` 是输入特殊字符的组合键（Option+P 打出 π），
  个别输入法下会干扰热键匹配。遇到「按了没反应」，把热键改成 `f8` 最稳。
- **设置页触发方式**：右键悬浮图标（首选）；有中键的鼠标也可在任意位置按鼠标中键。
  触控板没有中键，所以右键菜单是主入口。
- **悬浮图标**：用 PyObjC 原生 `NSPanel`（`macos/icon.py`）实现真正透明背景。
  动图帧很多，已做「后台解码 + 抽帧」，不卡主流程。相关配置：`idle_gif` / `active_gif` /
  `tray_size`（默认 220 像素）/ `tray_pos`（拖动后的位置，`null` = 右下角）。
- **拖动与点击如何区分**：按「按下 — 移动 — 抬起」三段判定，位移超过阈值才算拖动；
  只移动时**不触发**润色。位置会被夹在屏幕可视化区域内，越界会自动复位。
- 日志全部写入 `polish.log`，便于排查窗口读取 / OCR / API 调用问题。

## 设置页说明（提示词 / 模型与 Key / 软件作者 / 使用说明）

打开方式：**右键点击右下角悬浮图标 →「打开设置…」**（或按鼠标中键）；
打开后**默认停在「软件作者」页**，其余页签点顶部标题切换；`Esc` 关闭，`Ctrl+S` 保存当前页签。

### 提示词页签
- 左栏列出全部提示词（`内置` / `自定义` 徽章 + `✓ 生效` 标记），点一条即在右栏编辑。
- **保存**：写入 `config.json` 的 `prompts`。**恢复默认**：还原内置项。
  **设为生效**：切换当前使用的 system 提示词。**删除**：仅自定义项可删。

### 模型与 Key 页签
- 支持**多套模型配置**分别管理（如「DeepSeek 日常 + 豆包备用」）。
- 每条独立保存 **API 地址 / 模型名 / API Key**；点「设为生效」后立即用于后续调用。
- **Key 默认打码**，点「显示」才展开；列表与提示语一律只显示脱敏形式。
- **测试连接**：用最小请求验证 Key，子线程执行，不卡界面。
- 至少要保留一条配置（最后一条不允许删除）。

### 使用说明页签
- 左栏章节目录（点一下跳到对应节）+「复制全文」；右栏只读全文，可滚动、可选中复制。
- 正文**不硬编码在界面里**：直接渲染 `使用说明-给朋友.md`，所以**改文档即改界面文案**。
  解析逻辑在 `help_doc.py`，可离线单测。
- 文件缺失时不会留白：左栏写明缺哪个文件。

### 软件作者页签
- 左栏作者信息与引导文案，底部「加入交流群 / 作者公众号」入口；
  右栏两张二维码卡片并排（白底保证对比度），随窗口尺寸自适应重绘并限制最大边长。
- 资源文件：`author_qrcode.png`、`group_qrcode.png`。**文件缺失时对应入口自动隐藏**，
  卡片内写明缺了哪个文件，不留死链接。
- 底栏右侧另有「作者微信公众号 ｜ 使用交流群」两个快捷入口（位于「关闭」左侧）：
  悬停浮出小预览，点击弹大图。

> **安全提示**：按当前约定，API Key 以**明文**保存在 `config.json` 里 —— 这个文件本身就是凭据。
> 界面会打码显示、也不会把 Key 写进日志，但请**不要**把它发给别人或上传网盘
> （`.gitignore` 已排除该文件）。更保密的做法是用环境变量 `AI_API_KEY`。

## 查看实时日志

启动后（`run-mac.command` 或 `python3 main.py`）：

- **终端实时滚动**（默认开启，设 `console_log: false` 可关闭）：可看到
  - 发给大模型的**完整提示词**：`system` 角色设定 + `user` 草稿/上下文
  - 大模型的**响应原文**、状态码、耗时、Token 用量
  - 最终解析出的版本数
- **日志文件**：同样内容实时写入 `polish.log`，但**落盘即加密**（每行形如 `WPENC1:<base64>`）；
  直接打开是密文属正常现象，看明文请用下面的解密工具还原成副本。
  日志位置随运行方式变化：

  | 运行方式 | 日志位置 |
  |---|---|
  | 源码运行 | 项目目录下的 `polish.log` |
  | 打包成 .app | `~/Library/Logs/话术润色助手/polish.log`（Finder 里按 `⌘⇧G` 粘贴此路径） |

  同理，配置文件的差别是：源码运行用项目目录下的 `config.json`，
  打包运行用 `~/Library/Application Support/话术润色助手/config.json`。

相关配置项（`config.json`）：

| 字段 | 默认 | 说明 |
|---|---|---|
| `console_log` | `true` | 是否在终端实时输出日志；`false` 则只写文件 |
| `log_prompt` | `true` | 是否记录完整提示词与响应原文；`false` 则只记录摘要 |
| `ocr_context` | `true` | 是否用截图 + OCR 读聊天上下文；`false` 则只用草稿润色 |
| `grab_timeout` | `10` | 触发后等待/重试获取微信内容的最长秒数 |
| `context_messages` | `5` | 读取最近多少条消息作为上下文 |

> 日志**不会**记录 API Key（请求头不落盘），可放心查看。

### 日志加密与解密

`polish.log` 的每条记录在**写盘前**逐条加密（实现见 `log_crypto.py`，密钥常量为
`DEFAULT_LOG_KEY`），因此磁盘上只有密文；旧版本留下的明文日志会在下次启动时就地加密。
需要看明文时，用同密钥的还原工具输出一份副本（只读原文件，不会改动它）：

| 方式 | 命令 |
|---|---|
| 命令行工具 | `python3 log_decrypt.py` |
| 指定文件/输出 | `python3 log_decrypt.py 日志路径 -o ~/Desktop/plain.log`（`--stdout` 直接打印，`-k` 指定口令） |
| 程序自带入口 | `python3 main.py --decrypt-log` |

也可作为接口调用：`log_decrypt.decrypt_log_file(src, dst, key)`，返回
`{"src","dst","enc","plain","bad","total"}` 统计。

- 格式：一行一条 `WPENC1:<base64(nonce‖密文‖校验码)>`，记录内的换行一并加密。
- 算法：PBKDF2-HMAC-SHA256 派生密钥 + HMAC-SHA256 计数器模式密钥流 +
  HMAC-SHA256 完整性校验（encrypt-then-MAC），只用标准库，**不新增任何依赖**。
- 边界：口令硬编码在程序里，它防的是「日志被别的程序/别人直接打开看到明文」，**不是**防逆向。

## 文件结构

```
main.py            全局热键编排主流程（单实例 / 热键 / 弹窗路由 / 日志）
config.py          配置加载（config.json + 环境变量）、提示词与多模型管理
paths.py           统一路径管理（只读资源 / 可写数据分流）
ai_client.py       大模型调用（DeepSeek，多版本，JSON 解析，连接类重试）
ui.py              tkinter 浮动确认窗（微信聊天框风格）
tk_widgets.py      扁平按钮控件（Label 自绘，规避 Aqua Tk 忽略 bg 的问题）
config_editor.py   设置页（提示词 / 模型与 Key / 软件作者 / 使用说明 四页签）
first_run.py       首次运行引导（填写 API Key + 测试连接）
help_doc.py        使用说明文档的加载与解析（「使用说明」页签的数据来源）
log_crypto.py      polish.log 加解密：加密写盘 Handler + 历史明文日志就地加密
log_decrypt.py     日志解密工具（命令行还原明文，也可作为接口调用）

macos/             系统集成层（唯一碰系统框架的地方）
  __init__.py        对外入口：事件循环 / 能力自述 / 权限自检
  window.py          微信窗口定位（Quartz 窗口元数据）+ WindowRef
  capture.py         截图 + Vision OCR 读取聊天内容 + 行合并与左右分类 + Message
  ocr.py             Vision 框架 OCR
  ax.py              辅助功能（Accessibility）读写原语
  keys.py            键盘模拟（pynput 封装，修饰键为 Cmd）
  paste.py           回填：AX 写值 + 读回确认，退键盘逐字输入
  tray.py            悬浮图标宿主（同时持有主线程 Tk 解释器）
  icon.py            原生 NSPanel 悬浮图标
  instance.py        单实例（flock + 温和接管旧实例）

probe/             可选：macOS 适配探针（看窗口/AX/OCR 实测结果，不参与主流程）
tests/             单元测试（无需网络即可运行）
使用说明-给朋友.md  给使用者的说明书（同时是「使用说明」页签的正文来源）
run-mac.command    源码运行：一键启动（自动建 venv、装依赖、自检、启动）

── 打包与分发 ──
build_macos.sh         一键构建 .app + DMG（含环境检查 / 图标生成 / 产物校验）
话术润色助手.spec    PyInstaller 配置（资源、隐藏导入、Info.plist 键）
requirements-build.txt 构建期额外依赖（运行依赖 + PyInstaller）
installer/
  build_dmg.sh                 把 .app 打成 DMG（App + 应用程序快捷方式 + 脚本 + 说明）
  安装 话术润色助手.command   双击安装：复制 / 去隔离 / 校验 / 引导授权
  卸载 话术润色助手.command   双击卸载（默认保留配置与日志）
  安装说明.md                   给最终用户的一页纸说明（会放进 DMG）
icons/app.icns         构建时由 icon_source.png 生成（不纳入版本库）
```

## 单元测试

```bash
for t in tests/test_*.py; do python3 "$t" || exit 1; done
```

覆盖：AI 客户端的消息构造 / 多版本解析 / 重试策略、说明书 Markdown 解析、
日志加解密往返与篡改检测。全部离线，不需要网络或权限。

## 探针（可选）

`probe/` 下的脚本用于在换微信版本 / 换机器时重新实测窗口定位与 OCR 质量，
会输出截图与 JSON 报告到 `probe/probe_out/`（**含聊天内容，已被 .gitignore 排除**）：

```bash
python3 probe/mac_capability_probe.py --request-permissions   # 权限 + 微信窗口 + AX 可读性
python3 probe/mac_capability_probe.py
python3 probe/mac_capture_probe.py --delay 5                  # 截图 + OCR 端到端
```

## 打包与分发（让目标用户无需预装 Python）

### 为什么选「捆绑 Python」而不是「自动下载 Python」

| 方案 | 用户需要装 Python 吗 | 安装时要联网吗 | 需要管理员 | 结论 |
|---|---|---|---|---|
| **捆绑 Python（本方案）** | **不需要** | **不需要** | 不需要 | PyInstaller 把解释器 + 标准库 + Tk/Tcl + 全部依赖打进 `.app` |
| 安装时自动下载并装 Python | 不用手动，但需授权装一个系统级 Python | 需要 | 需要（装到 `/Library`） | 污染系统、还留下一个用户永远用不到的 Python |
| 依赖系统自带 Python | **需要**（且 Apple 自带的 3.9 配的是 Tk 8.5，界面画不出来） | — | — | 不可行 |

**结论：捆绑。** 安装包里没有「下载 Python」这一步，也完全不碰系统 Python。

### 安装包里包含哪些组件

```
话术润色助手-1.0.0.dmg                       （约 32MB）
├── 话术润色助手.app                        （自包含 App，约 56MB）
│   └── Contents/
│       ├── MacOS/话术润色助手                PyInstaller bootloader（App 的可执行文件）
│       ├── Frameworks/                         ← sys._MEIPASS，运行期从这里读
│       │     Python 3.12 解释器 + 标准库 + Tk/Tcl
│       │     PyObjC + Pillow + pynput + requests + pyperclip
│       │     运行期资源：1.webp / 2.webp / 两份二维码 / 使用说明.md
│       ├── Resources/                          资源副本
│       └── Info.plist                          bundle id / 版本 / 最低系统版本 / 权限说明文案
├── 应用程序                                    → /Applications 快捷方式（也支持直接拖拽）
├── 安装 话术润色助手.command                 双击安装
├── 卸载 话术润色助手.command                 双击卸载
└── 安装说明.md                                 给最终用户的一页纸说明
```

**明确不含**：任何 API Key、`config.json`、`.env`。
构建脚本会**强制检查**产物里没有这些，发现就直接构建失败。

### 构建步骤（必须在 macOS 上）

```bash
./build_macos.sh              # 出 .app + .dmg
./build_macos.sh --app-only   # 只出 .app
./build_macos.sh --smoke      # 出完再启动一次做冒烟验证
```

脚本按顺序做这些事，每步都打印 ✓/✗：

| 步骤 | 内容 |
|---|---|
| 1 | 环境检查：必须是 macOS；自动挑一个 **Tk ≥ 8.6** 的 Python（Apple 自带的 3.9 + Tk 8.5 画不出界面） |
| 2 | 建/复用 `.venv`，装 `requirements-build.txt`（运行依赖 + PyInstaller） |
| 3 | 由 `icon_source.png` 生成 `icons/app.icns`（`sips` + `iconutil`；失败则用默认图标） |
| 4 | `pyinstaller 话术润色助手.spec`：按 spec 收集解释器 / 依赖 / 资源，并做 ad-hoc 签名 |
| 5 | 校验产物：架构、Info.plist 关键键、资源是否真进包、**是否误带凭据**、签名、体积 |
| 6 | 生成 DMG |

**目标用户侧不需要配任何环境变量、也不需要改 PATH**：`Info.plist` 里已经声明了
bundle id / 版本 / 最低系统版本与权限说明文案，双击 App 即可运行。

### 安装流程（用户视角）

1. 双击 `安装 话术润色助手.command`
   - 被 Gatekeeper 拦时（未公证的 App 必然如此）：右键 →「打开」，弹窗里再点一次
2. 脚本自动完成：
   - 找到 App 并**校验架构**（arm64/x86_64 不匹配时给明确提示，而不是「打不开」）
   - 复制到 `/Applications`（不可写则退到 `~/Applications`；用 `ditto` 保住签名）
   - `xattr -dr com.apple.quarantine` —— **不做这步，之后每次打开都会被系统拦**
   - `codesign --verify --deep` 校验
3. 首次启动：弹出「还差一步授权」，引导授予两项系统权限（带「去授权」按钮直跳系统设置）
4. 接着弹出配置窗口，填自己的 API Key

> 也可以不用脚本：直接把 App 拖进「应用程序」。但拖拽**不会**去隔离标记，
> 首次打开需要右键 →「打开」。

### 打包环节最容易踩的坑（都是实测结论）

| 坑 | 现象 | 处理 |
|---|---|---|
| 用 onefile 而不是 onedir | 每次启动解压到临时目录，**TCC 权限记在临时路径上** → 下次启动权限「失效」，用户被反复要求授权 | 必须 onedir（`.app` 本身就是目录） |
| 忘了声明 pynput 的 macOS 后端 | 打包后**热键与鼠标监听直接失效，而且不报错** | spec 的 `hiddenimports` 显式列出 `pynput.keyboard._darwin` / `pynput.mouse._darwin` |
| **pynput 在子线程查键盘布局** | 打包成 .app 后启动 1~2 秒**闪退**，崩溃报告是 `EXC_BREAKPOINT / dispatch_assert_queue_fail / TSMCurrentKeyboardInputSourceRefCreate`，**没有任何 Python 回溯**，光看日志完全定位不到 | 见下方「pynput 闪退」专节 |
| 打成 .app 后没有终端 | 权限自检只 `print`，用户看到的是「点开没反应」 | 启动时用**对话框**呈现自检结果（`first_run.run_permission_guide`） |
| 日志写在 App 包内 | 重装/更新即丢，用户也找不到 | 冻结运行时日志放 `~/Library/Logs/话术润色助手/` |
| 先跳系统设置、再申请权限 | 系统设置的列表里**根本没有本程序**，用户无从勾选 | 「去授权」按钮先调 `request_permissions()` 触发系统弹窗，再跳设置页 |
| `LSUIElement=true` | 实测**会被 Tk 覆盖**（Tk 初始化时把激活策略设回 Regular），App 照样有 Dock 图标 | 不写这个键（写了是误导）；干脆接受 Dock 图标 —— 对非技术用户反而更友好 |
| 用 `cp -R` 而不是 `ditto` | 签名与扩展属性被破坏 → 校验失败，甚至无法启动 | 安装脚本统一用 `ditto` |

#### pynput 闪退（最容易误判为「打包坏了」的一个坑）

**现象**：双击 `.app`，关掉权限引导窗后 1~2 秒闪退，弹「意外退出」；日志停在
`=== 启动 build=... ===`，之后什么都没有；崩溃报告里是：

```
EXC_BREAKPOINT / SIGTRAP
libdispatch: _dispatch_assert_queue_fail
HIToolbox:   TSMCurrentKeyboardInputSourceRefCreate
HIToolbox:   _HaveOnlyOneKeyboardInputSource
libffi / _ctypes: ffi_call_SYSV / _ctypes_callproc
Python:      thread_run          ← 注意：在子线程
```

**原因**：`pynput.keyboard.Listener._run()` 在**自己的监听线程**里调
`keycode_context()` → `TISCopyCurrentKeyboardInputSource`，而 macOS 内部会走到
`islGetInputSourceListWithAdditions` —— 这个函数**要求主队列**，
在子线程调用直接触发 `dispatch_assert_queue` 断言，SIGTRAP 杀掉进程。
`Listener` 与 `Controller` 都会走它，所以热键与「逐字输入」两条路都在射程内。

**修法**（`macos/keys.py`）：`keycode_context()` 拿到的 `layout_data` 是一份 bytes
副本，**可以跨线程安全复用**。于是先在主线程取一次，再把
`pynput._util.darwin.keycode_context` 与 `pynput.keyboard._darwin.keycode_context`
**两处都**换成返回缓存的版本（后者是按值 import 的，只改前者不生效）。
补丁在 `macos/keys.py` 的 import 阶段执行，启动日志里会打印
`键盘布局缓存: 已就绪` 以便确认。

> 顺带一个观察：源码运行时不会崩、打包后才崩。原因是两种运行方式的
> TCC 身份不同（终端 vs .app），未授「辅助功能」时 pynput 会走到上面那条
> 主线程断言路径。**所以这类问题只在打包后才能发现**，必须真的跑一次产物。

### 架构说明

产物架构 = **构建机架构**：Apple Silicon 上构建得到 arm64，Intel 上得到 x86_64。
安装脚本会做架构校验，不匹配时给出明确提示。

不产出 universal2 的原因：本机 Python 虽然是 universal2，但实测 **Pillow 装到的是
arm64-only 轮子**，混合架构打包会失败。要出 universal2 需同时满足
「universal2 的 Python」+「所有依赖都有 universal2 wheel」。

### 分发前清单

- [ ] `./build_macos.sh` 的校验项全部为 ✓
- [ ] 产物里没有 `config.json` / `.env`（构建脚本已强制检查）
- [ ] 自己的 `config.json` 不要一起发出去（`.gitignore` 已排除）
- [ ] 告知接收方：首次打开若被拦，右键 →「打开」

### 打包相关边界

- **未做代码签名与公证**（需要 Apple Developer 账号，99 美元/年）。没有它，
  Gatekeeper 会拦首次打开；安装脚本通过解除隔离标记绕过这个问题，
  但**不能消除**「首次运行脚本时要右键打开」这一次操作。
- **未做自动更新**。升级方式：重新安装新版 DMG；配置与日志都不受影响。

---

## 已知限制

- **仅支持 macOS**（未提供其它桌面平台的实现，也不在计划内）。
- 聊天上下文读取基于图像识别，存在固有误差，且依赖「屏幕录制」权限。
- 回填依赖触发热键时的焦点位置。
- 分发包未签名公证，首次打开需要右键 →「打开」一次（见上一节）。
