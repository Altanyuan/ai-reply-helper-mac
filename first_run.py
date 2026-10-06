"""首次运行引导：引导用户填写**自己的** DeepSeek API Key。

为什么需要：
  分发给朋友的程序里不含任何 API Key（作者的 Key 不进包，避免泄露与被刷费用）。
  因此首次启动若检测不到 api_key，就弹出本窗口引导填写，否则程序无法工作。

设计要点：
  - 提供 DeepSeek 申请指引与免费额度说明，降低朋友的上手成本。
  - 「测试连接」用最小请求（max_tokens=1）验证 Key，几乎不产生费用。
  - 网络请求放在子线程，避免卡住 tkinter 界面。
  - 配色沿用 config_editor.py 的 CodeBuddy 深色主题，视觉风格统一。
"""
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path

import config
import macos
from ai_client import test_api_key
# 按钮统一走自绘控件：macOS 的 tk.Button 会忽略 bg，白字会落在浅色原生按钮上看不见
from tk_widgets import flat_button

# ---- 配色（与 config_editor.py 保持一致）----
BG = "#1e1e1e"
PANEL = "#252526"
BORDER = "#3c3c3c"
TEXT = "#d4d4d4"
MUTED = "#9da5b4"
ACCENT = "#0e639c"
ACCENT_HOVER = "#1177bb"
SECONDARY = "#3a3d41"
SECONDARY_HOVER = "#4a4d51"
OK = "#4ec9b0"
WARN = "#dcdcaa"
BAD = "#f48771"

LABEL_FONT = ("Microsoft YaHei", 10)
TITLE_FONT = ("Microsoft YaHei", 13, "bold")
SMALL_FONT = ("Microsoft YaHei", 9)

APPLY_URL = "https://platform.deepseek.com/api_keys"


def need_setup(cfg):
    """是否需要首次配置：没有 api_key 即需要。"""
    return not (cfg or {}).get("api_key")


