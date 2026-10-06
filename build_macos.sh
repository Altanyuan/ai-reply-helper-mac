#!/bin/zsh
# 一键构建 macOS 安装包：源码 → 自包含的 话术润色助手.app → 可直接分发的 DMG。
#
#   ./build_macos.sh              # 构建 .app（并顺手产出 DMG）
#   ./build_macos.sh --app-only   # 只构建 .app，不打 DMG
#   ./build_macos.sh --smoke      # 构建后再启动一次 .app 做冒烟验证
#
# 产物：
#   dist/话术润色助手.app                        双击即用的自包含 App
#   dist/话术润色助手-1.0.0.dmg                     给最终用户的分发包
#
# 目标用户**不需要**预装 Python：解释器、标准库、Tk/Tcl、PyObjC 与全部第三方
# 依赖都会被打进 .app 内部。
#
# ⚠️ 必须在 macOS 上构建（PyInstaller 不做交叉编译）。
# ⚠️ 产物架构 = 构建机架构（Apple Silicon → arm64，Intel → x86_64）。

set -u

APP_NAME="话术润色助手"
VERSION="1.0.0"
PROJ="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJ" || exit 1

MODE="${1:-}"
VENV=".venv"
PY="$VENV/bin/python"

step()  { print -r -- ""; print -r -- "── $1"; }
ok()    { print -r -- "   ✓ $1"; }
warn()  { print -r -- "   ! $1"; }
die()   { print -r -- "   ✗ $1"; print -r -- ""; exit 1; }

print -r -- "=============================================="
print -r -- " 构建 话术润色助手 $VERSION（macOS）"
print -r -- "=============================================="

# ---------------------------------------------------------------- 1) 环境检查
step "1/5 环境检查"

[ "$(uname -s)" = "Darwin" ] || die "本脚本只能在 macOS 上运行（PyInstaller 不支持交叉编译）。"
ok "macOS $(sw_vers -productVersion) / $(uname -m)"

# Apple 自带的 /usr/bin/python3 是 3.9 + Tk 8.5，界面画不出来 —— 必须挑 Tk>=8.6 的
pick_python() {
  local c p tk
  for c in python3.14 python3.13 python3.12 python3.11 python3.10 python3; do
    p="$(command -v "$c" 2>/dev/null)" || continue
    [ -x "$p" ] || continue
    tk="$("$p" -c 'import tkinter;print(int(tkinter.TkVersion*10))' 2>/dev/null)" || continue
    [ -n "${tk:-}" ] && [ "$tk" -ge 86 ] && { print -r -- "$p"; return 0; }
  done
  return 1
}
PY_BIN="$(pick_python)" || die "找不到 Tk>=8.6 的 python3。请先装 python.org 的 Python 3.12+（自带 Tk 8.6）。"
ok "构建用 Python: $PY_BIN ($("$PY_BIN" -V 2>&1))  Tk $("$PY_BIN" -c 'import tkinter;print(tkinter.TkVersion)')"

# ---------------------------------------------------------------- 2) 依赖
step "2/5 准备构建环境与依赖"

if [ ! -x "$PY" ]; then
  print -r -- "   创建虚拟环境 $VENV …"
  "$PY_BIN" -m venv "$VENV" || die "创建 venv 失败"
  "$PY" -m pip install -q --upgrade pip || die "升级 pip 失败"
fi
ok "虚拟环境就绪: $VENV"

print -r -- "   安装运行依赖 + PyInstaller（首次较慢）…"
"$PY" -m pip install -q -r requirements-build.txt \
  || die "依赖安装失败，请把上面的报错发我"
ok "依赖就绪（pyinstaller $("$PY" -m PyInstaller --version))"

# ---------------------------------------------------------------- 3) 图标
step "3/5 生成 App 图标（icns）"

ICON_DIR="$PROJ/icons"
ICON="$ICON_DIR/app.icns"
SRC_ICON="$PROJ/icon_source.png"

make_icns() {
  local src="$1" out="$2" tmp set
  tmp="$(mktemp -d)" || return 1
  set="$tmp/app.iconset"
  mkdir -p "$set"
  # iconset 的命名是固定的，不能随便来；@2x 就是两倍尺寸那份
  local -a spec=("16:icon_16x16" "32:icon_16x16@2x" "32:icon_32x32" "64:icon_32x32@2x" \
                 "128:icon_128x128" "256:icon_128x128@2x" "256:icon_256x256" \
                 "512:icon_256x256@2x" "512:icon_512x512" "1024:icon_512x512@2x")
  local item size name
  for item in "${spec[@]}"; do
    size="${item%%:*}"; name="${item##*:}"
    sips -z "$size" "$size" "$src" --out "$set/$name.png" >/dev/null 2>&1 || { rm -rf "$tmp"; return 1; }
  done
  mkdir -p "$(dirname "$out")"
  iconutil -c icns "$set" -o "$out" >/dev/null 2>&1 || { rm -rf "$tmp"; return 1; }
  rm -rf "$tmp"
  return 0
}

