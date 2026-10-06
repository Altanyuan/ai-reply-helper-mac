# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（macOS），产出 `dist/话术润色助手.app`。

目标：一个**自包含**的 .app —— 目标用户不需要预装 Python，也不需要联网 pip。

为什么这么配（每条都是坑）
--------------------------
1. **用 onedir（.app 本身就是目录），不用 onefile。**
   单文件模式每次启动都要解压到临时目录：启动慢，更要命的是 macOS 的权限
   （TCC）会记在那个**临时路径**上 —— 下次启动路径变了，授权就"失效"了，
   用户会被反复要求重新授权。onedir 的 App 路径稳定，授权一次长期有效。
2. **不设 LSUIElement**（虽然它是"后台助手"的惯例做法）。
   实测：设了也没用 —— **Tk 会在初始化时把激活策略强制设回 Regular**，
   App 照样有 Dock 图标。写在 plist 里只会让后来人以为它是无 Dock 图标的，
   属于误导性配置。
   既然改变不了，就接受它：有 Dock 图标对非技术用户反而更友好 ——
   能直观看到"程序在跑"，也能右键退出。右下角悬浮图标仍是主要入口。
   （因此两个交互对话框里都主动调了 `focus_force()`，保证窗口一定能拿到键盘焦点。）
3. **显式声明 pynput 的 macOS 后端**：`pynput.keyboard._darwin` /
   `pynput.mouse._darwin` 是运行时按平台字符串导入的，静态分析看不到；
   漏了会导致打包后热键与鼠标监听直接失效，而且不报错。
4. **PyObjC 各框架显式声明**：这些框架为了做到「缺依赖也能启动并给出可读提示」，
   是在函数内部 import 的，静态分析未必全部捕获。
5. **绝不打包任何凭据**：`config.json` / `.env` 一律不进包（见 EXCLUDES）。
   分发给别人的包里不能带作者的 Key。
6. **架构跟随构建机**：不设 target_arch。Apple Silicon 上构建得到 arm64 版，
   Intel 上得到 x86_64 版。真要做 universal2 得同时满足「universal2 的 Python」
   与「所有依赖都有 universal2 wheel」——实测 Pillow 在本机装的是 arm64-only，
   所以默认不做。安装器里带架构检查，不匹配时会给明确提示而不是「打不开」。
"""

from pathlib import Path

PROJ = Path(SPECPATH).resolve()
DIST_NAME = "话术润色助手"

#: 运行期真正要读的资源。全部通过 `paths.asset()` 定位，
#: 打包后从 `sys._MEIPASS` 读；用户在 App 同目录放同名文件也能覆盖。
DATAS = [
    "1.webp",                  # 右下角悬浮图标：空闲态
    "2.webp",                  # 右下角悬浮图标：处理中
    "author_qrcode.png",       # 「软件作者」页：作者公众号二维码
    "group_qrcode.png",        # 「软件作者」页：使用交流群二维码
    "使用说明-给朋友.md",        # 「使用说明」页正文（改文档即改界面）
]

#: 静态分析看不到、但运行期一定会用到的模块
HIDDEN = [
    "pynput.keyboard._darwin",
    "pynput.mouse._darwin",
    "objc",
    "Foundation",
    "AppKit",
    "Quartz",
    "Vision",
    "ApplicationServices",
    "CoreFoundation",
    "CoreGraphics",
    "CoreText",
    "QuartzCore",
    "tkinter",
    "tkinter.font",
    "tkinter.messagebox",
    "tkinter.filedialog",
]

#: 明确不打进去的东西（省体积，本程序确实用不到）
EXCLUDES = [
    "pytest",
    "_pytest",
    "numpy",
    "cv2",
    "matplotlib",
    "scipy",
    "pandas",
    "IPython",
    "notebook",
    "PIL.ImageQt",
    "probe",
    "tests",
]

_icon = PROJ / "icons" / "app.icns"

a = Analysis(
    [str(PROJ / "main.py")],
    pathex=[str(PROJ)],
    binaries=[],
    datas=[(str(PROJ / d), ".") for d in DATAS],
    hiddenimports=HIDDEN,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=DIST_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,                 # 双击不弹终端窗口
    disable_windowed_traceback=False,
    argv_emulation=False,          # 不需要处理文件/URL 打开事件
    target_arch=None,              # 跟随构建机架构（见顶部说明第 6 条）
    codesign_identity=None,        # 交给 PyInstaller 做 ad-hoc 签名（arm64 必需）
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name=DIST_NAME,
)

app = BUNDLE(
    coll,
    name=f"{DIST_NAME}.app",
    icon=str(_icon) if _icon.exists() else None,
    bundle_identifier="com.wechataipolish.assistant",
    info_plist={
        "CFBundleName": "话术润色助手",
        "CFBundleDisplayName": "话术润色助手",
        "CFBundleShortVersionString": "1.0.0",
        "CFBundleVersion": "1.0.0",
        "LSMinimumSystemVersion": "13.0",
        "NSHighResolutionCapable": True,
        # 屏幕录制权限的系统弹窗会展示这段文字
        "NSScreenCaptureUsageDescription":
            "用于截取微信窗口画面，在本机用系统 OCR 识别聊天上下文。"
            "截图只在本机处理、不会上传；识别出的文字会随润色请求发给你自己配置的大模型。",
        # 把润色结果写回微信输入框需要驱动其它 App
        "NSAppleEventsUsageDescription":
            "用于把润色结果写回微信输入框。",
    },
)
