# Auto_WuwaTappo

<img src="assets/icon.png" width="96" align="right">

自動領取 Wuwa Tappo 桌寵的寶箱（約每 30 分鐘一次），常駐在右下角系統匣。

## 下載與使用
1. 到 [Releases](../../releases) 下載 `AutoWuwaTappo.exe`，放在任意資料夾。
2. 執行（會跳 UAC，按「是」），右下角系統匣出現圖示即開始運作。
   - 滑鼠停在圖示上：目前狀態（倒數／下次檢查時間）
   - 右鍵：立即檢查、開啟紀錄、結束（雙擊圖示＝立即檢查）
3. 紀錄寫在 exe 旁的 `logs\auto_tappo.log`。

> 為什麼需要系統管理員權限？Wuwa Tappo 本身以系統管理員執行，
> Windows（UIPI）會擋掉一般權限程式送給它的滑鼠點擊。

## 運作方式
1. 截取桌寵視窗，用底色找出桌寵旁的泡泡框。
2. 泡泡是寶箱 → 點擊領取（模板比對 `templates/chest.png`）。
3. 泡泡是倒數（mm:ss）→ 用 Windows 內建 OCR 讀時間，睡到最短的倒數結束再檢查；
   最多 10 分鐘會重新確認一次，防止桌寵被移動或 OCR 讀錯。

支援多螢幕與不同縮放比例（模板依桌寵所在螢幕的 DPI 自動縮放）。

## 專案結構
```
auto_tappo.py        核心：偵測泡泡、OCR 倒數、點擊；也可單獨用命令列執行
tray_app.py          系統匣 App（打包成 exe 的進入點）
templates/chest.png  寶箱模板（150% 縮放下截取）
assets/              App 圖示（icon.png / icon.ico）
tools/make_icon.py   由 logo 圖產生圖示：去背＋加滑鼠游標（logo 原圖不在 repo）
build.bat            用 PyInstaller 打包成 dist\AutoWuwaTappo.exe
```

## 開發
需求：Windows 10/11（含英文 OCR 語言，一般預設就有）、Python 3.10+
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
| `--interval` | 30 | 找不到視窗或泡泡時的重試間隔（秒） |
| `--threshold` | 0.85 | 寶箱比對門檻 |
| `--dry` | | 只偵測不點擊 |
| `--no-elevate` | | 不自動要求系統管理員權限 |
| `--console` | | 不隱藏，在命令列視窗執行 |

同時只會有一份在執行（系統匣版與命令列版共用）。

### 換寶箱模板
寶箱外觀改變時：等寶箱出現時 `python auto_tappo.py snap`，把寶箱圖示（只裁圖示本體）
存成 PNG 放進 `templates/`。模板需在 **150% 縮放** 的螢幕上截取（`TEMPLATE_DPI = 144`），
否則請改 `auto_tappo.py` 中的常數。
