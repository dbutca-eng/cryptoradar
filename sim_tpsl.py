"""TP/SL backtest on daily candles (default: BTCUSDT, 100 USD from 2026-01-01).

Usage: python3 sim_tpsl.py [candles.csv] [--start 2026-01-01] [--usd 100] [--symbol BTCUSDT]
Without a CSV, daily candles are fetched from the Binance public API.
CSV columns: date,open,high,low,close (date = YYYY-MM-DD, one row per day), or a
TradingView "Export chart data" file (time in unix seconds; regular candles, not Heikin Ashi).

Rules:
- Buy with all cash at the open of the start day; buy again at the next day's open
  after every full exit (TP2 or SL).
- SL = entry - 2 x ATR(14) (Signum's GC band is not available historically);
  R = entry - SL; TP1 = entry + 1.5R; TP2 = entry + 3R.
- TP1: sell 50% of the position. TP2: sell the rest. SL: sell whatever is left.
- If SL and a TP fall inside the same candle, SL is assumed first (conservative).
- Fee FEE per side on every buy/sell.
"""
import csv, json, sys, urllib.request, datetime as dt
ATRN, K, TP1R, TP2R, FEE = 14, 2.0, 1.5, 3.0, 0.001
a = sys.argv[1:]
def opt(n, d):
    if n in a: i = a.index(n); v = a[i+1]; del a[i:i+2]; return v
    return d
START, USD, SYM = opt('--start', '2026-01-01'), float(opt('--usd', 100)), opt('--symbol', 'BTCUSDT')

def binance(sym, start):
    t0 = int(dt.datetime.fromisoformat(start).replace(tzinfo=dt.timezone.utc).timestamp()*1000) - (ATRN+2)*86400000
    out = []
    while True:
        u = f"https://api.binance.com/api/v3/klines?symbol={sym}&interval=1d&startTime={t0}&limit=1000"
        k = json.load(urllib.request.urlopen(u, timeout=30))
        out += [{'date': dt.datetime.fromtimestamp(x[0]/1000, dt.timezone.utc).date().isoformat(),
                 'open': float(x[1]), 'high': float(x[2]), 'low': float(x[3]), 'close': float(x[4])} for x in k]
        if len(k) < 1000: return out
        t0 = k[-1][0] + 86400000

if a:
    def day(r):  # 'date' (YYYY-MM-DD) or TradingView export 'time' (unix seconds)
        return r['date'] if 'date' in r else dt.datetime.fromtimestamp(int(float(r['time'])), dt.timezone.utc).date().isoformat()
    c = [{'date': day(r), **{k: float(r[k]) for k in ('open', 'high', 'low', 'close')}} for r in csv.DictReader(open(a[0]))]
else:
    c = binance(SYM, START)
c.sort(key=lambda r: r['date'])
c = c[:-1] if c and c[-1]['date'] >= dt.date.today().isoformat() else c  # drop the still-open candle
# ATR (Wilder) up to and including each day
tr = [c[0]['high']-c[0]['low']] + [max(r['high']-r['low'], abs(r['high']-p['close']), abs(r['low']-p['close'])) for p, r in zip(c, c[1:])]
atr = [None]*len(c)
for i in range(ATRN-1, len(c)):
    atr[i] = sum(tr[:ATRN])/ATRN if i == ATRN-1 else (atr[i-1]*(ATRN-1)+tr[i])/ATRN
s = next(i for i, r in enumerate(c) if r['date'] >= START)
if s < 1 or atr[s-1] is None: sys.exit(f'need at least {ATRN+1} candles before {START}')

cash, qty, pos, log, trade = USD, 0.0, None, [], 0
def sell(q, px, d, why):
    global cash, qty
    cash += q*px*(1-FEE); qty -= q
    log.append((d, why, px, q, cash + qty*px))
for i in range(s, len(c)):
    r = c[i]
    if pos is None:  # enter at the open; levels from ATR of the previous closed candle
        e = r['open']; sl = e - K*atr[i-1]; R = e - sl
        qty = cash*(1-FEE)/e; cash = 0.0; trade += 1
        pos = {'e': e, 'sl': sl, 'tp1': e+TP1R*R, 'tp2': e+TP2R*R, 'half': False}
        log.append((r['date'], f'BUY #{trade}  SL {sl:,.0f}  TP1 {pos["tp1"]:,.0f}  TP2 {pos["tp2"]:,.0f}', e, qty, qty*e))
    if r['low'] <= pos['sl']:
        sell(qty, min(pos['sl'], r['open']), r['date'], 'SL'); pos = None; continue
    if not pos['half'] and r['high'] >= pos['tp1']:
        sell(qty/2, max(pos['tp1'], r['open']), r['date'], 'TP1 (50%)'); pos['half'] = True
    if r['high'] >= pos['tp2']:
        sell(qty, max(pos['tp2'], r['open']), r['date'], 'TP2 (rest)'); pos = None

last = c[-1]
final = cash + qty*last['close']
bh = USD*(1-FEE)/c[s]['open']*last['close']
for d, why, px, q, v in log:
    print(f"{d}  {why:<48} @ {px:>11,.2f}   value {v:>8.2f} USD")
print(f"\n{SYM} {c[s]['date']} -> {last['date']}: {USD:.0f} USD -> {final:.2f} USD ({(final/USD-1)*100:+.1f}%)"
      f"{'  [position still open]' if pos else ''}")
print(f"Buy & hold for comparison: {bh:.2f} USD ({(bh/USD-1)*100:+.1f}%)")
n = sum(1 for x in log if x[1] == 'SL'); t2 = sum(1 for x in log if x[1].startswith('TP2')); t1 = sum(1 for x in log if x[1].startswith('TP1'))
print(f"Trades: {trade} | TP1 hits: {t1} | TP2 hits: {t2} | SL exits: {n}")
