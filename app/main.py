"""
app/main.py
-----------
The sample Flask web application that our test suite runs against.

Intentional bugs (so tests fail and the AI agent gets triggered):
  Bug 1 — /api/items/<id> raises a KeyError for unknown IDs instead
           of returning a proper 404. Causes a 500 crash.
  Bug 2 — The login error message element (#error-message) is never
           made visible in the JS, so Selenium cannot find it after
           a failed login attempt.
"""

from flask import Flask, render_template, request, jsonify, session

app = Flask(__name__)
app.secret_key = "super-secret-key-change-in-production"

# ── Simulated database ──────────────────────────────────────────────
USERS = {
    "admin": "password123",
    "user1": "user123",
}

ITEMS = {
    "1": {"name": "Product A", "price": 29.99, "stock": 150},
    "2": {"name": "Product B", "price": 49.99, "stock": 75},
    "3": {"name": "Product C", "price": 9.99,  "stock": 300},
}

# ── Routes ──────────────────────────────────────────────────────────

@app.route("/")
def home():
    return render_template("login.html")


@app.route("/login", methods=["POST"])
def login():
    username = request.form.get("username", "")
    password = request.form.get("password", "")

    if username in USERS and USERS[username] == password:
        session["user"] = username
        return jsonify({"status": "success", "redirect": "/dashboard"})

    # The HTML never makes #error-message visible after this 401 response.
    # That is the intentional bug that breaks the Selenium test.
    return jsonify({"status": "error", "message": "Invalid credentials"}), 401


@app.route("/dashboard")
def dashboard():
    if "user" not in session:
        return jsonify({"error": "Unauthorized"}), 403

    return jsonify({
        "user": session["user"],
        "stats": {"orders": 150, "revenue": "$12,500"},
        "items": list(ITEMS.values()),
    })


@app.route("/api/items/<item_id>")
def get_item(item_id):
    """
    BUG: ITEMS[item_id] raises a KeyError for unknown IDs (e.g. '999').
    Should return a 404 JSON response instead of crashing with a 500.
    """
    return jsonify({"item": ITEMS[item_id]})   # <── Bug here


@app.route("/api/items")
def list_items():
    return jsonify({"items": ITEMS, "count": len(ITEMS)})


@app.route("/health")
def health():
    """
    CI pipeline polls this endpoint before starting tests
    to confirm the app is fully up and accepting requests.
    """
    return jsonify({"status": "ok"}), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)