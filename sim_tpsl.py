"""TP/SL backtest on daily candles (default: BTCUSDT, 100 USD from 2026-01-01).

Usage: python3 sim_tpsl.py [candles.csv] [--start 2026-01-01] [--end YYYY-MM-DD] [--usd 100]
         [--symbol BTCUSDT] [--breakeven] [--trend 20] [--trend-first] [--k 2] [--tp1 1.5]
         [--tp2 3] [--tp1-frac 0.5] [--risk 0.05] [--max-sl 0.15]
         [--regime 100] [--trend-exit]
Without a CSV, daily candles are fetched from the Binance public API.
CSV columns: date,open,high,low,close (date = YYYY-MM-DD, one row per day), or a
TradingView "Export chart data" file (time in unix seconds; regular candles, not Heikin Ashi).

Rules:
- Buy at the open of the start day; buy again at the next day's open after every full
  exit (TP2 or SL).
- SL = entry - k x ATR(14) (Signum's GC band is not available historically);
  R = entry - SL; TP1 = entry + tp1 x R; TP2 = entry + tp2 x R.
- TP1: sell tp1-frac of the position. TP2: sell the rest. SL: sell whatever is left.
- --breakeven: after TP1, move SL to the entry price (active from the next candle).
- --trend N: after an exit, buy again only once a daily close is above its N-day
  simple moving average (entry at the next open). --trend-first applies it to the first
  buy too.
- --regime N: only buy while the last close is above its N-day SMA (every entry,
  including the first).
- --trend-exit: sell the whole position at the close when the close falls below the
  --trend SMA.
- --risk F: size each position so that hitting the SL loses F of the account
  (capped at the whole account); without it every buy uses all the cash.
- --max-sl F: skip an entry whose SL is more than F below the entry price.
- If SL and a TP fall inside the same candle, SL is assumed first (conservative).
- Fee FEE per side on every buy/sell.
- Max drawdown is measured on the account value at every daily close.
"""
import csv, json, sys, urllib.request, datetime as dt
ATRN, FEE = 14, 0.001
DEFAULTS = dict(k=2.0, tp1=1.5, tp2=3.0, tp1_frac=0.5, be=False, ma=0, ma_first=False, risk=None, max_sl=None, regime=0, trend_exit=False)

def load_csv(path):
    def day(r):  # 'date' (YYYY-MM-DD) or TradingView export 'time' (unix seconds)
        return r['date'] if 'date' in r else dt.datetime.fromtimestamp(int(float(r['time'])), dt.timezone.utc).date().isoformat()
    c = [{'date': day(r), **{k: float(r[k]) for k in ('open', 'high', 'low', 'close')}} for r in csv.DictReader(open(path))]
    return prepare(c)

def binance(sym, start):
    t0 = int(dt.datetime.fromisoformat(start).replace(tzinfo=dt.timezone.utc).timestamp()*1000) - 60*86400000
    out = []
    while True:
        u = f"https://api.binance.com/api/v3/klines?symbol={sym}&interval=1d&startTime={t0}&limit=1000"
        k = json.load(urllib.request.urlopen(u, timeout=30))
        out += [{'date': dt.datetime.fromtimestamp(x[0]/1000, dt.timezone.utc).date().isoformat(),
                 'open': float(x[1]), 'high': float(x[2]), 'low': float(x[3]), 'close': float(x[4])} for x in k]
        if len(k) < 1000: return prepare(out)
        t0 = k[-1][0] + 86400000

def prepare(c):
    c.sort(key=lambda r: r['date'])
    if c and c[-1]['date'] >= dt.date.today().isoformat(): c = c[:-1]  # drop the still-open candle
    # ATR (Wilder) up to and including each day
    tr = [c[0]['high']-c[0]['low']] + [max(r['high']-r['low'], abs(r['high']-p['close']), abs(r['low']-p['close'])) for p, r in zip(c, c[1:])]
    atr = [None]*len(c)
    for i in range(ATRN-1, len(c)):
        atr[i] = sum(tr[:ATRN])/ATRN if i == ATRN-1 else (atr[i-1]*(ATRN-1)+tr[i])/ATRN
    for r, x in zip(c, atr): r['atr'] = x
    return c

