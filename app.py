import os
import re
import threading
from datetime import date

from dotenv import load_dotenv
from flask import Flask, abort, flash, redirect, render_template, request, url_for

import checker
from db import get_conn, init_db

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET", "dev-secret")

CABINS = ["economy", "premium_economy", "business", "first"]
IATA_RE = re.compile(r"^[A-Z]{3}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _parse_form(form) -> tuple[dict | None, list[str]]:
    errors: list[str] = []
    origin = (form.get("origin") or "").strip().upper()
    destination = (form.get("destination") or "").strip().upper()
    start_date = (form.get("start_date") or "").strip()
    end_date = (form.get("end_date") or "").strip()
    cabin = (form.get("cabin") or "").strip()
    try:
        max_miles = int(form.get("max_miles") or 0)
    except ValueError:
        max_miles = 0
    try:
        passengers = int(form.get("passengers") or 1)
    except ValueError:
        passengers = 0

    if not IATA_RE.match(origin):
        errors.append("Origin must be a 3-letter IATA code (e.g. JFK).")
    if not IATA_RE.match(destination):
        errors.append("Destination must be a 3-letter IATA code (e.g. LHR).")
    if origin and origin == destination:
        errors.append("Origin and destination must differ.")
    if not DATE_RE.match(start_date) or not DATE_RE.match(end_date):
        errors.append("Dates must be YYYY-MM-DD.")
    else:
        try:
            if date.fromisoformat(end_date) < date.fromisoformat(start_date):
                errors.append("End date must be on or after start date.")
        except ValueError:
            errors.append("Invalid date.")
    if cabin not in CABINS:
        errors.append("Invalid cabin.")
    if max_miles <= 0:
        errors.append("Max miles must be a positive integer.")
    if passengers < 1 or passengers > 9:
        errors.append("Passengers must be between 1 and 9.")

    if errors:
        return None, errors
    return (
        {
            "origin": origin,
            "destination": destination,
            "start_date": start_date,
            "end_date": end_date,
            "cabin": cabin,
            "max_miles": max_miles,
            "passengers": passengers,
        },
        [],
    )


@app.route("/")
def index():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM routes ORDER BY enabled DESC, created_at DESC"
        ).fetchall()
    return render_template("routes.html", routes=[dict(r) for r in rows])


@app.route("/routes/new", methods=["GET", "POST"])
def route_new():
    if request.method == "POST":
        data, errors = _parse_form(request.form)
        if errors:
            for e in errors:
                flash(e, "error")
            return render_template(
                "route_form.html",
                route=request.form,
                cabins=CABINS,
                action_url=url_for("route_new"),
                title="Add route",
            )
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO routes(origin, destination, start_date, end_date, "
                "cabin, max_miles, passengers) VALUES (?,?,?,?,?,?,?)",
                (
                    data["origin"], data["destination"],
                    data["start_date"], data["end_date"],
                    data["cabin"], data["max_miles"], data["passengers"],
                ),
            )
            conn.commit()
        flash("Route added.", "ok")
        return redirect(url_for("index"))
    return render_template(
        "route_form.html",
        route={"passengers": 1},
        cabins=CABINS,
        action_url=url_for("route_new"),
        title="Add route",
    )


@app.route("/routes/<int:rid>/edit", methods=["GET", "POST"])
def route_edit(rid: int):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM routes WHERE id = ?", (rid,)).fetchone()
    if not row:
        abort(404)

    if request.method == "POST":
        data, errors = _parse_form(request.form)
        if errors:
            for e in errors:
                flash(e, "error")
            return render_template(
                "route_form.html",
                route=request.form,
                cabins=CABINS,
                action_url=url_for("route_edit", rid=rid),
                title=f"Edit route #{rid}",
            )
        with get_conn() as conn:
            conn.execute(
                "UPDATE routes SET origin=?, destination=?, start_date=?, end_date=?, "
                "cabin=?, max_miles=?, passengers=? WHERE id = ?",
                (
                    data["origin"], data["destination"],
                    data["start_date"], data["end_date"],
                    data["cabin"], data["max_miles"], data["passengers"],
                    rid,
                ),
            )
            conn.commit()
        flash("Route updated.", "ok")
        return redirect(url_for("index"))

    return render_template(
        "route_form.html",
        route=dict(row),
        cabins=CABINS,
        action_url=url_for("route_edit", rid=rid),
        title=f"Edit route #{rid}",
    )


@app.route("/routes/<int:rid>/toggle", methods=["POST"])
def route_toggle(rid: int):
    with get_conn() as conn:
        row = conn.execute("SELECT enabled FROM routes WHERE id = ?", (rid,)).fetchone()
        if not row:
            abort(404)
        new_val = 0 if row["enabled"] else 1
        conn.execute("UPDATE routes SET enabled = ? WHERE id = ?", (new_val, rid))
        conn.commit()
    flash(f"Route #{rid} {'enabled' if new_val else 'disabled'}.", "ok")
    return redirect(url_for("index"))


@app.route("/routes/<int:rid>/delete", methods=["POST"])
def route_delete(rid: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM routes WHERE id = ?", (rid,))
        conn.commit()
    flash(f"Route #{rid} deleted.", "ok")
    return redirect(url_for("index"))


@app.route("/run-now", methods=["POST"])
def run_now():
    # Run in a background thread so the request returns immediately.
    threading.Thread(target=checker.run_once, daemon=True).start()
    flash("Check started in background. Watch your inbox and checker.log.", "ok")
    return redirect(url_for("index"))


if __name__ == "__main__":
    init_db()
    app.run(host="127.0.0.1", port=5000, debug=False)
