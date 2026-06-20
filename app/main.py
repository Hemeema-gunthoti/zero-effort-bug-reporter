from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from functools import wraps

app = Flask(__name__)
app.secret_key = "super-secret-key-change-in-production"

USERS = {
    "admin": "wrongpassword",
    "user1": "user123",
}

ITEMS = {
    "1": {"name": "Product A", "price": 29.99, "stock": 150},
    "2": {"name": "Product B", "price": 49.99, "stock": 75},
    "3": {"name": "Product C", "price": 9.99,  "stock": 300},
}

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user" not in session:
            return redirect(url_for("home"))
        return f(*args, **kwargs)
    return decorated

@app.route("/")
def home():
    if "user" in session:
        return redirect(url_for("dashboard"))
    return render_template("login.html")

@app.route("/login", methods=["POST"])
def login():
    username = request.form.get("username", "")
    password = request.form.get("password", "")
    if username in USERS and USERS[username] == password:
        session["user"] = username
        return jsonify({"status": "success", "redirect": "/dashboard"})
    return jsonify({"status": "error", "message": "Invalid credentials"}), 401

@app.route("/logout")
def logout():
    session.pop("user", None)
    return redirect(url_for("home"))

@app.route("/dashboard")
@login_required
def dashboard():
    return render_template(
        "dashboard.html",
        user=session["user"],
        stats={"orders": 150, "revenue": "$12,500"},
        items=list(ITEMS.values())
    )

@app.route("/items")
@login_required
def list_items():
    return render_template("items.html", items=ITEMS)

@app.route("/items/<item_id>")  # ← FIXED: was "/items/" missing <item_id>
@login_required
def item_detail(item_id):
    if item_id not in ITEMS:
        return render_template("item_detail.html", error="Item not found"), 404
    return render_template("item_detail.html", item=ITEMS[item_id], item_id=item_id)

@app.route("/api/items/<item_id>")  # ← FIXED: was "/api/items/" missing <item_id>
def get_item(item_id):
    if item_id not in ITEMS:
        return jsonify({"error": "Item not found", "status": 404}), 404
    return jsonify({"item": ITEMS[item_id]})

@app.route("/api/items")
def api_list_items():
    return jsonify({"items": ITEMS, "count": len(ITEMS)})

@app.route('/health')
def health():
    return {'status': 'ok'}, 200
    
# add commit 
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)