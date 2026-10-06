#!/bin/zsh
# 启动入口（macOS）
#
# 用法：
#   双击本文件，或在终端执行  ./run-mac.command
#
# 首次运行会自动建 .venv 并安装 requirements.txt（约几分钟），之后直接启动。
#
# 注意：从终端启动时，「屏幕录制 / 辅助功能」权限是记在**终端 App** 上的，
#       所以要去「系统设置 › 隐私与安全性」给 Terminal（或 iTerm）授权。
#       屏幕录制授权后**必须退出重开终端**才生效。

set -u
cd "$(dirname "$0")" || exit 1
export PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

# 系统自带 Python 3.9 挂的是 Apple 已弃用的 Tk 8.5，在新版 macOS 上**无法渲染控件**
# （窗口能建、内容是空白，表现为悬浮图标变成一块白框）。这里静音它的废弃告警，
# 并且会自动优先挑一个自带 Tk 8.6 的 Python（见下方 pick_python）。
export TK_SILENCE_DEPRECATION=1

VENV=".venv"
STAMP="$VENV/.python-used"

# ---------- 挑一个 Tk >= 8.6 的 python3 ----------
# Apple 自带的 /usr/bin/python3 是 3.9 + Tk 8.5，界面画不出来；
# python.org 装的 3.12/3.13 自带 Tk 8.6，能正常显示。
pick_python() {
  local c p tk
  for c in python3.14 python3.13 python3.12 python3.11 python3.10 python3; do
    p="$(command -v "$c" 2>/dev/null)" || continue
    [ -x "$p" ] || continue
    tk="$("$p" -c 'import tkinter;print(int(tkinter.TkVersion*10))' 2>/dev/null)" || continue
    if [ -n "${tk:-}" ] && [ "$tk" -ge 86 ]; then
      echo "$p"
      return 0
    fi
  done
  # 没有 Tk>=8.6 的，退回默认 python3（界面会空白，程序启动时会明确告警）
  command -v python3
}

PY_BIN="$(pick_python)"
if [ -z "${PY_BIN:-}" ]; then
  echo "✗ 找不到 python3，请先安装 Python（推荐 python.org 的 3.12+）"
  exit 1
fi
PY_VER="$("$PY_BIN" -V 2>&1)"
# 版本比较交给 Python 做，避免 shell 里拼字符串判断出错
TK_VER="$("$PY_BIN" -c 'import tkinter;print(tkinter.TkVersion)' 2>/dev/null || echo '无')"
TK_OK="$("$PY_BIN" -c 'import tkinter;print(1 if tkinter.TkVersion>=8.6 else 0)' 2>/dev/null || echo 0)"

echo "=== 微信 AI 润色助手（macOS）==="
echo "Python : $PY_BIN  ($PY_VER)"
echo "Tk     : $TK_VER"
if [ "$TK_OK" != "1" ]; then
  echo
  echo "⚠️  Tk $TK_VER 偏低。Apple 自带 Tk 8.5 在新版 macOS 上画不出界面，"
  echo "    悬浮图标 / 弹窗会是一片空白。建议装 python.org 的 Python 3.12+ 再重跑本脚本："
  echo "      https://www.python.org/ftp/python/3.12.10/python-3.12.10-macos11.pkg"
  echo "    （装完直接重跑即可：脚本会自动切到新 Python 并重建 .venv）"
fi
echo

# ---------- 1) 建/重建 venv ----------
PY="$VENV/bin/python"
CUR_TAG="$("$PY_BIN" -c 'import sys;print("%s|%d.%d" % (sys.executable, sys.version_info[0], sys.version_info[1]))')"
if [ -x "$PY" ] && [ "$(cat "$STAMP" 2>/dev/null || echo)" != "$CUR_TAG" ]; then
  echo "检测到 Python 环境变化 → 重建虚拟环境（依赖会重新安装）"
  rm -rf "$VENV"
fi

if [ ! -x "$PY" ]; then
  echo "创建虚拟环境并安装依赖（几分钟，请稍候）…"
  "$PY_BIN" -m venv "$VENV" || { echo "✗ 创建 venv 失败"; exit 1; }
  "$PY" -m pip install -q --upgrade pip
  if ! "$PY" -m pip install -r requirements.txt; then
    echo "✗ 依赖安装失败，请把上面的报错发我"
    exit 1
  fi
  echo "$CUR_TAG" > "$STAMP"
  echo "✓ 依赖安装完成"
  echo
fi

# ---------- 2) 依赖 + 权限 + 环境自检 ----------
"$PY" - <<'PYEOF'
import sys
sys.path.insert(0, ".")
try:
    import macos
except Exception as e:
    print(f"✗ 系统集成层导入失败：{type(e).__name__}: {e}")
    raise SystemExit(1)

print(f"平台：{macos.describe()}")
hint = ""
try:
    hint = macos.permission_hint()
except Exception:
    pass
if hint:
    print()
    print("⚠️  还差一点（不影响启动，但相关功能会失效）：")
    print(hint)
    print()
else:
    print("✓ 依赖与权限就绪")
PYEOF
if [ $? -ne 0 ]; then exit 1; fi

# ---------- 3) 启动 ----------
echo "启动中… 关闭窗口或按 Ctrl+C 退出"
echo "用法：微信里写好草稿 → 按热键（默认 ctrl+alt+p）→ 选一个版本 → 回填"
echo "设置页：右键点右下角悬浮图标 → 「打开设置…」"
echo
exec "$PY" main.py