class _Wizard:
    def __init__(self, root, cfg, on_done):
        self.root = root
        self.cfg = cfg
        self.on_done = on_done
        self._testing = False
        self._build()

    # ---------- 布局 ----------
    def _build(self):
        # 顶栏
        header = tk.Frame(self.root, bg=PANEL, height=46)
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Frame(header, bg=ACCENT, width=4).pack(side="left", fill="y")
        tk.Label(header, text="首次使用 · 配置 API Key", bg=PANEL, fg="#ffffff",
                 font=TITLE_FONT, padx=12).pack(side="left")

        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True, padx=20, pady=(16, 8))

        # 说明
        intro = (
            "首次使用必须配置 API Key（必填，否则无法启动）。\n"
            "这个助手需要调用大模型来润色话术；请填写你自己的 Key —— "
            "它只保存在你本机，不会上传给任何人。"
        )
        tk.Label(body, text=intro, bg=BG, fg=TEXT, font=LABEL_FONT,
                 anchor="w", justify="left", wraplength=600).pack(fill="x")

        # 申请指引
        guide = tk.Frame(body, bg=PANEL, highlightthickness=1,
                         highlightbackground=BORDER)
        guide.pack(fill="x", pady=(12, 14))
        tk.Label(
            guide,
            text=(
                "还没有 Key？三步搞定：\n"
                f"1. 打开 {APPLY_URL}\n"
                "2. 用手机号注册 / 登录（新用户有免费额度，日常使用基本够）\n"
                "3. 点「创建 API Key」，复制以 sk- 开头的那串字符，粘贴到下面"
            ),
            bg=PANEL, fg=MUTED, font=SMALL_FONT, anchor="w", justify="left",
            wraplength=580, padx=12, pady=10,
        ).pack(fill="x")

        # 输入框（默认打码，点「显示」才展开）
        head = tk.Frame(body, bg=BG)
        head.pack(fill="x")
        tk.Label(head, text="API Key（以 sk- 开头）", bg=BG, fg=MUTED,
                 font=LABEL_FONT, anchor="w").pack(side="left")
        self._visible = False
        self.key_toggle = tk.Label(head, text="显示", bg=BG, fg=ACCENT,
                                   font=SMALL_FONT, cursor="hand2")
        self.key_toggle.pack(side="left", padx=(8, 0))
        self.key_toggle.bind("<Button-1>", lambda e: self._toggle_visible())
        self.entry = tk.Entry(body, bg=PANEL, fg=TEXT, insertbackground="#aeafad",
                              relief="solid", borderwidth=1,
                              highlightbackground=BORDER, highlightthickness=1,
                              font=LABEL_FONT, show="•")
        self.entry.pack(fill="x", pady=(2, 4), ipady=6)
        self.entry.focus_set()
        self.entry.bind("<Return>", lambda e: self._on_save())

        # 状态提示
        self.status = tk.Label(body, text="", bg=BG, fg=MUTED, font=SMALL_FONT,
                               anchor="w", wraplength=600, justify="left")
        self.status.pack(fill="x", pady=(6, 0))

        # 按钮
        # ⚠️ 「保存并启动」是深蓝底白字：若用 tk.Button，macOS 会忽略 bg 但保留 fg，
        # 于是变成浅底白字、**按钮上完全看不到字**。故统一用自绘按钮。
        btns = tk.Frame(self.root, bg=BG)
        btns.pack(fill="x", padx=20, pady=(6, 16))
        flat_button(btns, "退出", self._on_cancel, bg=SECONDARY, fg=TEXT,
                    hover=SECONDARY_HOVER, font=LABEL_FONT, width=10, pady=7
                    ).pack(side="left")
        self.test_btn = flat_button(btns, "测试连接", self._on_test,
                                    bg=SECONDARY, fg=TEXT,
                                    hover=SECONDARY_HOVER, font=LABEL_FONT,
                                    width=10, pady=7)
        self.test_btn.pack(side="right", padx=(8, 0))
        self.save_btn = flat_button(btns, "保存并启动", self._on_save,
                                    bg=ACCENT, fg="white",
                                    hover=ACCENT_HOVER,
                                    font=("Microsoft YaHei", 10, "bold"),
                                    width=12, pady=7)
        self.save_btn.pack(side="right")

    # ---------- 交互 ----------
    def _set_status(self, msg, color=MUTED):
        self.status.configure(text=msg, fg=color)

    def _toggle_visible(self):
        self._visible = not self._visible
        try:
            self.entry.configure(show="" if self._visible else "•")
            self.key_toggle.configure(text="隐藏" if self._visible else "显示")
        except Exception:
            pass

    def _on_test(self):
        if self._testing:
            return
        key = self.entry.get().strip()
        if not key:
            self._set_status("请先粘贴 API Key 再测试。", WARN)
            return

        self._testing = True
        self.test_btn.configure(state="disabled", text="测试中…")
        self._set_status("正在连接 DeepSeek 验证 Key…", MUTED)

        cfg = dict(self.cfg)
        cfg["api_key"] = key

        def _worker():
            ok, msg = test_api_key(cfg)
            # 回到主线程更新界面（子线程不能直接碰 tkinter）
            try:
                self.root.after(0, lambda: self._on_test_done(ok, msg))
            except Exception:
                pass

        threading.Thread(target=_worker, daemon=True).start()

    def _on_test_done(self, ok, msg):
        self._testing = False
        self.test_btn.configure(state="normal", text="测试连接")
        self._set_status(msg, OK if ok else BAD)

    def _on_save(self):
        key = self.entry.get().strip()
        if not key:
            self._set_status("请粘贴 API Key（以 sk- 开头）。", WARN)
            return
        if not key.startswith("sk-"):
            self._set_status(
                "这个 Key 看起来不太对（应以 sk- 开头），请确认复制完整。", WARN
            )
            return
        try:
            # 写入「模型配置」而不是裸 api_key：这样设置页里能直接看到并管理它。
            # save_model 也会把地址 / 模型名 / Key 同步到顶层字段，保证兼容。
            mid = config.ensure_default_model()
            ok, msg = config.save_model({
                "id": mid,
                "name": "默认模型",
                "api_base": (self.cfg.get("api_base")
                             or config.DEFAULTS["api_base"]),
                "model": (self.cfg.get("model") or config.DEFAULTS["model"]),
                "api_key": key,
                "note": "首次配置写入",
            })
            if not ok:
                self._set_status(msg, BAD)
                return
        except Exception as e:
            self._set_status(f"保存失败：{e}", BAD)
            return
        self.on_done(True)

    def _on_cancel(self):
        self.on_done(False)


def run_setup_wizard(cfg):
    """弹出引导窗口，阻塞直到用户完成或退出。

    返回 True 表示已保存 Key（可以启动主程序）；False 表示用户放弃（应退出）。
    """
    root = tk.Tk()
    root.title("话术润色助手 - 首次配置")
    root.configure(bg=BG)
    root.geometry("660x560")
    root.minsize(600, 500)
    root.attributes("-topmost", True)
    try:
        root.iconify()
        root.deiconify()
    except Exception:
        pass
    # 主动抢一次焦点：打包成 .app 后本程序是「后台助手」（LSUIElement，不占 Dock），
    # 不主动激活的话，输入框可能收不到键盘输入 —— 用户看到的是「窗口卡住了」。
    try:
        root.lift()
        root.focus_force()
    except Exception:
        pass

    result = {"saved": False}

    def _done(ok):
        result["saved"] = ok
        try:
            root.quit()
        except Exception:
            pass

    _Wizard(root, cfg, _done)
    root.mainloop()
    try:
        root.destroy()
    except Exception:
        pass
    return result["saved"]


