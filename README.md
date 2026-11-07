# QUANTUM·K TERMINAL — 智慧 K 線看盤儀表板

理財機器人期中報告：以 Python 爬取 Yahoo Finance 公開歷史股價，並用 Streamlit 自建網頁呈現 K 線圖。

## 功能
- **週期切換**：5分 / 15分 / 30分 / 60分 / 日線 / 週線 / 月線
- **均線勾選**：MA5、MA10、MA20、MA60（季線）、MA120（半年線）、MA240（年線）
- **副圖指標**：成交量（含均量線）＋ MACD / RSI / KD 切換，與 K 線時間軸同步
- **AI 技術面診斷**：多頭/空頭排列、黃金/死亡交叉、MACD、RSI、KD 綜合評分
- **介面**：深色/淺色主題、三種字體、台股紅漲綠跌、十字準心、OHLCV 游標數值、區域縮放
- **錯誤處理**：代碼錯誤、網路斷線、資料不足、Yahoo 限流皆有提示

## 本機執行
```bash
pip install -r requirements.txt
streamlit run stock_dashboard.py
```

## 技術
Python · Streamlit · yfinance · Plotly · pandas · NumPy

> 資料來源：Yahoo Finance（yfinance），僅供學術研究，不構成投資建議。
