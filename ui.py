"""簡易狀態視窗（tkinter，米白色系）。只在主執行緒操作；資料來自 auto_tappo.state。"""
import base64
import ctypes
import io
import re
import time
from collections import deque
import tkinter as tk
from datetime import datetime

from PIL import Image

import auto_tappo as core

FONT = "Microsoft JhengHei UI"
BG = "#FAF6EE"        # 米白底
CARD = "#FFFDF8"      # 卡片
LINE = "#E9DFCC"      # 邊框
TEXT = "#4A3F35"      # 深棕文字
MUTED = "#8C7F70"     # 次要文字
ACCENT = "#C9772B"    # 寶箱橘
ACCENT_HOVER = "#B5671F"
NOTICE = "#FBEBD3"    # 更新提示底色
BAD = "#B5483A"       # 錯誤紀錄

LOG_RE = re.compile(r"^\[(?:\d\d-\d\d )?(\d\d:\d\d:\d\d)\] (.*)$")
LOG_KEEP = 500        # 面板最多保留幾行
GOOD_WORDS = ("成功", "點擊 (", "可以點了")
BAD_WORDS = ("錯誤", "失敗", "無法", "終止", "被擋")

PHASE_TITLE = {
    "starting": "啟動中…",
    "countdown": "下一個寶箱",
    "waiting": "時間到，等待寶箱出現",
    "deferred": "寶箱已出現，暫緩點擊",
    "nopet": "找不到桌寵",
    "nobubble": "沒看到泡泡",
    "error": "發生錯誤，稍後重試",
}


def _button(parent, text, cmd, primary=False):
    bg, fg, hover = (ACCENT, "white", ACCENT_HOVER) if primary else (CARD, TEXT, "#F3EBDD")
    b = tk.Label(parent, text=text, bg=bg, fg=fg, font=(FONT, 10, "bold" if primary else "normal"),
                 padx=14, pady=7, cursor="hand2",
                 highlightthickness=0 if primary else 1, highlightbackground=LINE)
    b.bind("<Button-1>", lambda e: cmd())
    b.bind("<Enter>", lambda e: b.config(bg=hover))
    b.bind("<Leave>", lambda e: b.config(bg=bg))
    return b


def _photo(img, size):
    """PIL 圖 → tk.PhotoImage（用 Tk 內建 PNG 解碼；打包後 ImageTk 會失敗）。"""
    buf = io.BytesIO()
    img.resize((size, size), Image.LANCZOS).save(buf, "PNG")
    return tk.PhotoImage(data=base64.b64encode(buf.getvalue()))


def _card(parent, **kw):
    return tk.Frame(parent, bg=CARD, highlightthickness=1, highlightbackground=LINE, **kw)