# ---------------------------------------------------------------------------
# 权限引导（打包成 .app 双击启动时没有终端，自检结果必须用对话框告诉用户）
# ---------------------------------------------------------------------------

#: 引导窗口里逐项展示的权限：(权限键, 名称, 用来做什么, 生效时机)
#: 措辞用「打开开关」而不是「勾选」：macOS 11+ 的隐私列表是右侧开关，不是勾选框。
_PERM_ROWS = (
    ("accessibility", "辅助功能", "读输入框草稿、回填、监听全局热键", "打开开关后立即生效"),
    ("screen_recording", "屏幕录制", "截图读取聊天上下文", "打开开关后需退出重开本程序"),
)


def _running_app_path() -> str:
    """当前运行的是哪一份程序（.app 包路径）。

    显示它是为了解决一个真实踩到的坑：本程序用的是 ad-hoc 签名，**每次重新构建
    或重新安装都会改变签名指纹**，而 macOS 的权限是按签名指纹记的 —— 于是
    用户会遇到「我明明授权了，程序还说未授权」，实际是把权限授给了**另一份副本**
    （比如装到「应用程序」的那份，而当前跑的是别处的）。把当前路径摆出来，
    用户才能对照着确认。
    """
    exe = Path(sys.executable).resolve()
    for parent in exe.parents:
        if parent.suffix == ".app":
            return str(parent)
    return f"源码运行（{exe}）"


