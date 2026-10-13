"""Crypto Trend Radar daily report builder.

Usage: python3 build_report.py <trendradar-daily.json> [out.html]
Input: saved output of Signum get-trendradar-daily (assetClass=crypto, detector=gc,
indicatorFields=["bo","data.gc"], limit=100). Writes the HTML email body WITHOUT the
analyst conclusion / disclaimer (append those) and prints a JSON summary to stderr.
"""
import json, sys, datetime as dt, html
F=sys.argv[1]
d=json.load(open(F))
rows=[r for r in d['results'] if r.get('marketRank') is not None and r['marketRank']<=100]
run=rows[0]['runDate']; RD=dt.date.fromisoformat(run)
def days(s): return (RD-dt.date.fromisoformat(s)).days if s else None
def link(r):
    ex,pair=r['symbol'].split(':',1)
    return f"https://www.tradingview.com/symbols/{pair}/?exchange={ex}"
def coin(r):
    name=html.escape(r['baseCoin'] or r['symbol'].split(':')[1])
    return f'<a href="{link(r)}"><b>{name}</b></a>'
pos=[r for r in rows if r['trendTo']=='Green' and r['trendLongEntryStatus']=='OPEN']
neg=[r for r in rows if r['trendTo']=='Red' and r['trendLongEntryStatus']=='CLOSED']
recent=lambda r: days(r['trendChanged']) is not None and days(r['trendChanged'])<=7
# allocation
elig=[r for r in pos if (r['trendLongEntryPnlPct'] or 0)>=0 and days(r['trendLongEntryStatusDate'])>=3]
watch=[r for r in pos if r not in elig]
for r in elig: r['score']=max(r['trendLongEntryPnlPct'],0)+(101-r['marketRank'])*0.5
elig.sort(key=lambda r:-r['score'])
TOT,MIN,CAP=1000,50,300
def alloc(sel):
    w={id(r):r['score'] for r in sel}; fixed={}
    while True:
        free=TOT-sum(fixed.values()); rest=[r for r in sel if id(r) not in fixed]
        s=sum(w[id(r)] for r in rest)
        a={id(r):free*w[id(r)]/s for r in rest}
        over=[r for r in rest if a[id(r)]>CAP]
        if not over: a.update(fixed); return a
        for r in over: fixed[id(r)]=CAP
N=len(elig)
while N>0:
    sel=elig[:N]; a=alloc(sel)
    if min(a.values())>=MIN-1e-9: break
    N-=1
# integer rounding (largest remainder)
fl={k:int(v) for k,v in a.items()}
rem=TOT-sum(fl.values())
for k in sorted(a,key=lambda k:-(a[k]-int(a[k])))[:rem]: fl[k]+=1
notsel=elig[N:]
# TP/SL per asset: entry = refPriceUsd (last closed close); SL = GC lower band of the
# last closed candle (trend invalidation), falling back to breakout support below entry;
# R = entry - SL; TP1 = entry + 1.5R, TP2 = entry + 3R.
RISKMAX=20
def tpsl(r):
    ind=r.get('indicators') or {}; e=ind.get('refPriceUsd')
    if not e: return None
    c=sorted(ind.get('data') or [],key=lambda c:c['date'])
    lo=c[-1]['gc']['lower'] if c and c[-1].get('gc') else None
    sl,src=(lo,'banda GC') if lo and lo<e else ((r['breakoutSupport'],'support breakout') if r['breakoutSupport'] and r['breakoutSupport']<e else (None,None))
    if sl is None: return None
    R=e-sl
    return {'e':e,'sl':sl,'src':src,'risk':R/e*100,'tp1':e+1.5*R,'tp2':e+3*R}
for r in rows: r['tpsl']=tpsl(r)
def pct(x): return f"{x:+.1f}%" if x is not None else "—"
def bo(r):
    if r['breakoutInvalidatedAt']: return f'<font color="#c0392b">invalidat ({r["breakoutInvalidatedAt"]})</font>'
    if r['breakoutValidatedAt']: return f'<font color="#1e8449">validat ({r["breakoutValidatedAt"]})</font>'
    return '<font color="#888">neconfirmat</font>'
