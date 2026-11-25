"""Wuwa Tappo 桌寵寶箱自動領取。

流程：找桌寵旁的泡泡框 → 泡泡是寶箱就點 → 是倒數（mm:ss）就睡到時間到再檢查。

用法：
    python auto_tappo.py          # 在背景開始自動領取（會要求系統管理員權限，無視窗）
    python auto_tappo.py status   # 是否在執行＋最近的紀錄
    python auto_tappo.py stop     # 停止背景執行
    python auto_tappo.py --console  # 改在可見的命令列視窗執行
    python auto_tappo.py scan     # 只掃一次，印出偵測到的泡泡（除錯用）
    python auto_tappo.py snap     # 把桌寵視窗截圖存到 snaps/，用來裁寶箱模板
    python auto_tappo.py --dry    # 只偵測不點擊

模板：寶箱圖示存成 PNG 放進 templates/，需在 150% 縮放（TEMPLATE_DPI=144）的螢幕上截取；
程式會依桌寵所在螢幕的 DPI 自動縮放模板。
"""
import argparse
import asyncio
import ctypes
import glob
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta

import cv2
import numpy as np
import win32api
import win32con
import win32event
import win32gui
import win32process
import winerror
from PIL import Image, ImageGrab
from winrt.windows.globalization import Language
from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
from winrt.windows.media.ocr import OcrEngine
from winrt.windows.storage.streams import DataWriter

ctypes.windll.shcore.SetProcessDpiAwareness(2)  # 座標用實際像素，避免高 DPI 縮放錯位

FROZEN = getattr(sys, "frozen", False)  # PyInstaller 打包後
# ROOT：可寫入的資料夾（logs、snaps）；RES：唯讀資源（templates、assets）
ROOT = os.path.dirname(sys.executable if FROZEN else os.path.abspath(__file__))
RES = getattr(sys, "_MEIPASS", ROOT)
TEMPLATE_DIR = os.path.join(RES, "templates")
SNAP_DIR = os.path.join(ROOT, "snaps")
PROC_NAME = "wuwatappo.exe"
WINDOW_TITLE = "Wuwa Tappo"
TEMPLATE_DPI = 144  # templates/ 裡的圖是在 150% 縮放螢幕上截的
LOG_DIR = os.path.join(ROOT, "logs")
LOG_FILE = os.path.join(LOG_DIR, "auto_tappo.log")
STOP_FILE = os.path.join(ROOT, ".stop")
MUTEX_NAME = "Local\\AutoWuwaTappo"
LOG_MAX_BYTES = 1_000_000

# 泡泡底色（HSV）：倒數泡泡近白米色 S≈20、寶箱泡泡淡橘 S≈110，兩者 V 都很高
BUBBLE_HSV_LO = (8, 5, 225)
BUBBLE_HSV_HI = (35, 150, 255)
# 泡泡大小（100% 縮放下，像素），實際依 DPI 等比放大
BUBBLE_W = (25, 80)
BUBBLE_H = (12, 45)
OCR_LANG_PREF = ["en-US", "en-GB", "en"]  # 依序嘗試，都沒有就用系統語言
TIMER_RE = re.compile(r"(\d{1,2})\s*[:：.]\s*(\d{2})")


_log_to_file = False
stop_event = threading.Event()  # 系統匣「結束」
wake_event = threading.Event()  # 系統匣「立即檢查」
log_listeners = []  # 每筆紀錄都會呼叫 fn(msg)，系統匣用來更新狀態


def log(msg):
    line = f"[{datetime.now():%m-%d %H:%M:%S}] {msg}"
    for fn in log_listeners:
        try:
            fn(msg)
        except Exception:
            pass
    if sys.stdout:  # pythonw 下沒有 stdout
        try:
            print(line, flush=True)
        except Exception:
            pass
    if _log_to_file:
        os.makedirs(LOG_DIR, exist_ok=True)
        if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > LOG_MAX_BYTES:
            os.replace(LOG_FILE, LOG_FILE + ".1")
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def nap(sec):
    """可中斷的 sleep：收到停止要求（stop 指令或系統匣結束）回 True；
    「立即檢查」會提早醒來並回 False。"""
    end = time.time() + sec
    while time.time() < end:
        if stop_event.is_set() or os.path.exists(STOP_FILE):
            return True
        if wake_event.is_set():
            wake_event.clear()
            return False
        stop_event.wait(min(0.5, max(end - time.time(), 0)))
    return stop_event.is_set() or os.path.exists(STOP_FILE)


