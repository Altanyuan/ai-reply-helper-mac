"""扁平按钮（tkinter）。

为什么需要这个模块
------------------
**Aqua Tk 会忽略 `tk.Button` 的 `bg`** —— 无论传什么颜色，都渲染成系统原生的
浅色按钮；但 `fg` 依然生效。于是所有「彩色底 / 深色底 + 白字」的按钮都会变成
**浅底白字 → 文字完全看不见**（按钮只剩一个空白胶囊）。这是 macOS + Tk 的
特有行为，不看实际渲染结果几乎发现不了。

实测表现（macOS 26 + Tk 8.6）：
  · 结果弹窗里的「发送」按钮 → 空白，看不到「发送」二字
  · 设置页的「新增提示词」「新增模型」等主按钮 → 空白
  · 首次引导页的「保存并启动」按钮 → 空白

解决办法：用 `tk.Label` 自绘按钮。Label 的 `bg`/`fg` 一定生效，
外观稳定，还顺带能自己做悬停/按下的反馈。

用法
----
    from tk_widgets import flat_button
    flat_button(parent, "发送", on_send, bg="#07C160", fg="white").pack(side="right")

    # 想要指定的悬停色（否则按底色自动算一个更暗的）
    flat_button(p, "保存", on_save, bg=ACCENT, fg="white", hover=ACCENT_HOVER)

返回的对象是 `tk.Label`，因此：
  · `configure(text="…")` / `["state"]` / `state="disabled"` 照常可用
    （禁用时文字颜色取 `disabledforeground`，已默认设成低对比灰）；
  · `pack` / `grid` / `place` 与 `tk.Button` 用法完全一致。
"""

from __future__ import annotations

import tkinter as tk

#: 禁用态文字默认色（低对比灰，明确表达「不可点」）
DISABLED_FG = "#6b6b6b"


def shade(color, factor):
    """把 `#rrggbb` 按系数调暗/调亮；解析失败则原样返回。"""
    try:
        r = int(color[1:3], 16)
        g = int(color[3:5], 16)
        b = int(color[5:7], 16)
    except Exception:
        return color
    return "#%02x%02x%02x" % (
        max(0, min(255, int(r * factor))),
        max(0, min(255, int(g * factor))),
        max(0, min(255, int(b * factor))),
    )


def flat_button(parent, text, command, bg, fg, *, hover=None, width=None,
                font=None, padx=10, pady=5, disabled_fg=DISABLED_FG,
                cursor="hand2"):
    """扁平按钮（用 Label 自绘，避开 macOS 忽略 `tk.Button.bg` 的问题）。

    只在「按下之后仍在按钮范围内松开」时才触发 `command`，
    避免按住后把鼠标拖出去仍然误触。
    """
    hover = hover or shade(bg, 0.88)
    pressed = shade(bg, 0.76)

    lbl = tk.Label(parent, text=text, bg=bg, fg=fg, font=font,
                   cursor=cursor, padx=padx, pady=pady,
                   bd=0, highlightthickness=0,
                   disabledforeground=disabled_fg)
    if width:
        lbl.configure(width=width)

    # 调色板记在控件上：外部切换禁用态时可以据此复原（详见 config_editor._set_btn_enabled）
    lbl._flat_palette = {"bg": bg, "fg": fg, "hover": hover,
                         "pressed": pressed, "disabled_fg": disabled_fg}

    def _enabled():
        try:
            return str(lbl["state"]) != "disabled"
        except Exception:
            return True

    def _enter(_e):
        if _enabled():
            lbl.configure(bg=hover)

    def _leave(_e):
        if _enabled():
            lbl.configure(bg=bg)

    def _press(_e):
        if _enabled():
            lbl.configure(bg=pressed)

    def _release(_e):
        if not _enabled():
            return
        lbl.configure(bg=hover)
        try:
            command()
        except Exception:
            # 单个按钮的回调出错不该让整个弹窗崩掉
            import logging
            logging.getLogger("polish").exception("按钮回调异常")

    lbl.bind("<Enter>", _enter)
    lbl.bind("<Leave>", _leave)
    lbl.bind("<ButtonPress-1>", _press)
    lbl.bind("<ButtonRelease-1>", _release)
    return lbl
