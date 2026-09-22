# Liquidity Engine Web App

## Run
```bash
pip install -r requirements.txt
python server.py
```
Open: http://127.0.0.1:5000

## Current engine
- Binance Futures public H4/H1 klines (no API key)
- H4 market structure: HH/HL, LH/LL, BOS
- H1 pivots / Swing High-Low
- Equal High / Equal Low clustering using ATR-based tolerance
- Previous Day High / Low (UTC day)
- Approximate Volume Profile from H1 OHLCV
- Zone clustering + 0–100 strength score
- Configurable qualification threshold

## Important
OHLCV Volume Profile is an approximation because candle volume is distributed across the candle price range. For production, replace it with an aggTrades-based profile. The result is probable liquidity, not an exact liquidation heatmap.