class _PermissionGuide:
    """列出缺哪些权限、各自的作用，并提供直达系统设置的按钮。"""

    def __init__(self, root, issues, on_done):
        self.root = root
        self.issues = issues
        self.on_done = on_done
        self._cells = {}
        self._build()
        self._refresh()

    # ---------- 布局 ----------
    def _build(self):
        root = self.root

        header = tk.Frame(root, bg=PANEL)
        header.pack(fill="x")
        tk.Frame(header, bg=WARN, width=4).pack(side="left", fill="y")
        tk.Label(header, text="还差一步授权", bg=PANEL, fg="#ffffff",
                 font=TITLE_FONT, padx=12, pady=10).pack(side="left")

        # 底栏先 pack：内容区用 expand=True，若后 pack 会把底栏挤没（设置页踩过同一个坑）
        bottom = tk.Frame(root, bg=PANEL)
        bottom.pack(fill="x", side="bottom")
        self.status = tk.Label(bottom, text="", bg=PANEL, fg=MUTED,
                               font=SMALL_FONT, anchor="w", padx=14, pady=8,
                               wraplength=330, justify="left")
        self.status.pack(side="left", fill="x", expand=True)
        flat_button(bottom, "继续使用", self._done, bg=ACCENT, fg="white",
                    hover=ACCENT_HOVER, font=LABEL_FONT, width=8, pady=5
                    ).pack(side="right", padx=10, pady=5)
        flat_button(bottom, "重新检测", self._refresh, bg=SECONDARY, fg=TEXT,
                    hover=SECONDARY_HOVER, font=LABEL_FONT, width=8, pady=5
                    ).pack(side="right", pady=5)

        body = tk.Frame(root, bg=BG)
        body.pack(fill="both", expand=True, padx=16, pady=(12, 6))

        tk.Label(body,
                 text="macOS 要求手动授权后，本程序才能截图和响应热键。\n"
                      "没授权时的表现是「按热键没反应」——不是程序坏了。",
                 bg=BG, fg=TEXT, font=LABEL_FONT, justify="left", anchor="w",
                 wraplength=560).pack(fill="x", pady=(0, 6))

        # 把「当前运行的是哪一份」显示出来：权限是按签名指纹记的，换一份副本
        # （或重新构建/重新安装）就得重新授权，否则会出现「我明明授权了」的困惑。
        tk.Label(body, text=f"当前运行的程序：{_running_app_path()}",
                 bg=BG, fg=MUTED, font=SMALL_FONT, anchor="w",
                 justify="left", wraplength=560).pack(fill="x", pady=(0, 6))

        # 必须写出「系统设置里显示的那个名字」而不能想当然：列表里用的是
        # .app 文件名（源码运行时则是「终端」），写死中文名会让用户找不到条目。
        # 注意：Label 不解析 markdown，这里不要写 `**` —— 会原样显示出来
        tk.Label(body,
                 text=f"在系统设置列表里本程序显示为「{macos.settings_list_name()}」，"
                      f"请把最右侧的开关打开。\n"
                      f"注意：「去授权」只是跳到系统设置，不等于已授权。",
                 bg=BG, fg=WARN, font=LABEL_FONT, anchor="w",
                 justify="left", wraplength=560).pack(fill="x", pady=(0, 10))

        for key, name, why, when in _PERM_ROWS:
            card = tk.Frame(body, bg=PANEL, padx=12, pady=10)
            card.pack(fill="x", pady=(0, 8))
            top = tk.Frame(card, bg=PANEL)
            top.pack(fill="x")
            state = tk.Label(top, text="", bg=PANEL, font=LABEL_FONT, anchor="w")
            state.pack(side="left")
            btn = flat_button(top, "去授权", lambda k=key: self._open(k),
                              bg=ACCENT, fg="white", hover=ACCENT_HOVER,
                              font=LABEL_FONT, padx=10, pady=4)
            btn.pack(side="right")
            tk.Label(card, text=f"作用：{why}", bg=PANEL, fg=MUTED,
                     font=SMALL_FONT, anchor="w", justify="left",
                     wraplength=500).pack(fill="x", pady=(4, 0))
            tk.Label(card, text=f"生效时机：{when}", bg=PANEL, fg=MUTED,
                     font=SMALL_FONT, anchor="w").pack(fill="x")
            self._cells[key] = (state, btn)

        # 非权限类问题（缺依赖 / Tk 版本过低）原样展示，避免把信息吞掉
        others = [i["text"] for i in self.issues if i.get("kind") == "other"]
        if others:
            tk.Label(body, text="\n".join(others), bg=BG, fg=WARN,
                     font=SMALL_FONT, anchor="w", justify="left",
                     wraplength=560).pack(fill="x", pady=(2, 0))

    # ---------- 交互 ----------
    def _open(self, kind):
        # 顺序很重要：**先触发系统的授权弹窗**，再跳系统设置。
        # 因为「系统设置 › 隐私与安全性」里的列表只会列出**申请过**权限的程序，
        # 直接跳过去用户会发现列表里根本没有本程序，无从勾选。
        try:
            macos.request_permissions()
        except Exception:
            pass
        name = macos.settings_list_name()
        if not macos.open_settings_pane(kind):
            self.status.configure(
                text=f"没能自动打开系统设置，请手动到「系统设置 › 隐私与安全性」里"
                     f"找到「{name}」并打开它的开关。", fg=WARN)
            return
        self.status.configure(
            text=f"已打开系统设置：请找到「{name}」，把最右侧的开关打开，"
                 f"然后回到本窗口点「重新检测」。", fg=MUTED)

    def _refresh(self):
        try:
            rep = macos.permission_report()
        except Exception:
            rep = {}
        missing = 0
        unknown = False
        for key, name, _why, _when in _PERM_ROWS:
            state, btn = self._cells[key]
            val = rep.get(key)
            if val is True:
                state.configure(text=f"✅ {name}：已授权", fg=OK)
                try:
                    btn.configure(state="disabled", cursor="arrow")
                except Exception:
                    pass
            elif val is False:
                missing += 1
                state.configure(text=f"○ {name}：尚未授权", fg=WARN)
            else:
                unknown = True
                state.configure(text=f"? {name}：无法检测", fg=MUTED)
        if missing:
            name = macos.settings_list_name()
            self.status.configure(
                text=f"还缺 {missing} 项授权。请把系统设置里「{name}」的开关打开；"
                     f"开关本就是开的话，关掉再打开一次。"
                     f"开关只对上面显示的那一份程序有效（换过副本或重装过都要重新授权）；"
                     f"「屏幕录制」打开后需退出重开本程序。", fg=WARN)
        elif unknown:
            # 检测不出来就别夸口「已就绪」，如实说明
            self.status.configure(text="权限状态无法自动检测，请按上方说明手动确认。",
                                  fg=WARN)
        else:
            self.status.configure(
                text="权限已就绪。若刚给「屏幕录制」授权，请退出重开本程序。", fg=OK)

    def _done(self):
        self.on_done()


