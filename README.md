# Sim Racing Telemetry Agent

個人化的模擬賽車遙測分析工具 + AI 教練。**支援 ACC 與 iRacing**（自動偵測），
分析、儀表板、AI 教練全部共用。原始規格見 `ACC_Telemetry_Agent_Spec.md`。

## 支援遊戲

| 遊戲 | 資料來源 | 狀態 |
|---|---|---|
| Assetto Corsa Competizione | Shared Memory + Broadcasting UDP | ✅ |
| iRacing | 官方 SDK（pyirsdk） | ✅ |
| F1 25 | 官方 UDP 遙測（port 20777） | ✅ |

F1 25 需在遊戲內開啟：設定 → 遙測設定 → **UDP Telemetry = On**（port 20777、
格式 2025、broadcast 或填本機 IP）。Motion 封包提供真實世界座標，賽道地圖開箱即用；
trackId 自動對應賽道名（Monza、Spa、Suzuka…）。

### 對手遙測

錄製時自動同步記錄場上對手的圈（~10Hz），存進同一個 session、以車手名標記，
圈次選單按「我的圈 / 對手：某某」分組——可以直接「自己 vs 對手」跑整套
比較分析與 AI 教練。各遊戲保真度：

| 遊戲 | 對手資料 |
|---|---|
| F1 25 | 完整：速度/油門/煞車/方向盤/檔位/轉速（UDP 原生含全部 22 車） |
| iRacing | spline/圈速/檔位/轉速；速度由位置推導；無踏板 |
| ACC | 尚未支援（需整合 Broadcasting API，規劃中） |

對手經過 pit 的圈標記為無效；玩家最快圈（★）與圈速趨勢只計自己的圈。

按「開始錄製」後自動偵測哪個遊戲在賽道上，session 以 `[ACC]` / `[iR]` 標籤區分。
iRacing 對應：`LapDistPct` → spline、GPS 經緯度 → 賽道地圖座標、
`PlayerTrackSurface` → 有效圈判定、胎面中央溫度 → 胎溫卡（iRacing 無即時胎壓）。

## 開發進度

- [x] **階段一**：UDP 監聽與封包解析 + Shared Memory 讀取（已實測驗收 ✓）
- [x] **階段二**：SQLite 資料儲存與圈次記錄（已實測驗收 ✓）
- [x] **階段三**：圈次比較分析與視覺化（已實測驗收 ✓）
- [x] **儀表板 UI**：桌面 app（pywebview + Flask + uPlot），內建錄製與 session 管理
- [x] **階段四**：Claude AI 教練整合（含彎道對照表、賽道知識庫）

## 重要架構說明：兩個資料來源

原規格假設 UDP Broadcasting API 有油門/煞車/方向盤/RPM——**實際上沒有**。
Broadcasting API 是給轉播 overlay 用的，只有速度、spline position、圈速、名次等。
踏板與引擎資料要走 ACC 的另一條官方管道 **Shared Memory**（記憶體映射檔）。
因此本專案同時使用兩者：

| 資料 | 來源 |
|---|---|
| 油門、煞車、方向盤、檔位、RPM、速度 | Shared Memory (`Local\acpmf_physics`) |
| 圈速、spline position、有效圈 flag、sector | Shared Memory (`Local\acpmf_graphics`) |
| 賽道名、車型、玩家名 | Shared Memory (`Local\acpmf_static`) |
| 賽道長度、entry list、多車位置、轉播事件 | Broadcasting UDP API |

Shared Memory 無需任何設定，ACC 開著就有，且更新頻率高（物理引擎頻率），
是階段二逐點遙測的主要來源。Broadcasting API 作為輔助與交叉驗證。

## 環境設定

