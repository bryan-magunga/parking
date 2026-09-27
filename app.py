"""
app.py
------
Web layer (Flask). Ties the database + algorithms modules together and
exposes them as a browser-based system, per the brief's "running as a
web-based system" requirement.

Routes:
    GET  /                 -> dashboard: visual slot display + entry form
    POST /entry             -> Vehicle Entry module (park a car)
    GET  /checkout           -> preview fee for a plate before paying
    POST /checkout/confirm   -> Vehicle Exit + Payment + Barrier module
    GET  /history            -> log of completed sessions (from the DB)
"""

from datetime import datetime
import hmac
import os
from functools import wraps

from flask import Flask, jsonify, render_template, request, redirect, url_for, flash, session

from database import init_db, get_conn
from algorithms import SlotManager
from mpesa import MpesaError, initiate_stk_push, normalize_phone, stk_push_succeeded

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "development-secret-change-before-deployment")
STAFF_USERNAME = os.environ.get("STAFF_USERNAME", "staff")
STAFF_PASSWORD = os.environ.get("STAFF_PASSWORD", "Maasai@35")


def staff_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("staff_logged_in"):
            return redirect(url_for("staff_login", next=request.full_path))
        return view(*args, **kwargs)
    return wrapped

# One SlotManager instance shared for the app's lifetime - it is the live
# in-memory brain of the car park, backed by SQLite (see algorithms.py).
init_db()
manager = SlotManager()


@app.route("/")
@staff_required
def dashboard():
    slots = manager.get_slot_display()
    return render_template(
        "index.html",
        slots=slots,
        available=manager.available_count(),
        total=len(slots),
    )


@app.route("/entry", methods=["POST"])
@staff_required
def entry():
    plate = request.form.get("plate_number", "")
    if not plate.strip():
        flash("Please enter a number plate.", "error")
        return redirect(url_for("dashboard"))

    ok, result = manager.park_vehicle(plate)
    if ok:
        flash(f"Vehicle {plate.strip().upper()} parked in slot {result}.", "success")
    else:
        flash(result, "error")
    return redirect(url_for("dashboard"))


@app.route("/checkout", methods=["GET"])
@staff_required
def checkout_preview():
    plate = request.args.get("plate_number", "")
    preview = manager.preview_fee(plate) if plate else None
    if plate and not preview:
        flash("No active session found for that plate.", "error")
    return render_template("checkout.html", preview=preview, plate=plate)


@app.route("/checkout/confirm", methods=["POST"])
@staff_required
def checkout_confirm():
    plate = request.form.get("plate_number", "")
    phone = request.form.get("phone_number", "")
    preview = manager.preview_fee(plate)
    if not preview:
        flash("No active session found for this plate.", "error")
        return redirect(url_for("dashboard"))
    if preview["fee"] == 0:
        ok, result = manager.checkout_vehicle(plate)
        if ok:
            return render_template("receipt.html", receipt=result)
        flash(result, "error")
        return redirect(url_for("dashboard"))
    try:
        phone = normalize_phone(phone)
        response = initiate_stk_push(phone, preview["fee"], plate)
    except MpesaError as exc:
        flash(str(exc), "error")
        return redirect(url_for("checkout_preview", plate_number=plate))

    with get_conn() as conn:
        conn.execute(
            "INSERT INTO mpesa_payments "
            "(session_id, checkout_request_id, merchant_request_id, phone_number, amount, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, 'pending', ?)",
            (preview["session_id"], response["CheckoutRequestID"],
             response.get("MerchantRequestID"), phone, int(preview["fee"]), datetime.now().isoformat()),
        )
    return redirect(url_for("payment_wait", checkout_id=response["CheckoutRequestID"]))


@app.route("/payments/<checkout_id>")
@staff_required
def payment_wait(checkout_id):
    with get_conn() as conn:
        payment = conn.execute(
            "SELECT checkout_request_id, phone_number, amount, status, receipt_number "
            "FROM mpesa_payments WHERE checkout_request_id=?", (checkout_id,)
        ).fetchone()
    if not payment:
        flash("Payment request not found.", "error")
        return redirect(url_for("dashboard"))
    return render_template("payment.html", payment=payment)


