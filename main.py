import logging
import sys
import threading
import time
import queue as _queue
from pathlib import Path
from pynput import keyboard, mouse as pynput_mouse

import config
import first_run
import log_crypto
import macos
import paths
# 所有系统调用都收敛在 macos/ 里：Quartz 定位窗口、screencapture + Vision OCR 读
# 聊天上下文、辅助功能读写输入框。main.py 本身不出现任何系统框架 API。
from macos import (capture_draft, ensure_single_instance, find_wechat_control,
                   get_foreground_window, is_wechat_running, make_tray,
                   paste_back, read_wechat_input, read_wechat_recent)
from ai_client import polish
from ui import show_choices, show_error

tray = None  # 右下角浮动动图组件，main() 中初始化

# 日志：对外行为与改造前完全一致（根 logger + 单个文件 Handler、同样的格式与级别），
# 区别只在「写盘前逐条加密」——polish.log 里不会出现明文，还原见 log_decrypt.py
# 或带 --decrypt-log 参数启动本程序（见文件末尾的 _decrypt_cli）。
_LOG_FILE = paths.log_path()
_LOG_HANDLER = log_crypto.install_file_logging(
    _LOG_FILE,
    level=logging.INFO,
    fmt="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("polish")

_lock = threading.Lock()
# 已打开的设置窗口（只在主线程读写）。用它判断「是否已打开」而不是用锁：
# 窗口关掉后 winfo_exists() 自然为假，不会像锁那样万一没释放就再也打不开。
_config_win = None

# 版本标识：每次启动写入日志，便于确认跑的是哪个版本
APP_VERSION = "2026-09-26-macos-only"

# 单实例：flock 加锁 + 温和接管旧实例（见 macos/instance.py），
# 启动时由 ensure_single_instance() 处理。


def run_ui(build_fn):
    """把弹框构建函数安全地放到【主线程】执行（避免子线程新建 Tk 卡死）。

    - 有悬浮图标且其 Tk 根已就绪：通过 tray.run_ui() 入队，由主循环在该 Tcl
      解释器里构建 Toplevel；build_fn 立即返回、不阻塞业务线程。
    - 否则（无悬浮图标兜底场景）：另起线程自建 Tk + mainloop。
    """
    if tray is not None and tray.get_root() is not None:
        tray.run_ui(build_fn)
    else:
        threading.Thread(target=build_fn, daemon=True).start()


def _hotkey_to_pynput(hk):
    mapping = {
        "ctrl": "<ctrl>",
        "shift": "<shift>",
        "alt": "<alt>",
        "space": "<space>",
        "cmd": "<cmd>",
    }
    # 注意：只有修饰键需要 <...> 包裹；普通字符（如 p）必须保持裸写，
    # 否则 pynput 会把 <p> 当成 Key 枚举查找，导致 KeyError / ValueError。
    return "+".join(mapping.get(p.strip().lower(), p.strip().lower())
                    for p in hk.replace("+", " ").split())


_POLL_INTERVAL = 0.4      # 轮询微信窗口是否出现的间隔（秒）
_GRAB_GRACE = 3.0         # 窗口出现后，留给实际抓取内容的宽限时间（秒）


def _wid_of(ref):
    """取 WindowRef 的窗口 ID；ref 为空/异常时返回 None。

    窗口 ID 仅作为「粘性」提示传给窗口定位，用于避免多窗口尺寸接近时反复横跳；
    取不到也不影响定位（会退化为按面积挑主窗口）。
    """
    try:
        return int(ref.wid) or None
    except Exception:
        return None


def _grab_wechat(cfg, timeout=10.0):
    """在独立的 daemon 后台线程里等待并抓取微信上下文 + 输入框草稿。

    行为（满足“等待超过 N 秒仍拿不到才报错”的诉求）：
      • 在 timeout 秒内以 _POLL_INTERVAL 轮询微信主窗口是否出现；
        —— 用户按热键后才去打开微信的场景也能被覆盖。
      • 窗口一出现就立即抓取内容（上下文 + 草稿）并返回，不傻等到 timeout。
      • 若 timeout 秒内窗口始终没出现（或抓取异常），才返回超时标记，
        由调用方弹一次提示框。

    返回：
      {"context": str, "draft": str}            成功
      {"__timeout__": True, "window_found": b}  超时/失败（b 表示期间是否曾检测到窗口）
    超时后本函数立即返回（不阻塞触发线程），后台线程自行结束。
    """
    result_q = _queue.Queue()

    def _worker():
        window_found = False
        try:
            deadline = time.time() + timeout
            attempt = 0
            while True:
                attempt += 1
                # 单轮抓取异常（如微信刚启动、窗口尚未就绪）不当作最终失败，
                # 而是计入预算继续重试，避免过早弹框。
                try:
                    # Quartz 按 owner 精确匹配（微信 / WeChat），拿窗口 ID
                    win = find_wechat_control()
                    if win is not None:
                        window_found = True
                        ctx = read_wechat_recent(
                            int(cfg.get("context_messages", 5)),
                            use_ocr=bool(cfg.get("ocr_context", True)),
                            window_id=int(win.wid) if win.wid else None,
                        )
                        d = read_wechat_input()
                        if not d or not d.strip():
                            # 内部已对「无微信」做闸门，不会误发键
                            d = capture_draft()
                        result_q.put(
                            {"context": ctx or "", "draft": (d or "").strip()}
                        )
                        return
                except Exception as e:
                    log.warning(f"第 {attempt} 次抓取微信内容失败，将重试: {e}")
                if time.time() >= deadline:
                    break
                time.sleep(_POLL_INTERVAL)
            result_q.put({"__timeout__": True, "window_found": window_found})
        except Exception as e:
            log.exception(f"抓取微信上下文异常: {e}")
            try:
                result_q.put({"__timeout__": True, "window_found": window_found})
            except Exception:
                pass

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    try:
        # 硬兜底：等待窗口 timeout 秒 + 抓取宽限，避免极端情况下无限等待
        box = result_q.get(timeout=timeout + _GRAB_GRACE)
    except _queue.Empty:
        # 超时：放弃等待，daemon 后台线程最终自行结束，不阻塞本线程
        log.warning(f"获取微信窗口/上下文超时（>{timeout}s），放弃本次抓取")
        return {"__timeout__": True, "window_found": False}
    return box


def _nothing_read_message(reason: str) -> str:
    """「既没读到草稿、也没读到上下文」时给用户的提示。

    要点：把**真实原因**放在最前面。以前这里是一段固定文案 ——
    用户看到的是「输入框为空，且没有读到聊天上下文」，外加一段从 Windows 版
    留下来的说明（说需要安装中文 OCR 语言包；macOS 的 Vision 自带中文模型，
    根本不需要），完全指不到真正的问题（多数是权限没给，或授权后没重开程序）。
    """
    parts = ["没能从微信里读到内容，无法生成。"]
    if reason:
        parts.append(f"原因：{reason}")
    parts.append(
        "请依次确认：\n"
        "1) 微信里已打开一个具体的聊天窗口（不是只停在会话列表）\n"
        "2) 输入框里有草稿，或者聊天窗口里有可见的消息\n"
        "3) 两项系统权限都已授予（「设置 › 使用说明」里有一、安装与首次启动 一节）：\n"
        "   · 辅助功能 —— 读输入框草稿、回填、全局热键\n"
        "   · 屏幕录制 —— 截图读取聊天上下文\n"
        "4) 刚给「屏幕录制」授权的话，必须**退出并重开本程序**才生效\n\n"
        "另：若你安装过多个副本（比如一份在「应用程序」、另一份在别处），\n"
        "macOS 会把它们当成两个不同的程序 —— 权限必须授给**当前正在运行的这一份**。"
    )
    return "\n\n".join(parts)


def _handle():
    cfg = config.load_config()
    log.info("热键触发")
    if tray:
        tray.show_active()  # 按热键 → 右下角切到 2.webp
    # 记录当前前台窗口（通常是微信输入框），用于回填时还原焦点
    target = None
    try:
        target = get_foreground_window()
    except Exception:
        pass

    # 统一为“最多等待 N 秒”：期间持续轮询微信窗口（用户可在此期间打开微信），
    # 窗口一出现就立即抓取内容；只有超过 N 秒仍拿不到，才弹一次提示框。
    # 不再做“窗口不存在就立即弹框”的瞬时校验，避免误报且给用户反应时间。
    timeout = float(cfg.get("grab_timeout", 10))
    grabbed = _grab_wechat(cfg, timeout=timeout)
    if grabbed is None or grabbed.get("__timeout__"):
        window_found = bool(grabbed and grabbed.get("window_found"))
        log.warning(
            f"获取微信聊天窗口超时（>{timeout}s，期间检测到窗口={window_found}）"
        )
        if window_found:
            msg = (
                f"已检测到微信，但在 {timeout:.0f} 秒内读取聊天内容失败。\n\n"
                "建议：\n"
                "• 确认微信已登录并打开了具体的聊天窗口；\n"
                "• 窗口未被最小化、卡死；\n"
                "• 在输入框里写好草稿后重新按热键重试。"
            )
        elif is_wechat_running():
            # 微信进程在，但主窗口定位失败 —— 通常是窗口被收进程序坞，或未知改版
            log.warning("微信进程存在但主窗口定位失败（可能收进程序坞或版本改版）")
            msg = (
                f"检测到微信正在运行，但 {timeout:.0f} 秒内未能定位到微信主窗口。\n\n"
                "建议：\n"
                "• 把微信主窗口从最小化/程序坞里恢复出来，不要只留一个收起的窗口；\n"
                "• 进入具体的聊天窗口后再重试；\n"
                "• 若仍不行，可能是当前微信版本的窗口结构有变化，\n"
                "  可查看 polish.log 里「微信窗口已定位」相关日志确认定位结果。"
            )
        else:
            msg = (
                f"等待 {timeout:.0f} 秒仍未检测到微信窗口，无法获取聊天内容。\n\n"
                "建议：\n"
                "• 先打开微信并进入要回复的聊天窗口；\n"
                "• 在输入框里写好草稿；\n"
                "• 然后重新按热键（或点击右下角图标）重试。"
            )

        def _e2():
            show_error(
                msg,
                master=tray.get_root() if tray else None,
                on_close=lambda: tray.show_idle() if tray else None,
            )
        run_ui(_e2)
        return

    context = grabbed["context"]
    draft = grabbed["draft"]   # 已 strip 的字符串

    if not draft and not context.strip():
        reason = ""
        try:
            reason = macos.last_context_error()
        except Exception:
            pass
        msg = _nothing_read_message(reason)

        def _e3():
            show_error(
                msg,
                master=tray.get_root() if tray else None,
                on_close=lambda: tray.show_idle() if tray else None,
            )
        run_ui(_e3)
        return

    mode = "根据上下文生成回复" if not draft else "润色草稿"
    log.info(f"草稿长度={len(draft)} 上下文长度={len(context)} 模式={mode}")

    try:
        versions = polish(draft, context, cfg)
    except Exception as e:
        log.exception(f"AI 调用失败: {e}")
        raw = str(e)
        low = raw.lower()
        if "401" in raw or "invalid" in low or "authentication" in low:
            hint = ("大模型返回 401：API Key 无效。\n"
                    "请到 platform.deepseek.com 的【API keys】重新生成 Key，\n"
                    "然后按鼠标中键打开「设置 → 模型与 Key」替换并保存；\n"
                    "也可用环境变量 AI_API_KEY 临时覆盖。")
        elif any(k in low for k in ("ssl", "eof", "certificate", "connection",
                                    "max retries", "proxy", "getaddrinfo",
                                    "timed out", "timeout", "unreachable")):
            # 这类错误在「开着代理 / 走公司网络 / 杀软做 HTTPS 扫描」的机器上很常见，
            # 原始异常是一长串英文堆栈，用户看不出所以然 —— 这里翻译成能照着做的步骤。
            hint = ("网络连接失败：没能连上大模型接口（连接被中断 / TLS 握手失败）。\n\n"
                    "排查建议（按顺序试）：\n"
                    "• 关掉代理、VPN、加速器再试；若必须用代理，把 api.deepseek.com 设为直连；\n"
                    "• 公司/校园网络常拦这类请求，换成手机热点试一下；\n"
                    "• 杀毒软件的「HTTPS 扫描 / 网页防护」也会插一脚，临时关掉再试；\n"
                    "• 确认系统时间准确（时间偏差过大会导致 TLS 握手失败）。\n\n"
                    f"原始错误：{raw.splitlines()[0][:200]}")
        else:
            hint = raw.splitlines()[0][:300] if raw else "未知错误"
        def _e4():
            show_error(
                f"AI 润色失败：\n{hint}",
                master=tray.get_root() if tray else None,
                on_close=lambda: tray.show_idle() if tray else None,
            )
        run_ui(_e4)
        return

    if not versions:
        log.warning("AI 未返回任何版本")
        def _e5():
            show_error(
                "AI 未返回任何版本，请检查模型配置或网络。",
                master=tray.get_root() if tray else None,
                on_close=lambda: tray.show_idle() if tray else None,
            )
        run_ui(_e5)
        return

    def choose(text):
        # 回填放到独立线程：AX 写入 + 逐字键盘输入要发很多次系统调用，
        # 放主线程会把 Tk 主循环一起冻住（弹窗与悬浮图标都会卡住）。
        def _paste():
            try:
                # target 是按下热键时的前台窗口，其窗口 ID 作为「粘性」提示
                # 传给窗口定位，避免多窗口等大时挑错。
                paste_back(text, target_window_id=_wid_of(target))
            except Exception as e:
                log.exception(f"回填失败: {e}")
        threading.Thread(target=_paste, daemon=True).start()

    def _e6():
        show_choices(
            versions, choose,
            master=tray.get_root() if tray else None,
            on_close=lambda: tray.show_idle() if tray else None,
        )
    run_ui(_e6)
    return


def trigger():
    """点击右下角动图图标时触发：与热键等价，在独立守护线程跑同一套 _handle 流程。"""
    t = threading.Thread(target=on_activate, daemon=True)
    t.start()


def _open_config_editor():
    """在【主线程】创建 / 前置设置窗口（由 run_ui / tray.run_ui 路由进来）。

    macOS 只允许主线程创建 NSWindow：子线程建窗会被
    `NSInternalInconsistencyException: NSWindow should only be instantiated on
    the main thread!` 直接 abort 掉整个进程 —— 不是异常，捕获不住，进程当场死。
    """
    global _config_win

    # 已经开着 → 提到最前即可（替代原来的 _config_lock）
    if _config_win is not None:
        try:
            if _config_win.winfo_exists():
                _config_win.deiconify()
                _config_win.lift()
                _config_win.attributes("-topmost", True)
                log.info("设置页已打开，这次只把它提到最前")
                return
        except Exception:
            pass
        _config_win = None

    try:
        import config_editor
        _config_win = config_editor.open_editor(
            master=tray.get_root() if tray is not None else None
        )
        log.info("设置页已打开")
    except Exception as e:
        log.exception(f"打开设置页失败: {e}")


def open_config_editor():
    """打开设置页（默认停在「软件作者」页；鼠标中键 / 悬浮图标右键菜单触发）。

    窗口必须在**主线程**创建，所以这里不再「另起线程跑 tkinter」，
    而是复用悬浮图标已有的主线程队列 —— 与润色弹窗 show_choices 走同一条通路。
    """
    if tray is not None and tray.get_root() is not None:
        tray.run_ui(_open_config_editor)
        return

    # 无悬浮图标兜底：主线程在 run_event_loop(None) 里空转，没有 Tk 宿主可用。
    log.warning("悬浮图标未就绪，设置在独立线程中打开（可能无法显示）")
    threading.Thread(target=_open_config_editor, daemon=True).start()


def on_activate():
    if not _lock.acquire(blocking=False):
        log.info("已有处理在进行，忽略本次触发")
        return
    try:
        _handle()
    except Exception as e:
        log.exception(f"处理失败: {e}")
    finally:
        _lock.release()


def main():
    global tray

    # 第一件事：拦住「直接在安装包（DMG）里运行」。
    # 这是最坑的场景 —— DMG 里双击就能启动，看起来一切正常，但系统权限记在那个
    # 临时只读卷上，弹出映像 / 重新挂载后路径就变了（卷名还会变成「话术润色助手 2」），
    # 用户看到「我明明授权了，程序还说没授权」却完全查不到原因。这里直接说清楚再退出。
    # 必须放在 ensure_single_instance 之前：安装包里的这一次运行不应该接管单实例锁。
    try:
        vol = macos.transient_volume()
    except Exception as e:
        vol = None
        log.warning(f"只读卷检查失败（按正常安装处理）: {e}")
    if vol:
        log.warning(f"检测到程序运行在只读卷（DMG）上：{vol} —— 已拦截并提示先安装")
        print(f"[AI润色助手] 你正在安装包里直接运行（{vol}）。\n"
              f"          这样授权不会生效，请先双击「安装 话术润色助手.command」"
              f"装到「应用程序」再运行。")
        try:
            # 注意：这里**不能**写 `import first_run` —— first_run 已在文件顶部模块级
            # 导入，函数内再 import 一次会把它变成整个 main() 的局部名，于是后面
            # `first_run.need_setup(cfg)` 会抛 UnboundLocalError（Python 作用域规则）。
            first_run.run_dmg_guard(vol)
        except Exception as e:
            log.warning(f"弹出安装提示失败: {e}")
        return

    ensure_single_instance()      # 先接管旧实例（flock + 信号）
    # 旧实例退出前可能又追加过明文日志；此时放开句柄再就地加密一次，确保不留明文。
    if _LOG_HANDLER.reencrypt_pending():
        log.info("已将旧实例遗留的明文日志就地加密")
    cfg = config.load_config()

    # 能力自述：日志里能一眼看出上下文是怎么读到的
    try:
        log.info(f"平台: {macos.describe()}")
    except Exception as e:
        log.warning(f"平台信息探测失败: {e}")

    # pynput 的键盘布局查询必须发生在主线程，否则启动监听线程时会被 macOS 的
    # dispatch_assert_queue 断言直接 SIGTRAP 杀掉（闪退，无 Python 回溯）。
    # 补丁在 macos/keys.py 的 import 阶段就打好了，这里只是把结果写进日志备查。
    try:
        log.info("键盘布局缓存: " + ("已就绪" if macos.keys.layout_cache_ready()
                                    else "未就绪 —— 热键线程可能闪退"))
    except Exception as e:
        log.warning(f"键盘布局缓存检查失败: {e}")

    # 权限缺失的表现是「按了没反应」，所以启动就把缺哪一项写进日志，避免用户去猜。
    try:
        issues = macos.startup_issues()
        if issues:
            # 这里统一承载「缺依赖 / 缺权限 / Tk 过低」三类启动自检问题，
            # 目的只有一个：别让用户面对「程序在跑但什么都没反应」。
            hint = "\n".join(i["text"] for i in issues)
            log.warning("启动自检未通过：\n" + hint)
            print("[AI润色助手] 启动自检未通过（相关功能会失效）：\n" + hint)
            # 打包成 .app 双击启动时**没有终端**，print 出来用户根本看不到，
            # 所以补一个可见的授权引导窗，把「缺什么、去哪开」讲清楚。
            if paths.is_frozen() or sys.stdout is None:
                first_run.run_permission_guide(issues)
    except Exception as e:
        log.warning(f"权限探测失败（不影响主流程）: {e}")

    # 控制台实时日志：在终端直接看到提示词 / 响应（config.json 的 console_log）
    # 注意：无控制台启动（如打成 .app 后双击）时 sys.stdout 为 None，必须判空
    if cfg.get("console_log", True) and sys.stdout is not None:
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
        _console = logging.StreamHandler(sys.stdout)
        _console.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(message)s")
        )
        logging.getLogger().addHandler(_console)

    # 首次运行引导：分发版不含任何 Key，必须由使用者填写自己的
    # （放在创建悬浮图标之前，此时主线程还没有 Tk 根，不会与之冲突）
    if first_run.need_setup(cfg):
        log.info("未检测到 API Key，弹出首次配置引导")
        if not first_run.run_setup_wizard(cfg):
            log.info("用户未完成 API Key 配置，程序退出")
            return
        cfg = config.load_config()
        log.info("首次配置完成，已保存 API Key")

    combo = _hotkey_to_pynput(cfg.get("hotkey") or macos.DEFAULT_HOTKEY)

    # 启动时记录一次 OCR 可用性：读聊天上下文依赖它，
    # 写进日志便于用户/开发者排查「读不到上下文」的原因（不影响主流程）。
    if cfg.get("ocr_context", True):
        try:
            log.info(f"OCR 状态: {macos.ocr_status_text()}")
        except Exception as e:
            log.warning(f"OCR 状态探测失败（不影响主流程）: {e}")

    # 右下角浮动动图：启动即显示 1.webp，按热键切 2.webp，关弹窗切回 1.webp
    # 图标可用鼠标左键拖动；松手后位置写入 config.json 的 tray_pos，下次启动沿用。
    def _save_tray_pos(x, y):
        try:
            config.update_config({"tray_pos": [int(x), int(y)]})
        except Exception as e:
            log.warning(f"写入悬浮图标位置失败（不影响本次使用）: {e}")

    tray = make_tray(
        str(paths.asset(cfg.get("idle_gif", "1.webp"))),
        str(paths.asset(cfg.get("active_gif", "2.webp"))),
        size=int(cfg.get("tray_size", 220)),
        pos=cfg.get("tray_pos"),
        on_move=_save_tray_pos,
    )
    if tray:
        # 点击右下角动图 = 与按热键等价的另一种触发方式
        tray.set_command(trigger)
        # macOS 触控板没有中键：右键菜单里也放一个「打开设置…」入口
        if hasattr(tray, "set_settings_command"):
            try:
                tray.set_settings_command(open_config_editor)
            except Exception as e:
                log.warning(f"挂载设置页入口失败（不影响主流程）: {e}")

    log.info(f"=== 启动 build={APP_VERSION}，热键={combo} ===")
    print(f"[AI润色助手] 已启动 build={APP_VERSION}，热键 {combo}。"
          f"在微信输入框打字后按热键即可润色。")
    print(f"[AI润色助手] {macos.SETTINGS_HINT}；Ctrl+C 退出。")

    try:
        hotkeys = keyboard.GlobalHotKeys({combo: on_activate})
        hotkeys.start()
    except Exception as e:
        hotkeys = None
        log.exception(f"全局热键注册失败: {e}")
        print(f"[AI润色助手] 全局热键 {combo} 注册失败：{e}")
        print(macos.permission_hint())

    # pynput 缺「辅助功能」权限时**不会报错**，只是收不到任何按键 ——
    # 表现出来就是「程序在跑、按热键没反应」，这是最难排查的一类问题，
    # 所以这里主动把它变成一句明确的话告诉用户，并给出临时替代入口。
    try:
        if hotkeys is not None and not macos.hotkey_ready():
            _msg = ("⚠️  全局热键不会生效：尚未授予「辅助功能」权限。\n"
                    "   临时替代：点击右下角悬浮图标，或用右键菜单的「立即润色」。\n"
                    "   去 系统设置 › 隐私与安全性 › 辅助功能 勾选本程序（立即生效，无需重启）。")
            log.warning(_msg)
            print("[AI润色助手] " + _msg)
    except Exception:
        pass

    # Option(alt) 是「组合字符」修饰键，Option+P 会输入 π。
    # 个别输入法/键盘布局下会干扰热键匹配，出现「按了没反应」时换成功能键最稳。
    try:
        if "alt" in combo:
            print(f"[AI润色助手] 提示：macOS 上 Option+字母（当前 {combo}）偶尔会与输入法"
                  f"组合字符冲突；若按热键没反应，把 config.json 的 hotkey 改成 f8 再试。")
    except Exception:
        pass

    # 全局鼠标监听：中键按下 -> 打开配置页（与键盘热键互斥独立，互不阻塞）
    def _on_mouse_press(x, y, button, pressed):
        if pressed and button == pynput_mouse.Button.middle:
            log.info("鼠标中键按下 -> 打开配置页")
            open_config_editor()

    mouse_listener = None
    try:
        mouse_listener = pynput_mouse.Listener(on_click=_on_mouse_press)
        mouse_listener.start()
    except Exception as e:
        # 少数环境（未授予辅助功能、无输入设备）会启动失败；不能因此让程序退出
        log.warning(f"鼠标监听启动失败（不影响热键与悬浮图标）: {e}")

    try:
        # 主线程常驻：有悬浮动图就跑 Tk mainloop（tkinter 在 macOS 必须主线程），
        # 否则退化为纯等待，保证进程不退出、热键继续有效。
        macos.run_event_loop(tray)
    except KeyboardInterrupt:
        pass
    finally:
        if hotkeys:
            hotkeys.stop()
        if mouse_listener:
            mouse_listener.stop()
        if tray:
            tray.stop()
        log.info("已退出")


