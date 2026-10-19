"""Expense & mileage tracking (Everlance-style): expenses with receipts,
mileage trips with saved places, dashboard, CSV export, and PDF reports.
Admin-only — this is company financial data."""
import calendar
import csv
import io
import os
import re
import secrets
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi import File

from . import db
from .auth import require_admin
from .web import page

router = APIRouter()

KINDS = ("entity", "vehicle", "category", "payment")
KIND_LABELS = {"entity": "Entities", "vehicle": "Vehicles",
               "category": "Categories", "payment": "Payment methods"}
RECEIPT_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".pdf", ".heic"}
MAX_RECEIPT = 15 * 1024 * 1024  # 15 MB


# ---------- helpers ----------

def _lists(kind, active_only=True):
    sql = "SELECT * FROM expense_lists WHERE kind = ?"
    if active_only:
        sql += " AND active = 1"
    return db.query(sql + " ORDER BY name", (kind,))


def _item(item_id, kind=None):
    row = db.query_one("SELECT * FROM expense_lists WHERE id = ?", (item_id,))
    if not row or (kind and row["kind"] != kind):
        raise HTTPException(422, "bad list item")
    return row


def _parse_date(raw, fallback=None):
    raw = (raw or "").strip()
    if not raw:
        return fallback
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        raise HTTPException(422, "dates must be YYYY-MM-DD")
    return raw


def _filters(request: Request):
    """Read the period + entity filters from the query string.

    Defaults to the current month. Returns (start, end, entity_id, qs) where
    qs re-encodes the filters for links that must preserve them.
    """
    today = date.today()
    rng = request.query_params.get("range", "")
    if rng == "last":
        end_d = today.replace(day=1) - timedelta(days=1)
        start, end = end_d.replace(day=1).isoformat(), end_d.isoformat()
    elif rng == "ytd":
        start, end = today.replace(month=1, day=1).isoformat(), today.isoformat()
    elif rng == "all":
        start, end = None, None
    else:
        start = _parse_date(request.query_params.get("start"))
        end = _parse_date(request.query_params.get("end"))
        if start is None and end is None and rng != "custom":
            start = today.replace(day=1).isoformat()
            end = today.replace(day=calendar.monthrange(today.year, today.month)[1]).isoformat()
    entity_raw = request.query_params.get("entity", "")
    entity_id = int(entity_raw) if entity_raw.isdigit() else None
    parts = []
    if start:
        parts.append(f"start={start}")
    if end:
        parts.append(f"end={end}")
    if start is None and end is None:
        parts.append("range=all")
    if entity_id:
        parts.append(f"entity={entity_id}")
    return start, end, entity_id, ("?" + "&".join(parts)) if parts else ""


def _where(start, end, entity_id):
    conds, args = [], []
    if start:
        conds.append("t.date >= ?")
        args.append(start)
    if end:
        conds.append("t.date <= ?")
        args.append(end)
    if entity_id:
        conds.append("t.entity_id = ?")
        args.append(entity_id)
    return (" AND ".join(conds) or "1=1"), args


def _cents(raw: str) -> int:
    try:
        value = Decimal(str(raw).replace("$", "").replace(",", "").strip())
        cents = int((value * 100).to_integral_value(ROUND_HALF_UP))
    except (InvalidOperation, ValueError):
        raise HTTPException(422, "amount must be a number")
    if cents < 0:
        raise HTTPException(422, "amount cannot be negative")
    return cents


def _miles(raw: str) -> float:
    try:
        miles = round(float(str(raw).strip()), 1)
    except ValueError:
        raise HTTPException(422, "miles must be a number")
    if miles < 0:
        raise HTTPException(422, "miles cannot be negative")
    return miles


