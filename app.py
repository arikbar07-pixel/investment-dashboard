import json
import os
import time
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
from flask import Flask, jsonify, render_template, request, session, redirect, url_for, send_from_directory
import requests
import yfinance as yf

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-key-change-me')

DASHBOARD_PASSWORD = os.environ.get('DASHBOARD_PASSWORD', 'Ab170107')


def login_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated


@app.route('/manifest.json')
def manifest():
    return send_from_directory('static', 'manifest.json')


@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        if request.form.get('password') == DASHBOARD_PASSWORD:
            session['logged_in'] = True
            return redirect(url_for('index'))
        error = 'סיסמה שגויה'
    return render_template('login.html', error=error)


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))
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


COINGECKO_IDS = {
    'BTC-USD': 'bitcoin',
    'ETH-USD': 'ethereum'
}


def cg_price(ticker):
    cg_id = COINGECKO_IDS.get(ticker)
    if not cg_id:
        return None
    r = requests.get(
        'https://api.coingecko.com/api/v3/simple/price',
        params={'ids': cg_id, 'vs_currencies': 'usd', 'include_24hr_change': 'true'},
        headers={'Accept': 'application/json'},
        timeout=10
    )
    r.raise_for_status()
    data = r.json().get(cg_id, {})
    price = data.get('usd')
    if not price:
        return None
    return {
        'price':      round(float(price), 6),
        'change_pct': round(float(data.get('usd_24h_change', 0)), 3),
        'prev_close': None
    }


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route('/')
@login_required
def index():
    return render_template('index.html')


@app.route('/api/portfolio/history')
@login_required
def portfolio_history():
    portfolio = load_portfolio()
    today = datetime.now()
    current_month = today.strftime('%Y-%m')

    month_labels_he = {
        '01':'ינו׳','02':'פבר׳','03':'מרץ','04':'אפר׳',
        '05':'מאי','06':'יוני','07':'יולי','08':'אוג׳',
        '09':'ספט׳','10':'אוק׳','11':'נוב׳','12':'דצמ׳'
    }

    def month_label(month_key):
        mm = month_key.split('-')[1]
        yy = month_key[2:4]
        return month_labels_he.get(mm, mm) + " '" + yy

    # Collect all investments
    all_investments = [inv for cat in portfolio.values()
                       if isinstance(cat, list) for inv in cat]
    if not all_investments:
        return jsonify([])

    # Fetch current prices via Finnhub (same source as dashboard cards)
    tickers = list(set(inv['ticker'] for inv in all_investments if inv.get('purchase_price')))
    current_prices = {}
    for ticker in tickers:
        try:
            cached_price = _cache.get('price:' + ticker)
            if cached_price and cached_price['data'] and cached_price['data'].get('price'):
                current_prices[ticker] = cached_price['data']['price']
            elif is_crypto(ticker):
                data = cg_price(ticker)
                if data and data.get('price'):
                    current_prices[ticker] = data['price']
            else:
                data = fh_get('quote', {'symbol': fh_symbol(ticker)})
                if data.get('c'):
                    current_prices[ticker] = round(float(data['c']), 4)
        except Exception:
            pass

    # Fetch current USD/ILS rate via open.er-api (same as /api/rate/usdils)
    try:
        cached_rate = _cache.get('rate:usdils')
        if cached_rate and cached_rate['data']:
            current_rate = cached_rate['data']['rate']
        else:
            r = requests.get('https://open.er-api.com/v6/latest/USD', timeout=10)
            current_rate = round(float(r.json()['rates']['ILS']), 4)
    except Exception:
        current_rate = 3.65

    # Calculate live portfolio value right now
    live_value = 0.0
    for inv in all_investments:
        if not inv.get('purchase_price') or inv['purchase_price'] == 0:
            continue
        price = current_prices.get(inv['ticker'])
        if not price:
            continue
        qty = inv['invested_usd'] / inv['purchase_price']
        live_value += qty * price * current_rate

    # Save current month snapshot (overwrites on every call — freezes naturally when month ends)
    snapshots = dict(portfolio.get('_snapshots', {}))
    snapshots[current_month] = round(live_value, 2)
    portfolio['_snapshots'] = snapshots
    save_portfolio_data(portfolio)

    # Build result: past frozen snapshots + current live bar
    result = []
    for month_key in sorted(snapshots.keys()):
        is_live = (month_key == current_month)
        result.append({
            'month': month_key,
            'label': month_label(month_key),
            'value': snapshots[month_key],
            'live': is_live
        })

    return jsonify(result)


