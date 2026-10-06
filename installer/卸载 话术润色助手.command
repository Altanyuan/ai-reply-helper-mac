#!/bin/zsh
# 话术润色助手 —— 双击卸载
#
# 默认只删除程序本体，**保留**你的配置与日志（万一是误删还能装回来继续用）；
# 想连配置一起清掉，运行时会问你要不要。

set -u

APP_NAME="话术润色助手"
APP_DISPLAY="话术润色助手"
CONFIG_DIR="$HOME/Library/Application Support/$APP_NAME"
LOG_DIR="$HOME/Library/Logs/$APP_NAME"

say()  { print -r -- "$1"; }
ok()   { print -r -- "   ✓ $1"; }
info() { print -r -- "   · $1"; }

say "=================================================="
say "  $APP_DISPLAY  卸载程序"
say "=================================================="
say ""

FOUND=0
for DEST in "/Applications/$APP_NAME.app" "$HOME/Applications/$APP_NAME.app"; do
  if [ -d "$DEST" ]; then
    FOUND=1
    say "将删除：$DEST"
  fi
done

if [ "$FOUND" -eq 0 ]; then
  say "没有找到已安装的 $APP_DISPLAY。"
  say ""
  read -r "REPLY?仍然要清理配置与日志吗？[y/N] "
  [[ "$REPLY" == [Yy]* ]] || { say "（本窗口可以直接关掉）"; exit 0; }
else
  say ""
  read -r "REPLY?确认卸载吗？这会删除上面的程序本体。[y/N] "
  if [[ "$REPLY" != [Yy]* ]]; then
    say "已取消，什么都没做。"
    say "（本窗口可以直接关掉）"
    exit 0
  fi

  # 先退出正在运行的实例，否则删不干净
  for P in "/Applications/$APP_NAME.app" "$HOME/Applications/$APP_NAME.app"; do
    if pgrep -f "$P/Contents/MacOS/$APP_NAME" >/dev/null 2>&1; then
      pkill -f "$P/Contents/MacOS/$APP_NAME" >/dev/null 2>&1
      sleep 1
      ok "已退出正在运行的实例"
    fi
  done

  for DEST in "/Applications/$APP_NAME.app" "$HOME/Applications/$APP_NAME.app"; do
    if [ -d "$DEST" ]; then
      if rm -rf "$DEST"; then ok "已删除 $DEST"; else say "   ✗ 删除 $DEST 失败（可能没有权限）"; fi
    fi
  done
fi

# ---- 配置与日志 ----
say ""
read -r "REPLY?要一并删除配置（含 API Key）与日志吗？[y/N] "
if [[ "$REPLY" == [Yy]* ]]; then
  for D in "$CONFIG_DIR" "$LOG_DIR"; do
    if [ -d "$D" ]; then
      rm -rf "$D" && ok "已删除 $D" || say "   ✗ 删除 $D 失败"
    fi
  done
else
  info "已保留配置与日志（想彻底清理可手动删除下面两个目录）："
  info "$CONFIG_DIR"
  info "$LOG_DIR"
fi

say ""
say "还有一件事需要手动做（系统不允许脚本代劳）："
say "  到「系统设置 › 隐私与安全性 › 辅助功能 / 录屏与系统录音」里，"
say "  把「$APP_DISPLAY」从列表中移除。"
say ""
say "（本窗口可以直接关掉）"