def _notify(text, title="话术润色助手", error=False):
    """弹窗提示（无控制台启动时没有终端，只能靠弹窗告知结果）。

    拿不到 Tk（极少数环境）时静默跳过，不因为提示本身出错影响退出码。
    """
    if sys.stdout is not None:
        try:
            print(f"[{title}] {text}")
        except Exception:
            pass
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        (messagebox.showerror if error else messagebox.showinfo)(title, text)
        root.destroy()
    except Exception:
        pass


def _decrypt_cli(argv):
    """`--decrypt-log` 入口：用相同口令把加密日志还原成明文。

    用法：
        python3 main.py --decrypt-log                          # 默认日志 -> 同目录 polish.decrypted.log
        python3 main.py --decrypt-log -o ~/Desktop/plain.log    # 指定还原后的文件
        python3 main.py --decrypt-log -k zhaomh666 其它日志路径   # 指定口令 / 指定要解密的日志

    该分支在 main() 之前拦截，不影响正常启动流程与任何原有功能。
    """
    import log_decrypt

    src = out = key = None
    rest = [a for a in argv[1:] if a != "--decrypt-log"]
    i = 0
    while i < len(rest):
        a = rest[i]
        if a in ("-o", "--out") and i + 1 < len(rest):
            out = rest[i + 1]
            i += 2
            continue
        if a in ("-k", "--key") and i + 1 < len(rest):
            key = rest[i + 1]
            i += 2
            continue
        if not a.startswith("-") and src is None:
            src = a
            i += 1
            continue
        i += 1

    log_file = Path(src) if src else Path(paths.log_path())
    if not log_file.exists():
        _notify(f"未找到日志文件：\n{log_file}\n\n请确认程序至少运行过一次。",
                title="解密日志", error=True)
        return 2

    try:
        info = log_decrypt.decrypt_log_file(log_file, out, key)
    except Exception as exc:
        _notify(f"解密失败：{exc}", title="解密日志", error=True)
        return 1

    msg = (f"日志已还原成明文：\n{info['dst']}\n\n"
           f"共 {info['total']} 条记录（解密成功 {info['enc']}，"
           f"原本明文 {info['plain']}，失败 {info['bad']}）。\n"
           f"原日志（密文）未被改动。")
    if info["bad"]:
        msg += "\n\n提示：有记录解密失败，通常是口令不一致或日志被改过。"
    _notify(msg, title="解密日志")
    return 0


if __name__ == "__main__":
    # 解密入口必须最先拦截：不触碰悬浮图标/热键/微信读取等任何主流程
    if "--decrypt-log" in sys.argv[1:]:
        raise SystemExit(_decrypt_cli(sys.argv))
    main()
