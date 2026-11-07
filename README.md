# QUANTUM·K — Financial Research Platform

理財機器人期中報告：以 Python 爬取 Yahoo Finance 公開歷史股價，並用 Streamlit 自建的極簡金融研究平台呈現 K 線圖與技術分析。

**線上版本**：https://liao950715-stock-k-dashboard-stock-dashboard-5fuskn.streamlit.app/

## 功能
- **週期**：5分 / 15分 / 30分 / 60分 / 日線 / 週線 / 月線（週、月線由日線正確彙整 OHLCV）
- **均線**：MA5、MA10、MA20、MA60、MA120、MA240，依目前週期的 K 棒計算，各自獨立開關
- **副圖**：成交量（含均量線）＋ MACD / RSI / KD 切換，所有圖層共用同一時間軸
- **技術面研究摘要**：規則式技術診斷（偏多 / 偏空 / 盤整 / 訊號分歧 / 資料不足），附判斷依據與風險說明
- **市場指標**：區間最高、最低、平均成交量、區間報酬率（皆標示區間定義）
- **介面**：淺色（預設）/ 深色主題、三種字體分工、台股紅漲綠跌、十字準心、OHLCV 游標數值、區域縮放
- **響應式**：桌面 / 平板 / 手機（≥1200px、768–1199px、≤767px）
- **錯誤處理**：代碼錯誤、查無資料、網路中斷、逾時、限流、資料不足，皆以中文提示並可重新載入
- **資料表與匯出**：歷史 OHLCV 與指標明細、CSV 下載（Excel 可直接開啟）

## 本機執行
```bash
pip install -r requirements.txt
streamlit run stock_dashboard.py
```

## 技術
Python · Streamlit · yfinance · Plotly · pandas · NumPy

> 資料來源：Yahoo Finance（yfinance），日線為歷史收盤資料、分鐘線可能延遲，僅供學術研究，不構成投資建議。