async def _save_receipt(upload: UploadFile | None):
    """Store an uploaded receipt; returns (stored_name, orig_name) or ('', '')."""
    if upload is None or not (upload.filename or "").strip():
        return "", ""
    orig = os.path.basename(upload.filename)
    ext = os.path.splitext(orig)[1].lower()
    if ext not in RECEIPT_EXTS:
        raise HTTPException(422, "receipt must be an image or PDF")
    stored = secrets.token_hex(12) + ext
    path = os.path.join(db.UPLOAD_DIR, stored)
    size = 0
    with open(path, "wb") as out:
        while chunk := await upload.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_RECEIPT:
                out.close()
                os.remove(path)
                raise HTTPException(413, "receipt exceeds 15 MB limit")
            out.write(chunk)
    return stored, orig


def _remove_receipt(stored_name):
    if stored_name:
        path = os.path.join(db.UPLOAD_DIR, stored_name)
        if os.path.isfile(path):
            os.remove(path)


def _base_ctx(request):
    start, end, entity_id, qs = _filters(request)
    return {
        "start": start or "", "end": end or "", "entity_id": entity_id, "qs": qs,
        "entities": _lists("entity"), "all_time": start is None and end is None,
    }


def _resolve_category(category_id: str, category_new: str):
    """Category select value -> id. 'new' creates the typed-in category on the fly."""
    if category_id == "new":
        name = category_new.strip()
        if not name:
            raise HTTPException(422, "type a name for the new category")
        db.execute("INSERT OR IGNORE INTO expense_lists (kind, name) VALUES ('category', ?)", (name,))
        row = db.query_one("SELECT id FROM expense_lists WHERE kind = 'category' AND name = ?", (name,))
        db.execute("UPDATE expense_lists SET active = 1 WHERE id = ?", (row["id"],))
        return row["id"]
    cat = int(category_id) if category_id else None
    if cat:
        _item(cat, "category")
    return cat


# ---------- expenses ----------

def _expense_rows(start, end, entity_id, q=""):
    where, args = _where(start, end, entity_id)
    if q:
        where += " AND (t.merchant LIKE ? OR t.tags LIKE ? OR t.notes LIKE ?)"
        args += [f"%{q}%"] * 3
    return db.query(
        f"""SELECT t.*, e.name AS entity, c.name AS category, p.name AS payment
            FROM expenses t
            JOIN expense_lists e ON e.id = t.entity_id
            LEFT JOIN expense_lists c ON c.id = t.category_id
            LEFT JOIN expense_lists p ON p.id = t.payment_id
            WHERE {where} ORDER BY t.date DESC, t.id DESC""", args)


@router.get("/expenses")
def expenses_list(request: Request, q: str = "", user=Depends(require_admin)):
    ctx = _base_ctx(request)
    rows = _expense_rows(ctx["start"] or None, ctx["end"] or None, ctx["entity_id"], q.strip())
    total = sum(r["amount_cents"] for r in rows)
    return page(request, "expenses.html", user=user, tab="expenses", q=q.strip(),
                rows=rows, total=total, today=date.today().isoformat(),
                categories=_lists("category"), payments=_lists("payment"), **ctx)


