import os
import sqlite3
from datetime import datetime
from flask import Flask, jsonify, request, send_from_directory, g

app = Flask(__name__, static_folder='static')

# Use /data/finance.db on Railway (volume mount), fall back to /tmp if not writable
_raw_path = os.environ.get('DB_PATH', '/data/finance.db')
DB_DIR = os.path.dirname(_raw_path)
DB_PATH = _raw_path

CATEGORIES = [
    'Income', 'Housing', 'Food', 'Transport', 'Utilities',
    'Healthcare', 'Entertainment', 'Shopping', 'Education', 'Other'
]

def _ensure_db_dir():
    """Create the DB directory if needed; fall back to /tmp if not writable."""
    global DB_PATH
    try:
        os.makedirs(DB_DIR, exist_ok=True)
        test = os.path.join(DB_DIR, '.write_test')
        with open(test, 'w') as f:
            f.write('ok')
        os.remove(test)
    except OSError:
        DB_PATH = '/tmp/finance.db'
        app.logger.warning('Cannot write to %s — falling back to %s', DB_DIR, DB_PATH)

def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(e=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()

def init_db():
    _ensure_db_dir()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                amount REAL NOT NULL,
                category TEXT NOT NULL,
                date TEXT NOT NULL,
                description TEXT,
                type TEXT NOT NULL CHECK(type IN ('income','expense')),
                created_at TEXT DEFAULT (datetime('now'))
            )
        ''')
        conn.commit()

# Initialise DB on first request — safe with gunicorn multi-worker
@app.before_request
def ensure_db_ready():
    if not hasattr(app, '_db_initialised'):
        init_db()
        app._db_initialised = True

# ── Routes ────────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return send_from_directory('static', 'index.html')

@app.route('/manifest.json')
def manifest():
    return send_from_directory('static', 'manifest.json')

@app.route('/api/categories')
def categories():
    return jsonify(CATEGORIES)

@app.route('/api/transactions', methods=['GET'])
def get_transactions():
    db = get_db()
    query = 'SELECT * FROM transactions WHERE 1=1'
    params = []
    category = request.args.get('category')
    if category:
        query += ' AND category = ?'
        params.append(category)
    date_from = request.args.get('date_from')
    if date_from:
        query += ' AND date >= ?'
        params.append(date_from)
    date_to = request.args.get('date_to')
    if date_to:
        query += ' AND date <= ?'
        params.append(date_to)
    query += ' ORDER BY date DESC, created_at DESC'
    rows = db.execute(query, params).fetchall()
    return jsonify([dict(r) for r in rows])

@app.route('/api/transactions', methods=['POST'])
def add_transaction():
    data = request.json
    amount = float(data['amount'])
    category = data['category']
    tx_date = data['date']
    description = data.get('description', '')
    tx_type = 'income' if category == 'Income' else 'expense'
    db = get_db()
    cursor = db.execute(
        'INSERT INTO transactions (amount, category, date, description, type) VALUES (?,?,?,?,?)',
        (amount, category, tx_date, description, tx_type)
    )
    db.commit()
    row = db.execute('SELECT * FROM transactions WHERE id=?', (cursor.lastrowid,)).fetchone()
    return jsonify(dict(row)), 201

@app.route('/api/transactions/<int:tx_id>', methods=['PUT'])
def update_transaction(tx_id):
    data = request.json
    amount = float(data['amount'])
    category = data['category']
    tx_date = data['date']
    description = data.get('description', '')
    tx_type = 'income' if category == 'Income' else 'expense'
    db = get_db()
    db.execute(
        'UPDATE transactions SET amount=?, category=?, date=?, description=?, type=? WHERE id=?',
        (amount, category, tx_date, description, tx_type, tx_id)
    )
    db.commit()
    row = db.execute('SELECT * FROM transactions WHERE id=?', (tx_id,)).fetchone()
    if row is None:
        return jsonify({'error': 'Not found'}), 404
    return jsonify(dict(row))

@app.route('/api/transactions/<int:tx_id>', methods=['DELETE'])
def delete_transaction(tx_id):
    db = get_db()
    db.execute('DELETE FROM transactions WHERE id=?', (tx_id,))
    db.commit()
    return jsonify({'ok': True})

@app.route('/api/summary')
def summary():
    db = get_db()
    month = request.args.get('month')
    if not month:
        month = datetime.now().strftime('%Y-%m')
    rows = db.execute(
        "SELECT * FROM transactions WHERE strftime('%Y-%m', date)=? ORDER BY date DESC",
        (month,)
    ).fetchall()
    income = 0.0
    expenses = 0.0
    by_category = {}
    for r in rows:
        r = dict(r)
        cat = r['category']
        amt = r['amount']
        if r['type'] == 'income':
            income += amt
        else:
            expenses += amt
        by_category.setdefault(cat, 0.0)
        by_category[cat] += amt
    return jsonify({
        'month': month,
        'income': income,
        'expenses': expenses,
        'net': income - expenses,
        'by_category': by_category,
        'transactions': [dict(r) for r in rows]
    })

@app.route('/api/months')
def available_months():
    db = get_db()
    rows = db.execute(
        "SELECT DISTINCT strftime('%Y-%m', date) as month FROM transactions ORDER BY month DESC"
    ).fetchall()
    return jsonify([r['month'] for r in rows])

if __name__ == '__main__':
    init_db()
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