使用 [uv](https://docs.astral.sh/uv/) 管理依賴與虛擬環境（`pyproject.toml`）：

```powershell
cd acc-telemetry-agent
uv sync              # 建立 .venv 並安裝依賴（一次性）
```

所有指令用 `uv run` 執行，不需要手動 activate。

### 啟用 Broadcasting API（一次性設定）

編輯 `Documents\Assetto Corsa Competizione\Config\broadcasting.json`：

```json
{
    "updListenerPort": 9000,
    "connectionPassword": "asd",
    "commandPassword": ""
}
```

（欄位名 `updListenerPort` 是官方的拼字，不是筆誤。）改完需重開 ACC。

## 階段一：即時遙測顯示

ACC 進入賽道後執行：

```powershell
uv run python -m telemetry_listener.live_console
```

會即時顯示速度 / 檔位 / RPM / 油門% / 煞車% / 方向盤 / 圈數 / spline位置 / 圈速 / 有效圈。

選項：
- `--shm-only` 只讀 shared memory（不需要 broadcasting.json 設定）
- `--udp-only` 只測 Broadcasting API
- `--port 9000 --password asd` 對應 broadcasting.json 的設定

### 沒開遊戲時的離線測試

用假 server 模擬 ACC 的 Broadcasting API：

```powershell
# 終端機 1
uv run python tools/fake_broadcast_server.py
# 終端機 2
uv run python -m telemetry_listener.live_console --udp-only
```

### 驗收清單（進遊戲核對）

1. 速度、RPM、檔位與遊戲內 HUD 一致
2. 踩油門/煞車時 T% / B% 即時反應（0~100%）
3. 跑完一圈後 `last` 圈速與遊戲內顯示一致
4. 出界/切西瓜後顯示 INVALID
5. UDP 側的檔位換算是否正確（parser 內有標註待核對，若不對回報即修）

## 階段二：圈次錄製

ACC 進入賽道後執行：

```powershell
uv run python -m data_store.record          # 開始錄製，Ctrl+C 結束
```

每完成一圈自動存檔（預設 `data/telemetry.sqlite3`），以約 50Hz 記錄
時間、spline、速度、油門、煞車、方向盤、檔位、RPM。
圈界以 `completedLaps` 遞增偵測；`isValidLap` 在圈中任一時刻為 False 該圈即記 invalid；
起錄時已在圈中或 Ctrl+C 中斷的圈記為 incomplete。

查詢：

```powershell
uv run python -m data_store.inspect                      # 列出所有 session 與圈次
uv run python -m data_store.inspect --lap-id 3           # 該圈摘要 + 抽樣點
uv run python -m data_store.inspect --lap-id 3 --csv lap3.csv   # 整圈匯出 CSV
```

### 驗收清單（進遊戲跑 3-5 圈）

1. 每過終點線 console 出現 `[saved] lap N: <圈速>`，圈速與遊戲內顯示一致
2. `python -m data_store.inspect` 列出的圈數、圈速正確
3. `--lap-id N` 能撈出完整一圈逐點資料，spline 從 ~0 遞增到 ~1
4. 切西瓜的圈標記 invalid；Ctrl+C 中斷的圈標記 incomplete

## 階段三：圈次比較與視覺化

```powershell
uv run python -m analysis.compare_laps               # 最新 session：最快圈 vs 最近一圈
uv run python -m analysis.compare_laps 3 7           # 指定 lap_id（A=參考圈, B=比較圈）
uv run python -m analysis.compare_laps 3 7 --out my.png
```

輸出兩樣東西：

1. **文字摘要**：整段時間差 + 每個煞車區段的煞車點差異、彎中最低速、
   出口速度、損失秒數（依損失排序）——這也是階段四餵給 AI 教練的 context。
2. **比較圖 PNG**：三面板共用賽道位置橫軸——速度疊圖、油門(實線)/煞車(虛線)、
   delta time 曲線（紅色面積 = B 落後累積），損失最大的區段以灰底標出。

原理：兩圈各自內插到統一的 spline 網格（0.1% 解析度），
delta_ms(s) = B 到達位置 s 的時間 − A 的時間，煞車區段以「任一圈踏板 >10%」切段。

### 驗收清單

1. 拿階段二錄的資料跑 `python -m analysis.compare_laps`，圖能打開且曲線合理
2. 摘要指出的「損失最大彎」與你自己開的體感一致
3. delta 曲線終點 ≈ 兩圈圈速差

## 首頁

開 app 先進**首頁**（不再直接進儀表板）：

- **開始錄製**：自動偵測遊戲，錄製中即時狀態
- **555 訓練**：一鍵開始訓練（見下），即時面板顯示階段/進度/得分
- **個人最佳**：每條賽道你的 PB（跨 session）
- **Session 卡片**：點卡片進儀表板；重新命名/刪除在儀表板側欄
- **訓練紀錄**：歷次 555 得分
- 右上 ⚙ 設定（API 金鑰、教練模型）

點 session 進儀表板後，左上「← 首頁」返回。

## 555 訓練

賽道一致性訓練，四階段狀態機（錄製時即時追蹤）：

1. **基準期**：連續 5 圈零失誤（出界/切彎/不完整）→ 算出基準均速 A1。中途失誤歸零重數。
2. **超越期**：連續 5 圈每圈都乾淨且快過 A1 → 算出 A2。慢過 A1 或失誤都歸零重數。
3. **設定目標**：依 A2 自訂目標圈速（面板預填「A2 快 0.5 秒」的建議）。
4. **達標期**：在剩餘圈中累積 5 圈 ≤ 目標（不必連續；無效圈不算數也不影響）。

完成後計分（0–100）：一致性（基準 5 圈離散度）、進步幅度（A1→A2）、
企圖心（目標低於 A2 多少）、達標效率（湊到 5 圈花了幾圈），加權為總分並存進訓練紀錄。
失誤定義 = 無效圈（出界/切彎）或不完整圈，這類圈一律不計圈速。

## 儀表板 App

```powershell
uv run python -m webapp.desktop      # 桌面版（原生視窗，建議）
# 或直接雙擊專案根目錄的 ACC-Telemetry.bat
uv run python -m webapp.app          # 瀏覽器版 http://127.0.0.1:5000（開發用）
```

- **App 內錄製**：左上角「● 開始錄製」→ 進 ACC 跑圈（會顯示等待/REC 即時狀態、
  目前圈與已存圈數，每存一圈清單自動刷新）→「■ 停止錄製」。
  CLI 錄製（`data_store.record`）仍可用，兩者寫同一個資料庫。
- **Session 管理**：session 選單下方可自訂名稱、刪除（兩段式確認，連帶刪除圈次與逐點資料）。

深色賽車儀表板：左側選 session 與 A/B 兩圈（含圈速趨勢條）。
**比較圈選「— 單圈分析 —」可看單圈**：速度/踏板/方向盤/檔位軌跡、車速著色的
賽道地圖、單圈煞車區段表（煞車點/入彎/彎中/出口速度），AI 教練也支援單圈模式。
只有一圈的 session 會自動進單圈分析。比較模式顯示——

- 統計磚：兩圈圈速、總差、最大損失彎
- **賽道地圖**：路徑依「B 在該路段賺/損」著紅綠色，游標連動位置點（需 v2 錄製資料）
- **微分段 delta 條**：賽道等分 25 段，紅/綠深淺 = 損失/賺的幅度，hover 看數值
- 五張游標同步、拖曳縮放同步的圖（uPlot，已 vendor，不依賴 CDN）：
  速度、油門/煞車、方向盤、檔位、Δt 曲線（紅面積 = 落後）
- **胎心溫度 / 胎壓卡**：整圈平均，依工作區間著色（需 v2 錄製資料）
- 煞車區段分析表：依損失排序，含**進彎/出彎相位拆解**（Coach Dave Delta 式歸因）

### v2 錄製通道

錄製器現在額外記錄：世界座標（賽道地圖）、橫向/縱向 G、四輪胎心溫度與胎壓。
舊資料庫會自動遷移（新欄位為 NULL），地圖與胎溫卡只在新錄的圈出現，
其餘功能（微分段、相位拆解、方向盤/檔位圖）舊資料也能用。
階段四的 AI 教練對話會直接嵌進這個介面。

## 階段四：AI 教練

儀表板底部的「AI 教練」對話欄。**點左上角 ⚙ 開設定面板**：貼上 Claude API 金鑰
（console.anthropic.com 取得）、選教練模型、按「測試連線」確認——存進本機
`config.json`（已在 .gitignore），即存即用不需重啟。也支援環境變數
`ANTHROPIC_API_KEY`（設定檔優先）。

- Context 自動組裝：教練人設 + 賽道知識（`knowledge/<track>.md`）+ 當前比較兩圈的
  分析摘要（含彎名、相位拆解），掛 prompt cache 讓多輪追問便宜又快
- 模型預設 `claude-sonnet-5`，設定面板可切換（Sonnet 5 / Opus 4.8 / Haiku 4.5），
  `COACH_MODEL` 環境變數優先序最高
- 切換比較圈時對話自動重置（context 已失效）
- 快速提問：「這圈哪裡可以更快？」等一鍵發問；支援追問（例如「那 Ascari 呢？」）
- **對話自動儲存**（SQLite `coach_chats` 表，以圈組合為鍵）：切換圈次再切回來，
  對話自動恢復可繼續追問；「清除對話」按鈕可重來
- 單圈模式下教練拿到的是單圈摘要（煞車點/各彎速度絕對值），會明說哪些判斷需要更多圈數驗證

### 彎道對照表與賽道知識

- `data/tracks/<game>/<track>.json`：spline → 官方彎名對照（已建 ACC Monza，
  由實際遙測校準）。有對照表的賽道，儀表板與 AI 全部以彎名呈現（「Variante Ascari
  (T8-T10)」而非「區段 #5」）；沒有的優雅退化回編號
- `knowledge/<track>.md`：賽道攻略知識（已寫 Monza），依 session 的賽道名整份注入
  AI context——賽道少、每次只涉及一條，不需要向量檢索
- 兩者皆按遊戲/賽道擴充：跑新賽道後把偵測到的煞車區段位置對上彎名清單即可建表

## 專案結構

```
telemetry_listener/          # 階段一
  broadcast/
    protocol.py              # Broadcasting API 二進位封包編碼/解析
    client.py                # UDP client（handshake、心跳、callback 分派）
  shared_memory.py           # physics/graphics/static 三頁讀取
  live_console.py            # 即時 console 顯示（驗收工具）
data_store/                  # 階段二
  db.py                      # SQLite schema 與讀寫（sessions/laps/telemetry_points）
  recorder.py                # 圈界偵測與逐點緩衝邏輯（純邏輯，可離線測試）
  record.py                  # 錄製 CLI
  inspect.py                 # 查詢/匯出 CLI
analysis/                    # 階段三
  loader.py                  # 讀圈 + spline 網格重取樣
  compare.py                 # delta time / 煞車區段分析 / 文字摘要
  plot.py                    # 三面板比較圖
  compare_laps.py            # 比較 CLI
sources/                     # 多遊戲遙測來源（統一 reader 介面）
  acc.py                     # ACC（包裝 shared memory）
  iracing.py                 # iRacing（pyirsdk 映射）
training/                    # 555 訓練
  five55.py                  # 四階段狀態機 + 計分（純邏輯）
agent/                       # 階段四
  coach.py                   # AI 教練（context 組裝 + Claude API 呼叫）
data/tracks/<game>/          # 彎道對照表（spline → 彎名）
knowledge/                   # 賽道攻略知識（AI context 用）
webapp/                      # 儀表板 App
  desktop.py                 # 桌面版入口（pywebview 原生視窗）
  app.py                     # Flask server + JSON API（compare/record/session 管理）
  recording.py               # App 內錄製服務（背景 thread 狀態機）
  static/                    # 前端（index.html / app.js / style.css / vendor uPlot）
ACC-Telemetry.bat            # 雙擊啟動桌面版
tools/
  fake_broadcast_server.py   # 模擬 ACC broadcasting server，離線測試用
tests/
  test_offline.py            # 階段一：假 server 端對端測試
  test_recorder.py           # 階段二：合成三圈資料驗證錄製與查詢
  test_analysis.py           # 階段三：合成兩圈驗證 delta 與煞車區段偵測
```
