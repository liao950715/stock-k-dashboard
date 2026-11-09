# 股票歷史 K 線圖（理財機器人期中報告）

用 Python 的 yfinance 抓取 Yahoo Finance 公開的歷史股價，再用 Streamlit 做成網頁，畫出 K 線圖。

**線上網址**：https://liao950715-stock-k-dashboard-stock-dashboard-5fuskn.streamlit.app/

## 功能
- 頂部導覽列：股票搜尋（台股輸入數字即可，例如 2330；美股如 AAPL）、深色 / 淺色主題切換
- 資料跨度：1 天、5 天、10 天、20 天、1 個月 ～ 5 年
- K 棒週期：5 分、15 分、60 分、日線、週線、月線（切換跨度時自動帶入合適週期，仍可手動改）
- 均線：MA5、MA10、MA20、MA60、MA120、MA240；布林通道 (20, 2)
- 副圖：成交量、MACD、KDJ；K 線圖顏色採台股紅漲綠跌（同時以 ▲▼ 與正負號標示）
- 相關新聞：Google 新聞 RSS（去重、依時間排序、關鍵字標示偏多 / 偏空）、新聞分析總覽、證交所 / 櫃買中心每日重大訊息
- 歷史資料表可下載 CSV
- 找不到股票代碼或網路錯誤時會顯示提示

## 執行方式
```bash
pip install -r requirements.txt
streamlit run stock_dashboard.py
```

> 先前版本備份：完整版 `backup/full-version`（v1-full）、精簡版 `backup/slim-version`（v2-slim）。