def is_running():
    """用具名 mutex 判斷是否已有一份在執行（被管理員程式建立時會回存取拒絕，也算存在）。"""
    try:
        h = win32event.OpenMutex(win32con.SYNCHRONIZE, False, MUTEX_NAME)
        win32api.CloseHandle(h)
        return True
    except win32api.error as e:
        return e.winerror == winerror.ERROR_ACCESS_DENIED


def fmt(sec):
    return f"{int(sec) // 60:02d}:{int(sec) % 60:02d}"


# ---------- 視窗與截圖 ----------

def find_pet_window():
    """回傳桌寵主視窗（Tauri Window, 標題 Wuwa Tappo）的 hwnd，找不到回 None。"""
    out = subprocess.run(["tasklist", "/fo", "csv", "/nh"], capture_output=True, text=True,
                         creationflags=subprocess.CREATE_NO_WINDOW).stdout  # 無主控台時不閃黑窗
    pids = {int(p[1]) for p in (l.strip('"').split('","') for l in out.splitlines())
            if len(p) >= 2 and p[0].lower() == PROC_NAME}
    found = []

    def cb(hwnd, _):
        if win32process.GetWindowThreadProcessId(hwnd)[1] in pids \
                and win32gui.IsWindowVisible(hwnd) \
                and win32gui.GetWindowText(hwnd) == WINDOW_TITLE:
            found.append(hwnd)

    win32gui.EnumWindows(cb, None)
    return found[0] if found else None


def window_dpi(hwnd):
    mon = ctypes.windll.user32.MonitorFromWindow(hwnd, 2)
    x, y = ctypes.c_uint(), ctypes.c_uint()
    ctypes.windll.shcore.GetDpiForMonitor(mon, 0, ctypes.byref(x), ctypes.byref(y))
    return x.value or 96


def grab(hwnd):
    """截桌寵視窗範圍，回傳 (BGR 影像, 視窗左上角螢幕座標)。"""
    l, t, r, b = win32gui.GetWindowRect(hwnd)
    img = ImageGrab.grab(bbox=(l, t, r, b), all_screens=True)
    return cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR), (l, t)


# ---------- 偵測 ----------

def load_templates(dpi):
    """讀 templates/*.png，並依螢幕 DPI 縮放。"""
    s = dpi / TEMPLATE_DPI
    tpls = []
    for path in sorted(glob.glob(os.path.join(TEMPLATE_DIR, "*.png"))):
        img = cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_COLOR)  # 支援中文路徑
        if img is None:
            continue
        if abs(s - 1) > 1e-3:
            img = cv2.resize(img, None, fx=s, fy=s,
                             interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_CUBIC)
        tpls.append((os.path.basename(path), img))
    return tpls