@router.post("/expenses/add")
async def expense_add(request: Request, user=Depends(require_admin),
                      date_: str = Form(..., alias="date"), entity_id: int = Form(...),
                      merchant: str = Form(...), category_id: str = Form(""),
                      category_new: str = Form(""),
                      payment_id: str = Form(""), amount: str = Form(...),
                      tags: str = Form(""), notes: str = Form(""),
                      receipt: UploadFile | None = File(None)):
    _item(entity_id, "entity")
    cat = _resolve_category(category_id, category_new)
    pay = int(payment_id) if payment_id else None
    if pay:
        _item(pay, "payment")
    stored, orig = await _save_receipt(receipt)
    db.execute(
        """INSERT INTO expenses (date, entity_id, merchant, category_id, payment_id,
           amount_cents, tags, notes, receipt_name, receipt_orig, created_by)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (_parse_date(date_, date.today().isoformat()), entity_id, merchant.strip(), cat, pay,
         _cents(amount), tags.strip(), notes.strip(), stored, orig, user["id"]))
    return RedirectResponse("/expenses?msg=Expense+added", status_code=303)


def _get_expense(expense_id: int):
    row = db.query_one("SELECT * FROM expenses WHERE id = ?", (expense_id,))
    if not row:
        raise HTTPException(404, "expense not found")
    return row


@router.get("/expenses/entry/{expense_id}/edit")
def expense_edit_form(request: Request, expense_id: int, user=Depends(require_admin)):
    return page(request, "expense_edit.html", user=user, exp=_get_expense(expense_id),
                entities=_lists("entity"), categories=_lists("category"),
                payments=_lists("payment"))


@router.post("/expenses/entry/{expense_id}/edit")
async def expense_edit(expense_id: int, user=Depends(require_admin),
                       date_: str = Form(..., alias="date"), entity_id: int = Form(...),
                       merchant: str = Form(...), category_id: str = Form(""),
                       category_new: str = Form(""),
                       payment_id: str = Form(""), amount: str = Form(...),
                       tags: str = Form(""), notes: str = Form(""),
                       remove_receipt: str = Form(""),
                       receipt: UploadFile | None = File(None)):
    exp = _get_expense(expense_id)
    _item(entity_id, "entity")
    cat = _resolve_category(category_id, category_new)
    pay = int(payment_id) if payment_id else None
    if pay:
        _item(pay, "payment")
    stored, orig = await _save_receipt(receipt)
    if stored or remove_receipt:
        _remove_receipt(exp["receipt_name"])
    if not stored and not remove_receipt:
        stored, orig = exp["receipt_name"], exp["receipt_orig"]
    db.execute(
        """UPDATE expenses SET date = ?, entity_id = ?, merchant = ?, category_id = ?,
           payment_id = ?, amount_cents = ?, tags = ?, notes = ?,
           receipt_name = ?, receipt_orig = ? WHERE id = ?""",
        (_parse_date(date_, exp["date"]), entity_id, merchant.strip(), cat, pay,
         _cents(amount), tags.strip(), notes.strip(), stored, orig, expense_id))
    return RedirectResponse("/expenses?msg=Expense+saved", status_code=303)


@router.post("/expenses/entry/{expense_id}/delete")
def expense_delete(expense_id: int, user=Depends(require_admin)):
    exp = _get_expense(expense_id)
    db.execute("DELETE FROM expenses WHERE id = ?", (expense_id,))
    _remove_receipt(exp["receipt_name"])
    return RedirectResponse("/expenses?msg=Expense+deleted", status_code=303)


@router.get("/expenses/entry/{expense_id}/receipt")
def expense_receipt(expense_id: int, user=Depends(require_admin)):
    exp = _get_expense(expense_id)
    if not exp["receipt_name"]:
        raise HTTPException(404, "no receipt attached")
    path = os.path.join(db.UPLOAD_DIR, exp["receipt_name"])
    if not os.path.isfile(path):
        raise HTTPException(410, "receipt missing from disk")
    return FileResponse(path, filename=exp["receipt_orig"] or "receipt")


# ---------- mileage ----------

def _trip_rows(start, end, entity_id):
    where, args = _where(start, end, entity_id)
    return db.query(
        f"""SELECT t.*, e.name AS entity, v.name AS vehicle
            FROM mileage_trips t
            JOIN expense_lists e ON e.id = t.entity_id
            LEFT JOIN expense_lists v ON v.id = t.vehicle_id
            WHERE {where} ORDER BY t.date DESC, t.id DESC""", args)


def _places():
    rows = db.query("""SELECT from_place AS p FROM mileage_trips
                       UNION SELECT to_place FROM mileage_trips ORDER BY p""")
    return [r["p"] for r in rows if r["p"]]


@router.get("/expenses/mileage")
def mileage_list(request: Request, user=Depends(require_admin)):
    ctx = _base_ctx(request)
    rows = _trip_rows(ctx["start"] or None, ctx["end"] or None, ctx["entity_id"])
    return page(request, "mileage.html", user=user, tab="mileage",
                rows=rows, total_miles=round(sum(r["miles"] for r in rows), 1),
                today=date.today().isoformat(), vehicles=_lists("vehicle"),
                places=_places(), **ctx)


@router.post("/expenses/mileage/add")
def trip_add(user=Depends(require_admin),
             date_: str = Form(..., alias="date"), entity_id: int = Form(...),
             vehicle_id: str = Form(""), from_place: str = Form(""),
             to_place: str = Form(""), miles: str = Form(...),
             round_trip: str = Form(""), notes: str = Form("")):
    _item(entity_id, "entity")
    veh = int(vehicle_id) if vehicle_id else None
    if veh:
        _item(veh, "vehicle")
    total = _miles(miles)
    if round_trip:
        total = round(total * 2, 1)
    db.execute(
        """INSERT INTO mileage_trips (date, entity_id, vehicle_id, from_place, to_place,
           miles, round_trip, notes, created_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (_parse_date(date_, date.today().isoformat()), entity_id, veh, from_place.strip(),
         to_place.strip(), total, 1 if round_trip else 0, notes.strip(), user["id"]))
    return RedirectResponse("/expenses/mileage?msg=Trip+added", status_code=303)


def _get_trip(trip_id: int):
    row = db.query_one("SELECT * FROM mileage_trips WHERE id = ?", (trip_id,))
    if not row:
        raise HTTPException(404, "trip not found")
    return row


@router.get("/expenses/mileage/{trip_id}/edit")
def trip_edit_form(request: Request, trip_id: int, user=Depends(require_admin)):
    return page(request, "trip_edit.html", user=user, trip=_get_trip(trip_id),
                entities=_lists("entity"), vehicles=_lists("vehicle"), places=_places())


@router.post("/expenses/mileage/{trip_id}/edit")
def trip_edit(trip_id: int, user=Depends(require_admin),
              date_: str = Form(..., alias="date"), entity_id: int = Form(...),
              vehicle_id: str = Form(""), from_place: str = Form(""),
              to_place: str = Form(""), miles: str = Form(...), notes: str = Form("")):
    trip = _get_trip(trip_id)
    _item(entity_id, "entity")
    veh = int(vehicle_id) if vehicle_id else None
    if veh:
        _item(veh, "vehicle")
    db.execute(
        """UPDATE mileage_trips SET date = ?, entity_id = ?, vehicle_id = ?, from_place = ?,
           to_place = ?, miles = ?, notes = ? WHERE id = ?""",
        (_parse_date(date_, trip["date"]), entity_id, veh, from_place.strip(),
         to_place.strip(), _miles(miles), notes.strip(), trip_id))
    return RedirectResponse("/expenses/mileage?msg=Trip+saved", status_code=303)


@router.post("/expenses/mileage/{trip_id}/delete")
def trip_delete(trip_id: int, user=Depends(require_admin)):
    _get_trip(trip_id)
    db.execute("DELETE FROM mileage_trips WHERE id = ?", (trip_id,))
    return RedirectResponse("/expenses/mileage?msg=Trip+deleted", status_code=303)


# ---------- dashboard ----------

def _month_series(table, value_expr, entity_id):
    """Totals per month for the trailing 12 months, oldest first."""
    today = date.today()
    months = []
    y, m = today.year, today.month
    for _ in range(12):
        months.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    months.reverse()
    where, args = _where(months[0] + "-01", None, entity_id)
    rows = db.query(
        f"""SELECT substr(t.date, 1, 7) AS ym, {value_expr} AS v FROM {table} t
            WHERE {where} GROUP BY ym""", args)
    by_month = {r["ym"]: r["v"] or 0 for r in rows}
    peak = max([by_month.get(ym, 0) for ym in months] + [1])
    out = []
    for ym in months:
        v = by_month.get(ym, 0)
        out.append({"ym": ym, "value": v, "pct": round(v * 100 / peak, 1),
                    "label": date(int(ym[:4]), int(ym[5:7]), 1).strftime("%b %Y"),
                    "short": date(int(ym[:4]), int(ym[5:7]), 1).strftime("%b")})
    return out


def _sum(table, value_expr, start, end, entity_id):
    where, args = _where(start, end, entity_id)
    row = db.query_one(f"SELECT {value_expr} AS v FROM {table} t WHERE {where}", args)
    return row["v"] or 0


@router.get("/expenses/dashboard")
def dashboard(request: Request, user=Depends(require_admin)):
    entity_raw = request.query_params.get("entity", "")
    entity_id = int(entity_raw) if entity_raw.isdigit() else None
    today = date.today()
    month_start = today.replace(day=1).isoformat()
    year_start = today.replace(month=1, day=1).isoformat()

    cards = {
        "month_spend": _sum("expenses", "SUM(t.amount_cents)", month_start, None, entity_id),
        "ytd_spend": _sum("expenses", "SUM(t.amount_cents)", year_start, None, entity_id),
        "month_miles": round(_sum("mileage_trips", "SUM(t.miles)", month_start, None, entity_id), 1),
        "ytd_miles": round(_sum("mileage_trips", "SUM(t.miles)", year_start, None, entity_id), 1),
    }
    spend_months = _month_series("expenses", "SUM(t.amount_cents)", entity_id)
    miles_months = _month_series("mileage_trips", "SUM(t.miles)", entity_id)

    where, args = _where(year_start, None, entity_id)
    categories = db.query(
        f"""SELECT COALESCE(c.name, 'Uncategorized') AS name, SUM(t.amount_cents) AS cents
            FROM expenses t LEFT JOIN expense_lists c ON c.id = t.category_id
            WHERE {where} GROUP BY name ORDER BY cents DESC""", args)
    cat_peak = max([c["cents"] for c in categories] + [1])

    where_e, args_e = _where(year_start, None, None)
    entity_rows = db.query(
        f"""SELECT e.id, e.name,
              (SELECT COALESCE(SUM(t.amount_cents), 0) FROM expenses t
               WHERE {where_e} AND t.entity_id = e.id) AS cents,
              (SELECT COALESCE(SUM(t.miles), 0) FROM mileage_trips t
               WHERE {where_e} AND t.entity_id = e.id) AS miles
            FROM expense_lists e WHERE e.kind = 'entity' AND e.active = 1
            ORDER BY e.name""", args_e + args_e)

    return page(request, "expenses_dashboard.html", user=user, tab="dashboard",
                entities=_lists("entity"), entity_id=entity_id, year=today.year,
                cards=cards, spend_months=spend_months, miles_months=miles_months,
                categories=categories, cat_peak=cat_peak, entity_rows=entity_rows)


# ---------- settings ----------

def _in_use(item):
    checks = {
        "entity": ("SELECT 1 FROM expenses WHERE entity_id = ? LIMIT 1",
                   "SELECT 1 FROM mileage_trips WHERE entity_id = ? LIMIT 1"),
        "vehicle": ("SELECT 1 FROM mileage_trips WHERE vehicle_id = ? LIMIT 1",),
        "category": ("SELECT 1 FROM expenses WHERE category_id = ? LIMIT 1",),
        "payment": ("SELECT 1 FROM expenses WHERE payment_id = ? LIMIT 1",),
    }
    return any(db.query_one(sql, (item["id"],)) for sql in checks[item["kind"]])


@router.get("/expenses/settings")
def settings(request: Request, user=Depends(require_admin)):
    groups = []
    for kind in KINDS:
        entries = [dict(i) | {"in_use": _in_use(i)} for i in _lists(kind, active_only=False)]
        groups.append({"kind": kind, "label": KIND_LABELS[kind], "entries": entries})
    return page(request, "expenses_settings.html", user=user, tab="settings", groups=groups)


@router.post("/expenses/settings/{kind}/add")
def settings_add(kind: str, name: str = Form(...), user=Depends(require_admin)):
    if kind not in KINDS:
        raise HTTPException(422, "bad kind")
    name = name.strip()
    if name:
        db.execute("INSERT OR IGNORE INTO expense_lists (kind, name) VALUES (?, ?)", (kind, name))
    return RedirectResponse("/expenses/settings", status_code=303)


@router.post("/expenses/settings/item/{item_id}/rename")
def settings_rename(item_id: int, name: str = Form(...), user=Depends(require_admin)):
    item = _item(item_id)
    name = name.strip()
    if name and not db.query_one("SELECT 1 FROM expense_lists WHERE kind = ? AND name = ? AND id != ?",
                                 (item["kind"], name, item_id)):
        db.execute("UPDATE expense_lists SET name = ? WHERE id = ?", (name, item_id))
        return RedirectResponse("/expenses/settings", status_code=303)
    return RedirectResponse("/expenses/settings?msg=That+name+is+empty+or+already+taken", status_code=303)


@router.post("/expenses/settings/item/{item_id}/toggle")
def settings_toggle(item_id: int, user=Depends(require_admin)):
    _item(item_id)
    db.execute("UPDATE expense_lists SET active = 1 - active WHERE id = ?", (item_id,))
    return RedirectResponse("/expenses/settings", status_code=303)


@router.post("/expenses/settings/item/{item_id}/delete")
def settings_delete(item_id: int, user=Depends(require_admin)):
    item = _item(item_id)
    if _in_use(item):
        return RedirectResponse(
            "/expenses/settings?msg=In+use+by+existing+entries+—+deactivate+it+instead", status_code=303)
    db.execute("DELETE FROM expense_lists WHERE id = ?", (item_id,))
    return RedirectResponse("/expenses/settings", status_code=303)


# ---------- CSV export ----------

def _csv_response(name, header, rows):
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    writer.writerows(rows)
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/expenses/export/expenses.csv")
def export_expenses(request: Request, user=Depends(require_admin)):
    start, end, entity_id, _ = _filters(request)
    rows = _expense_rows(start, end, entity_id)
    return _csv_response(
        "expenses.csv",
        ["Date", "Entity", "Merchant", "Category", "Amount", "Payment", "Tags", "Notes", "Receipt"],
        [[r["date"], r["entity"], r["merchant"], r["category"] or "",
          f"{r['amount_cents'] / 100:.2f}", r["payment"] or "", r["tags"], r["notes"],
          "yes" if r["receipt_name"] else ""] for r in rows])


@router.get("/expenses/export/mileage.csv")
def export_mileage(request: Request, user=Depends(require_admin)):
    start, end, entity_id, _ = _filters(request)
    rows = _trip_rows(start, end, entity_id)
    return _csv_response(
        "mileage.csv",
        ["Date", "Entity", "Vehicle", "From", "To", "Miles", "Round trip", "Notes"],
        [[r["date"], r["entity"], r["vehicle"] or "", r["from_place"], r["to_place"],
          r["miles"], "yes" if r["round_trip"] else "", r["notes"]] for r in rows])


# ---------- PDF report ----------

def _latin(text):
    return str(text if text is not None else "").encode("latin-1", "replace").decode("latin-1")


@router.get("/expenses/report.pdf")
def report_pdf(request: Request, user=Depends(require_admin)):
    try:
        from fpdf import FPDF
    except ImportError:
        raise HTTPException(503, "PDF export needs the fpdf2 package: pip install fpdf2")

    start, end, entity_id, _ = _filters(request)
    expenses = _expense_rows(start, end, entity_id)
    trips = _trip_rows(start, end, entity_id)
    entity = _item(entity_id, "entity")["name"] if entity_id else "All entities"
    period = f"{start or 'beginning'} to {end or 'today'}"
    total = sum(r["amount_cents"] for r in expenses)
    total_miles = round(sum(r["miles"] for r in trips), 1)

    pdf = FPDF()
    pdf.set_auto_page_break(True, margin=15)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 9, "Expense Report", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(105, 112, 128)
    pdf.cell(0, 6, _latin(f"{entity}  ·  {period}"), new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 6, _latin(f"Total expenses: ${total / 100:,.2f}   ·   Total miles: {total_miles:,}"),
             new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)
    pdf.ln(3)

    def table(title, headers, widths, data, aligns):
        pdf.set_font("Helvetica", "B", 12)
        pdf.cell(0, 8, title, new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "B", 8)
        pdf.set_fill_color(234, 240, 248)
        for h, w in zip(headers, widths):
            pdf.cell(w, 6, h, border=1, fill=True)
        pdf.ln()
        pdf.set_font("Helvetica", "", 8)
        for row in data:
            if pdf.get_y() > 270:
                pdf.add_page()
            for value, w, a in zip(row, widths, aligns):
                text = _latin(value)
                while pdf.get_string_width(text) > w - 2 and len(text) > 1:
                    text = text[:-2] + "~"
                pdf.cell(w, 6, text, border=1, align=a)
            pdf.ln()
        pdf.ln(4)

    if expenses:
        table(f"Expenses (${total / 100:,.2f})",
              ["Date", "Entity", "Merchant", "Category", "Payment", "Amount"],
              [20, 28, 48, 36, 30, 24],
              [[r["date"], r["entity"], r["merchant"], r["category"] or "",
                r["payment"] or "", f"${r['amount_cents'] / 100:,.2f}"] for r in expenses],
              ["L", "L", "L", "L", "L", "R"])

        by_cat = {}
        for r in expenses:
            by_cat[r["category"] or "Uncategorized"] = \
                by_cat.get(r["category"] or "Uncategorized", 0) + r["amount_cents"]
        table("By category", ["Category", "Amount"], [80, 30],
              [[name, f"${cents / 100:,.2f}"]
               for name, cents in sorted(by_cat.items(), key=lambda kv: -kv[1])],
              ["L", "R"])

    if trips:
        table(f"Mileage ({total_miles:,} mi)",
              ["Date", "Entity", "Vehicle", "From", "To", "Miles"],
              [20, 28, 28, 44, 44, 20],
              [[r["date"], r["entity"], r["vehicle"] or "", r["from_place"], r["to_place"],
                f"{r['miles']:,}"] for r in trips],
              ["L", "L", "L", "L", "L", "R"])

    receipts = [r for r in expenses if r["receipt_name"]
                and os.path.splitext(r["receipt_name"])[1] in (".jpg", ".jpeg", ".png", ".gif", ".webp")]
    for r in receipts:
        path = os.path.join(db.UPLOAD_DIR, r["receipt_name"])
        if not os.path.isfile(path):
            continue
        try:
            pdf.add_page()
            pdf.set_font("Helvetica", "B", 10)
            pdf.cell(0, 7, _latin(f"Receipt — {r['date']} · {r['merchant']} · ${r['amount_cents'] / 100:,.2f}"),
                     new_x="LMARGIN", new_y="NEXT")
            pdf.image(path, x=15, w=120)
        except Exception:
            pdf.set_font("Helvetica", "I", 9)
            pdf.cell(0, 6, "(receipt image could not be embedded)", new_x="LMARGIN", new_y="NEXT")

    return Response(bytes(pdf.output()), media_type="application/pdf",
                    headers={"Content-Disposition": 'attachment; filename="expense-report.pdf"'})
