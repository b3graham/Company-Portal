"""Import Everlance CSV exports (Trips and Transactions) into expenses/mileage.

Two-step flow: upload -> preview with mappings -> commit. Each commit is recorded
as a batch so it can be undone in one click. Admin-only, like the rest of expenses.
"""
import csv
import glob
import io
import json
import os
import re
import secrets
import time
import urllib.request
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse

from . import db
from .auth import require_admin
from .web import page

router = APIRouter()

IMPORT_DIR = os.path.join(db.DATA_DIR, "imports")
MAX_UPLOAD = 50 * 1024 * 1024
MAX_RECEIPT = 15 * 1024 * 1024
RECEIPT_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
                 "image/gif": ".gif", "application/pdf": ".pdf", "image/heic": ".heic"}

# Normalised header name -> field. Everlance header spelling varies a little between
# export types and plan tiers, so match on a cleaned-up key.
ALIASES = {
    "date": "date", "transactiondate": "date", "tripdate": "date",
    "miles": "miles", "distance": "miles", "distancemiles": "miles",
    "amount": "amount", "total": "amount",
    "value": "value",
    "merchant": "merchant", "vendor": "merchant", "payee": "merchant", "name": "merchant",
    "description": "description", "bankdescription": "description",
    "category": "category",
    "purpose": "purpose",
    "businessline": "business_line",
    "tags": "tags", "tag": "tags",
    "notes": "notes", "note": "notes", "memo": "notes",
    "receipturl": "receipt_url", "photourl": "receipt_url", "receipt": "receipt_url",
    "receipts": "receipt_url", "attachment": "receipt_url", "attachmenturl": "receipt_url",
    "bankaccount": "account", "account": "account", "paymentmethod": "account", "card": "account",
    "type": "type", "transactiontype": "type",
    "from": "from_place", "to": "to_place",
    "fromfulladdress": "from_full", "tofulladdress": "to_full",
    "vehicle": "vehicle",
    "timestarted": "time_started", "timeended": "time_ended",
    "durationhours": "duration",
}
REVENUE = re.compile(r"revenue|income", re.I)


def _key(header):
    return re.sub(r"[^a-z]", "", (header or "").lower())


def _parse_date(raw):
    raw = (raw or "").strip()
    if "T" in raw:
        raw = raw[:10]
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _cents(raw):
    txt = re.sub(r"[^\d.\-]", "", str(raw or ""))
    if not txt:
        return None
    try:
        return abs(int((Decimal(txt) * 100).to_integral_value(ROUND_HALF_UP)))
    except InvalidOperation:
        return None


def _miles(raw):
    txt = re.sub(r"[^\d.\-]", "", str(raw or ""))
    if not txt:
        return None
    try:
        return round(abs(float(txt)), 1)
    except ValueError:
        return None


def _cell(value):
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, datetime):
        return value.date().isoformat()
    return str(value).strip()


def load_rows(data: bytes, filename: str):
    """Turn an uploaded CSV or XLSX export into a list of string rows."""
    if filename.lower().endswith((".xlsx", ".xlsm")):
        try:
            import openpyxl
        except ImportError:
            raise HTTPException(503, "Excel import needs the openpyxl package: pip install openpyxl")
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        ws = wb.worksheets[0]
        return [[_cell(c) for c in row] for row in ws.iter_rows(values_only=True)]
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("latin-1")
    return list(csv.reader(io.StringIO(text)))


