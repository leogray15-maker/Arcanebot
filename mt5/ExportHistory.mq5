//+------------------------------------------------------------------+
//| ExportHistory.mq5                                                 |
//| Exports XAUUSD candles from MT5 to a CSV in Arcanebot's format:   |
//|   timestamp,open,high,low,close,volume   (timestamp in UTC)       |
//|                                                                    |
//| Usage:                                                             |
//|  1. In MT5: open MetaEditor (F4). File > New > Script, paste this. |
//|  2. Compile (F7).                                                  |
//|  3. Open a XAUUSD chart, drag "ExportHistory" onto it from the     |
//|     Navigator > Scripts panel.                                     |
//|  4. Set the timeframe input and the server->UTC offset (see below).|
//|  5. The CSV lands in  MQL5/Files/  (MT5: File > Open Data Folder). |
//|     Move it into the bot's  data/  folder.                         |
//|                                                                    |
//| IMPORTANT — timezone: IC Markets' MT5 server time is usually       |
//| UTC+2 (winter) / UTC+3 (summer). The kill-zone logic needs UTC, so |
//| set InpServerToUTCOffsetHours to  -2  or  -3  accordingly. If you  |
//| are unsure, export with 0, run the backtest, and tell me — I can   |
//| detect the offset from the session activity and correct it.        |
//+------------------------------------------------------------------+
#property script_show_inputs
#property strict

input string        InpSymbol                = "XAUUSD";       // symbol (match your broker's exact name)
input ENUM_TIMEFRAMES InpTimeframe           = PERIOD_M5;      // M5 for entries (also export M15 separately if you like)
input int           InpBars                  = 300000;         // how many bars back (300k M5 ~ 3 years)
input int           InpServerToUTCOffsetHours = -3;            // add this to server time to get UTC (IC Markets: -2 winter / -3 summer)
input string        InpFileName              = "xauusd_m5.csv";// output file (in MQL5/Files/)

void OnStart()
{
   MqlRates rates[];
   ArraySetAsSeries(rates, true);
   int copied = CopyRates(InpSymbol, InpTimeframe, 0, InpBars, rates);
   if(copied <= 0)
   {
      PrintFormat("CopyRates failed for %s: error %d. Try opening the %s chart first "
                  "and scrolling back to load history.", InpSymbol, GetLastError(), InpSymbol);
      return;
   }

   int fh = FileOpen(InpFileName, FILE_WRITE | FILE_CSV | FILE_ANSI, ',');
   if(fh == INVALID_HANDLE)
   {
      PrintFormat("FileOpen failed for %s: error %d", InpFileName, GetLastError());
      return;
   }

   FileWrite(fh, "timestamp", "open", "high", "low", "close", "volume");

   int offset = InpServerToUTCOffsetHours * 3600;
   // Write oldest -> newest (loader expects ascending timestamps).
   for(int i = copied - 1; i >= 0; i--)
   {
      datetime utc = rates[i].time + offset;
      MqlDateTime dt;
      TimeToStruct(utc, dt);
      string ts = StringFormat("%04d-%02d-%02d %02d:%02d:00",
                               dt.year, dt.mon, dt.day, dt.hour, dt.min);
      FileWrite(fh, ts,
                DoubleToString(rates[i].open, _Digits),
                DoubleToString(rates[i].high, _Digits),
                DoubleToString(rates[i].low, _Digits),
                DoubleToString(rates[i].close, _Digits),
                (long)rates[i].tick_volume);
   }

   FileClose(fh);
   PrintFormat("Exported %d %s bars to MQL5/Files/%s (UTC offset %+d h). "
               "Move it into the bot's data/ folder.",
               copied, InpSymbol, InpFileName, InpServerToUTCOffsetHours);
}
//+------------------------------------------------------------------+