def num(x):
    if x is None: return "—"
    return f"{x:,.2f}" if x>=1 else f"{x:.6g}"
def table(head,body):
    return '<table border="1" cellpadding="4" cellspacing="0" width="100%" style="border-collapse:collapse;border-color:#eaecf0;font-size:12px"><tr>'+''.join(f'<th bgcolor="#f2f4f7" align="left">{h}</th>' for h in head)+'</tr>'+''.join('<tr>'+''.join(f'<td>{c}</td>' for c in row)+'</tr>' for row in body)+'</table>'
btc=next(r for r in rows if r['baseCoin']=='BTC'); eth=next(r for r in rows if r['baseCoin']=='ETH')
def ctx(r):
    st='trend pozitiv (Green, semnal OPEN)' if r['trendTo']=='Green' else 'trend negativ (Red)'
    return f"<b>{r['baseCoin']}</b>: {st} din {r['trendChanged']}, PnL semnal {pct(r['trendLongEntryPnlPct'])}, breakout {bo(r)}, preț ref. ${num(r['indicators']['refPriceUsd'])}"
X,Y=len(pos),len(neg)
subj=f"Crypto Trend Radar – {run} | {X} pozitive · {Y} negative"
o=[]
o.append(f'<p style="font-family:Arial,Helvetica,sans-serif;font-size:18px;margin:0 0 4px"><b>{subj}</b></p>')
o.append('<div style="font-family:Arial,Helvetica,sans-serif;color:#1d2939;max-width:900px">')
o.append(f'<p style="color:#667085;margin:0 0 16px">Signum Trend Radar · detector GC · interval 1D · top 100 după capitalizare · rulare {run}</p>')
# summary
gpct=X/len(rows)*100
o.append('<h3>Sumar</h3><ul style="font-size:13px;line-height:1.6">')
o.append(f'<li><b>{X}</b> semnale pozitive (Green/OPEN) și <b>{Y}</b> negative (Red/CLOSED) din {len(rows)} monede analizate ({gpct:.0f}% în trend pozitiv).</li>')
o.append(f'<li>{ctx(btc)}</li><li>{ctx(eth)}</li>')
o.append(f'<li>Semnale pozitive cu PnL negativ (în observație): {len(watch)}. Alocare construită pe {N} poziții eligibile.</li></ul>')
# changes 7 days
ch=[r for r in rows if recent(r)]
o.append('<h3>Schimbări de trend în ultimele 7 zile</h3>')
if ch:
    o.append(table(['Monedă','Rank','Schimbare','Data','Zile'],[[coin(r),r['marketRank'],f"{r['trendFrom']} → {r['trendTo']}",r['trendChanged'],days(r['trendChanged'])] for r in sorted(ch,key=lambda r:r['trendChanged'],reverse=True)]))
else: o.append('<p style="font-size:13px">Nicio schimbare.</p>')
near=[r for r in rows if not recent(r) and days(r['trendChanged'])<=9]
if near: o.append('<p style="font-size:12px;color:#667085">Imediat în afara ferestrei: '+', '.join(f"{r['baseCoin']} ({r['trendFrom']}→{r['trendTo']}, {r['trendChanged']})" for r in near)+'.</p>')
# positive table
o.append(f'<h3 style="color:#1e8449">Trend pozitiv ({X})</h3>')
o.append(table(['Monedă','Rank','Trend din','PnL semnal','Support / Rezistență breakout','Stare breakout'],
 [[coin(r)+(' 🆕' if recent(r) else ''),r['marketRank'],r['trendChanged'],f'<font color="{"#1e8449" if (r["trendLongEntryPnlPct"] or 0)>=0 else "#c0392b"}">{pct(r["trendLongEntryPnlPct"])}</font>',f"{num(r['breakoutSupport'])} / {num(r['breakoutResistance'])}",bo(r)] for r in pos]))