def simulate(c, start, usd=100.0, end=None, **p):
    p = {**DEFAULTS, **p}; ma = p['ma']
    s = next(i for i, r in enumerate(c) if r['date'] >= start)
    if s < ATRN + 1: raise ValueError(f'need at least {ATRN+1} candles before {start}')
    n = next((i for i, r in enumerate(c) if end and r['date'] > end), len(c))
    cum = [0.0]
    for r in c: cum.append(cum[-1] + r['close'])
    cash, qty, pos, log, trade = usd, 0.0, None, [], 0
    peak, mdd, mdd_at = usd, 0.0, None
    def sell(q, px, d, why):
        nonlocal cash, qty
        cash += q*px*(1-FEE); qty -= q
        log.append((d, why, px, cash + qty*px))
    for i in range(s, n):
        r = c[i]
        if pos is None:
            sma = lambda n: (cum[i]-cum[max(0, i-n)])/(i-max(0, i-n))  # SMA of the closes before day i (available history)
            ok = not ma or not (trade or p['ma_first']) or c[i-1]['close'] > sma(ma)
            ok = ok and (not p['regime'] or c[i-1]['close'] > sma(p['regime']))  # regime applies to every entry
            e = r['open']; sl = e - p['k']*c[i-1]['atr']; R = e - sl  # ATR of the previous closed candle
            if ok and not (p['max_sl'] and R/e > p['max_sl']):
                eq = cash
                spend = eq if not p['risk'] else min(eq, eq*p['risk']/(R/e))
                qty = spend*(1-FEE)/e; cash -= spend; trade += 1
                pos = {'e': e, 'sl': sl, 'tp1': e+p['tp1']*R, 'tp2': e+p['tp2']*R, 'half': False}
                log.append((r['date'], f'BUY #{trade}  SL {px(sl)}  TP1 {px(pos["tp1"])}  TP2 {px(pos["tp2"])}'
                            + (f'  ({spend/eq*100:.0f}% of account)' if p['risk'] else ''), e, cash + qty*e))
        if pos and r['low'] <= pos['sl']:
            sell(qty, min(pos['sl'], r['open']), r['date'], 'SL (breakeven)' if pos['half'] and p['be'] else 'SL'); pos = None
        if pos and not pos['half'] and r['high'] >= pos['tp1']:
            sell(qty*p['tp1_frac'], max(pos['tp1'], r['open']), r['date'], f'TP1 ({p["tp1_frac"]*100:.0f}%)'); pos['half'] = True
            if p['be']: pos['sl'] = pos['e']
        if pos and r['high'] >= pos['tp2']:
            sell(qty, max(pos['tp2'], r['open']), r['date'], 'TP2 (rest)'); pos = None
        if pos and p['trend_exit'] and ma:  # close below the trend SMA: sell everything at that close
            j = max(0, i+1-ma)
            if r['close'] < (cum[i+1]-cum[j])/(i+1-j):
                sell(qty, r['close'], r['date'], f'EXIT (close < SMA{ma})'); pos = None
        v = cash + qty*r['close']
        peak = max(peak, v)
        if 1 - v/peak > mdd: mdd, mdd_at = 1 - v/peak, r['date']
    last = c[n-1]
    final = cash + qty*last['close']
    return dict(final=final, ret=final/usd - 1, mdd=mdd, mdd_at=mdd_at, log=log, open=pos is not None, trades=trade,
                first=c[s]['date'], last=last['date'], bh=usd*(1-FEE)/c[s]['open']*last['close'])

def px(x): return f'{x:,.0f}' if x >= 1000 else (f'{x:,.2f}' if x >= 1 else f'{x:.5g}')

if __name__ == '__main__':
    a = sys.argv[1:]
    def opt(n, d, f=str):
        if n in a: i = a.index(n); v = a[i+1]; del a[i:i+2]; return f(v)
        return d
    def flag(n):
        global a
        on = n in a; a = [x for x in a if x != n]; return on
    p = dict(be=flag('--breakeven'), ma_first=flag('--trend-first'), ma=opt('--trend', 0, int), k=opt('--k', 2.0, float),
             tp1=opt('--tp1', 1.5, float), tp2=opt('--tp2', 3.0, float), tp1_frac=opt('--tp1-frac', 0.5, float),
             risk=opt('--risk', None, float), max_sl=opt('--max-sl', None, float),
             regime=opt('--regime', 0, int), trend_exit=flag('--trend-exit'))
    START, END, USD, SYM = opt('--start', '2026-01-01'), opt('--end', None), opt('--usd', 100.0, float), opt('--symbol', 'BTCUSDT')
    c = load_csv(a[0]) if a else binance(SYM, START)
    r = simulate(c, START, USD, END, **p)
    for d, why, pr, v in r['log']:
        print(f"{d}  {why:<62} @ {px(pr):>11}   value {v:>8.2f} USD")
    t = lambda k: sum(1 for x in r['log'] if x[1].startswith(k))
    print(f"\n{SYM} {r['first']} -> {r['last']}: {USD:.0f} USD -> {r['final']:.2f} USD ({r['ret']*100:+.1f}%)"
          f"{'  [position still open]' if r['open'] else ''}")
    print(f"Max drawdown: {r['mdd']*100:.1f}% (on {r['mdd_at']})")
    print(f"Buy & hold for comparison: {r['bh']:.2f} USD ({(r['bh']/USD-1)*100:+.1f}%)")
    print(f"Trades: {r['trades']} | TP1 hits: {t('TP1')} | TP2 hits: {t('TP2')} | SL exits: {t('SL')}")
