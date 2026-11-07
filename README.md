# 股票歷史 K 線圖（理財機器人期中報告）

用 Python 的 yfinance 抓取 Yahoo Finance 公開的歷史股價，再用 Streamlit 做成網頁，畫出 K 線圖。

**線上網址**：https://liao950715-stock-k-dashboard-stock-dashboard-5fuskn.streamlit.app/

## 功能
- 輸入股票代碼（台股輸入數字即可，例如 2330；美股如 AAPL）
- 週期：1 分 K、5 分 K、15 分 K、日線、週線、月線
- 資料跨度：1 個月 ～ 5 年（分 K 自動依 Yahoo 限制縮短）
- 均線：MA5、MA10、MA20、MA60、MA120、MA240，分 K 另有當日均價線 VWAP
- 副圖：成交量 / MACD / KDJ 下拉切換
- K 線圖，顏色採台股紅漲綠跌；歷史資料表可下載 CSV
- 找不到股票代碼或網路錯誤時會顯示提示

## 執行方式
```bash
pip install -r requirements.txt
streamlit run stock_dashboard.py
```

> 先前版本備份：完整版 `backup/full-version`（v1-full）、精簡版 `backup/slim-version`（v2-slim）。