# allocation
o.append('<h3>Alocare ipotetică 1000 USD</h3><p style="font-size:12px;color:#667085">Scor = max(PnL, 0) + (101 − rank) × 0,5; alocare proporțională, minim 50 USD/poziție, maxim 30%/monedă. Excluse: PnL negativ sau semnal activ &lt; 3 zile. Minimul de 50 USD limitează portofoliul la primele '+str(N)+' scoruri.</p>')
def why(r):
    p=r['trendLongEntryPnlPct']; parts=[]
    if r['marketRank']<=10: parts.append('large cap')
    if p>=100: parts.append(f'momentum puternic ({pct(p)})')
    elif p>=40: parts.append(f'PnL solid ({pct(p)})')
    else: parts.append(f'PnL {pct(p)}')
    if r['breakoutValidatedAt'] and not r['breakoutInvalidatedAt']: parts.append('breakout validat')
    if r['breakoutInvalidatedAt']: parts.append('atenție: breakout invalidat')
    if fl[id(r)]>=CAP: parts.append('plafonat la 30%')
    return f"scor {r['score']:.1f} · "+', '.join(parts)
o.append(table(['Monedă','USD','%','Justificare'],[[coin(r),f"${fl[id(r)]}",f"{fl[id(r)]/10:.1f}%",why(r)] for r in sel]+[['<b>Total</b>',f"<b>${sum(fl.values())}</b>",'<b>100%</b>','']]))
# TP/SL
o.append('<h3>Take Profit / Stop Loss</h3><p style="font-size:12px;color:#667085">Intrare = prețul de închidere al ultimei lumânări 1D. SL = banda inferioară GC (nivelul care invalidează trendul; dacă lipsește, support-ul de breakout). R = intrare − SL; TP1 = intrare + 1,5R, TP2 = intrare + 3R. Risc USD = suma alocată × distanța până la SL. ⚠ = SL mai departe de '+str(RISKMAX)+'%.</p>')
def tsrow(r):
    t=r['tpsl']; u=fl.get(id(r))
    if not t: return [coin(r),'—','—','—','—','—','—']
    return [coin(r),'$'+num(t['e']),f'<font color="#c0392b">${num(t["sl"])}</font> ({-t["risk"]:.1f}%{" ⚠" if t["risk"]>RISKMAX else ""})',t['src'],f'<font color="#1e8449">${num(t["tp1"])}</font> (+{1.5*t["risk"]:.1f}%)',f'<font color="#1e8449">${num(t["tp2"])}</font> (+{3*t["risk"]:.1f}%)',f"${u*t['risk']/100:.0f}" if u else '—']
rk=sum(fl[id(r)]*r['tpsl']['risk']/100 for r in sel if r['tpsl'])
o.append(table(['Monedă','Intrare','SL','Sursă SL','TP1 (1,5R)','TP2 (3R)','Risc USD'],[tsrow(r) for r in sel]+[['<b>Total</b>','','','','','',f'<b>${rk:.0f}</b> ({rk/TOT*100:.1f}%)']]))
if watch: o.append('<p style="font-size:12px"><b>În observație (excluse):</b> '+', '.join(f"{r['baseCoin']} ({pct(r['trendLongEntryPnlPct'])})" for r in watch)+'.</p>')
if notsel: o.append('<p style="font-size:12px;color:#667085"><b>Eligibile dar sub prag (scor mai mic):</b> '+', '.join(r['baseCoin'] for r in notsel[:10])+(f' și alte {len(notsel)-10}' if len(notsel)>10 else '')+'.</p>')
# negative
o.append(f'<h3 style="color:#c0392b">Trend negativ (primele 10 după rank, din {Y})</h3>')
o.append(table(['Monedă','Rank','Schimbat la'],[[coin(r),r['marketRank'],r['trendChanged']] for r in sorted(neg,key=lambda r:r['marketRank'])[:10]]))
print(json.dumps({'subj':subj,'N':N,'alloc':[(r['baseCoin'],fl[id(r)],round(r['score'],1)) for r in sel],'watch':[r['baseCoin'] for r in watch],'ch':[r['baseCoin'] for r in ch],'X':X,'Y':Y,'tpsl':{r['baseCoin']:{k:round(v,6) if isinstance(v,float) else v for k,v in r['tpsl'].items()} for r in sel if r['tpsl']}}),file=sys.stderr)
open(sys.argv[2] if len(sys.argv)>2 else 'body.html','w').write('\n'.join(o))
