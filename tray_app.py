"""Wuwa Tappo 自動領寶箱：系統匣（右下角）版本。

右鍵選單：目前狀態／立即檢查／開啟紀錄／結束。
打包成 exe 時會內嵌系統管理員權限需求（--uac-admin），開啟時跳一次 UAC。
"""
import argparse
import ctypes
import os
import subprocess
import sys
import threading

import pystray
from PIL import Image

import auto_tappo as core

APP_NAME = "Wuwa Tappo 自動領寶箱"
ICON_PATH = os.path.join(core.RES, "assets", "icon.png")


def relaunch_as_admin():
    """開發時（未打包）用 pythonw 以系統管理員重新啟動自己。"""
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    exe = pyw if os.path.exists(pyw) else sys.executable
    arg = subprocess.list2cmdline([os.path.abspath(__file__)] + sys.argv[1:])
    return ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, arg, core.ROOT, 0) > 32


class TrayApp:
    def __init__(self):
        self.status = "啟動中…"
        self.icon = pystray.Icon(
            "AutoWuwaTappo", Image.open(ICON_PATH), APP_NAME,
            menu=pystray.Menu(
                pystray.MenuItem(APP_NAME, None, enabled=False),
                pystray.MenuItem(lambda _: self.status, None, enabled=False),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("立即檢查", self.check_now, default=True),  # 左鍵雙擊也是這個
                pystray.MenuItem("開啟紀錄", self.open_log),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("結束", self.quit),
            ))
        core.log_listeners.append(self.on_log)

    def on_log(self, msg):
        self.status = msg if len(msg) <= 60 else msg[:57] + "…"
        self.icon.title = f"{APP_NAME}\n{msg}"[:127]  # Windows tooltip 上限 127 字
        try:
            self.icon.update_menu()
        except Exception:
            pass

    def check_now(self, *_):
        core.log("手動觸發檢查")
        core.wake_event.set()

    def open_log(self, *_):
        if os.path.exists(core.LOG_FILE):
            os.startfile(core.LOG_FILE)

    def quit(self, *_):
        core.stop_event.set()
        self.icon.stop()

    def worker(self):
        args = argparse.Namespace(threshold=0.85, interval=30.0, max_wait=600.0, poll=5.0,
                                  dry=False)
        try:
            ok = core.cmd_run(args)
            if ok is False:
                self.icon.notify("已經有一份在執行了（或缺少寶箱模板），這個視窗將關閉。", APP_NAME)
                core.stop_event.wait(4)
        except Exception as e:
            core.log(f"程式終止：{e!r}")
            self.icon.notify(f"發生錯誤而停止：{e}", APP_NAME)
            core.stop_event.wait(4)
        self.icon.stop()

    def run(self):
        def setup(icon):
            icon.visible = True
            threading.Thread(target=self.worker, daemon=True).start()
        self.icon.run(setup=setup)


def selftest():
    """掃一次並寫入紀錄，用來確認打包後 OCR／模板比對正常（不需管理員權限）。"""
    core._log_to_file = True
    hwnd = core.find_pet_window()
    if not hwnd:
        core.log("[selftest] 找不到桌寵視窗")
        return
    chests, timers = core.scan(hwnd, 0.85)
    core.log(f"[selftest] 模板 {os.listdir(core.TEMPLATE_DIR)}；寶箱 {len(chests)}；"
             f"倒數 {[core.fmt(t) for t in timers]}；圖示 {os.path.exists(ICON_PATH)}")


def main():
    if "--selftest" in sys.argv:
        selftest()
        return
    if not core.is_admin():
        # WuwaTappo 是系統管理員程式，沒權限的話點擊會被 Windows 擋掉
        if not core.FROZEN and relaunch_as_admin():
            return
        ctypes.windll.user32.MessageBoxW(
            None, "需要系統管理員權限才能點擊 Wuwa Tappo 的寶箱。", APP_NAME, 0x30)
        return
    core._log_to_file = True
    TrayApp().run()


if __name__ == "__main__":
    main()
