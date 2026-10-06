"""Vision 框架 OCR。

要点
----
1. 用 `VNRecognizeTextRequest` + **accurate** 级别 + `zh-Hans`
   （识别模型由系统自带，无需额外安装语言包）。
2. Vision 返回的 `boundingBox` 是**归一化坐标、原点在左下**，而图像/PIL 习惯是
   左上原点。本模块统一在这里做一次换算，对外只暴露 top-origin 坐标。
3. 传 `region_of_interest` 时，Vision 返回的框是**相对 ROI** 的，必须换算回整图——
   这是很容易踩且很难发现的坑。
4. `warm()` 先在小画布上跑一次，把首次约 0.7s 的模型加载成本提前付掉。
"""

from __future__ import annotations

import logging

log = logging.getLogger("polish")

#: 默认语言：中文优先，英文兜底（数字、链接、英文名）
LANGUAGES = ("zh-Hans", "en-US")


def available() -> bool:
    """Vision 后端是否可用。"""
    try:
        import Vision  # noqa: F401
        return True
    except Exception:
        return False


def status_text() -> str:
    return "可用（macOS Vision）" if available() else "不可用（缺 pyobjc-framework-Vision）"


def _rect_parts(rect):
    if rect is None:
        return None
    try:
        (x, y), (w, h) = rect
        return float(x), float(y), float(w), float(h)
    except Exception:
        pass
    o, s = getattr(rect, "origin", None), getattr(rect, "size", None)
    if o is not None and s is not None:
        return float(o.x), float(o.y), float(s.width), float(s.height)
    try:
        vals = list(rect)
        return float(vals[0]), float(vals[1]), float(vals[2]), float(vals[3])
    except Exception:
        return None


def recognize(cgimage, languages=LANGUAGES, roi=None) -> list:
    """对 CGImage 做 OCR。

    返回 `[{"text", "conf", "x", "y", "w", "h", "top", "right"}]`，
    坐标为**归一化、原点左上**（已从 Vision 的左下原点换算过）。
    roi 为归一化 (x, y, w, h)（原点左下）时只识别该区域，返回坐标仍按整图计。
    """
    try:
        import Vision
    except Exception as e:
        log.warning(f"Vision 不可用（{type(e).__name__}），OCR 跳过")
        return []

    raw = []

    def _handler(request, error):
        if error is not None:
            log.warning(f"Vision 识别回调报错: {error}")
            return
        for obs in (request.results() or []):
            try:
                cands = obs.topCandidates_(1)
            except Exception:
                continue
            if not cands:
                continue
            cand = cands[0]
            t = str(cand.string() or "").strip()
            if not t:
                continue
            try:
                conf = float(cand.confidence())
            except Exception:
                conf = 0.0
            parts = _rect_parts(obs.boundingBox())
            if parts is None:
                continue
            x, y, w, h = parts
            if roi:                                  # ROI 相对坐标 → 整图归一化
                rx, ry, rw, rh = roi
                x, y, w, h = rx + x * rw, ry + y * rh, w * rw, h * rh
            raw.append((t, conf, x, y, w, h))

    req = Vision.VNRecognizeTextRequest.alloc().initWithCompletionHandler_(_handler)
    try:
        req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    except Exception:
        pass
    try:
        req.setRecognitionLanguages_(list(languages))
    except Exception:
        pass
    try:
        req.setUsesLanguageCorrection_(True)
    except Exception:
        pass
    if roi:
        import Quartz
        try:
            req.setRegionOfInterest_(Quartz.CGRectMake(*roi))
        except Exception:
            pass

    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(cgimage, None)
    try:
        handler.performRequests_error_([req], None)
    except Exception as e:
        log.warning(f"Vision 识别失败: {e}")
        return []

    out = []
    for t, conf, x, y, w, h in raw:
        out.append({
            "text": t, "conf": conf,
            "x": x, "y": y, "w": w, "h": h,
            "top": 1.0 - y - h,            # 左下原点 → 左上原点，且取盒子上沿
            "right": x + w,
        })
    return out


def warm() -> float:
    """预热 Vision：在 64x64 白画布上跑一次，返回耗时（毫秒）；失败返回 -1。"""
    import time
    try:
        import Quartz
        cs = Quartz.CGColorSpaceCreateDeviceRGB()
        ctx = Quartz.CGBitmapContextCreate(None, 64, 64, 8, 0, cs,
                                           Quartz.kCGImageAlphaPremultipliedLast)
        img = Quartz.CGBitmapContextCreateImage(ctx)
        t0 = time.time()
        recognize(img, languages=("en-US",))
        return (time.time() - t0) * 1000.0
    except Exception as e:
        log.warning(f"OCR 预热失败（不影响主流程）: {e}")
        return -1.0
