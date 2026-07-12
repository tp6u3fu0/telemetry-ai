# Telemetry AI

個人化的模擬賽車遙測分析工具 + AI 教練。錄圈、比較、賽道地圖、AI 教練建議，
一個桌面 app 全包。**自動偵測 ACC / iRacing / F1 25**。

## 支援遊戲

| 遊戲 | 資料來源 | 對手資料 |
|---|---|---|
| Assetto Corsa Competizione | Shared Memory + Broadcasting UDP | 尚未支援 |
| iRacing | 官方 SDK（pyirsdk） | spline/圈速/檔位/轉速（速度由位置推導） |
| F1 25 | 官方 UDP 遙測（port 20777） | 完整（含全部 22 車） |

- **F1 25**：遊戲內 設定 → 遙測 → **UDP Telemetry = On**（port 20777、格式 2025）。
- **ACC**：需啟用 Broadcasting API——編輯 `Documents\Assetto Corsa Competizione\Config\broadcasting.json`，
  設 `updListenerPort: 9000`、`connectionPassword: "asd"`，重開遊戲。
- **iRacing**：開著即可，無需設定。

## 安裝與執行

### 方式一：下載 exe（不需 Python）

到 [Releases](https://github.com/tp6u3fu0/telemetry-ai/releases) 下載 `Telemetry-AI.exe`，雙擊執行。
需要 Windows 內建的 WebView2 Runtime（Win11 預裝）。設定與資料存於 `%LOCALAPPDATA%\Telemetry-AI\`。

### 方式二：從原始碼跑

用 [uv](https://docs.astral.sh/uv/) 管理依賴：

```powershell
uv sync                          # 一次性安裝依賴
uv run python -m webapp.desktop  # 啟動桌面版（或雙擊 Telemetry-AI.bat）
```

## 使用流程

1. **首頁** → 按「開始錄製」，進遊戲跑圈（自動偵測遊戲、即時顯示狀態）。
2. 錄完點 **session 卡片**進儀表板。
3. 左側把圈**拖到 A / B 比較欄**（B 留空＝單圈分析），下方即時重算。
4. 底部 **AI 教練**問問題（先在 ⚙ 設定填 Claude API 金鑰）。

## 核心功能

- **儀表板**：速度/踏板/方向盤/檔位/Δt 五張同步圖（可單獨放大）、車速著色的賽道地圖、
  微分段 delta、煞車區段分析（含進彎/出彎相位拆解）、胎溫/胎壓卡。分頁式排版、
  支援亮/暗主題、直式螢幕自適應。
- **對手比較**：錄製時同步記錄場上對手的圈，可直接「自己 vs 對手」跑整套分析。
- **555 訓練**：賽道一致性四階段訓練（基準→超越→設目標→達標），即時專注畫面 +
  完成計分，可**暫停續傳**。訓練法概念源自影片
  [《不養成好習慣, 就是在培養一個壞習慣》](https://youtu.be/_JJEQIiwphw)。
- **AI 教練**：Claude 驅動，自動組裝賽道知識 + 兩圈分析摘要當 context，逐字串流回覆，
  對話自動存檔。模型可選（Sonnet 5 / Opus 4.8 / Haiku 4.5）。

### AI 教練設定

點左上 ⚙ → 貼上 Claude API 金鑰（[console.anthropic.com](https://console.anthropic.com) 取得）→
測試連線。金鑰存本機 `config.json`（已 gitignore），即存即用。也支援環境變數
`ANTHROPIC_API_KEY` / `COACH_MODEL`。

## 打包成 exe

```powershell
uv run --with pyinstaller pyinstaller telemetry-ai.spec --noconfirm
# 產出 dist/Telemetry-AI.exe（~37MB）
```

發佈：推 tag（`v*`）→ `.github/workflows/build.yml` 於 windows runner 自動建置並附到 Release。

```powershell
git tag v1.0.2 && git push origin v1.0.2
```

打包細節（可寫路徑分離、絕對 import 入口等）見 `webapp/paths.py`、`run_app.py`、`telemetry-ai.spec`。

## 進階：CLI

儀表板以外，各階段也有獨立 CLI（開發/除錯用）：

```powershell
uv run python -m telemetry_listener.live_console   # 即時遙測 console
uv run python -m data_store.record                 # CLI 錄製
uv run python -m data_store.inspect --lap-id 3      # 查詢/匯出圈次
uv run python -m analysis.compare_laps 3 7          # CLI 比較兩圈 + 輸出 PNG
```

## 測試

純 Python，逐一執行（無需瀏覽器）：

```powershell
uv run python tests/test_webapp.py     # 前端 smoke test（DOM 契約 + compare 對齊）
uv run python tests/test_training.py   # 555 狀態機
# 其餘：test_offline / test_recorder / test_analysis / test_iracing / test_f1 / test_coach / test_opponents
```

## 專案結構

```
telemetry_listener/   遙測來源：Broadcasting UDP、Shared Memory
sources/              多遊戲統一 reader（acc / iracing / f1_25）
data_store/           SQLite 儲存、圈界偵測、對手 tracker
analysis/             spline 重取樣、delta/煞車區段分析、彎道對照表
training/five55.py    555 訓練狀態機（純邏輯）
agent/coach.py        AI 教練（context 組裝 + Claude API）
webapp/               桌面 app：desktop.py 入口、app.py（Flask API）、static/（前端）
data/tracks/          彎道對照表（spline → 彎名）
knowledge/            賽道攻略知識（AI context 用）
run_app.py            PyInstaller 打包入口
```
