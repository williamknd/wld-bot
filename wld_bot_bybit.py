"""
WLD Pivot Point Mean Reversion Bot - Bybit + Dashboard
Timeframe: 5min | Stop Loss: 1% | Hold: 60 candles
"""
import os, time, logging, json, threading
from datetime import datetime
from pybit.unified_trading import HTTP
from http.server import HTTPServer, BaseHTTPRequestHandler

# ── CONFIG ──────────────────────────────────────────
API_KEY       = os.environ.get("API_KEY", "")
API_SECRET    = os.environ.get("API_SECRET", "")
TESTNET       = os.environ.get("TESTNET", "true").lower() == "true"
SYMBOL        = "WLDUSDT"
CATEGORY      = "linear"
TIMEFRAME     = "5"
QTY           = os.environ.get("QTY", "2")
LEVERAGE      = int(os.environ.get("LEVERAGE", "1"))
STOP_LOSS_PCT = 0.01   # 1%
MAX_CANDLES   = 60
PIVOT_PERIOD  = 48
LOOP_INTERVAL = int(os.environ.get("LOOP_INTERVAL", "30"))
PORT          = int(os.environ.get("PORT", "8080"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("WLDBot")

if not API_KEY or not API_SECRET:
    log.error("API_KEY e API_SECRET nao configurados!")
    exit(1)

session = HTTP(testnet=TESTNET, api_key=API_KEY, api_secret=API_SECRET)

# ── ESTADO GLOBAL ────────────────────────────────────
state = {
    "status": "AGUARDANDO",
    "price": 0,
    "pivot": 0,
    "signal": "NENHUM",
    "position": None,
    "trades": [],
    "wins": 0,
    "losses": 0,
    "candles_held": 0,
    "last_update": "",
    "testnet": TESTNET,
    "symbol": SYMBOL,
    "recent_candles": []
}

# ── ESTRATÉGIA ───────────────────────────────────────
def calc_pivot(highs, lows, closes, period=48):
    pivots = [0.0] * len(closes)
    for i in range(period, len(closes)):
        h = max(highs[i-period:i])
        l = min(lows[i-period:i])
        c = closes[i-1]
        pivots[i] = (h + l + c) / 3
    return pivots

def get_signal(closes, pivots):
    if len(closes) == 0 or len(pivots) == 0: return 0
    i = len(closes) - 1
    if i >= len(pivots): return 0
    if pivots[i] <= 0: return 0
    if closes[i] < pivots[i]: return 1   # LONG — preço abaixo do pivot
    if closes[i] > pivots[i]: return -1  # SHORT — preço acima do pivot
    return 0

def fetch_candles(limit=250):
    resp = session.get_kline(category=CATEGORY, symbol=SYMBOL, interval=TIMEFRAME, limit=limit)
    candles = list(reversed(resp["result"]["list"]))
    opens  = [float(c[1]) for c in candles]
    highs  = [float(c[2]) for c in candles]
    lows   = [float(c[3]) for c in candles]
    closes = [float(c[4]) for c in candles]
    times  = [int(c[0])   for c in candles]
    state["recent_candles"] = [{"o": float(c[1]), "c": float(c[4]), "h": float(c[2]), "l": float(c[3])} for c in candles[-20:]]
    return opens, highs, lows, closes, times

def get_last_price():
    return float(session.get_tickers(category=CATEGORY, symbol=SYMBOL)["result"]["list"][0]["lastPrice"])

def get_position():
    for p in session.get_positions(category=CATEGORY, symbol=SYMBOL)["result"]["list"]:
        if float(p["size"]) > 0: return p
    return None

def set_leverage():
    try:
        session.set_leverage(category=CATEGORY, symbol=SYMBOL,
                             buyLeverage=str(LEVERAGE), sellLeverage=str(LEVERAGE))
    except: pass

def open_position(side, price):
    sl = round(price * (1 - STOP_LOSS_PCT if side == "Buy" else 1 + STOP_LOSS_PCT), 4)
    try:
        session.place_order(category=CATEGORY, symbol=SYMBOL, side=side,
                            orderType="Market", qty=QTY, stopLoss=str(sl), timeInForce="GTC")
        log.info(f"ABRIU {side} | Preco: {price} | SL: {sl}")
        state["trades"].append({"type": side, "entry": price, "sl": sl,
                                 "time": datetime.now().strftime("%H:%M:%S"),
                                 "result": "ABERTA", "exit": None, "pnl": None})
        state["position"] = {"side": side, "entry": price, "sl": sl}
        state["status"]   = "LONG ATIVO" if side == "Buy" else "SHORT ATIVO"
        return True
    except Exception as e:
        log.error(f"Erro ao abrir: {e}")
        return False

def close_position(position):
    side = "Sell" if position["side"] == "Buy" else "Buy"
    try:
        price = get_last_price()
        session.place_order(category=CATEGORY, symbol=SYMBOL, side=side,
                            orderType="Market", qty=position["size"], reduceOnly=True)
        entry = state["position"]["entry"] if state["position"] else price
        pnl   = (price - entry) if position["side"] == "Buy" else (entry - price)
        won   = pnl > 0
        if won: state["wins"] += 1
        else:   state["losses"] += 1
        if state["trades"]:
            state["trades"][-1]["result"] = "WIN" if won else "LOSS"
            state["trades"][-1]["exit"]   = price
            state["trades"][-1]["pnl"]    = round(pnl, 4)
        state["position"] = None
        state["status"]   = "AGUARDANDO"
        log.info(f"FECHOU | {'WIN' if won else 'LOSS'} | PnL: {pnl:.4f}")
        return True
    except Exception as e:
        log.error(f"Erro ao fechar: {e}")
        return False

# ── DASHBOARD HTML ───────────────────────────────────
DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>WLD Bot Dashboard</title>
<link href="https://fonts.googleapis.com/css2?family=Share+Tech+Mono&family=Exo+2:wght@300;600;800&display=swap" rel="stylesheet">
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#050a0f;color:#e0f0ff;font-family:'Exo 2',sans-serif;min-height:100vh;padding:20px}
body::before{content:'';position:fixed;inset:0;background:radial-gradient(ellipse at 20% 50%,#0a1628 0%,#050a0f 60%);z-index:-1}
h1{font-size:1.8rem;font-weight:800;letter-spacing:4px;text-transform:uppercase;color:#f59e0b;text-shadow:0 0 20px #f59e0b55;margin-bottom:4px}
.subtitle{font-family:'Share Tech Mono';font-size:.75rem;color:#4a7a9b;letter-spacing:2px;margin-bottom:24px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-bottom:20px}
.card{background:#0a1628;border:1px solid #0d2540;border-radius:12px;padding:16px;position:relative;overflow:hidden}
.card::before{content:'';position:absolute;top:0;left:0;right:0;height:2px;background:linear-gradient(90deg,transparent,#f59e0b44,transparent)}
.card-label{font-size:.65rem;letter-spacing:2px;text-transform:uppercase;color:#4a7a9b;margin-bottom:8px}
.card-value{font-family:'Share Tech Mono';font-size:1.5rem;font-weight:700;color:#f59e0b}
.card-value.green{color:#00ff88}.card-value.red{color:#ff4466}.card-value.yellow{color:#ffd700}
.winrate-bar{height:6px;background:#0d2540;border-radius:3px;margin-top:6px;overflow:hidden}
.winrate-fill{height:100%;background:linear-gradient(90deg,#f59e0b,#00d4ff);border-radius:3px;transition:width .5s}
.section-title{font-size:.7rem;letter-spacing:3px;text-transform:uppercase;color:#4a7a9b;margin:20px 0 10px}
.candles{display:flex;gap:3px;align-items:flex-end;height:80px;background:#0a1628;border-radius:8px;padding:8px;border:1px solid #0d2540;position:relative}
.candle-wrap{display:flex;flex-direction:column;align-items:center;flex:1;height:100%}
.candle-body{width:8px;border-radius:2px;min-height:4px}
.candle-body.green{background:#00ff88}.candle-body.red{background:#ff4466}
.pivot-line{position:absolute;left:8px;right:8px;height:2px;background:#f59e0b88;border-top:1px dashed #f59e0b}
.trades-list{background:#0a1628;border-radius:12px;border:1px solid #0d2540;overflow:hidden}
.trade-row{display:grid;grid-template-columns:60px 50px 90px 90px 80px 70px;gap:8px;padding:10px 16px;border-bottom:1px solid #060e1a;font-family:'Share Tech Mono';font-size:.75rem;align-items:center}
.trade-row.header{background:#060e1a;color:#4a7a9b;font-size:.65rem;letter-spacing:1px}
.badge{padding:2px 8px;border-radius:10px;font-size:.65rem;font-weight:600}
.badge.win{background:#003322;color:#00ff88}.badge.loss{background:#330011;color:#ff4466}
.badge.open{background:#1a1200;color:#f59e0b}.badge.buy{background:#003322;color:#00ff88}.badge.sell{background:#330011;color:#ff4466}
.last-update{font-family:'Share Tech Mono';font-size:.65rem;color:#2a4a6b;margin-top:16px;text-align:right}
</style>
</head>
<body>
<h1>WLD Bot</h1>
<div class="subtitle" id="subtitle">carregando...</div>
<div class="grid">
  <div class="card"><div class="card-label">Status</div><div id="status" class="card-value">--</div></div>
  <div class="card"><div class="card-label">Preco WLD</div><div id="price" class="card-value">--</div></div>
  <div class="card"><div class="card-label">Pivot Point</div><div id="pivot" class="card-value">--</div></div>
  <div class="card"><div class="card-label">Sinal</div><div id="signal" class="card-value">--</div></div>
  <div class="card"><div class="card-label">Wins</div><div id="wins" class="card-value green">--</div></div>
  <div class="card"><div class="card-label">Losses</div><div id="losses" class="card-value red">--</div></div>
  <div class="card"><div class="card-label">Win Rate</div><div id="winrate" class="card-value yellow">--%</div><div class="winrate-bar"><div class="winrate-fill" id="winrate-bar" style="width:0%"></div></div></div>
  <div class="card"><div class="card-label">Velas Abertas</div><div id="candles_held" class="card-value">--</div></div>
</div>
<div class="section-title">Ultimas 20 Velas (5min) + Pivot</div>
<div class="candles" id="candles"><div class="pivot-line" id="pivot-line" style="bottom:50%"></div></div>
<div class="section-title">Historico de Trades</div>
<div class="trades-list">
  <div class="trade-row header"><span>Hora</span><span>Tipo</span><span>Entrada</span><span>Saida</span><span>PnL</span><span>Result</span></div>
  <div id="trades"></div>
</div>
<div class="last-update">Atualizado: <span id="last_update">--</span></div>
<script>
async function update(){
  try{
    const r=await fetch('/api');const d=await r.json();
    document.getElementById('subtitle').textContent=(d.testnet?'TESTNET':'MAINNET')+' | WLDUSDT | SL: 1% | Hold: 60 velas | Pivot: 48 periodos';
    const st=document.getElementById('status');st.textContent=d.status;
    st.className='card-value '+(d.status.includes('LONG')?'green':d.status.includes('SHORT')?'red':'');
    document.getElementById('price').textContent='$'+d.price.toFixed(4);
    document.getElementById('pivot').textContent='$'+d.pivot.toFixed(4);
    const sg=document.getElementById('signal');sg.textContent=d.signal;
    sg.className='card-value '+(d.signal=='LONG'?'green':d.signal=='SHORT'?'red':'');
    document.getElementById('wins').textContent=d.wins;
    document.getElementById('losses').textContent=d.losses;
    const total=d.wins+d.losses;const wr=total>0?Math.round(d.wins/total*100):0;
    document.getElementById('winrate').textContent=wr+'%';
    document.getElementById('winrate-bar').style.width=wr+'%';
    document.getElementById('candles_held').textContent=d.candles_held+'/60';
    document.getElementById('last_update').textContent=d.last_update;
    const cc=document.getElementById('candles');
    const pivotLine=document.getElementById('pivot-line');
    const existing=[...cc.children].filter(c=>c!==pivotLine);
    existing.forEach(c=>c.remove());
    if(d.recent_candles&&d.recent_candles.length){
      const maxH=Math.max(...d.recent_candles.map(c=>c.h));
      const minL=Math.min(...d.recent_candles.map(c=>c.l));
      const range=maxH-minL||1;
      d.recent_candles.forEach(c=>{
        const isGreen=c.c>=c.o;
        const bodyH=Math.max(4,Math.abs(c.c-c.o)/range*60);
        const w=document.createElement('div');w.className='candle-wrap';
        const b=document.createElement('div');b.className='candle-body '+(isGreen?'green':'red');
        b.style.height=bodyH+'px';b.style.marginTop='auto';
        w.appendChild(b);cc.appendChild(w);
      });
      if(d.pivot>0){
        const pivotPct=(d.pivot-minL)/range*100;
        pivotLine.style.bottom=Math.min(95,Math.max(5,pivotPct))+'%';
      }
    }
    const tl=document.getElementById('trades');tl.innerHTML='';
    const trades=[...d.trades].reverse().slice(0,10);
    if(!trades.length){tl.innerHTML='<div style="padding:16px;text-align:center;color:#2a4a6b;font-size:.75rem;font-family:Share Tech Mono">Nenhum trade ainda</div>';}
    trades.forEach(t=>{
      const row=document.createElement('div');row.className='trade-row';
      const res=t.result==='WIN'?'win':t.result==='LOSS'?'loss':'open';
      const tipo=t.type==='Buy'?'buy':'sell';
      row.innerHTML=`<span>${t.time}</span><span><span class="badge ${tipo}">${t.type==='Buy'?'LONG':'SHORT'}</span></span><span>$${t.entry.toFixed(4)}</span><span>${t.exit?'$'+t.exit.toFixed(4):'-'}</span><span style="color:${t.pnl>0?'#00ff88':t.pnl<0?'#ff4466':'#4a7a9b'}">${t.pnl!=null?(t.pnl>0?'+':'')+t.pnl.toFixed(4):'-'}</span><span><span class="badge ${res}">${t.result}</span></span>`;
      tl.appendChild(row);
    });
  }catch(e){console.log(e)}
}
update();setInterval(update,5000);
</script>
</body>
</html>"""

# ── SERVIDOR WEB ─────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_GET(self):
        if self.path == '/api':
            data = json.dumps(state).encode()
            self.send_response(200)
            self.send_header('Content-Type','application/json')
            self.send_header('Access-Control-Allow-Origin','*')
            self.end_headers()
            self.wfile.write(data)
        else:
            self.send_response(200)
            self.send_header('Content-Type','text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(DASHBOARD_HTML.encode())

def start_server():
    server = HTTPServer(('0.0.0.0', PORT), Handler)
    log.info(f"Dashboard na porta {PORT}")
    server.serve_forever()

# ── LOOP PRINCIPAL ───────────────────────────────────
def run():
    log.info(f"WLD Bot | {SYMBOL} | Testnet: {TESTNET} | Qty: {QTY} | SL: 1% | Hold: 60 velas")
    set_leverage()
    threading.Thread(target=start_server, daemon=True).start()

    position_open_candle = None
    last_signal_candle   = None

    while True:
        try:
            opens, highs, lows, closes, timestamps = fetch_candles()
            pivots = calc_pivot(highs, lows, closes, PIVOT_PERIOD)
            signal = get_signal(closes, pivots)
            price  = get_last_price()
            now_ts = timestamps[-1]

            state["price"]       = price
            state["pivot"]       = round(pivots[-1], 4)
            state["signal"]      = "LONG" if signal==1 else "SHORT" if signal==-1 else "NENHUM"
            state["last_update"] = datetime.now().strftime("%H:%M:%S")

            log.info(f"Preco: {price:.4f} | Pivot: {pivots[-1]:.4f} | Sinal: {state['signal']}")

            position = get_position()
            if position:
                if position_open_candle:
                    candles_held = sum(1 for t in timestamps if t > position_open_candle)
                    state["candles_held"] = candles_held
                    log.info(f"{position['side']} ativo | Velas: {candles_held}/{MAX_CANDLES}")
                    if candles_held >= MAX_CANDLES:
                        if close_position(position):
                            position_open_candle = None
                            state["candles_held"] = 0
            else:
                state["candles_held"] = 0
                state["position"]     = None
                state["status"]       = "AGUARDANDO"
                if signal != 0 and now_ts != last_signal_candle:
                    side = "Buy" if signal == 1 else "Sell"
                    log.info(f"{'LONG' if signal==1 else 'SHORT'} detectado!")
                    if open_position(side, price):
                        position_open_candle = now_ts
                        last_signal_candle   = now_ts

        except Exception as e:
            log.error(f"Erro: {e}")

        time.sleep(LOOP_INTERVAL)

if __name__ == "__main__":
    run()