def run_permission_guide(issues=None):
    """弹出权限引导窗口，阻塞到用户关闭。

    只在「没有终端」的启动方式下调用（打包成 .app 双击）；
    从终端启动时权限提示已经打在终端里，不必再弹窗打断。
    """
    if issues is None:
        issues = macos.startup_issues()
    root = tk.Tk()
    root.title("话术润色助手 - 需要授权")
    root.configure(bg=BG)
    root.geometry("620x600")
    root.minsize(560, 540)
    root.attributes("-topmost", True)
    try:
        root.iconify()
        root.deiconify()
    except Exception:
        pass
    # 同上：后台助手模式下必须主动激活，否则窗口拿不到键盘焦点
    try:
        root.lift()
        root.focus_force()
    except Exception:
        pass
    _PermissionGuide(root, issues, root.quit)
    root.mainloop()
    try:
        root.destroy()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 「在安装包里直接运行」的拦截
# ---------------------------------------------------------------------------

def _find_installer():
    """在 App 所在的卷根目录里找安装脚本；找不到返回 None。"""
    try:
        volume = Path(sys.executable).resolve().parents[3]   # …/X.app/Contents/MacOS/x → 卷根
    except Exception:
        return None
    try:
        hits = sorted(volume.glob("安装*.command"))
        return hits[0] if hits else None
    except Exception:
        return None


def run_dmg_guard(volume):
    """拦住「直接在安装包（DMG）里运行」。

    这是本项目最容易踩、又最难自查的坑 —— 双击 DMG 里的图标就能启动，看起来
    一切正常，于是用户直接在 DMG 里授权。但 DMG 是只读卷，系统权限记在那个临时
    路径上，弹出映像或重新挂载后路径就变了，表现为「我明明授权了，程序还说没授权」，
    而且没有任何线索指向真实原因。所以启动第一步就拦下来讲清楚。
    """
    root = tk.Tk()
    root.title("话术润色助手 - 请先安装")
    root.configure(bg=BG)
    root.geometry("640x520")
    root.minsize(600, 480)
    root.attributes("-topmost", True)
    try:
        root.iconify()
        root.deiconify()
        root.lift()
        root.focus_force()
    except Exception:
        pass

    header = tk.Frame(root, bg=PANEL)
    header.pack(fill="x")
    tk.Frame(header, bg=BAD, width=4).pack(side="left", fill="y")
    tk.Label(header, text="请先安装，不要在安装包里直接运行", bg=PANEL, fg="#ffffff",
             font=TITLE_FONT, padx=12, pady=10).pack(side="left")

    installer = _find_installer()

    bottom = tk.Frame(root, bg=PANEL)
    bottom.pack(fill="x", side="bottom")
    flat_button(bottom, "退出", root.destroy, bg=SECONDARY, fg=TEXT,
                hover=SECONDARY_HOVER, font=LABEL_FONT, width=8, pady=6
                ).pack(side="right", padx=10, pady=8)
    if installer is not None:
        flat_button(bottom, "在 Finder 中显示安装脚本",
                    lambda: subprocess.Popen(["open", "-R", str(installer)]),
                    bg=ACCENT, fg="white", hover=ACCENT_HOVER,
                    font=LABEL_FONT, pady=6).pack(side="right", pady=8)

    body = tk.Frame(root, bg=BG)
    body.pack(fill="both", expand=True, padx=16, pady=12)

    def para(text, fg=TEXT, font=None):
        tk.Label(body, text=text, bg=BG, fg=fg, font=font or LABEL_FONT,
                 anchor="w", justify="left", wraplength=580
                 ).pack(fill="x", pady=(0, 8))

    para(f"你现在运行的是安装包磁盘映像（DMG）里的程序：\n{volume}", WARN)
    para("这样授权不会生效 —— 这就是你「明明授权了，程序还说没授权」的原因：")
    # 注意：Label 不解析 markdown，这里不要写 `**`（会原样显示出来）
    para("· DMG 是只读的，macOS 无法把「辅助功能 / 屏幕录制」权限稳定地记在上面\n"
         "· 每次挂载，系统卷名都会变（话术润色助手 → 话术润色助手 1 → 2），\n"
         "   你这次给的授权，下次启动就对不上了", MUTED, SMALL_FONT)

    para("正确做法只要三步：", WARN)
    para("1. 关掉本窗口（程序会退出）\n"
         "2. 在安装包窗口里双击「安装 话术润色助手.command」\n"
         "      （若提示「无法打开，因为它来自身份不明的开发者」，\n"
         "       在它上面右键 →「打开」，再点一次「打开」）\n"
         "3. 装好后从「应用程序」文件夹启动，那时再授权 —— 一次就长期有效")
    if installer is None:
        para("（没自动找到安装脚本，请在安装包窗口里手动找「安装 …command」）", MUTED,
             SMALL_FONT)

    root.mainloop()
    try:
        root.destroy()
    except Exception:
        pass