@app.route("/api/payments/<checkout_id>")
@staff_required
def payment_status(checkout_id):
    with get_conn() as conn:
        payment = conn.execute(
            "SELECT status, receipt_number, result_description FROM mpesa_payments "
            "WHERE checkout_request_id=?", (checkout_id,)
        ).fetchone()
    if not payment:
        return jsonify({"status": "not_found"}), 404
    return jsonify(dict(payment))


@app.route("/mpesa/callback", methods=["POST"])
def mpesa_callback():
    data = request.get_json(silent=True) or {}
    callback = data.get("Body", {}).get("stkCallback", {})
    checkout_id = callback.get("CheckoutRequestID")
    if not checkout_id:
        return jsonify({"ResultCode": 1, "ResultDesc": "Missing checkout request ID"}), 400

    result_code = callback.get("ResultCode")
    description = callback.get("ResultDesc", "Payment did not complete.")
    metadata = callback.get("CallbackMetadata", {}).get("Item", [])
    receipt = next((item.get("Value") for item in metadata if item.get("Name") == "MpesaReceiptNumber"), None)
    with get_conn() as conn:
        payment = conn.execute(
            "SELECT session_id, status, amount, phone_number FROM mpesa_payments WHERE checkout_request_id=?",
            (checkout_id,)
        ).fetchone()
        if not payment or payment["status"] != "pending":
            return jsonify({"ResultCode": 0, "ResultDesc": "Callback received"})
        paid_amount = next((item.get("Value") for item in metadata if item.get("Name") == "Amount"), None)
        paid_phone = next((item.get("Value") for item in metadata if item.get("Name") == "PhoneNumber"), None)
        try:
            amount_matches = paid_amount is not None and int(paid_amount) == payment["amount"]
        except (TypeError, ValueError):
            amount_matches = False
        try:
            phone_matches = normalize_phone(str(paid_phone)) == payment["phone_number"] if paid_phone else False
        except MpesaError:
            phone_matches = False
        daraja_confirmed = False
        if result_code == 0 and receipt and amount_matches and phone_matches:
            daraja_confirmed = stk_push_succeeded(checkout_id)
        if daraja_confirmed:
            conn.execute(
                "UPDATE mpesa_payments SET status='completed', receipt_number=?, result_description=? "
                "WHERE checkout_request_id=?", (str(receipt), description, checkout_id)
            )
            session = conn.execute(
                "SELECT plate_number FROM sessions WHERE id=? AND status='active'", (payment["session_id"],)
            ).fetchone()
        else:
            conn.execute(
                "UPDATE mpesa_payments SET status='failed', result_description=? WHERE checkout_request_id=?",
                (description, checkout_id)
            )
            session = None

    # Close out the parking session only after Daraja confirms payment.
    if daraja_confirmed and session:
        manager.checkout_vehicle(session["plate_number"], fee_override=payment["amount"])
    return jsonify({"ResultCode": 0, "ResultDesc": "Callback received"})


@app.route("/history")
@staff_required
def history():
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT s.plate_number, sl.slot_number, s.entry_time, s.exit_time, s.fee, s.status
            FROM sessions s
            JOIN slots sl ON sl.id = s.slot_id
            ORDER BY s.id DESC
            LIMIT 100
            """
        ).fetchall()
    return render_template("history.html", rows=rows)


@app.route("/staff/login", methods=["GET", "POST"])
def staff_login():
    if session.get("staff_logged_in"):
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        username_ok = hmac.compare_digest(username.encode(), STAFF_USERNAME.encode())
        password_ok = hmac.compare_digest(password.encode(), STAFF_PASSWORD.encode())
        if username_ok and password_ok:
            session.clear()
            session["staff_logged_in"] = True
            destination = request.form.get("next", "")
            if destination.startswith("/") and not destination.startswith("//"):
                return redirect(destination)
            return redirect(url_for("dashboard"))
        flash("That staff username or password is incorrect.", "error")
    return render_template("login.html", next=request.args.get("next", ""))


@app.route("/staff/logout", methods=["POST"])
def staff_logout():
    session.clear()
    flash("You have been signed out.", "success")
    return redirect(url_for("staff_login"))


if __name__ == "__main__":
    # host=0.0.0.0 so it's reachable on a local network (e.g. a barrier
    # kiosk / display screen at the gate), debug=True for development only.
    app.run(host="0.0.0.0", port=5000, debug=True)
