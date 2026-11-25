"""Wuwa Tappo 自動領寶箱：系統匣（右下角）＋狀態視窗版本。

右鍵選單：開啟視窗／立即檢查／開啟紀錄（視窗內的紀錄面板）／結束。雙擊圖示開啟視窗。
啟動時若已有一份在執行（例如舊版），會先請它結束再接手。
每天檢查一次 GitHub 上有沒有新版本（也可手動檢查），有的話提示（不會自動下載）。
打包成 exe 時會內嵌系統管理員權限需求（--uac-admin），開啟時跳一次 UAC。
"""
import argparse
import ctypes
import json
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
import urllib.request
from datetime import datetime

import pystray
from PIL import Image

import auto_tappo as core
from ui import MainWindow

APP_NAME = "Wuwa Tappo 自動領寶箱"
ICON_PATH = os.path.join(core.RES, "assets", "icon.png")
REPO = "partner0487/Auto_WuwaTappo"
UPDATE_API = f"https://api.github.com/repos/{REPO}/releases/latest"
UPDATE_EVERY = 24 * 3600


def relaunch_as_admin():
    """開發時（未打包）用 pythonw 以系統管理員重新啟動自己。"""
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    exe = pyw if os.path.exists(pyw) else sys.executable
    arg = subprocess.list2cmdline([os.path.abspath(__file__)] + sys.argv[1:])
    return ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, arg, core.ROOT, 0) > 32


def _ver(tag):
    try:
        return tuple(int(p) for p in tag.lstrip("vV").split("."))
    except ValueError:
        return ()


def latest_release():
    """回傳 (tag, 網頁網址)；失敗回 None。"""
    req = urllib.request.Request(UPDATE_API, headers={
        "Accept": "application/vnd.github+json", "User-Agent": "AutoWuwaTappo-update-check"})
    with urllib.request.urlopen(req, timeout=10) as r:
        data = json.load(r)
    return data.get("tag_name", ""), data.get("html_url", f"https://github.com/{REPO}/releases")