def parse_everlance(reader):
    """Return (kind, rows, meta) from a list of rows. kind is 'trips' or 'transactions'."""
    meta = {}
    header_idx = None
    for i, row in enumerate(reader):
        if len(row) >= 2 and row[0].strip() in ("Date Range", "Export Type", "Purpose", "Business Line"):
            meta[row[0].strip()] = row[1].strip()
        keys = {_key(c) for c in row}
        if "date" in keys and ("miles" in keys or ("amount" in keys and ("merchant" in keys or "category" in keys))):
            header_idx = i
            break
    if header_idx is None:
        raise HTTPException(422, "Couldn't find a header row with Date and Miles/Amount columns. "
                                 "Is this an Everlance Trips or Transactions CSV export?")
    header = reader[header_idx]
    fields = [ALIASES.get(_key(h)) for h in header]
    kind = "trips" if "miles" in fields else "transactions"
    rows = []
    for raw in reader[header_idx + 1:]:
        if not any(c.strip() for c in raw):
            continue
        rec = {}
        for f, val in zip(fields, raw):
            if f and f not in rec:
                rec[f] = val.strip()
        d = _parse_date(rec.get("date"))
        if not d:
            continue
        rec["date"] = d
        if kind == "trips":
            rec["miles"] = _miles(rec.get("miles"))
            if rec["miles"] is None:
                continue
            rec["from_place"] = rec.get("from_place") or rec.get("from_full", "")
            rec["to_place"] = rec.get("to_place") or rec.get("to_full", "")
        else:
            rec["amount_cents"] = _cents(rec.get("amount"))
            if rec["amount_cents"] is None:
                continue
            rec["merchant"] = rec.get("merchant") or rec.get("description") or "Unknown merchant"
        rec["purpose"] = rec.get("purpose", "")
        rec["business_line"] = rec.get("business_line", "")
        rows.append(rec)
    return kind, rows, meta


def _purpose_ok(purpose, mode):
    p = (purpose or "").strip().lower()
    if mode == "all":
        return True
    if p == "personal":
        return False
    if mode == "work":
        return p in ("work", "business")
    return True  # "work_unclassified": work plus unclassified


def _distinct(rows, field):
    return sorted({r.get(field, "") for r in rows}, key=str.lower)


def _match(name, items):
    """Pick a list item id whose name matches `name` loosely, else None."""
    n = _key(name)
    if not n:
        return None
    for it in items:
        if _key(it["name"]) == n:
            return it["id"]
    for it in items:
        k = _key(it["name"])
        if k and (k in n or n in k):
            return it["id"]
    return None


def _lists(kind):
    return db.query("SELECT * FROM expense_lists WHERE kind = ? AND active = 1 ORDER BY name", (kind,))


def _cleanup():
    cutoff = time.time() - 86400
    for path in glob.glob(os.path.join(IMPORT_DIR, "*.json")):
        if os.path.getmtime(path) < cutoff:
            os.remove(path)


def _pending_path(token):
    if not re.fullmatch(r"[0-9a-f]{24}", token or ""):
        raise HTTPException(422, "bad import token")
    return os.path.join(IMPORT_DIR, token + ".json")


def _batches():
    return db.query("""SELECT b.*, u.name AS who FROM import_batches b
                       LEFT JOIN users u ON u.id = b.created_by ORDER BY b.created_at DESC""")


# ---------- routes ----------

@router.get("/expenses/import")
def import_form(request: Request, user=Depends(require_admin)):
    os.makedirs(IMPORT_DIR, exist_ok=True)
    _cleanup()
    return page(request, "expenses_import.html", user=user, tab="import", batches=_batches())


@router.post("/expenses/import/preview")
async def import_preview(request: Request, user=Depends(require_admin),
                         upload: UploadFile = File(...)):
    os.makedirs(IMPORT_DIR, exist_ok=True)
    raw = await upload.read(MAX_UPLOAD + 1)
    if len(raw) > MAX_UPLOAD:
        raise HTTPException(413, "export exceeds 50 MB")
    kind, rows, meta = parse_everlance(load_rows(raw, upload.filename or ""))
    if not rows:
        return RedirectResponse("/expenses/import?msg=No+rows+with+a+date+found+in+that+file", status_code=303)

    token = secrets.token_hex(12)
    pending = {"kind": kind, "filename": os.path.basename(upload.filename or "export.csv"),
               "meta": meta, "rows": rows}
    with open(_pending_path(token), "w", encoding="utf-8") as fh:
        json.dump(pending, fh)

    entities, vehicles = _lists("entity"), _lists("vehicle")
    categories, payments = _lists("category"), _lists("payment")
    purposes = {}
    for r in rows:
        p = r["purpose"] or "Unclassified"
        purposes[p] = purposes.get(p, 0) + 1
    ctx = {
        "token": token, "kind": kind, "filename": pending["filename"], "meta": meta,
        "count": len(rows), "first": min(r["date"] for r in rows), "last": max(r["date"] for r in rows),
        "purposes": sorted(purposes.items(), key=lambda kv: -kv[1]),
        "lines": [(bl, _match(bl, entities)) for bl in _distinct(rows, "business_line")],
        "entities": entities, "vehicles": vehicles, "categories": categories, "payments": payments,
        "sample": rows[:8],
        "has_receipts": any(r.get("receipt_url") for r in rows),
        "year_start": date.today().replace(month=1, day=1).isoformat(),
    }
    if kind == "trips":
        ctx["total_miles"] = round(sum(r["miles"] for r in rows), 1)
        ctx["vehicle_names"] = [(v, _match(v, vehicles)) for v in _distinct(rows, "vehicle")]
    else:
        ctx["total_cents"] = sum(r["amount_cents"] for r in rows if not REVENUE.search(r.get("type", "")))
        ctx["category_names"] = [(c, _match(c, categories)) for c in _distinct(rows, "category")]
        ctx["account_names"] = [(a, _match(a, payments)) for a in _distinct(rows, "account")]
        ctx["revenue_rows"] = sum(1 for r in rows if REVENUE.search(r.get("type", "")))
    return page(request, "expenses_import_preview.html", user=user, tab="import", **ctx)