@app.route('/api/portfolio', methods=['GET'])
@login_required
def get_portfolio():
    return jsonify(load_portfolio())


@app.route('/api/portfolio', methods=['POST'])
@login_required
def save_portfolio():
    data = request.get_json()
    save_portfolio_data(data)
    return jsonify({'ok': True})


@app.route('/api/price/<path:ticker>')
def get_price(ticker):
    def fetch():
        if is_crypto(ticker):
            return cg_price(ticker)
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
        dt = datetime.strptime(date, '%Y-%m-%d')

        # Use yfinance for historical price (works for stocks + crypto)
        hist = yf.Ticker(ticker).history(
            start=(dt - timedelta(days=5)).strftime('%Y-%m-%d'),
            end=(dt + timedelta(days=5)).strftime('%Y-%m-%d'),
            interval='1d'
        )

        if not hist.empty:
            import pandas as pd
            tz = hist.index.tz
            target = pd.Timestamp(dt.date()).tz_localize(tz) if tz else pd.Timestamp(dt.date())
            past = hist[hist.index <= target]
            if past.empty:
                past = hist
            price = float(past['Close'].iloc[-1])
            return jsonify({'price': round(price, 6)})

        # Fallback: Finnhub current price
        symbol = fh_symbol(ticker)
        quote  = fh_get('quote', {'symbol': symbol})
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

    # 1d: use Finnhub real-time change_pct directly
    if range_ == '1d':
        fh = _cache.get('price:' + ticker)
        if fh and fh['data'] and fh['data'].get('change_pct') is not None:
            return jsonify({'pct': round(float(fh['data']['change_pct']), 2)})
        try:
            symbol = fh_symbol(ticker)
            data   = fh_get('quote', {'symbol': symbol})
            pct    = data.get('dp', 0)
            return jsonify({'pct': round(float(pct), 2)})
        except Exception:
            return jsonify({'pct': None})

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


@app.route('/api/chart/<path:ticker>')
def get_chart(ticker):
    range_    = request.args.get('range', '1mo')
    key       = 'chart:' + ticker + ':' + range_
    intraday  = range_ == '1d'
    days_back = {'7d': 7, '1mo': 30, '3mo': 91, '1y': 365}.get(range_, 30)
    cache_ttl = 60 if intraday else 300

    cached_val = _cache.get(key)
    if cached_val and time.time() - cached_val['ts'] < cache_ttl:
        data = list(cached_val['data'])
        fh = _cache.get('price:' + ticker)
        if fh and fh['data'] and fh['data'].get('price') and data:
            data[-1] = {'date': data[-1]['date'], 'price': fh['data']['price']}
        return jsonify(data)

    try:
        if intraday:
            hist = yf.Ticker(ticker).history(period='1d', interval='5m')
            fmt  = '%Y-%m-%dT%H:%M'
        else:
            start = (datetime.now() - timedelta(days=days_back)).strftime('%Y-%m-%d')
            hist  = yf.Ticker(ticker).history(start=start, interval='1d')
            fmt   = '%Y-%m-%d'

        if hist.empty:
            return jsonify([])

        points = []
        for ts, row in hist.iterrows():
            points.append({
                'date':  ts.strftime(fmt),
                'open':  round(float(row['Open']),  4),
                'high':  round(float(row['High']),  4),
                'low':   round(float(row['Low']),   4),
                'price': round(float(row['Close']), 4)
            })

        _cache[key] = {'ts': time.time(), 'data': points}
        fh = _cache.get('price:' + ticker)
        if fh and fh['data'] and fh['data'].get('price') and points:
            points[-1] = {'date': points[-1]['date'], 'price': fh['data']['price']}
        return jsonify(points)
    except Exception as e:
        print(f'[chart] {ticker}: {e}')
        return jsonify([])


