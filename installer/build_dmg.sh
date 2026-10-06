#!/bin/zsh
# 把 dist/<App>.app 打成可直接分发给最终用户的 DMG。
#
#   zsh installer/build_dmg.sh [AppName] [Version]
#
# 一般不用手动跑 —— build_macos.sh 构建完会自动调用它。
#
# DMG 里放什么（都是为了「非技术用户点开就知道该干嘛」）：
#   话术润色助手.app                程序本体（自包含，不需要 Python）
#   应用程序                           指向 /Applications 的快捷方式（也能直接拖）
#   安装 话术润色助手.command        双击安装：复制 + 去隔离 + 校验 + 引导授权
#   卸载 话术润色助手.command        双击卸载
#   安装说明.md                        一页纸说明（先看这个）

set -u

APP_NAME="${1:-话术润色助手}"
VERSION="${2:-1.0.0}"
DISPLAY_NAME="话术润色助手"

PROJ="$(cd "$(dirname "$0")/.." && pwd)"
APP="$PROJ/dist/$APP_NAME.app"
DMG="$PROJ/dist/$DISPLAY_NAME-$VERSION.dmg"

say() { print -r -- "$1"; }

[ -d "$APP" ] || { say "✗ 找不到 $APP，请先运行 ./build_macos.sh"; exit 1; }

STAGE_ROOT="$(mktemp -d)" || exit 1
STAGE="$STAGE_ROOT/stage"
mkdir -p "$STAGE" || exit 1
# 无论中途哪一步失败都要清掉临时目录
trap 'rm -rf "$STAGE_ROOT"' EXIT INT TERM

# 1) 程序本体。用 ditto 保住签名与扩展属性（cp -R 会破坏签名）
ditto "$APP" "$STAGE/$APP_NAME.app" || { say "✗ 复制 App 失败"; exit 1; }

# 2) 「应用程序」快捷方式：会拖的用户直接拖，不会的用安装脚本
ln -s /Applications "$STAGE/应用程序"

# 3) 安装 / 卸载脚本（必须可执行，否则双击是「文本编辑」打开）
for f in "安装 $APP_NAME.command" "卸载 $APP_NAME.command"; do
  if [ -f "$PROJ/installer/$f" ]; then
    cp "$PROJ/installer/$f" "$STAGE/$f" || { say "✗ 复制 $f 失败"; exit 1; }
    chmod +x "$STAGE/$f"
  else
    say "! 缺少 installer/$f"
  fi
done

# 4) 面向用户的说明
[ -f "$PROJ/installer/安装说明.md" ] && cp "$PROJ/installer/安装说明.md" "$STAGE/"

# 5) 生成 DMG（UDZO = 压缩只读，体积最小）
rm -f "$DMG"
hdiutil create \
  -volname "$DISPLAY_NAME" \
  -srcfolder "$STAGE" \
  -ov -format UDZO -quiet \
  "$DMG" || { say "✗ hdiutil 生成 DMG 失败"; exit 1; }

[ -f "$DMG" ] || { say "✗ DMG 没有生成"; exit 1; }
say "   ✓ $(basename "$DMG")  ($(du -sh "$DMG" | cut -f1))"
exit 0