def _resolve(conn, kind, name, choice):
    """Turn a mapping form value into a list-item id ('' -> None, 'new' -> create)."""
    if choice == "new":
        if not name.strip():
            return None
        conn.execute("INSERT OR IGNORE INTO expense_lists (kind, name) VALUES (?, ?)", (kind, name.strip()))
        return conn.execute("SELECT id FROM expense_lists WHERE kind = ? AND name = ?",
                            (kind, name.strip())).fetchone()["id"]
    if choice.isdigit():
        row = conn.execute("SELECT id FROM expense_lists WHERE id = ? AND kind = ?",
                           (int(choice), kind)).fetchone()
        return row["id"] if row else None
    return None


def _fetch_receipt(url):
    """Download a receipt image from an Everlance link into the uploads folder."""
    if not re.match(r"https?://", url or ""):
        return "", ""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "portal-import/1.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            ext = RECEIPT_TYPES.get(ctype)
            if not ext:
                ext = os.path.splitext(url.split("?")[0])[1].lower()
                if ext not in RECEIPT_TYPES.values():
                    return "", ""
            data = resp.read(MAX_RECEIPT + 1)
    except Exception:
        return "", ""
    if not data or len(data) > MAX_RECEIPT:
        return "", ""
    stored = secrets.token_hex(12) + ext
    with open(os.path.join(db.UPLOAD_DIR, stored), "wb") as out:
        out.write(data)
    return stored, "everlance-receipt" + ext


