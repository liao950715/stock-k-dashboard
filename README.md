# 股票歷史 K 線圖（理財機器人期中報告）

用 Python 的 yfinance 抓取 Yahoo Finance 公開的歷史股價，再用 Streamlit 做成網頁，畫出 K 線圖。

**線上網址**：https://liao950715-stock-k-dashboard-stock-dashboard-5fuskn.streamlit.app/

## 功能
- 輸入股票代碼（台股輸入數字即可，例如 2330；美股如 AAPL）
- 週期切換：日線、週線、月線（週線、月線由日線資料 resample 而來）
- 均線勾選：MA5、MA10、MA20
- K 線圖與成交量圖，顏色採台股紅漲綠跌
- 找不到股票代碼或網路錯誤時會顯示提示

## 執行方式
```bash
pip install -r requirements.txt
streamlit run stock_dashboard.py
```

> 完整版（含技術指標與更多功能）備份在 `backup/full-version` 分支 / `v1-full` 標籤。