if [ -f "$SRC_ICON" ] && make_icns "$SRC_ICON" "$ICON"; then
  ok "已由 icon_source.png 生成 $ICON"
else
  warn "未能生成 icns（缺 icon_source.png 或缺 sips/iconutil），将使用 PyInstaller 默认图标"
  rm -f "$ICON"
fi

# ---------------------------------------------------------------- 4) 构建
step "4/5 打包（PyInstaller）"

rm -rf build dist
"$PY" -m PyInstaller 话术润色助手.spec --noconfirm --clean 2>&1 \
  | grep -viE "^[0-9]+ INFO: (Analyzing|Processing|Loading|Including|Excluding|Looking)" \
  || die "打包失败，请把上面的报错发我"

APP="dist/$APP_NAME.app"
[ -d "$APP" ] || die "没有产出 $APP"

# ---------------------------------------------------------------- 5) 校验
step "5/5 校验产物"

# 架构
ARCHS="$(lipo -archs "$APP/Contents/MacOS/$APP_NAME" 2>/dev/null)"
[ -n "$ARCHS" ] || die "无法识别可执行文件架构"
ok "可执行架构: $ARCHS（目标 Mac 需匹配）"

# Info.plist 关键键
for key in CFBundleIdentifier CFBundleName LSMinimumSystemVersion; do
  v="$(/usr/libexec/PlistBuddy -c "Print :$key" "$APP/Contents/Info.plist" 2>/dev/null)"
  [ -n "$v" ] && ok "$key = $v" || warn "Info.plist 缺少 $key"
done

# 资源是否真的进包了（漏了就表现为「悬浮图标不出来 / 说明书空白」）
MISSING=0
for res in 1.webp 2.webp author_qrcode.png group_qrcode.png "使用说明-给朋友.md"; do
  if find "$APP" -name "$res" -print -quit 2>/dev/null | grep -q .; then :; else
    warn "资源未进包: $res"; MISSING=1
  fi
done
[ "$MISSING" -eq 0 ] && ok "运行期资源齐备（动图 / 二维码 / 说明书）"

# 自包含：确认没有把 config.json / .env 这类凭据打进去
if find "$APP" \( -name 'config.json' -o -name '.env' \) -print -quit 2>/dev/null | grep -q .; then
  die "产物里发现凭据文件！这会把 API Key 一起分发出去，请检查 spec 的 datas"
fi
ok "未包含任何凭据文件（config.json / .env）"

# 签名（PyInstaller 默认 ad-hoc；arm64 上必须有签名才能运行）
if codesign -dv "$APP" >/dev/null 2>&1; then
  ok "已签名（ad-hoc）：$(codesign -dv "$APP" 2>&1 | grep -i 'Signature' | head -1 | sed 's/^Signature=//')"
else
  warn "未签名 —— 在 Apple Silicon 上可能无法运行"
fi

SIZE="$(du -sh "$APP" | cut -f1)"
ok "体积: $SIZE"

# ---------------------------------------------------------------- 冒烟（可选）
if [ "$MODE" = "--smoke" ]; then
  step "冒烟验证：启动 .app 并检查它真的在跑"
  LOG="$HOME/Library/Logs/$APP_NAME/polish.log"
  rm -f "$LOG"
  open -a "$APP"
  sleep 8
  if pgrep -f "$APP/Contents/MacOS/$APP_NAME" >/dev/null; then
    ok "进程在运行"
    if [ -f "$LOG" ]; then
      ok "日志已生成: $LOG"
    else
      warn "未生成日志（可能权限引导窗挡住了启动流程，属正常）"
    fi
    print -r -- "   关闭它：把鼠标移到右下角悬浮图标上 → 点右上角的 ×"
  else
    die "启动失败（进程没起来）。可手动执行看报错："
  fi
fi

# ---------------------------------------------------------------- DMG
if [ "$MODE" != "--app-only" ]; then
  step "生成 DMG"
  if zsh "$PROJ/installer/build_dmg.sh" "$APP_NAME" "$VERSION"; then
    ok "DMG 已生成: dist/话术润色助手-$VERSION.dmg"
  else
    warn "DMG 生成失败（不影响 .app，可直接分发 .app 的 zip）"
  fi
fi

print -r -- ""
print -r -- "=============================================="
print -r -- " 完成"
print -r -- "  App : $APP"
[ "$MODE" != "--app-only" ] && [ -f "dist/话术润色助手-$VERSION.dmg" ] && \
  print -r -- "  DMG : dist/话术润色助手-$VERSION.dmg"
print -r -- "  架构: $ARCHS"
print -r -- "=============================================="