class MainWindow:
    def __init__(self, root, icon_path, on_check, on_log, on_quit):
        self.root = root
        self.last_msg = ""
        self.update_url = None
        self._hidden_once = False
        self.on_hide_first = None  # 第一次縮到右下角時呼叫（顯示提示）

        root.title("Wuwa Tappo 自動領寶箱")
        root.configure(bg=BG)
        root.resizable(False, False)
        img = Image.open(icon_path).convert("RGBA")
        self._icon_big = _photo(img, 44)
        self._icon_win = _photo(img, 64)
        root.iconphoto(True, self._icon_win)
        root.protocol("WM_DELETE_WINDOW", self.hide)

        body = tk.Frame(root, bg=BG, padx=20, pady=18)
        body.pack(fill="both")

        # 標題
        head = tk.Frame(body, bg=BG)
        head.pack(fill="x")
        tk.Label(head, image=self._icon_big, bg=BG).pack(side="left")
        tt = tk.Frame(head, bg=BG, padx=10)
        tt.pack(side="left")
        tk.Label(tt, text="Wuwa Tappo 自動領寶箱", bg=BG, fg=TEXT,
                 font=(FONT, 13, "bold")).pack(anchor="w")
        sub = tk.Frame(tt, bg=BG)
        sub.pack(anchor="w")
        tk.Label(sub, text=f"v{core.VERSION}　・　", bg=BG, fg=MUTED,
                 font=(FONT, 9)).pack(side="left")
        self.upd = tk.Label(sub, text="檢查更新", bg=BG, fg=ACCENT, cursor="hand2",
                            font=(FONT, 9, "underline"))
        self.upd.pack(side="left")
        self.upd.bind("<Button-1>", lambda e: self._click_update())
        self._upd_kind = "idle"
        self.on_check_update = None  # 由系統匣設定：手動檢查更新

        # 更新提示（有新版才顯示）
        self.notice = tk.Frame(body, bg=NOTICE, padx=12, pady=8)
        self.notice_text = tk.Label(self.notice, bg=NOTICE, fg=TEXT, font=(FONT, 10))
        self.notice_text.pack(side="left")
        link = tk.Label(self.notice, text="前往下載 ›", bg=NOTICE, fg=ACCENT, cursor="hand2",
                        font=(FONT, 10, "bold"))
        link.pack(side="right")
        link.bind("<Button-1>", lambda e: self._open_update())
        self.notice_anchor = tk.Frame(body, bg=BG, height=14)
        self.notice_anchor.pack(fill="x")

        # 狀態卡
        card = _card(body, padx=18, pady=14)
        card.pack(fill="x")
        tk.Label(card, text="目前狀態", bg=CARD, fg=MUTED, font=(FONT, 9)).pack(anchor="w")
        self.title = tk.Label(card, bg=CARD, fg=TEXT, font=(FONT, 14, "bold"))
        self.title.pack(anchor="w", pady=(2, 0))
        self.big = tk.Label(card, bg=CARD, fg=ACCENT, font=(FONT, 30, "bold"))
        self.big.pack(anchor="w")
        self.detail = tk.Label(card, bg=CARD, fg=MUTED, font=(FONT, 9), wraplength=300,
                               justify="left")
        self.detail.pack(anchor="w", pady=(2, 0))

        # 統計
        stats = tk.Frame(body, bg=BG, pady=12)
        stats.pack(fill="x")
        stats.columnconfigure((0, 1), weight=1, uniform="s")
        self.claims, self.claims_sub = self._stat(stats, 0, "本次啟動後已領取")
        self.last, self.last_sub = self._stat(stats, 1, "上次領取")

        # 按鈕
        btns = tk.Frame(body, bg=BG)
        btns.pack(fill="x")
        _button(btns, "立即檢查", on_check, primary=True).pack(side="left")
        self.log_btn = _button(btns, "紀錄 ▾", self.toggle_log)
        self.log_btn.pack(side="left", padx=8)
        _button(btns, "結束程式", on_quit).pack(side="right")

        # 紀錄面板（按「紀錄」展開）
        self.log_panel = _card(body, padx=10, pady=8)
        top = tk.Frame(self.log_panel, bg=CARD)
        top.pack(fill="x")
        tk.Label(top, text="最近紀錄", bg=CARD, fg=MUTED, font=(FONT, 9)).pack(side="left")
        ext = tk.Label(top, text="以記事本開啟 ›", bg=CARD, fg=ACCENT, cursor="hand2",
                       font=(FONT, 9))
        ext.pack(side="right")
        ext.bind("<Button-1>", lambda e: on_log())
        self.log_text = tk.Text(self.log_panel, height=12, width=1, wrap="word", bg=CARD,
                                fg=TEXT, font=(FONT, 9), relief="flat", bd=0,
                                highlightthickness=0, padx=2, pady=4, cursor="arrow",
                                spacing1=1, spacing3=3)
        self.log_text.pack(fill="x", pady=(4, 0))
        self.log_text.tag_config("time", foreground=MUTED)
        self.log_text.tag_config("good", foreground=ACCENT)
        self.log_text.tag_config("bad", foreground=BAD)
        self.log_text.config(state="disabled")
        self._log_pending = deque()  # 其他執行緒丟進來，refresh() 時寫入
        self._load_log_file()

        self.footer = tk.Label(body, text="關閉視窗會縮到右下角，程式持續在背景執行", bg=BG,
                               fg=MUTED, font=(FONT, 9))
        self.footer.pack(anchor="w", pady=(14, 0))

        self.refresh()
        self._place_center()
        self._tint_titlebar()

    # ---- 紀錄 ----
    def add_log(self, line):
        """可從任何執行緒呼叫。line 格式同紀錄檔：[mm-dd HH:MM:SS] 訊息"""
        self._log_pending.append(line)

    def _load_log_file(self, n=80):
        try:
            with open(core.LOG_FILE, encoding="utf-8") as f:
                lines = f.readlines()[-n:]
        except OSError:
            return
        self._log_pending.extend(l.rstrip("\n") for l in lines if l.strip())

    def _flush_log(self):
        if not self._log_pending:
            return
        t = self.log_text
        at_bottom = t.yview()[1] >= 0.999
        t.config(state="normal")
        while self._log_pending:
            line = self._log_pending.popleft()
            m = LOG_RE.match(line)
            stamp, msg = (m.group(1), m.group(2)) if m else ("", line)
            tag = "bad" if any(k in msg for k in BAD_WORDS) else \
                "good" if any(k in msg for k in GOOD_WORDS) else ""
            if stamp:
                t.insert("end", stamp + "  ", "time")
            t.insert("end", msg + "\n", tag)
        extra = int(t.index("end-1c").split(".")[0]) - LOG_KEEP
        if extra > 0:
            t.delete("1.0", f"{extra + 1}.0")
        t.config(state="disabled")
        if at_bottom:
            t.see("end")

    def toggle_log(self, force=None):
        show = not self.log_panel.winfo_ismapped() if force is None else force
        if show and not self.log_panel.winfo_ismapped():
            self.log_panel.pack(fill="x", pady=(12, 0), before=self.footer)
            self.log_btn.config(text="紀錄 ▴")
            self._flush_log()
            self.log_text.see("end")
        elif not show and self.log_panel.winfo_ismapped():
            self.log_panel.pack_forget()
            self.log_btn.config(text="紀錄 ▾")
        self._keep_anchored()

    def show_log(self):
        self.show()
        self.toggle_log(True)

    def _tint_titlebar(self):
        """Windows 11：標題列改成米白色（舊版 Windows 不支援就維持預設）。"""
        try:
            self.root.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())

            def color(hexstr):
                r, g, b = int(hexstr[1:3], 16), int(hexstr[3:5], 16), int(hexstr[5:7], 16)
                return ctypes.c_int(r | g << 8 | b << 16)  # COLORREF = 0x00BBGGRR
            for attr, val in ((35, color(BG)), (36, color(TEXT)), (34, color(LINE))):
                # 35 CAPTION_COLOR、36 TEXT_COLOR、34 BORDER_COLOR
                ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(val), 4)
        except Exception:
            pass

    def _stat(self, parent, col, label):
        c = _card(parent, padx=14, pady=10)
        c.grid(row=0, column=col, sticky="nsew", padx=(0, 6) if col == 0 else (6, 0))
        tk.Label(c, text=label, bg=CARD, fg=MUTED, font=(FONT, 9)).pack(anchor="w")
        v = tk.Label(c, bg=CARD, fg=TEXT, font=(FONT, 12, "bold"))
        v.pack(anchor="w")
        sub = tk.Label(c, text=" ", bg=CARD, fg=MUTED, font=(FONT, 9))
        sub.pack(anchor="w")
        return v, sub

    def _place_center(self):
        self.root.update_idletasks()
        w, h = self.root.winfo_reqwidth(), self.root.winfo_reqheight()
        try:
            import win32api
            work = win32api.GetMonitorInfo(win32api.MonitorFromPoint((0, 0), 2))["Work"]
            x, y = (work[0] + work[2] - w) // 2, (work[1] + work[3] - h) // 2
        except Exception:
            x, y = (self.root.winfo_screenwidth() - w) // 2, (self.root.winfo_screenheight() - h) // 2
        self.root.geometry(f"+{x}+{y}")
        self._placed, self._size = (x, y), (w, h)

    def _keep_anchored(self):
        """大小隨狀態變化時，維持置中（使用者自己拖動過就不動）。"""
        self.root.update_idletasks()
        size = (self.root.winfo_reqwidth(), self.root.winfo_reqheight())
        if size == self._size:
            return
        if (self.root.winfo_x(), self.root.winfo_y()) == self._placed \
                or not self.root.winfo_ismapped():
            self._place_center()
        else:
            self._size = size

    # ---- 顯示／隱藏 ----
    def show(self):
        self.root.deiconify()
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after(200, lambda: self.root.attributes("-topmost", False))
        self.root.focus_force()

    def hide(self):
        self.root.withdraw()
        if not self._hidden_once:
            self._hidden_once = True
            if self.on_hide_first:
                self.on_hide_first()

    # ---- 更新 ----
    def set_update(self, tag, url):
        self.update_url = url
        self.notice_text.config(text=f"有新版本 {tag} 可以下載")
        self.notice.pack(fill="x", pady=(14, 0), before=self.notice_anchor)

    def set_update_status(self, kind, tag=None):
        """kind：checking / latest / new / fail。顯示在標題的版本號旁邊。"""
        self._upd_kind = kind
        text, fg, font, cur = {
            "checking": ("檢查中…", MUTED, (FONT, 9), "arrow"),
            "latest": ("已是最新版 ✓", MUTED, (FONT, 9), "hand2"),
            "new": (f"下載新版 {tag} ›", ACCENT, (FONT, 9, "bold underline"), "hand2"),
            "fail": ("檢查失敗，重試", BAD, (FONT, 9, "underline"), "hand2"),
        }[kind]
        self.upd.config(text=text, fg=fg, font=font, cursor=cur)

    def _click_update(self):
        if self._upd_kind == "checking":
            return
        if self._upd_kind == "new":
            self._open_update()
        elif self.on_check_update:
            self.set_update_status("checking")
            self.on_check_update()

    def _open_update(self):
        if self.update_url:
            import webbrowser
            webbrowser.open(self.update_url)

    def refresh(self):
        s = core.state
        phase = s["phase"]
        self.title.config(text=PHASE_TITLE.get(phase, phase))
        if phase == "countdown" and s["until"]:
            left = max(0, s["until"] - time.time())
            self.big.config(text=core.fmt(left))
            if not self.big.winfo_ismapped():
                self.big.pack(anchor="w", after=self.title)
            detail = f"約 {datetime.fromtimestamp(s['until']):%H:%M} 出現　・　{s['detail']}"
        else:
            self.big.pack_forget()
            detail = s["detail"]
        self.detail.config(text=detail or self.last_msg)
        self.claims.config(text=f"{s['claims']} 次")
        if s["last_claim"]:
            self.last.config(text=f"{datetime.fromtimestamp(s['last_claim']):%H:%M}")
            self.last_sub.config(text=s["last_method"])
        else:
            self.last.config(text="—")
            self.last_sub.config(text=" ")
        self._flush_log()
        if hasattr(self, "_placed"):
            self._keep_anchored()
