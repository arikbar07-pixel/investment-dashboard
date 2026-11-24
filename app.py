import json
import os
import time
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
from flask import Flask, jsonify, render_template, request, session, redirect, url_for, send_from_directory
from werkzeug.security import generate_password_hash, check_password_hash
import requests
import yfinance as yf

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-key-change-me')
app.permanent_session_lifetime = timedelta(days=30)


def login_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('user_id'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated


@app.route('/manifest.json')
def manifest():
    return send_from_directory('static', 'manifest.json')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if session.get('user_id'):
        return redirect(url_for('index'))
    error = None
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        remember = bool(request.form.get('remember'))
        try:
            _ensure_tables()
            conn = _get_db()
            with conn.cursor() as cur:
                cur.execute('SELECT id, password_hash FROM users WHERE username = %s', (username,))
                user = cur.fetchone()
            conn.close()
            if user and check_password_hash(user[1], password):
                session.permanent = remember
                session['user_id']  = user[0]
                session['username'] = username
                return redirect(url_for('index'))
            error = 'שם משתמש או סיסמה שגויים'
        except Exception as e:
            error = 'שגיאת שרת — נסה שוב'
            print(f'[login] {e}')
    return render_template('login.html', error=error)


@app.route('/register', methods=['GET', 'POST'])
def register():
    if session.get('user_id'):
        return redirect(url_for('index'))
    error = None
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        if len(username) < 3:
            error = 'שם משתמש חייב להכיל לפחות 3 תווים'
        elif len(password) < 6:
            error = 'סיסמה חייבת להכיל לפחות 6 תווים'
        else:
            try:
                _ensure_tables()
                conn = _get_db()
                with conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            'INSERT INTO users (username, password_hash) VALUES (%s, %s) RETURNING id',
                            (username, generate_password_hash(password))
                        )
                        new_id = cur.fetchone()[0]
                conn.close()
                session.permanent = False
                session['user_id']  = new_id
                session['username'] = username
                return redirect(url_for('index'))
            except Exception as e:
                if 'unique' in str(e).lower():
                    error = 'שם המשתמש כבר תפוס — בחר אחר'
                else:
                    error = 'שגיאה ביצירת החשבון'
                    print(f'[register] {e}')
    return render_template('register.html', error=error)


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


