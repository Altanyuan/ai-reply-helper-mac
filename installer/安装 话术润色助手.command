#!/bin/zsh
# 话术润色助手 —— 双击安装
#
# 它做三件事：
#   1. 把 App 复制到「应用程序」
#   2. 去掉 macOS 的隔离标记（否则每次打开都会被系统拦下来）
#   3. 校验签名并告诉你接下来要授权什么
#
# 为什么要用脚本而不是「把 App 拖进应用程序」：
#   拖拽**不会**清除隔离标记，也不做架构与签名的校验；出了问题用户只会看到
#   「打不开」，完全没有线索。这里每一步都会说清楚发生了什么。

set -u

APP_NAME="话术润色助手"
APP_DISPLAY="话术润色助手"
HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="$HERE/$APP_NAME.app"

say()  { print -r -- "$1"; }
step() { print -r -- ""; print -r -- "▸ $1"; }
ok()   { print -r -- "   ✓ $1"; }
warn() { print -r -- "   ! $1"; }
fail() { print -r -- "   ✗ $1"; }
bye()  { print -r -- ""; print -r -- "（本窗口可以直接关掉）"; exit "${1:-0}"; }

print -r -- "=================================================="
print -r -- "  $APP_DISPLAY  安装程序"
print -r -- "=================================================="
say ""
say "· 会把它装到「应用程序」文件夹"
say "· 会解除系统隔离标记，之后双击即可正常打开"
say "· **不需要你预装 Python** —— 运行环境已经打包在 App 内部"

# ---------------------------------------------------------------- 1) 找 App
step "1/4  找到要安装的 App"
if [ ! -d "$SRC" ]; then
  # 兜底：在已挂载的磁盘映像里找（用户可能只把脚本单独拷出来了）
  for v in /Volumes/*; do
    if [ -d "$v/$APP_NAME.app" ]; then SRC="$v/$APP_NAME.app"; break; fi
  done
fi
if [ ! -d "$SRC" ]; then
  fail "没找到 $APP_NAME.app"
  say "      请把它与本安装脚本放在同一个文件夹里再运行；"
  say "      或者手动把 App 拖进「应用程序」，然后右键 →「打开」一次。"
  bye 1
fi
ok "找到：$SRC"

# ---------------------------------------------------------------- 2) 架构
step "2/4  检查是否适配这台 Mac"
MACHINE="$(uname -m)"
APP_ARCH="$(lipo -archs "$SRC/Contents/MacOS/$APP_NAME" 2>/dev/null)"
if [ -z "$APP_ARCH" ]; then
  warn "读不出 App 的架构信息，跳过这一项检查"
elif [[ " $APP_ARCH " == *" $MACHINE "* ]]; then
  ok "App 支持 $APP_ARCH，本机是 $MACHINE"
else
  fail "架构不匹配：这个 App 是为「$APP_ARCH」构建的，本机是「$MACHINE」"
  say "      Apple Silicon（M 系列）与 Intel 芯片的程序不能混用。"
  say "      请在对应架构的 Mac 上重新构建，或向作者索取对应版本。"
  bye 1
fi

# ---------------------------------------------------------------- 3) 复制
step "3/4  复制到「应用程序」"
# 默认装到 /Applications；想装到别处可先设环境变量 WECHAT_AI_POLISH_DEST
# （也方便自动化测试时不动真实系统目录）
DEST_DIR="${WECHAT_AI_POLISH_DEST:-/Applications}"
if [ ! -w "$DEST_DIR" ] && [ "$DEST_DIR" != "/Applications" ]; then
  warn "指定的目录不可写：$DEST_DIR"
  bye 1
fi
if [ ! -w "$DEST_DIR" ]; then
  warn "「/Applications」当前不可写，改装到你的个人目录：~/Applications"
  DEST_DIR="$HOME/Applications"
  mkdir -p "$DEST_DIR" || { fail "创建 $DEST_DIR 失败"; bye 1; }
fi
DEST="$DEST_DIR/$APP_NAME.app"

# 正在运行的旧实例会让替换失败，先温和退掉
if pgrep -f "$DEST/Contents/MacOS/$APP_NAME" >/dev/null 2>&1; then
  pkill -f "$DEST/Contents/MacOS/$APP_NAME" >/dev/null 2>&1
  ok "已退出正在运行的旧版本"
  sleep 1
fi

if [ -d "$DEST" ]; then
  say "   检测到已安装过，将替换为新版本（你的 API Key 与设置都会保留）"
  rm -rf "$DEST" || { fail "删除旧版本失败，请先手动把它拖进废纸篓"; bye 1; }
fi

# 用 ditto 而不是 cp -R：能保住代码签名、扩展属性与资源分叉（cp 会破坏签名）
if ditto "$SRC" "$DEST"; then
  ok "已安装到：$DEST"
else
  fail "复制失败（磁盘空间不足？没有写入权限？）"
  bye 1
fi

# ---------------------------------------------------------------- 4) 隔离与签名
step "4/4  解除系统隔离标记并校验"
if xattr -dr com.apple.quarantine "$DEST" 2>/dev/null; then
  ok "已解除隔离标记（之后双击就能打开，不再被系统拦）"
else
  warn "解除隔离标记时出错，首次打开请右键 →「打开」"
fi

if codesign --verify --deep "$DEST" 2>/dev/null; then
  ok "代码签名校验通过"
else
  warn "签名校验未通过（程序仍可运行；首次打开若被拦，请右键 →「打开」）"
fi

# ---------------------------------------------------------------- 完成
say ""
print -r -- "=================================================="
print -r -- " 安装完成"
print -r -- "=================================================="
say ""
say "接下来会发生什么："
say ""
say "  1) 第一次启动会弹出「还差一步授权」，按提示给它两项系统权限："
say "       · 辅助功能 —— 打开开关后立即生效（读草稿、回填、全局热键）"
say "       · 屏幕录制 —— 打开开关后需要退出重开 App（截图读聊天上下文）"
say ""
say "     注意：在系统设置的列表里，本程序显示为「$APP_NAME」（App 的文件名），"
say "           不是中文名「$APP_DISPLAY」—— 照中文名去找是找不到的。"
say "           另外：「去授权」按钮只是帮你跳到设置页，不等于已授权，"
say "           跳过去之后还要自己把那一行最右侧的开关拨开。"
say ""
say "  2) 接着会弹出配置窗口，把你自己的 DeepSeek API Key 粘进去"
say "       （申请地址 https://platform.deepseek.com/api_keys，新用户有免费额度）"
say ""
say "  3) 之后在微信里写好草稿，按 Ctrl+Alt+P 就会弹出润色结果"
say ""
say "它会同时出现在两个地方："
say "      · 屏幕右下角的小图标 —— 主要入口：点它＝润色，右键它＝打开设置"
say "      · Dock 里的图标 —— 方便你看到它在运行"
say "      退出：鼠标移到右下角图标上，点右上角浮现的 ×；或右键 Dock 图标 →「退出」。"
say ""
read -r "REPLY?输入 y 立即启动，直接回车关闭本窗口："
if [[ "$REPLY" == [Yy]* ]]; then
  if open -a "$DEST"; then
    ok "已启动 —— 请看屏幕右下角出现的小图标"
  else
    warn "启动失败，可到「应用程序」里双击试试"
  fi
fi
bye 0