def find_bubbles(frame, scale):
    """用底色找泡泡框，回傳 [(x, y, w, h, region)]（視窗內座標）。
    region 是 bbox 內「泡泡內部」的遮罩（含文字/圖示的洞），用來排除外框與背景。"""
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, BUBBLE_HSV_LO, BUBBLE_HSV_HI)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
    boxes = []
    for i, (x, y, w, h, area) in enumerate(stats):
        if i == 0:
            continue
        if BUBBLE_W[0] * scale <= w <= BUBBLE_W[1] * scale \
                and BUBBLE_H[0] * scale <= h <= BUBBLE_H[1] * scale \
                and area >= 0.35 * w * h:
            comp = (labels[y:y + h, x:x + w] == i).astype(np.uint8) * 255
            cnts, _ = cv2.findContours(comp, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            region = np.zeros_like(comp)
            cv2.drawContours(region, cnts, -1, 255, cv2.FILLED)  # 補上文字造成的洞
            boxes.append((int(x), int(y), int(w), int(h), region))
    return boxes


_ocr_engine = None
_ocr_checked = False


def get_ocr_engine():
    """取得 Windows OCR 引擎：優先英文，沒有就用使用者的系統語言（如繁中），再不行用任一已安裝語言。
    數字與冒號各語言都認得，所以不一定要英文語言包。都沒有時回 None（只點寶箱、不讀倒數）。"""
    global _ocr_engine, _ocr_checked
    if _ocr_checked:
        return _ocr_engine
    _ocr_checked = True
    engine = None
    for tag in OCR_LANG_PREF:
        if OcrEngine.is_language_supported(Language(tag)):
            engine = OcrEngine.try_create_from_language(Language(tag))
            break
    if engine is None:
        engine = OcrEngine.try_create_from_user_profile_languages()
    if engine is None:
        langs = list(OcrEngine.available_recognizer_languages)
        if langs:
            engine = OcrEngine.try_create_from_language(langs[0])
    if engine is None:
        log("這台電腦沒有任何 Windows OCR 語言，無法讀倒數；改為定時檢查寶箱。"
            "可到「設定 → 時間與語言 → 語言」新增英文（含光學字元辨識）")
    else:
        log(f"OCR 語言：{engine.recognizer_language.language_tag}")
    _ocr_engine = engine
    return engine


def ocr_text(gray):
    engine = get_ocr_engine()
    if engine is None:
        return ""
    rgba = cv2.cvtColor(gray, cv2.COLOR_GRAY2RGBA)
    writer = DataWriter()
    writer.write_bytes(rgba.tobytes())
    bmp = SoftwareBitmap.create_copy_from_buffer(
        writer.detach_buffer(), BitmapPixelFormat.RGBA8, rgba.shape[1], rgba.shape[0])

    async def run():
        return await engine.recognize_async(bmp)

    return asyncio.run(run()).text


def read_timer(crop, region, scale):
    """OCR 泡泡內的 mm:ss，回傳秒數或 None。
    只留泡泡內部，二值化成白底黑字再放大，避免外框/背景干擾 OCR。"""
    if get_ocr_engine() is None:
        return None
    v = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)[:, :, 2]
    img = np.full_like(v, 255)
    img[(region > 0) & (v < 205)] = 0  # 咖啡色文字
    for f in (4 / scale, 3 / scale):  # 放大到相當於 100% 縮放的 3~4 倍
        big = cv2.resize(img, None, fx=f, fy=f, interpolation=cv2.INTER_CUBIC)
        big = cv2.copyMakeBorder(big, 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=255)
        text = ocr_text(big)
        m = TIMER_RE.search(re.sub(r"[\s•·]", "", text))
        if m:
            mm, ss = int(m.group(1)), int(m.group(2))
            if ss < 60 and mm * 60 + ss <= 31 * 60:  # 寶箱週期 30 分鐘
                return mm * 60 + ss
    return None


def scan(hwnd, threshold):
    """掃一次桌寵視窗。回傳 (chests=[(螢幕x, 螢幕y, 分數, 模板名)], timers=[秒數])。"""
    dpi = window_dpi(hwnd)
    scale = dpi / 96
    frame, (ox, oy) = grab(hwnd)
    tpls = load_templates(dpi)
    pad = int(6 * scale)
    chests, timers = [], []
    for x, y, w, h, region in find_bubbles(frame, scale):
        x0, y0 = max(x - pad, 0), max(y - pad, 0)
        crop = frame[y0:y + h + pad, x0:x + w + pad]
        best = None
        for name, tpl in tpls:
            th, tw = tpl.shape[:2]
            if th > crop.shape[0] or tw > crop.shape[1]:
                continue
            _, score, _, (mx, my) = cv2.minMaxLoc(
                cv2.matchTemplate(crop, tpl, cv2.TM_CCOEFF_NORMED))
            if score >= threshold and (best is None or score > best[2]):
                best = (ox + x0 + mx + tw // 2, oy + y0 + my + th // 2, score, name)
        if best:
            chests.append(best)
            continue
        sec = read_timer(frame[y:y + h, x:x + w], region, scale)
        if sec is not None:
            timers.append(sec)
    return chests, timers


# ---------- 點擊 ----------

def click(x, y):
    """點擊螢幕座標 (x, y)，點完把滑鼠移回原位。"""
    old = win32api.GetCursorPos()
    win32api.SetCursorPos((x - 2, y - 2))
    time.sleep(0.05)
    win32api.SetCursorPos((x, y))  # 先產生 hover，WebView 才會正確 hit-test
    time.sleep(0.1)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0)
    time.sleep(0.08)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0)
    time.sleep(0.05)
    win32api.SetCursorPos(old)


