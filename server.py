from flask import Flask, jsonify, send_from_directory, request
import urllib.request, urllib.parse, json, math, statistics
from datetime import datetime, timezone

app = Flask(__name__, static_folder='.')
BASE='https://fapi.binance.com'

def get_json(path, params=None):
    url=BASE+path
    if params: url += '?' + urllib.parse.urlencode(params)
    req=urllib.request.Request(url, headers={'User-Agent':'LiquidityEngine/1.0'})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode())

def klines(symbol, interval, limit):
    raw=get_json('/fapi/v1/klines', {'symbol':symbol,'interval':interval,'limit':limit})
    return [{'t':x[0],'o':float(x[1]),'h':float(x[2]),'l':float(x[3]),'c':float(x[4]),'v':float(x[5])} for x in raw]

def atr(c, n=14):
    trs=[]
    for i in range(1,len(c)):
        trs.append(max(c[i]['h']-c[i]['l'], abs(c[i]['h']-c[i-1]['c']), abs(c[i]['l']-c[i-1]['c'])))
    return sum(trs[-n:])/min(n,len(trs)) if trs else 0

def pivots(c, n=3):
    highs=[]; lows=[]
    for i in range(n,len(c)-n):
        h=c[i]['h']; l=c[i]['l']
        if h==max(x['h'] for x in c[i-n:i+n+1]): highs.append({'i':i,'price':h,'t':c[i]['t']})
        if l==min(x['l'] for x in c[i-n:i+n+1]): lows.append({'i':i,'price':l,'t':c[i]['t']})
    return highs,lows

def h4_structure(c):
    hs,ls=pivots(c,3); hs=hs[-3:]; ls=ls[-3:]
    trend='RANGE'; structure='MIXED'; bos='NONE'
    if len(hs)>=2 and len(ls)>=2:
        if hs[-1]['price']>hs[-2]['price'] and ls[-1]['price']>ls[-2]['price']:
            trend='BULLISH'; structure='HH/HL'
        elif hs[-1]['price']<hs[-2]['price'] and ls[-1]['price']<ls[-2]['price']:
            trend='BEARISH'; structure='LH/LL'
        last=c[-1]['c']
        if last>hs[-2]['price']: bos='BULLISH'
        elif last<ls[-2]['price']: bos='BEARISH'
    return {'trend':trend,'structure':structure,'bos':bos,'swing_highs':hs,'swing_lows':ls}

def previous_day_levels(c):
    # derive UTC day groups from H1 candles; last completed day
    groups={}
    for x in c:
        d=datetime.fromtimestamp(x['t']/1000, timezone.utc).date().isoformat()
        groups.setdefault(d,[]).append(x)
    days=sorted(groups)
    if len(days)<2: return None,None
    prev=groups[days[-2]]
    return max(x['h'] for x in prev), min(x['l'] for x in prev)

def volume_profile(c, bins=60):
    lo=min(x['l'] for x in c); hi=max(x['h'] for x in c)
    step=(hi-lo)/bins if hi>lo else 1
    vols=[0.0]*bins
    # Approximation: distribute each candle's volume uniformly across bins crossed by candle.
    for x in c:
        a=max(0,min(bins-1,int((x['l']-lo)/step))); b=max(0,min(bins-1,int((x['h']-lo)/step)))
        count=b-a+1
        for j in range(a,b+1): vols[j]+=x['v']/count
    med=statistics.median(vols) if vols else 0
    nodes=[]
    for j,v in enumerate(vols):
        if med and v>=med*1.6:
            nodes.append({'low':lo+j*step,'high':lo+(j+1)*step,'volume':v,'ratio':v/med})
    return nodes, {'low':lo,'high':hi,'step':step,'bins':bins,'volumes':vols}

def cluster_levels(levels, tol):
    if not levels: return []
    levels=sorted(levels,key=lambda x:x['price'])
    clusters=[]
    for lv in levels:
        if not clusters or lv['price']-clusters[-1]['max_price']>tol:
            clusters.append({'items':[lv],'min_price':lv['price'],'max_price':lv['price']})
        else:
            cl=clusters[-1]; cl['items'].append(lv); cl['min_price']=min(cl['min_price'],lv['price']); cl['max_price']=max(cl['max_price'],lv['price'])
    return clusters

