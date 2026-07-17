請根據以上遙測分析摘要產出一份結構化分析報告。

只輸出一個 JSON 物件，不要加任何說明文字或 Markdown code fence。格式：

{
  "overall": "整體評語，2-3 句，繁體中文",
  "findings": [
    {
      "corner": "彎名或區段名稱",
      "priority": 1,
      "time_lost_s": 0.412,
      "phase": "entry",
      "diagnosis": "診斷：發生了什麼（引用摘要中的實際數字）",
      "prescription": "處方：具體怎麼改（煞車點/釋放/油門時機/走線）",
      "expected_gain_s": 0.3
    }
  ]
}

規則：
- findings 依 priority 排序（1 = 最優先），最多 5 項，只列真正值得改的
- phase 只能是 "entry"（進彎損失主導）或 "exit"（出彎損失主導）
- time_lost_s / expected_gain_s 為秒數數字；單圈分析沒有比較基準時 time_lost_s 填 null
- 所有文字使用繁體中文，語氣同教練人設