@app.route('/api/news/<path:ticker>')
def get_news(ticker):
    key = 'news:' + ticker
    cached_val = _cache.get(key)
    if cached_val and time.time() - cached_val['ts'] < 1800:
        return jsonify(cached_val['data'])

    try:
        symbol   = fh_symbol(ticker).replace('BINANCE:', '')
        date_to  = datetime.now().strftime('%Y-%m-%d')
        date_from = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')

        if is_crypto(ticker):
            raw = fh_get('news', {'category': 'crypto', 'minId': 0})
            raw = [n for n in raw if any(k in (n.get('headline','') + n.get('summary','')).upper()
                   for k in [ticker.replace('-USD',''), 'BTC', 'ETH', 'BITCOIN', 'ETHEREUM'])][:20]
        else:
            raw = fh_get('company-news', {'symbol': ticker, 'from': date_from, 'to': date_to})

        if not raw:
            return jsonify([])

        # Build news text for Claude
        news_text = ''
        for i, n in enumerate(raw[:20]):
            ts   = datetime.fromtimestamp(n.get('datetime', 0)).strftime('%d.%m.%Y')
            news_text += f"{i+1}. [{ts}] {n.get('headline','')}\n{n.get('summary','')}\n\n"

        anthropic_key = os.environ.get('ANTHROPIC_KEY', '')
        if not anthropic_key:
            result = [{'date': datetime.fromtimestamp(n.get('datetime',0)).strftime('%d.%m.%Y'),
                       'headline': n.get('headline',''), 'summary': n.get('summary','')}
                      for n in raw[:8]]
            _cache[key] = {'ts': time.time(), 'data': result}
            return jsonify(result)

        import anthropic as ac
        client = ac.Anthropic(api_key=anthropic_key)

        system_prompt = """You are a financial news analyst for a long-term investor holding index funds, crypto, and a few individual stocks.

Keep only news that:
- Directly impacts the price or outlook of the specific asset
- Reports macro events with clear market implications (Fed decisions, CPI, GDP, employment)
- Covers crypto regulation or legal decisions
- Reports major earnings results
- Covers significant geopolitical events with direct market impact
- Contains company-specific events (leadership changes, lawsuits, acquisitions, analyst upgrades/downgrades)

Filter out:
- Corporate PR and marketing announcements
- General sector commentary not tied to the specific asset
- Opinion pieces without concrete facts
- News older than 48 hours
- Vague predictions without factual basis

For each relevant item, write a clear summary IN HEBREW of what happened and why it matters for this specific asset.
The summary should be 2-4 sentences: first explain what happened, then explain the potential market impact.

Respond in this exact JSON format:
[{"date":"DD.MM.YYYY","headline":"כותרת בעברית","summary":"סיכום בעברית של האירוע והשפעתו הצפויה על הנכס"}]
Return ONLY the JSON array, no other text."""

        msg = client.messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=2000,
            system=system_prompt,
            messages=[{'role': 'user', 'content': f'Asset: {ticker}\n\nNews:\n{news_text}'}]
        )

        import json as _json
        text = msg.content[0].text.strip()
        if text.startswith('```'):
            text = text.split('```')[1]
            if text.startswith('json'):
                text = text[4:]
        filtered = _json.loads(text.strip())

        _cache[key] = {'ts': time.time(), 'data': filtered}
        return jsonify(filtered)

    except Exception as e:
        print(f'[news] {ticker}: {e}')
        return jsonify([])


if __name__ == '__main__':
    print('=' * 50)
    print('  דשבורד השקעות מופעל')
    print('  פתח בדפדפן: http://localhost:5000')
    print('=' * 50)
    app.run(debug=False, port=int(os.environ.get('PORT', 5000)), host='0.0.0.0')