class TrayApp:
    def __init__(self):
        self.tasks = queue.Queue()  # 其他執行緒要操作視窗時，丟進來由主執行緒執行
        self.update_now = threading.Event()  # 手動檢查更新
        self.root = tk.Tk()
        try:
            self.win = MainWindow(self.root, ICON_PATH, self.check_now, self.open_log, self.quit)
            self.win.on_hide_first = lambda: self.icon.notify(
                "已縮到右下角，程式持續執行。\n雙擊圖示可再開啟視窗，右鍵可結束。", APP_NAME)
            self.win.on_check_update = self.check_update_now
        except Exception as e:  # 視窗壞掉也要能繼續自動領取
            core.log(f"狀態視窗無法建立，僅以系統匣執行：{e!r}")
            self.win = None
            self.root.withdraw()
        self.status = "啟動中…"
        self.icon = pystray.Icon(
            "AutoWuwaTappo", Image.open(ICON_PATH), APP_NAME,
            menu=pystray.Menu(
                pystray.MenuItem(APP_NAME, None, enabled=False),
                pystray.MenuItem(lambda _: self.status, None, enabled=False),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("開啟視窗", lambda *_: self.tasks.put(self.show_window),
                                 default=True),  # 左鍵雙擊
                pystray.MenuItem("立即檢查", self.check_now),
                pystray.MenuItem("開啟紀錄", lambda *_: self.tasks.put(self.show_log)),
                pystray.MenuItem("檢查更新", self.check_update_now),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("結束", self.quit),
            ))
        core.log_listeners.append(self.on_log)
        self.worker_thread = None

    def on_log(self, msg):
        self.status = msg if len(msg) <= 60 else msg[:57] + "…"
        if self.win:
            self.win.last_msg = msg
            self.win.add_log(f"[{datetime.now():%m-%d %H:%M:%S}] {msg}")
        self.icon.title = f"{APP_NAME}\n{msg}"[:127]  # Windows tooltip 上限 127 字
        try:
            self.icon.update_menu()
        except Exception:
            pass

    def show_window(self):
        if self.win:
            self.win.show()
        else:
            self.open_log()

    def show_log(self):
        if self.win:
            self.win.show_log()
        else:
            self.open_log()

    def check_now(self, *_):
        core.log("手動觸發檢查")
        core.wake_event.set()

    def open_log(self, *_):
        if os.path.exists(core.LOG_FILE):
            os.startfile(core.LOG_FILE)

    def quit(self, *_):
        core.stop_event.set()  # worker 結束後主執行緒會關閉視窗與圖示

    def worker(self):
        args = argparse.Namespace(threshold=0.85, interval=30.0, max_wait=600.0, poll=5.0,
                                  dry=False)
        try:
            if core.cmd_run(args) is False:
                self.tasks.put(lambda: ctypes.windll.user32.MessageBoxW(
                    None, "已經有一份在執行，或缺少寶箱模板。", APP_NAME, 0x30))
        except Exception as e:
            core.log(f"程式終止：{e!r}")
            self.tasks.put(lambda: ctypes.windll.user32.MessageBoxW(
                None, f"發生錯誤而停止：{e}", APP_NAME, 0x10))
        core.stop_event.set()

    def check_update_now(self, *_):
        """手動檢查更新（視窗的「檢查更新」或系統匣選單）。"""
        if self.win:
            self.tasks.put(lambda: self.win.set_update_status("checking"))
        self.update_now.set()

    def _ui(self, fn):
        if self.win:
            self.tasks.put(fn)

    def update_checker(self):
        """啟動 5 秒後檢查一次，之後每天一次；手動觸發時立即檢查。"""
        self.update_now.wait(5)
        notified = None
        while not core.stop_event.is_set():
            self.update_now.clear()
            self._ui(lambda: self.win.set_update_status("checking"))
            try:
                tag, url = latest_release()
                if _ver(tag) > _ver(core.VERSION):
                    if notified != tag:
                        core.log(f"有新版本 {tag}：{url}")
                        self.icon.notify(f"有新版本 {tag}，開啟視窗可前往下載。", APP_NAME)
                        notified = tag
                    self._ui(lambda t=tag, u=url: (self.win.set_update(t, u),
                                                   self.win.set_update_status("new", t)))
                else:
                    core.log(f"檢查更新：目前 v{core.VERSION} 已是最新版本")
                    self._ui(lambda: self.win.set_update_status("latest"))
            except Exception as e:
                core.log(f"檢查更新失敗：{e}")
                self._ui(lambda: self.win.set_update_status("fail"))
            end = time.time() + UPDATE_EVERY
            while time.time() < end and not core.stop_event.is_set():
                if self.update_now.wait(1):
                    break

    def poll(self):
        while True:
            try:
                self.tasks.get_nowait()()
            except queue.Empty:
                break
            except Exception:
                pass
        if core.stop_event.is_set() and not (self.worker_thread and self.worker_thread.is_alive()):
            self.root.destroy()
            return
        if self.win:
            self.win.refresh()
        self.root.after(500, self.poll)

    def run(self):
        self.icon.run_detached()
        self.icon.visible = True
        self.worker_thread = threading.Thread(target=self.worker, daemon=True)
        self.worker_thread.start()
        threading.Thread(target=self.update_checker, daemon=True).start()
        if self.win:
            self.win.show()
        self.root.after(500, self.poll)
        self.root.mainloop()
        self.icon.stop()


def selftest():
    """掃一次並寫入紀錄，用來確認打包後 OCR／模板比對正常（不需管理員權限）。"""
    core._log_to_file = True
    hwnd = core.find_pet_window()
    if not hwnd:
        core.log("[selftest] 找不到桌寵視窗")
        return
    chests, timers = core.scan(hwnd, 0.85)
    core.log(f"[selftest] v{core.VERSION} 模板 {os.listdir(core.TEMPLATE_DIR)}；寶箱 {len(chests)}；"
             f"倒數 {[core.fmt(t) for t in timers]}；圖示 {os.path.exists(ICON_PATH)}；"
             f"WGC {core._wgc_ok}")


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
    core.take_over()  # 已有舊的一份在跑就先請它結束
    TrayApp().run()


if __name__ == "__main__":
    main()