def analyze(symbol='BTCUSDT', threshold=60):
    h4=klines(symbol,'4h',220); h1=klines(symbol,'1h',500)
    price=h1[-1]['c']; a=atr(h1); tol=max(a*0.18, price*0.00025)
    struct=h4_structure(h4)
    highs,lows=pivots(h1,3); pdh,pdl=previous_day_levels(h1); vp_nodes,vp=volume_profile(h1[-240:],60)

    levels=[]
    # recent swing candidates
    for x in highs[-35:]: levels.append({'price':x['price'],'kind':'SWING_HIGH','side':'BUY','weight':15})
    for x in lows[-35:]: levels.append({'price':x['price'],'kind':'SWING_LOW','side':'SELL','weight':15})
    if pdh: levels.append({'price':pdh,'kind':'PDH','side':'BUY','weight':10})
    if pdl: levels.append({'price':pdl,'kind':'PDL','side':'SELL','weight':10})

    # Equal high/low: reward repeated pivots inside tolerance
    for arr,side,kind in [(highs[-35:],'BUY','EQH'),(lows[-35:],'SELL','EQL')]:
        cs=cluster_levels([{'price':x['price']} for x in arr],tol)
        for cl in cs:
            if len(cl['items'])>=2:
                levels.append({'price':(cl['min_price']+cl['max_price'])/2,'kind':kind,'side':side,'weight':min(20,10+3*len(cl['items'])),'touches':len(cl['items'])})

    # Add HVN centers as neutral evidence; side resolved by cluster location
    for n in vp_nodes:
        levels.append({'price':(n['low']+n['high'])/2,'kind':'HVN','side':'NEUTRAL','weight':min(20,8+n['ratio']*3),'vp_low':n['low'],'vp_high':n['high']})

    raw_clusters=cluster_levels(levels, max(tol, vp['step']*1.5))
    zones=[]
    for cl in raw_clusters:
        items=cl['items']; center=sum(x['price'] for x in items)/len(items)
        sides=[x['side'] for x in items if x['side']!='NEUTRAL']
        side = ('BUY' if center>price else 'SELL') if not sides else max(set(sides), key=sides.count)
        # only keep logically placed liquidity
        if side=='BUY' and center<price: continue
        if side=='SELL' and center>price: continue
        kinds={x['kind'] for x in items}
        score=0
        score += min(20, sum(x.get('weight',0) for x in items if x['kind'] in ('EQH','EQL')))
        score += 15 if any(x['kind'].startswith('SWING') for x in items) else 0
        score += 10 if ('PDH' in kinds or 'PDL' in kinds) else 0
        score += min(20, sum(x.get('weight',0) for x in items if x['kind']=='HVN'))
        # confluence bonus capped; structure alignment is contextual, not directional entry signal
        score += min(15, max(0,(len(kinds)-1)*5))
        if (struct['trend']=='BULLISH' and side=='BUY') or (struct['trend']=='BEARISH' and side=='SELL'): score += 10
        score=min(100,round(score))
        pad=max(tol*0.55, vp['step']*0.7)
        zones.append({'type':'BUY_SIDE' if side=='BUY' else 'SELL_SIDE','low':cl['min_price']-pad,'high':cl['max_price']+pad,'center':center,'distance_pct':abs(center-price)/price*100,'strength':score,'evidence':sorted(kinds),'count':len(items),'qualified':score>=threshold})
    zones=sorted(zones,key=lambda z:(not z['qualified'],-z['strength'],z['distance_pct']))[:12]
    return {'symbol':symbol,'price':price,'atr_h1':a,'threshold':threshold,'h4_structure':struct,'pdh':pdh,'pdl':pdl,'zones':zones,'note':'Volume Profile is approximated from H1 OHLCV; use aggTrades for a more precise traded-at-price profile.'}

@app.route('/')
def index(): return send_from_directory('.', 'index.html')
@app.route('/api/analyze')
def api_analyze():
    try:
        symbol=request.args.get('symbol','BTCUSDT').upper().strip()
        threshold=max(0,min(100,int(request.args.get('threshold','60'))))
        return jsonify(analyze(symbol,threshold))
    except Exception as e:
        return jsonify({'error':str(e)}),500

if __name__=='__main__': app.run(host='127.0.0.1',port=5000,debug=False)
