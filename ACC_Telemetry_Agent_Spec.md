# ACC Sim Racing Telemetry AI Agent — 專案規格書

## 1. 專案目標

打造一個個人化的 Assetto Corsa Competizione(ACC)遙測分析工具,結合 AI Agent,
讓使用者能透過自然語言詢問「這圈哪裡可以更快」,並取得基於實際遙測資料的教練式建議。

專案分四個階段循序漸進開發,每個階段都要有可獨立驗證的產出。

---

## 2. 技術背景

### 2.1 資料來源:ACC UDP Broadcasting API

ACC 提供官方 UDP Broadcasting API,可即時串流賽道上的資料,包含:
- 車輛遙測(速度、煞車、油門、方向盤角度、檔位、轉速)
- 圈速與分段時間(sector times)
- 輪胎溫度與磨損
- 車輛位置(賽道座標,用於疊圖)

啟用方式:編輯 ACC 設定目錄下的 `broadcasting.json`
(路徑通常在 `Documents/Assetto Corsa Competizione/Config/broadcasting.json`),
設定監聽 port、更新頻率、connection password 等。

協議細節:官方有發布 Broadcasting SDK 文件與 C# 範例,協議是二進位封包格式,
需自行實作 parser(或參考社群已有的 Python/Node 實作作為 reference,但建議自己刻一份以確保理解封包結構)。

### 2.2 開發環境
- 作業系統:Windows(ACC 只在 Windows 上跑,遙測監聽程式建議與遊戲同機執行)
- 語言:Python 3.11+(建議,生態系對 UDP socket、資料處理、後續接 LLM API 都方便)
- 資料庫:先用 SQLite(單檔案、免安裝,適合個人專案),之後有需要再考慮遷移
- AI:Claude API(Anthropic),使用 `claude-sonnet-4-6` 或當時可用的最新一般模型

---

## 3. 開發階段規劃

### 階段一:UDP 監聽與封包解析(最小可行版本)

**目標**:寫一個 Python script 連上 ACC Broadcasting API,成功接收並印出即時資料。

**任務清單**:
1. 研究 ACC Broadcasting API 官方文件與封包格式(message types: registration, entry list,
   realtime update, realtime car update, track data 等)
2. 實作 UDP socket 連線與 handshake(送出 registration request,取得 connection ID)
3. 實作封包 parser(二進位 → Python dict/dataclass),至少涵蓋:
   - 車速(speed)
   - 油門/煞車輸入(throttle/brake, 0-100%)
   - 方向盤角度(steering angle)
   - 檔位(gear)
   - 引擎轉速(RPM)
   - 賽道位置百分比(normalized spline position,用於後續對齊賽道座標)
   - 目前圈數與圈速(current lap, last lap time)
4. 在 console 即時印出關鍵欄位,驗證資料正確性(可對照遊戲內 HUD 手動核對數值)

**驗收標準**:跑一圈賽道,console 能即時顯示速度/煞車/油門變化,且數值與遊戲內顯示一致。

---

### 階段二:資料儲存與圈次記錄

**目標**:將即時遙測資料結構化儲存,支援之後的分析比對。

**任務清單**:
1. 設計 SQLite schema,建議至少兩張表:
   - `sessions`(session_id, track, car, date, session_type)
   - `laps`(lap_id, session_id, lap_number, lap_time, is_valid)
   - `telemetry_points`(lap_id, timestamp_ms, spline_position, speed, throttle, brake,
     steering, gear, rpm, ...)
2. 偵測「新的一圈開始」的邏輯(通常 Broadcasting API 會回報 lap 數變化或 spline position 歸零)
3. 將每一圈的逐點資料寫入資料庫,確保能事後依 lap_id 撈出完整一圈的時序資料
4. 加入簡單的資料驗證(is_valid lap:是否有出界、切西瓜等,ACC 封包應該有相關 flag)

**驗收標準**:跑 3-5 圈後,能用 SQL query 撈出「第 N 圈」的完整逐點遙測資料,且圈速與遊戲內顯示吻合。

---

### 階段三:分析與視覺化(不含 AI)

**目標**:做出實用的圈次比較工具,證明資料具備分析價值。

**任務清單**:
1. 選定視覺化方式:
   - 建議用 Python(matplotlib/plotly)先出圖,之後可考慮包成簡單網頁(Streamlit 或 Flask + Chart.js)
2. 核心圖表:
   - 速度 vs 賽道位置(spline position)疊圖,可疊多圈比較
   - 煞車/油門 vs 賽道位置疊圖
   - 找出「哪裡比最快圈慢」的差異區段(delta time / delta speed 分析)
3. 進階(可選):
   - 賽道地圖疊加速度熱區(需要車輛 X/Y 座標,若 Broadcasting API 有提供)
   - Sector time 拆解比較

**驗收標準**:選兩圈(例如最快圈 vs 某一圈),能產生一張圖清楚顯示在哪個彎煞車點不同、
出彎速度差多少。

---

### 階段四:AI Agent 整合

**目標**:讓 Claude 讀取分析結果,以教練口吻給出具體建議,並支援對話式追問。

**任務清單**:
1. 資料轉文字摘要:寫一個函式,把階段三的分析結果(例如「第3彎煞車點比最快圈晚0.2秒,
   出彎速度少8km/h」)轉換成結構化文字,作為 LLM 的 context
2. 設計 system prompt:定位為賽車教練,強調具體、可執行的建議(煞車點、油門時機),
   避免空泛的話術
3. 呼叫 Claude API(建議先用一般文字對話介面測試,例如 CLI 或簡單網頁聊天框)
4. 支援使用者追問(例如「那第5彎呢」),需要維持對話上下文與資料 context
5.(進階)導入簡易 RAG:建立賽道特性/車輛設定的知識庫,讓 agent 回答時能引用更廣的背景知識

**驗收標準**:使用者輸入「這圈哪裡可以更快」,agent 能根據實際遙測數據,具體指出彎道、
現象(煞車太早/太晚、油門介入時機)、以及建議動作。

---

## 4. 給 Claude Code 的建議工作方式

- 每個階段完成後停下來讓使用者實際測試(跑車驗證),再進入下一階段,不要一次寫完四階段
- 階段一是全案基礎,務必先把封包解析做對、做穩,這裡出錯後面全部白工
- Python 依賴請用虛擬環境管理(venv),並提供 `requirements.txt`
- 程式碼結構建議按階段拆模組,例如:
  ```
  /telemetry_listener/   # 階段一:UDP 監聽與封包解析
  /data_store/           # 階段二:SQLite 儲存邏輯
  /analysis/             # 階段三:圈次比較與視覺化
  /agent/                # 階段四:LLM 整合
  ```
- 每個模組附上簡單的使用說明或 CLI 指令,方便單獨測試
- 若 ACC Broadcasting API 官方文件有不清楚之處,可參考社群開源實作作為交叉驗證,
  但封包解析邏輯建議自行重寫理解,不要直接搬運整包程式碼

---

## 5. 開放問題(開發過程中待確認)

- ACC Broadcasting API 是否有提供車輛 X/Y 座標(賽道地圖視覺化需要,若無則跳過該功能)
- 是否需要同時支援 F1 25(建議先不做,ACC 做完架構穩定後再評估是否擴充)
- 資料庫未來是否有換成雲端/跨裝置同步的需求(目前先假設單機使用)
- AI Agent 的介面形式:CLI / 簡單網頁 / 之後接入現有的 Notion 或 Line Bot(可延後決定)
