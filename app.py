import json
import os
import time
from datetime import datetime, timedelta
from flask import Flask, jsonify, render_template, request
import requests
import yfinance as yf

app = Flask(__name__)
BASE_DIR       = os.path.dirname(os.path.abspath(__file__))
PORTFOLIO_FILE = os.path.join(BASE_DIR, 'portfolio.json')

FINNHUB_KEY = os.environ.get('FINNHUB_KEY', 'd7b0h31r01qtpbha2j80d7b0h31r01qtpbha2j8g')
FINNHUB     = 'https://finnhub.io/api/v1'

_cache = {}

# ── DB helpers ───────────────────────────────────────────────────────────────

def _use_db():
    return bool(os.environ.get('DATABASE_URL'))


def _get_db():
    import psycopg2, psycopg2.extras
    url = os.environ['DATABASE_URL']
    conn = psycopg2.connect(url, sslmode='require')
    return conn


def _ensure_table():
    conn = _get_db()
    with conn:
        with conn.cursor() as cur:
            cur.execute('''
                CREATE TABLE IF NOT EXISTS portfolio (
                    id SERIAL PRIMARY KEY,
                    data JSONB NOT NULL,
                    updated_at TIMESTAMP DEFAULT NOW()
                )
            ''')
    conn.close()


def load_portfolio():
    if _use_db():
        _ensure_table()
        conn = _get_db()
        with conn.cursor() as cur:
            cur.execute('SELECT data FROM portfolio ORDER BY id DESC LIMIT 1')
            row = cur.fetchone()
        conn.close()
        if row:
            return row[0]
        # seed from local file if db is empty
        if os.path.exists(PORTFOLIO_FILE):
            with open(PORTFOLIO_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            save_portfolio_data(data)
            return data
        return {}
    else:
        with open(PORTFOLIO_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)


def save_portfolio_data(data):
    if _use_db():
        _ensure_table()
        conn = _get_db()
        with conn:
            with conn.cursor() as cur:
                cur.execute('INSERT INTO portfolio (data) VALUES (%s)', (json.dumps(data),))
        conn.close()
    else:
        with open(PORTFOLIO_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)


# ── Cache / API helpers ───────────────────────────────────────────────────────

def cached(key, ttl, fn):
    now = time.time()
    if key in _cache and now - _cache[key]['ts'] < ttl:
        return _cache[key]['data']
    data = fn()
    _cache[key] = {'ts': now, 'data': data}
    return data


def fh_symbol(ticker):
    m = {'BTC-USD': 'BINANCE:BTCUSDT', 'ETH-USD': 'BINANCE:ETHUSDT'}
    return m.get(ticker, ticker)


def fh_get(path, params={}):
    p = dict(params)
    p['token'] = FINNHUB_KEY
    r = requests.get(f'{FINNHUB}/{path}', params=p, timeout=10)
    r.raise_for_status()
    return r.json()


def is_crypto(ticker):
    return '-USD' in ticker or ticker.startswith('BINANCE:')


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/portfolio', methods=['GET'])
def get_portfolio():
    return jsonify(load_portfolio())


@app.route('/api/portfolio', methods=['POST'])
def save_portfolio():
    data = request.get_json()
    save_portfolio_data(data)
    return jsonify({'ok': True})


@app.route('/api/price/<path:ticker>')
def get_price(ticker):
    def fetch():
        symbol = fh_symbol(ticker)
        data   = fh_get('quote', {'symbol': symbol})
        price  = data.get('c')
        prev   = data.get('pc')
        pct    = data.get('dp', 0)
        if not price:
            return None
        return {
            'price':      round(float(price), 6),
            'change_pct': round(float(pct), 3),
            'prev_close': round(float(prev), 6) if prev else None
        }
    try:
        data = cached('price:' + ticker, 60, fetch)
        if not data:
            return jsonify({'error': 'no data'}), 500
        return jsonify(data)
    except Exception as e:
        print(f'[price] {ticker}: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/history/<path:ticker>/<date>')
def get_history(ticker, date):
    try:
        symbol  = fh_symbol(ticker)
        dt      = datetime.strptime(date, '%Y-%m-%d')
        period1 = int(dt.timestamp())
        period2 = int((dt + timedelta(days=7)).timestamp())

        endpoint = 'crypto/candle' if is_crypto(ticker) else 'stock/candle'
        data = fh_get(endpoint, {
            'symbol': symbol, 'resolution': 'D',
            'from': period1, 'to': period2
        })

        if data.get('s') == 'ok' and data.get('c'):
            return jsonify({'price': round(float(data['c'][0]), 6)})

        quote = fh_get('quote', {'symbol': symbol})
        return jsonify({'price': round(float(quote['c']), 6), 'fallback': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 404


@app.route('/api/search/<path:query>')
def search_ticker(query):
    try:
        r = requests.get(
            'https://query1.finance.yahoo.com/v1/finance/search'
            '?q=' + requests.utils.quote(query) + '&quotesCount=8&newsCount=0&enableFuzzyQuery=true',
            headers={'User-Agent': 'Mozilla/5.0'},
            timeout=10
        )
        results = []
        for q in r.json().get('quotes', []):
            if q.get('quoteType') in ('EQUITY', 'ETF', 'CRYPTOCURRENCY', 'MUTUALFUND'):
                results.append({
                    'ticker':   q.get('symbol', ''),
                    'name':     q.get('longname') or q.get('shortname', q.get('symbol', '')),
                    'type':     q.get('quoteType', ''),
                    'exchange': q.get('exchDisp', '')
                })
        return jsonify(results[:7])
    except Exception:
        return jsonify([])


@app.route('/api/rate/usdils')
def usd_ils():
    def fetch():
        r    = requests.get('https://open.er-api.com/v6/latest/USD', timeout=10)
        rate = r.json()['rates']['ILS']
        return {'rate': round(float(rate), 4)}
    try:
        return jsonify(cached('rate:usdils', 3600, fetch))
    except Exception:
        return jsonify({'rate': 3.65})


@app.route('/api/period_change/<path:ticker>')
def period_change(ticker):
    range_    = request.args.get('range', '1mo')
    key       = 'pchg:' + ticker + ':' + range_
    days_back = {'7d': 7, '1mo': 30, '3mo': 91, '1y': 365}.get(range_, 30)

    cached_val = _cache.get(key)
    if cached_val and time.time() - cached_val['ts'] < 300 and cached_val['data'].get('pct') is not None:
        return jsonify(cached_val['data'])

    try:
        import pandas as pd
        target_start = datetime.now() - timedelta(days=days_back)

        hist = yf.Ticker(ticker).history(
            start=(target_start - timedelta(days=7)).strftime('%Y-%m-%d'),
            interval='1d'
        )
        if hist.empty or len(hist) < 2:
            return jsonify({'pct': None})

        tz     = hist.index.tz
        target = pd.Timestamp(target_start.date()).tz_localize(tz) if tz else pd.Timestamp(target_start.date())
        past   = hist[hist.index <= target]
        if past.empty:
            past = hist.head(1)
        start_price = float(past['Close'].iloc[-1])

        end_price = float(hist['Close'].iloc[-1])
        fh = _cache.get('price:' + ticker)
        if fh and fh['data'] and fh['data'].get('price'):
            end_price = fh['data']['price']

        pct    = (end_price - start_price) / start_price * 100
        result = {'pct': round(pct, 2)}
        _cache[key] = {'ts': time.time(), 'data': result}
        return jsonify(result)
    except Exception as e:
        print(f'[period_change] {ticker} {range_}: {e}')
        return jsonify({'pct': None})


@app.route('/api/target/<path:ticker>')
def get_target(ticker):
    def fetch():
        if is_crypto(ticker):
            return {'target': None}
        data   = fh_get('stock/price-target', {'symbol': ticker})
        target = data.get('targetMean')
        return {'target': target}
    try:
        return jsonify(cached('target:' + ticker, 3600, fetch))
    except Exception:
        return jsonify({'target': None})


if __name__ == '__main__':
    print('=' * 50)
    print('  דשבורד השקעות מופעל')
    print('  פתח בדפדפן: http://localhost:5000')
    print('=' * 50)
    app.run(debug=False, port=int(os.environ.get('PORT', 5000)), host='0.0.0.0')