def _ensure_tables():
    conn = _get_db()
    with conn:
        with conn.cursor() as cur:
            cur.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    username VARCHAR(50) UNIQUE NOT NULL,
                    password_hash VARCHAR(255) NOT NULL,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            ''')
            cur.execute('''
                CREATE TABLE IF NOT EXISTS portfolio (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                    data JSONB NOT NULL,
                    updated_at TIMESTAMP DEFAULT NOW()
                )
            ''')
            cur.execute('ALTER TABLE portfolio ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(id)')
    conn.close()


def load_portfolio(user_id=None):
    if _use_db():
        _ensure_tables()
        conn = _get_db()
        with conn.cursor() as cur:
            if user_id:
                cur.execute('SELECT data FROM portfolio WHERE user_id = %s ORDER BY id DESC LIMIT 1', (user_id,))
            else:
                cur.execute('SELECT data FROM portfolio ORDER BY id DESC LIMIT 1')
            row = cur.fetchone()
        conn.close()
        return row[0] if row else {}
    else:
        if os.path.exists(PORTFOLIO_FILE):
            with open(PORTFOLIO_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {}


def save_portfolio_data(data, user_id=None):
    if _use_db():
        _ensure_tables()
        conn = _get_db()
        with conn:
            with conn.cursor() as cur:
                cur.execute('INSERT INTO portfolio (user_id, data) VALUES (%s, %s)', (user_id, json.dumps(data)))
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


def cg_price(ticker):
    # 1. Try Finnhub (BINANCE:BTCUSDT mapping)
    try:
        data  = fh_get('quote', {'symbol': fh_symbol(ticker)})
        price = data.get('c')
        if price and float(price) > 0:
            return {
                'price':      round(float(price), 6),
                'change_pct': round(float(data.get('dp', 0)), 3),
                'prev_close': round(float(data['pc']), 6) if data.get('pc') else None
            }
    except Exception:
        pass

    # 2. Try Kraken (no geo-restrictions)
    kraken_pairs = {'BTC-USD': 'XBTUSD', 'ETH-USD': 'ETHUSD'}
    pair = kraken_pairs.get(ticker)
    if pair:
        try:
            r = requests.get(
                'https://api.kraken.com/0/public/Ticker',
                params={'pair': pair}, timeout=10
            )
            result = r.json().get('result', {})
            row    = next(iter(result.values()), {})
            price  = row.get('c', [None])[0]
            open_  = row.get('o')
            if price and float(price) > 0:
                pct = (float(price) - float(open_)) / float(open_) * 100 if open_ else 0
                return {
                    'price':      round(float(price), 6),
                    'change_pct': round(pct, 3),
                    'prev_close': None
                }
        except Exception:
            pass

    # 3. Fallback: yfinance
    try:
        h = yf.Ticker(ticker).history(period='2d', interval='5m')
        if not h.empty:
            price = float(h['Close'].iloc[-1])
            h2    = yf.Ticker(ticker).history(period='5d', interval='1d')
            pct   = 0
            if len(h2) >= 2:
                prev = float(h2['Close'].iloc[-2])
                pct  = (price - prev) / prev * 100
            return {'price': round(price, 6), 'change_pct': round(pct, 3), 'prev_close': None}
    except Exception:
        pass

    return None


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route('/')
@login_required
def index():
    return render_template('index.html')


@app.route('/api/portfolio/history')
@login_required
def portfolio_history():
    portfolio = load_portfolio(session['user_id'])
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
    save_portfolio_data(portfolio, session['user_id'])

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
    return jsonify(load_portfolio(session['user_id']))


@app.route('/api/portfolio', methods=['POST'])
@login_required
def save_portfolio():
    try:
        data = request.get_json()
        save_portfolio_data(data, session['user_id'])
        return jsonify({'ok': True})
    except Exception as e:
        print(f'[save_portfolio] {e}')
        return jsonify({'error': str(e)}), 500


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


NEWS_MAX       = 5
NEWS_CACHE_TTL = 600  # seconds

_CRYPTO_NEWS_KEYS = {
    'BTC-USD': ['BTC', 'BITCOIN'],
    'ETH-USD': ['ETH', 'ETHEREUM', 'ETHER '],
}


def _news_item(ts, headline, summary, source='', url=''):
    return {'ts': int(ts or 0), 'headline': (headline or '').strip(),
            'summary': (summary or '').strip(), 'source': source or '', 'url': url or ''}


def _news_finnhub(ticker, since_ts):
    """Company news (stocks/ETFs) or general crypto news filtered by coin, from Finnhub."""
    try:
        if is_crypto(ticker):
            raw  = fh_get('news', {'category': 'crypto'}) or []
            keys = _CRYPTO_NEWS_KEYS.get(ticker, [ticker.replace('-USD', '')])
            raw  = [n for n in raw if any(k in (n.get('headline', '') + ' ' + n.get('summary', '')).upper() for k in keys)]
        else:
            date_to   = datetime.utcnow().strftime('%Y-%m-%d')
            date_from = datetime.utcfromtimestamp(since_ts).strftime('%Y-%m-%d')
            raw = fh_get('company-news', {'symbol': ticker, 'from': date_from, 'to': date_to}) or []
        return [_news_item(n.get('datetime'), n.get('headline'), n.get('summary'), n.get('source'), n.get('url'))
                for n in raw if isinstance(n, dict)]
    except Exception as e:
        print(f'[news] finnhub {ticker}: {e}')
        return []


def _news_yahoo(ticker):
    """Yahoo Finance news via yfinance — covers ETFs and crypto that Finnhub misses."""
    out = []
    try:
        items = yf.Ticker(ticker).news or []
    except Exception as e:
        print(f'[news] yahoo {ticker}: {e}')
        return out
    for n in items:
        if not isinstance(n, dict):
            continue
        c = n.get('content') if isinstance(n.get('content'), dict) else None
        if c:  # newer yfinance format
            ts = 0
            pub = c.get('pubDate') or c.get('displayTime')
            if pub:
                try:
                    ts = int(datetime.fromisoformat(pub.replace('Z', '+00:00')).timestamp())
                except Exception:
                    ts = 0
            url = ((c.get('canonicalUrl') or {}).get('url') or (c.get('clickThroughUrl') or {}).get('url') or '')
            out.append(_news_item(ts, c.get('title'), c.get('summary') or c.get('description'),
                                  (c.get('provider') or {}).get('displayName', ''), url))
        else:  # older yfinance format
            out.append(_news_item(n.get('providerPublishTime'), n.get('title'), n.get('summary', ''),
                                  n.get('publisher', ''), n.get('link', '')))
    return out


def _collect_news(ticker):
    """Merge sources, dedupe, newest first. Prefer the last 24h; widen to 3 days only if 24h is empty."""
    now = time.time()
    since = now - 3 * 86400
    items = _news_finnhub(ticker, since) + _news_yahoo(ticker)
    seen, uniq = set(), []
    for n in sorted(items, key=lambda x: x['ts'], reverse=True):
        key = n['headline'].lower()[:80]
        if not n['headline'] or not n['ts'] or key in seen:
            continue
        seen.add(key)
        uniq.append(n)
    last_day = [n for n in uniq if n['ts'] >= now - 86400]
    if last_day:
        return last_day[:25], True
    return [n for n in uniq if n['ts'] >= since][:25], False


def _fmt_news_out(n, impact='neutral', headline=None, summary=None):
    return {
        'ts':       n['ts'],
        'date':     datetime.fromtimestamp(n['ts']).strftime('%d.%m.%Y'),
        'headline': headline or n['headline'],
        'summary':  summary if summary is not None else n['summary'][:400],
        'impact':   impact,
        'source':   n['source'],
        'url':      n['url'],
    }


_NEWS_SCHEMA = {
    'type': 'object',
    'properties': {
        'items': {
            'type': 'array',
            'items': {
                'type': 'object',
                'properties': {
                    'index':    {'type': 'integer'},
                    'headline': {'type': 'string'},
                    'summary':  {'type': 'string'},
                    'impact':   {'type': 'string', 'enum': ['positive', 'negative', 'neutral']},
                },
                'required': ['index', 'headline', 'summary', 'impact'],
                'additionalProperties': False,
            },
        },
    },
    'required': ['items'],
    'additionalProperties': False,
}

_NEWS_SYSTEM = """You are a financial news analyst for a long-term investor.

From the numbered news list, pick AT MOST 5 items most likely to move the price of the given asset, most important first.
Prefer: earnings and guidance, analyst upgrades/downgrades and price targets, regulation and lawsuits, M&A,
leadership changes, major product/contract news, and macro events with direct impact (Fed, CPI, jobs) for index funds and crypto.
Skip PR fluff, generic listicles, and items not really about this asset. Never invent facts beyond the given text.

For each picked item return:
- index: the item's number from the list
- headline: a short Hebrew headline
- summary: 1-2 Hebrew sentences — what happened and how it may affect the price
- impact: positive / negative / neutral for the asset's price"""


@app.route('/api/news/<path:ticker>')
def get_news(ticker):
    key = 'news:' + ticker
    cached_val = _cache.get(key)
    if cached_val and time.time() - cached_val['ts'] < NEWS_CACHE_TTL:
        return jsonify(cached_val['data'])

    raw, is_last_day = _collect_news(ticker)
    if not raw:
        return jsonify([])  # not cached, so a later request retries

    fallback = [_fmt_news_out(n) for n in raw[:NEWS_MAX]]
    result = fallback

    anthropic_key = os.environ.get('ANTHROPIC_KEY', '') or os.environ.get('ANTHROPIC_API_KEY', '')
    if anthropic_key:
        try:
            import anthropic as ac
            client = ac.Anthropic(api_key=anthropic_key, timeout=40.0)
            news_text = ''
            for i, n in enumerate(raw, 1):
                when = datetime.fromtimestamp(n['ts']).strftime('%d.%m %H:%M')
                news_text += f"{i}. [{when}] {n['headline']}\n{n['summary'][:500]}\n\n"

            msg = client.messages.create(
                model='claude-haiku-5-5',
                max_tokens=4000,
                system=_NEWS_SYSTEM,
                output_config={'effort': 'low',
                               'format': {'type': 'json_schema', 'schema': _NEWS_SCHEMA}},
                messages=[{'role': 'user', 'content': f'Asset: {ticker}\n\nNews:\n{news_text}'}],
            )
            if msg.stop_reason not in ('refusal', 'max_tokens'):
                text = next((b.text for b in msg.content if b.type == 'text'), '')
                picked = json.loads(text).get('items', [])
                out, used = [], set()
                for it in picked:
                    idx = it.get('index')
                    if not isinstance(idx, int) or not (1 <= idx <= len(raw)) or idx in used:
                        continue
                    used.add(idx)
                    out.append(_fmt_news_out(raw[idx - 1], it.get('impact', 'neutral'),
                                             it.get('headline'), it.get('summary', '')))
                    if len(out) >= NEWS_MAX:
                        break
                if out:
                    result = out
            else:
                print(f'[news] claude stop_reason={msg.stop_reason} for {ticker}')
        except Exception as e:
            print(f'[news] claude {ticker}: {e}')

    for r in result:
        r['last_day'] = is_last_day
    _cache[key] = {'ts': time.time(), 'data': result}
    return jsonify(result)


if __name__ == '__main__':
    print('=' * 50)
    print('  דשבורד השקעות מופעל')
    print('  פתח בדפדפן: http://localhost:5000')
    print('=' * 50)
    app.run(debug=False, port=int(os.environ.get('PORT', 5000)), host='0.0.0.0')
