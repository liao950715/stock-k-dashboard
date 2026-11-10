# 股票歷史 K 線圖（理財機器人期中報告）

用 Python 的 yfinance 抓取 Yahoo Finance 公開的歷史股價，再用 Streamlit 做成網頁，畫出 K 線圖。

**線上網址**：https://liao950715-stock-k-dashboard-stock-dashboard-5fuskn.streamlit.app/

## 功能
- 頂部導覽列：股票搜尋（台股輸入數字即可，例如 2330；美股如 AAPL）、深色 / 淺色主題切換
- 資料跨度：1 天、5 天、10 天、20 天、1 個月 ～ 5 年
- K 棒週期：5 分、15 分、60 分、日線、週線、月線（切換跨度時自動帶入合適週期，仍可手動改）
- 均線：MA5、MA10、MA20、MA60、MA120、MA240；布林通道 (20, 2)
- 副圖：成交量、MACD、KDJ；K 線圖顏色採台股紅漲綠跌（同時以 ▲▼ 與正負號標示）
- 新聞情緒量化分析 V2.1：事件去重與版本、加權收縮新聞情緒指數 NSI、分析穩健程度、多空抵銷率、重大事件與傳聞警示、每日快照與趨勢、規則式摘要（設定 ANTHROPIC_API_KEY 後可啟用 AI 摘要）。說明見 [docs/NEWS_SENTIMENT_V2_1.md](docs/NEWS_SENTIMENT_V2_1.md)；移植到其他專案見 [docs/NEWS_INTEGRATION_GUIDE.md](docs/NEWS_INTEGRATION_GUIDE.md)
- 歷史資料表可下載 CSV
- 找不到股票代碼或網路錯誤時會顯示提示

## 測試
```bash
pip install pytest
python -m pytest tests
```

## 執行方式
```bash
pip install -r requirements.txt
streamlit run stock_dashboard.py
```

> 先前版本備份：完整版 `backup/full-version`（v1-full）、精簡版 `backup/slim-version`（v2-slim）。
