# MT5 integration

Two separate things live here:

## 1. Export real data from MT5 (`ExportHistory.mq5`) — do this now

This is how you "connect to MT5" for **backtesting**: pull your broker's real
XAUUSD candles into a CSV the bot can read.

**Steps**
1. In MT5, press **F4** to open MetaEditor.
2. **File → New → Script**, name it `ExportHistory`, and paste the contents of
   `ExportHistory.mq5`. Press **F7** to compile (should say "0 errors").
3. Back in MT5, open a **XAUUSD** chart. In the **Navigator** panel, expand
   **Scripts**, and drag **ExportHistory** onto the chart.
4. In the inputs dialog:
   - `InpTimeframe` = `PERIOD_M5`
   - `InpServerToUTCOffsetHours` = **-3** (IC Markets summer) or **-2** (winter).
     Not sure? Leave it and tell me — I can detect and correct the offset from
     session activity.
5. Click OK. The CSV appears in **MQL5/Files/** (MT5: **File → Open Data
   Folder** → `MQL5/Files`). Move `xauusd_m5.csv` into the bot's `data/` folder.
6. Optionally repeat with `PERIOD_M15` → `xauusd_m15.csv` (otherwise the bot
   resamples M15 from M5 automatically).

**Then run the real backtest:**
```bash
python3 -m arcanebot.backtest.run --m5 data/xauusd_m5.csv --serve
```
and the walk-forward optimiser:
```bash
python3 -m arcanebot.backtest.optimize --m5 data/xauusd_m5.csv --mode walk --folds 5
```

## 2. Live/demo execution (later — locked until the backtest is proven)

Live trading runs through **MetaApi** (`arcanebot/execution/metaapi_adapter.py`),
which bridges to an MT5 **demo** account. It is intentionally gated: it refuses
to run until you pass `approved=True`, set `TRADING_MODE=demo`, and provide
credentials in `.env`. There is no point wiring this up before the strategy has
shown a real, out-of-sample edge on the data from step 1 — demo-trading an
unproven strategy just burns time.