# ---------- 指令 ----------

def cmd_snap():
    hwnd = find_pet_window()
    if not hwnd:
        log("找不到 Wuwa Tappo 視窗，請先開啟桌寵")
        return
    os.makedirs(SNAP_DIR, exist_ok=True)
    frame, _ = grab(hwnd)
    path = os.path.join(SNAP_DIR, f"snap_{datetime.now():%Y%m%d_%H%M%S}.png")
    cv2.imencode(".png", frame)[1].tofile(path)
    log(f"已存 {path}")


def cmd_scan(args):
    hwnd = find_pet_window()
    if not hwnd:
        log("找不到 Wuwa Tappo 視窗")
        return
    chests, timers = scan(hwnd, args.threshold)
    log(f"DPI {window_dpi(hwnd)}；寶箱 {[(x, y, round(s, 2)) for x, y, s, _ in chests]}；"
        f"倒數 {[fmt(t) for t in timers]}")


def cmd_run(args):
    if not glob.glob(os.path.join(TEMPLATE_DIR, "*.png")):
        log("templates/ 裡沒有寶箱模板")
        return False
    mutex = win32event.CreateMutex(None, False, MUTEX_NAME)  # 保持 handle 直到程式結束
    if win32api.GetLastError() == winerror.ERROR_ALREADY_EXISTS:
        log("已經有一份在執行了，這次不啟動")
        return False
    if os.path.exists(STOP_FILE):
        os.remove(STOP_FILE)
    log("開始自動領取" + ("（dry run，不點擊）" if args.dry else "") + f"（PID {os.getpid()}）")
    hwnd, stuck = None, 0
    while True:
        try:
            if not hwnd or not win32gui.IsWindow(hwnd):
                hwnd = find_pet_window()
                if not hwnd:
                    log(f"找不到桌寵視窗，{args.interval:.0f}s 後再試")
                    if nap(args.interval):
                        break
                    continue
            chests, timers = scan(hwnd, args.threshold)

            if chests:
                x, y, score, name = chests[0]
                log(f"寶箱 x{len(chests)}，點擊 ({x}, {y})（{name} {score:.2f}）")
                if args.dry:
                    if nap(args.interval):
                        break
                    continue
                click(x, y)
                if nap(2):  # 等泡泡切換成倒數
                    break
                stuck += 1
                if stuck >= 3:  # 連點 3 次寶箱都還在，多半是點擊被擋（沒有系統管理員權限）
                    log("連續點擊後寶箱仍在，點擊可能被擋；60 秒後再試")
                    if nap(60):
                        break
                    stuck = 0
                continue
            stuck = 0

            if timers:
                wait = min(min(timers) + 2, args.max_wait)
                log(f"倒數 {', '.join(fmt(t) for t in sorted(timers))}；"
                    f"{datetime.now() + timedelta(seconds=wait):%H:%M:%S} 再檢查")
                if nap(wait):
                    break
                continue

            if get_ocr_engine() is None:
                log(f"無 OCR 可讀倒數，{args.interval:.0f}s 後再檢查寶箱")
            else:
                log(f"沒看到泡泡框（桌寵被移走或隱藏？），{args.interval:.0f}s 後再試")
            if nap(args.interval):
                break
        except KeyboardInterrupt:
            raise
        except Exception as e:  # 截圖偶發失敗（鎖定畫面、切換螢幕等）不中斷
            log(f"錯誤：{e}")
            hwnd = None
            if nap(args.interval):
                break
    if os.path.exists(STOP_FILE):
        os.remove(STOP_FILE)
    log("收到停止指令，結束")
    win32api.CloseHandle(mutex)
    return True