@router.post("/expenses/import/commit")
async def import_commit(request: Request, user=Depends(require_admin)):
    form = await request.form()
    token = form.get("token", "")
    path = _pending_path(token)
    if not os.path.isfile(path):
        return RedirectResponse("/expenses/import?msg=That+preview+expired.+Upload+the+file+again",
                                status_code=303)
    with open(path, encoding="utf-8") as fh:
        pending = json.load(fh)
    kind, rows = pending["kind"], pending["rows"]

    purpose_mode = form.get("purpose_mode", "work")
    allow_dupes = bool(form.get("allow_dupes"))
    fetch_receipts = bool(form.get("fetch_receipts"))
    start = (form.get("start") or "").strip() or None
    end = (form.get("end") or "").strip() or None

    conn = db.connect()
    try:
        entity_map = {bl: _resolve(conn, "entity", bl, form.get("entity__" + bl, ""))
                      for bl in _distinct(rows, "business_line")}
        if any(v is None for v in entity_map.values()):
            conn.rollback()
            return RedirectResponse("/expenses/import?msg=Every+business+line+needs+an+entity",
                                    status_code=303)
        vehicle_map = category_map = payment_map = {}
        if kind == "trips":
            vehicle_map = {v: _resolve(conn, "vehicle", v, form.get("vehicle__" + v, ""))
                           for v in _distinct(rows, "vehicle")}
        else:
            category_map = {c: _resolve(conn, "category", c, form.get("category__" + c, ""))
                            for c in _distinct(rows, "category")}
            payment_map = {a: _resolve(conn, "payment", a, form.get("account__" + a, ""))
                           for a in _distinct(rows, "account")}

        batch = token
        inserted = skipped = receipts = 0
        for r in rows:
            if not _purpose_ok(r["purpose"], purpose_mode):
                skipped += 1
                continue
            if (start and r["date"] < start) or (end and r["date"] > end):
                skipped += 1
                continue
            entity_id = entity_map[r["business_line"]]
            if kind == "trips":
                if not allow_dupes and conn.execute(
                        """SELECT 1 FROM mileage_trips WHERE date = ? AND miles = ?
                           AND from_place = ? AND to_place = ? AND import_batch != ? LIMIT 1""",
                        (r["date"], r["miles"], r["from_place"], r["to_place"], batch)).fetchone():
                    skipped += 1
                    continue
                notes = r.get("notes", "")
                if r.get("time_started"):
                    span = r["time_started"] + ("-" + r["time_ended"] if r.get("time_ended") else "")
                    notes = f"{notes} ({span})" if notes else span
                if not r["purpose"]:
                    notes = f"[unclassified] {notes}".strip()
                conn.execute(
                    """INSERT INTO mileage_trips (date, entity_id, vehicle_id, from_place, to_place,
                       miles, round_trip, notes, created_by, import_batch)
                       VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?)""",
                    (r["date"], entity_id, vehicle_map.get(r.get("vehicle", "")), r["from_place"],
                     r["to_place"], r["miles"], notes, user["id"], batch))
            else:
                if REVENUE.search(r.get("type", "")):
                    skipped += 1
                    continue
                if not allow_dupes and conn.execute(
                        """SELECT 1 FROM expenses WHERE date = ? AND amount_cents = ? AND merchant = ?
                           AND import_batch != ? LIMIT 1""",
                        (r["date"], r["amount_cents"], r["merchant"], batch)).fetchone():
                    skipped += 1
                    continue
                stored, orig = "", ""
                if fetch_receipts and r.get("receipt_url"):
                    stored, orig = _fetch_receipt(r["receipt_url"])
                    receipts += 1 if stored else 0
                notes = r.get("notes", "")
                if r.get("receipt_url") and not stored:
                    notes = f"{notes}\nEverlance receipt: {r['receipt_url']}".strip()
                conn.execute(
                    """INSERT INTO expenses (date, entity_id, merchant, category_id, payment_id,
                       amount_cents, tags, notes, receipt_name, receipt_orig, created_by, import_batch)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (r["date"], entity_id, r["merchant"], category_map.get(r.get("category", "")),
                     payment_map.get(r.get("account", "")), r["amount_cents"], r.get("tags", ""),
                     notes, stored, orig, user["id"], batch))
            inserted += 1
        conn.execute(
            """INSERT INTO import_batches (id, kind, filename, inserted, skipped, created_by)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (batch, kind, pending["filename"], inserted, skipped, user["id"]))
        conn.commit()
    finally:
        conn.close()
    os.remove(path)
    msg = f"Imported {inserted} {'trips' if kind == 'trips' else 'expenses'}, skipped {skipped}"
    if receipts:
        msg += f", downloaded {receipts} receipts"
    return RedirectResponse("/expenses/import?msg=" + msg.replace(" ", "+"), status_code=303)


@router.post("/expenses/import/{batch_id}/undo")
def import_undo(batch_id: str, user=Depends(require_admin)):
    batch = db.query_one("SELECT * FROM import_batches WHERE id = ?", (batch_id,))
    if not batch:
        raise HTTPException(404, "batch not found")
    conn = db.connect()
    try:
        if batch["kind"] == "trips":
            conn.execute("DELETE FROM mileage_trips WHERE import_batch = ?", (batch_id,))
        else:
            for row in conn.execute("SELECT receipt_name FROM expenses WHERE import_batch = ?", (batch_id,)):
                if row["receipt_name"]:
                    p = os.path.join(db.UPLOAD_DIR, row["receipt_name"])
                    if os.path.isfile(p):
                        os.remove(p)
            conn.execute("DELETE FROM expenses WHERE import_batch = ?", (batch_id,))
        conn.execute("DELETE FROM import_batches WHERE id = ?", (batch_id,))
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse("/expenses/import?msg=Import+undone", status_code=303)
