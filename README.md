# Auto_WuwaTappo

<img src="assets/icon.png" width="96" align="right">

自動領取 Wuwa Tappo 桌寵的寶箱（約每 30 分鐘一次），常駐在右下角系統匣。

## 下載與使用
1. 到 [Releases](../../releases) 下載 `AutoWuwaTappo.exe`，放在任意資料夾。
2. 執行（會跳 UAC，按「是」），會開啟狀態視窗並在右下角系統匣常駐。
   - 視窗：目前狀態、下一個寶箱倒數、已領取次數、上次領取時間；按「紀錄」可展開最近紀錄。
     關閉視窗只會縮到右下角
   - 系統匣圖示：雙擊開啟視窗；右鍵可立即檢查、開啟紀錄、結束
3. 紀錄寫在 exe 旁的 `logs\auto_tappo.log`（視窗紀錄面板可用記事本開啟完整檔案）。
4. 更新：程式每天檢查一次 GitHub 有沒有新版，有的話會通知（不會自動下載）。
   下載新版後直接執行即可，會自動關掉正在執行的舊版再接手。

> 為什麼需要系統管理員權限？Wuwa Tappo 本身以系統管理員執行，
> Windows（UIPI）會擋掉一般權限程式送給它的滑鼠點擊。

## 運作方式
1. 用 Windows Graphics Capture 只截桌寵視窗本身（被其他視窗或全螢幕遊戲蓋住也截得到，
   也不會截到 Discord 等其他視窗裡的 Tappo 圖片），用底色找出桌寵旁的泡泡框。
   無法使用時改截螢幕上的該範圍（此時桌寵需露在畫面上）。
2. 泡泡是寶箱 → 點擊領取（模板比對 `templates/chest.png`）。優先用背景點擊
   （直接把點擊訊息送進桌寵視窗，不移動滑鼠、不搶焦點）；無效時才改用移動滑鼠點擊。
3. 泡泡是倒數（mm:ss）→ 用 Windows 內建 OCR 讀時間，睡到最短的倒數結束再檢查；
   最多 10 分鐘會重新確認一次，防止桌寵被移動或 OCR 讀錯。
4. 時間到後每 5 秒檢查一次，直到偵測到寶箱並點掉。
5. 背景點擊時，玩全螢幕遊戲、桌寵被蓋住也能照常領取（單螢幕也可以）。改用滑鼠點擊時，
   前景若是全螢幕程式（遊戲、全螢幕影片）、滑鼠被遊戲鎖住，或寶箱被其他視窗蓋住，就暫緩點擊，
   不會搶走遊戲焦點或點到別的視窗。

支援多螢幕、不同螢幕縮放比例與桌寵大小（模板依泡泡框大小自動縮放，以縮放截圖模擬測過桌寵 50%～200%）。
桌寵 50% 且螢幕縮放 100% 時倒數字太小讀不到，會改為每 30 秒檢查寶箱。

## 專案結構
```
auto_tappo.py        核心：偵測泡泡、OCR 倒數、點擊；也可單獨用命令列執行
tray_app.py          系統匣 App（打包成 exe 的進入點；更新檢查、接手舊版）
ui.py                狀態視窗（tkinter，米白色系）
templates/chest.png  寶箱模板（比對時依泡泡大小自動縮放）
assets/              App 圖示（icon.png / icon.ico）
tools/make_icon.py   由 logo 圖產生圖示：去背＋加滑鼠游標（logo 原圖不在 repo）
build.bat            用 PyInstaller 打包成 dist\AutoWuwaTappo.exe
```

## 開發
需求：Windows 10/11（OCR 優先用英文，沒有就用系統語言如繁中）、Python 3.10+
```
pip install -r requirements.txt
python tray_app.py            # 系統匣版
build.bat                     # 打包 exe（另需 pip install pyinstaller）
```

### 命令列版本
```
python auto_tappo.py          # 背景執行（無視窗）
python auto_tappo.py status   # 是否在執行＋最近紀錄
python auto_tappo.py stop     # 停止
python auto_tappo.py --console  # 在可見的命令列視窗執行
python auto_tappo.py scan     # 掃一次，印出目前的寶箱與倒數（除錯用）
python auto_tappo.py snap     # 截桌寵視窗到 snaps/
python auto_tappo.py --dry    # 只偵測不點擊
```

| 參數 | 預設 | 說明 |
|---|---|---|
| `--max-wait` | 600 | 倒數時最多睡幾秒就重新確認 |
| `--poll` | 5 | 倒數結束後的檢查間隔（秒），直到偵測到寶箱 |
| `--interval` | 30 | 找不到視窗或泡泡時的重試間隔（秒） |
| `--threshold` | 0.85 | 寶箱比對門檻 |
| `--dry` | | 只偵測不點擊 |
| `--no-elevate` | | 不自動要求系統管理員權限 |
| `--console` | | 不隱藏，在命令列視窗執行 |

同時只會有一份在執行（系統匣版與命令列版共用）。

### 換寶箱模板
寶箱外觀改變時：等寶箱出現時 `python auto_tappo.py snap`，把寶箱圖示（只裁圖示本體）
存成 PNG 放進 `templates/`，並把 `auto_tappo.py` 的 `CHEST_BUBBLE_H`、`CHEST_BUBBLE_W` 改成截圖時寶箱泡泡框的高、寬（像素），
程式會以此為基準依實際泡泡大小縮放模板。

## 授權
[MIT License](LICENSE)