def cmd_stop():
    if not is_running():
        print("目前沒有在執行")
        return
    open(STOP_FILE, "w").close()
    for _ in range(10):
        time.sleep(0.5)
        if not is_running():
            print("已停止")
            return
    print("已送出停止指令，但程式尚未結束；稍後用 status 確認")


def cmd_status():
    print("執行中" if is_running() else "沒有在執行")
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, encoding="utf-8") as f:
            lines = f.readlines()[-8:]
        print(f"--- 最近紀錄（{LOG_FILE}）---")
        print("".join(lines), end="")


def is_admin():
    return bool(ctypes.windll.shell32.IsUserAnAdmin())


def relaunch_as_admin(console):
    """WuwaTappo 以系統管理員執行，Windows (UIPI) 會擋掉一般權限程式送來的滑鼠點擊，
    所以本程式也必須以系統管理員執行。會跳出 UAC 視窗。
    預設用 pythonw 在背景執行（無視窗、紀錄寫到 logs/）；console=True 則開可見的命令列視窗。"""
    script = os.path.abspath(__file__)
    if console:
        params = subprocess.list2cmdline([script] + sys.argv[1:])
        # 包一層 cmd /k，結束或出錯時視窗不會直接關掉
        exe, arg, show = "cmd.exe", f'/k ""{sys.executable}" {params}"', 1
    else:
        pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        exe = pyw if os.path.exists(pyw) else sys.executable
        arg, show = subprocess.list2cmdline([script, "--background"] + sys.argv[1:]), 0
    rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, arg, ROOT, show)
    if rc <= 32:
        log("取得系統管理員權限失敗（UAC 被取消？），點擊會被 Windows 擋掉")
        return False
    return True


def main():
    ap = argparse.ArgumentParser(description="Wuwa Tappo 寶箱自動領取")
    ap.add_argument("mode", nargs="?", choices=["run", "status", "stop", "scan", "snap"],
                    default="run")
    ap.add_argument("--interval", type=float, default=30.0,
                    help="找不到視窗/泡泡時的重試間隔秒數")
    ap.add_argument("--max-wait", type=float, default=600.0,
                    help="倒數時最多睡幾秒就重新確認一次（防桌寵被移動或 OCR 讀錯）")
    ap.add_argument("--threshold", type=float, default=0.85, help="寶箱比對門檻 0~1")
    ap.add_argument("--dry", action="store_true", help="只偵測不點擊")
    ap.add_argument("--no-elevate", action="store_true", help="不要自動要求系統管理員權限")
    ap.add_argument("--console", action="store_true", help="在可見的命令列視窗執行（不隱藏）")
    ap.add_argument("--background", action="store_true", help=argparse.SUPPRESS)  # 內部用
    args = ap.parse_args()

    global _log_to_file
    if args.mode == "run":
        _log_to_file = True
        if is_running():
            print("已經在執行了（python auto_tappo.py status 查看、stop 停止）")
            return
        if not args.dry and not args.no_elevate and not is_admin():
            if relaunch_as_admin(args.console):
                if not args.console:
                    print(f"已在背景啟動。紀錄：{LOG_FILE}\n"
                          "查看：python auto_tappo.py status　停止：python auto_tappo.py stop")
                return
    try:
        {"snap": cmd_snap, "scan": lambda: cmd_scan(args), "run": lambda: cmd_run(args),
         "stop": cmd_stop, "status": cmd_status}[args.mode]()
    except KeyboardInterrupt:
        log("結束")
    except Exception as e:  # 背景執行時沒有視窗，致命錯誤也要留紀錄
        log(f"程式終止：{e!r}")
        raise


if __name__ == "__main__":
    main()
