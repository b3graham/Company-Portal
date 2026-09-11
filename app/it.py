"""IT department section. Visible only to users in the IT department (and admins).
Home dashboard, asset tracker, software & licenses, and vendor contacts live here;
the policy/runbook/DR document engine is in it_docs.py."""
import csv
import io
import re
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response

from . import db
from .auth import require_department
from .web import page

router = APIRouter()
require_it = require_department("it")

ASSET_TYPES = ["laptop", "desktop", "server", "network", "phone", "tablet",
               "printer", "peripheral", "storage", "software", "other"]
TYPE_LABELS = {"laptop": "Laptop", "desktop": "Desktop", "server": "Server",
               "network": "Network gear", "phone": "Phone", "tablet": "Tablet",
               "printer": "Printer", "peripheral": "Peripheral", "storage": "Storage",
               "software": "Software / license", "other": "Other"}

STATUSES = ["in_service", "spare", "repair", "retired", "disposed"]
STATUS_LABELS = {"in_service": "In service", "spare": "Spare", "repair": "In repair",
                 "retired": "Retired", "disposed": "Disposed"}
ACTIVE_STATUSES = ("in_service", "spare", "repair")

LICENSE_CATEGORIES = ["productivity", "security", "cloud", "domain", "development",
                      "communication", "backup", "other"]
LICENSE_CAT_LABELS = {"productivity": "Productivity / office", "security": "Security tooling",
                      "cloud": "Cloud / hosting", "domain": "Domain / DNS / certs",
                      "development": "Development", "communication": "Communication",
                      "backup": "Backup / storage", "other": "Other"}
BILLING = ["monthly", "annual", "one_time"]
BILLING_LABELS = {"monthly": "Monthly", "annual": "Annual", "one_time": "One-time"}

VENDOR_CATEGORIES = ["isp", "hosting", "domain", "hardware", "software", "telecom",
                     "msp", "other"]
VENDOR_CAT_LABELS = {"isp": "Internet provider", "hosting": "Hosting / cloud",
                     "domain": "Domain / registrar", "hardware": "Hardware",
                     "software": "Software", "telecom": "Phone / telecom",
                     "msp": "Managed service", "other": "Other"}

SECTION_LABELS = {"policies": "Policy", "runbooks": "Runbook", "dr": "DR", "ir": "IR plan"}

REVIEW_WINDOW_DAYS = 90   # warranties
RENEWAL_WINDOW_DAYS = 30  # licenses


def _opt_date(raw: str) -> str:
    raw = (raw or "").strip()
    if raw and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        raise HTTPException(422, "dates must be YYYY-MM-DD")
    return raw


def _opt_cents(raw: str):
    raw = (raw or "").strip().replace("$", "").replace(",", "")
    if not raw:
        return None
    try:
        return round(float(raw) * 100)
    except ValueError:
        raise HTTPException(422, "cost must be a number")


def _opt_int(raw: str, what="number"):
    raw = (raw or "").strip()
    if not raw:
        return None
    if not raw.isdigit():
        raise HTTPException(422, f"{what} must be a whole number")
    return int(raw)


def _opt_user(raw: str):
    raw = (raw or "").strip()
    if not raw:
        return None
    if not raw.isdigit() or not db.query_one("SELECT 1 FROM users WHERE id = ?", (int(raw),)):
        raise HTTPException(422, "bad user id")
    return int(raw)


def _check_enum(value, allowed, what):
    if value not in allowed:
        raise HTTPException(422, f"bad {what}")
    return value


def _people():
    return db.query("SELECT id, name FROM users WHERE active = 1 ORDER BY name")


def _csv(name, header, rows):
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    writer.writerows(rows)
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


def _dates():
    today = date.today()
    return (today.isoformat(),
            (today + timedelta(days=REVIEW_WINDOW_DAYS)).isoformat(),
            (today + timedelta(days=RENEWAL_WINDOW_DAYS)).isoformat())


# ---------- IT home ----------

@router.get("/it")
def it_home(request: Request, user=Depends(require_it)):
    today, warranty_soon, renew_soon = _dates()
    n_active = db.query_one(
        "SELECT COUNT(*) AS n FROM assets WHERE status IN ('in_service', 'spare', 'repair')")["n"]
    warranties = db.query(
        """SELECT a.id, a.tag, a.name, a.warranty_end, u.name AS assignee FROM assets a
           LEFT JOIN users u ON u.id = a.assigned_to
           WHERE a.status IN ('in_service', 'spare', 'repair') AND a.warranty_end != ''
             AND a.warranty_end <= ? ORDER BY a.warranty_end""", (warranty_soon,))
    renewals = db.query(
        """SELECT l.id, l.name, l.vendor, l.renewal_date, l.auto_renew, l.cost_cents, l.billing
           FROM licenses l WHERE l.status = 'active' AND l.renewal_date != ''
             AND l.renewal_date <= ? ORDER BY l.renewal_date""", (renew_soon,))
    reviews = db.query(
        """SELECT d.id, d.section, d.title, d.review_date, d.status, u.name AS owner
           FROM it_docs d LEFT JOIN users u ON u.id = d.owner_id
           WHERE d.status != 'retired' AND d.review_date != '' AND d.review_date <= ?
           ORDER BY d.review_date""", (today,))
    drafts = db.query_one("SELECT COUNT(*) AS n FROM it_docs WHERE status = 'draft'")["n"]
    n_licenses = db.query_one("SELECT COUNT(*) AS n FROM licenses WHERE status = 'active'")["n"]
    annual = db.query_one(
        """SELECT COALESCE(SUM(CASE billing WHEN 'monthly' THEN cost_cents * 12
                                            WHEN 'annual' THEN cost_cents ELSE 0 END), 0) AS c
           FROM licenses WHERE status = 'active'""")["c"]
    n_vendors = db.query_one("SELECT COUNT(*) AS n FROM vendors")["n"]
    stale = (date.today() - timedelta(days=90)).isoformat()
    access = db.query_one(
        """SELECT SUM(CASE WHEN g.last_review = '' OR g.last_review < ? THEN 1 ELSE 0 END) AS unreviewed,
                  SUM(CASE WHEN g.mfa = 0 THEN 1 ELSE 0 END) AS no_mfa,
                  SUM(CASE WHEN u.id IS NOT NULL AND u.active = 0 THEN 1 ELSE 0 END) AS orphaned
           FROM access_grants g LEFT JOIN users u ON u.id = g.user_id WHERE g.revoked = ''""",
        (stale,))
    test_due = (date.today() - timedelta(days=180)).isoformat()
    backups_due = db.query_one(
        """SELECT COUNT(*) AS n FROM backups b WHERE b.active = 1 AND COALESCE(
             (SELECT MAX(tested_on) FROM backup_tests t WHERE t.backup_id = b.id), '') < ?""",
        (test_due,))["n"]
    incidents = db.query(
        """SELECT i.id, i.title, i.severity, i.status, i.detected_on, u.name AS lead
           FROM incidents i LEFT JOIN users u ON u.id = i.lead_id WHERE i.status != 'closed'
           ORDER BY CASE i.severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1
                                    WHEN 'medium' THEN 2 ELSE 3 END, i.detected_on""")
    return page(request, "it_home.html", user=user, tab="home", today=today,
                n_active=n_active, warranties=warranties, renewals=renewals, reviews=reviews,
                drafts=drafts, n_licenses=n_licenses, annual=annual, n_vendors=n_vendors,
                access=access, backups_due=backups_due, incidents=incidents,
                section_labels=SECTION_LABELS)



# ---------- assets ----------

def _asset_rows(where="1=1", args=()):
    return db.query(
        f"""SELECT a.*, u.name AS assignee
            FROM assets a LEFT JOIN users u ON u.id = a.assigned_to
            WHERE {where}
            ORDER BY CASE a.status WHEN 'in_service' THEN 0 WHEN 'repair' THEN 1
                                   WHEN 'spare' THEN 2 WHEN 'retired' THEN 3 ELSE 4 END,
                     a.tag, a.name""", args)


def _asset_filters(q: str, status: str, type_: str):
    where, args = [], []
    q = q.strip()
    if q:
        like = f"%{q}%"
        where.append("""(a.tag LIKE ? OR a.name LIKE ? OR a.make LIKE ? OR a.model LIKE ?
                         OR a.serial LIKE ? OR a.location LIKE ? OR a.notes LIKE ? OR u.name LIKE ?)""")
        args += [like] * 8
    if status == "active":
        where.append("a.status IN ('in_service', 'spare', 'repair')")
    elif status:
        where.append("a.status = ?")
        args.append(_check_enum(status, STATUSES, "status"))
    if type_:
        where.append("a.type = ?")
        args.append(_check_enum(type_, ASSET_TYPES, "type"))
    return " AND ".join(where) or "1=1", tuple(args)


def _get_asset(asset_id: int):
    asset = db.query_one("SELECT * FROM assets WHERE id = ?", (asset_id,))
    if not asset:
        raise HTTPException(404, "asset not found")
    return asset


@router.get("/it/assets")
def assets_list(request: Request, q: str = "", status: str = "active", type: str = "",
                user=Depends(require_it)):
    where, args = _asset_filters(q, status, type)
    rows = _asset_rows(where, args)
    today, soon, _ = _dates()
    counts = {r["status"]: r["n"] for r in db.query(
        "SELECT status, COUNT(*) AS n FROM assets GROUP BY status")}
    n_active = sum(counts.get(s, 0) for s in ACTIVE_STATUSES)
    warranty_soon = db.query_one(
        """SELECT COUNT(*) AS n FROM assets
           WHERE status IN ('in_service', 'spare', 'repair')
             AND warranty_end != '' AND warranty_end BETWEEN ? AND ?""", (today, soon))["n"]
    unassigned = db.query_one(
        "SELECT COUNT(*) AS n FROM assets WHERE status = 'in_service' AND assigned_to IS NULL")["n"]
    value = db.query_one(
        """SELECT COALESCE(SUM(cost_cents), 0) AS c FROM assets
           WHERE status IN ('in_service', 'spare', 'repair')""")["c"]
    qs = "&".join(f"{k}={v}" for k, v in (("q", q), ("status", status), ("type", type)) if v)
    return page(request, "it_assets.html", user=user, tab="assets",
                rows=rows, q=q, status=status, type=type, qs=("?" + qs) if qs else "",
                today=today, soon=soon, counts=counts, n_active=n_active,
                warranty_soon=warranty_soon, unassigned=unassigned, value=value,
                types=ASSET_TYPES, type_labels=TYPE_LABELS,
                statuses=STATUSES, status_labels=STATUS_LABELS, people=_people())


def _asset_values(form):
    return (form["tag"].strip(), form["name"].strip(),
            _check_enum(form["type"], ASSET_TYPES, "type"),
            form["make"].strip(), form["model"].strip(), form["serial"].strip(),
            _opt_date(form["purchase_date"]), _opt_cents(form["cost"]), form["vendor"].strip(),
            _opt_date(form["in_service_date"]), _check_enum(form["status"], STATUSES, "status"),
            _opt_user(form["assigned_to"]), form["location"].strip(),
            _opt_date(form["warranty_end"]), form["notes"].strip())


@router.post("/it/assets/add")
def asset_add(user=Depends(require_it),
              tag: str = Form(""), name: str = Form(...), type: str = Form("other"),
              make: str = Form(""), model: str = Form(""), serial: str = Form(""),
              purchase_date: str = Form(""), cost: str = Form(""), vendor: str = Form(""),
              in_service_date: str = Form(""), status: str = Form("in_service"),
              assigned_to: str = Form(""), location: str = Form(""),
              warranty_end: str = Form(""), notes: str = Form("")):
    values = _asset_values(locals())
    db.execute(
        """INSERT INTO assets (tag, name, type, make, model, serial, purchase_date, cost_cents,
                               vendor, in_service_date, status, assigned_to, location,
                               warranty_end, notes, created_by)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", values + (user["id"],))
    return RedirectResponse("/it/assets?msg=Asset+added", status_code=303)


@router.get("/it/assets/{asset_id}/edit")
def asset_edit_form(request: Request, asset_id: int, user=Depends(require_it)):
    return page(request, "asset_edit.html", user=user, asset=_get_asset(asset_id), tab="assets",
                types=ASSET_TYPES, type_labels=TYPE_LABELS,
                statuses=STATUSES, status_labels=STATUS_LABELS, people=_people())


@router.post("/it/assets/{asset_id}/edit")
def asset_edit(asset_id: int, user=Depends(require_it),
               tag: str = Form(""), name: str = Form(...), type: str = Form("other"),
               make: str = Form(""), model: str = Form(""), serial: str = Form(""),
               purchase_date: str = Form(""), cost: str = Form(""), vendor: str = Form(""),
               in_service_date: str = Form(""), status: str = Form("in_service"),
               assigned_to: str = Form(""), location: str = Form(""),
               warranty_end: str = Form(""), notes: str = Form("")):
    _get_asset(asset_id)
    values = _asset_values(locals())
    db.execute(
        """UPDATE assets SET tag = ?, name = ?, type = ?, make = ?, model = ?, serial = ?,
           purchase_date = ?, cost_cents = ?, vendor = ?, in_service_date = ?, status = ?,
           assigned_to = ?, location = ?, warranty_end = ?, notes = ?,
           updated_at = datetime('now') WHERE id = ?""", values + (asset_id,))
    return RedirectResponse("/it/assets?msg=Asset+saved", status_code=303)


@router.post("/it/assets/{asset_id}/status")
def asset_status(asset_id: int, status: str = Form(...), user=Depends(require_it)):
    _get_asset(asset_id)
    db.execute("UPDATE assets SET status = ?, updated_at = datetime('now') WHERE id = ?",
               (_check_enum(status, STATUSES, "status"), asset_id))
    return RedirectResponse("/it/assets", status_code=303)


@router.post("/it/assets/{asset_id}/delete")
def asset_delete(asset_id: int, user=Depends(require_it)):
    _get_asset(asset_id)
    db.execute("DELETE FROM assets WHERE id = ?", (asset_id,))
    return RedirectResponse("/it/assets?msg=Asset+deleted", status_code=303)


@router.get("/it/assets/export/assets.csv")
def assets_export(q: str = "", status: str = "", type: str = "", user=Depends(require_it)):
    where, args = _asset_filters(q, status, type)
    return _csv("assets.csv",
                ["Tag", "Name", "Type", "Make", "Model", "Serial", "Purchase date", "Cost",
                 "Vendor", "In service", "Status", "Assigned to", "Location", "Warranty end", "Notes"],
                [[a["tag"], a["name"], TYPE_LABELS.get(a["type"], a["type"]), a["make"], a["model"],
                  a["serial"], a["purchase_date"],
                  f"{a['cost_cents'] / 100:.2f}" if a["cost_cents"] is not None else "",
                  a["vendor"], a["in_service_date"], STATUS_LABELS.get(a["status"], a["status"]),
                  a["assignee"] or "", a["location"], a["warranty_end"], a["notes"]]
                 for a in _asset_rows(where, args)])


# ---------- software & licenses ----------

def _license_rows(where="1=1", args=()):
    return db.query(
        f"""SELECT l.*, u.name AS owner FROM licenses l LEFT JOIN users u ON u.id = l.owner_id
            WHERE {where}
            ORDER BY l.status, CASE WHEN l.renewal_date = '' THEN 1 ELSE 0 END, l.renewal_date, l.name""",
        args)


def _license_filters(q: str, status: str, category: str):
    where, args = [], []
    q = q.strip()
    if q:
        like = f"%{q}%"
        where.append("(l.name LIKE ? OR l.vendor LIKE ? OR l.account LIKE ? OR l.notes LIKE ? OR u.name LIKE ?)")
        args += [like] * 5
    if status:
        where.append("l.status = ?")
        args.append(_check_enum(status, ("active", "cancelled"), "status"))
    if category:
        where.append("l.category = ?")
        args.append(_check_enum(category, LICENSE_CATEGORIES, "category"))
    return " AND ".join(where) or "1=1", tuple(args)


def _get_license(license_id: int):
    row = db.query_one("SELECT * FROM licenses WHERE id = ?", (license_id,))
    if not row:
        raise HTTPException(404, "license not found")
    return row


def _license_ctx():
    return dict(categories=LICENSE_CATEGORIES, cat_labels=LICENSE_CAT_LABELS,
                billing=BILLING, billing_labels=BILLING_LABELS, people=_people())


@router.get("/it/licenses")
def licenses_list(request: Request, q: str = "", status: str = "active", category: str = "",
                  user=Depends(require_it)):
    where, args = _license_filters(q, status, category)
    rows = _license_rows(where, args)
    today, _, renew_soon = _dates()
    n_active = db.query_one("SELECT COUNT(*) AS n FROM licenses WHERE status = 'active'")["n"]
    due = db.query_one(
        """SELECT COUNT(*) AS n FROM licenses WHERE status = 'active' AND renewal_date != ''
           AND renewal_date <= ?""", (renew_soon,))["n"]
    annual = db.query_one(
        """SELECT COALESCE(SUM(CASE billing WHEN 'monthly' THEN cost_cents * 12
                                            WHEN 'annual' THEN cost_cents ELSE 0 END), 0) AS c
           FROM licenses WHERE status = 'active'""")["c"]
    seats = db.query_one(
        "SELECT COALESCE(SUM(seats), 0) AS n FROM licenses WHERE status = 'active'")["n"]
    qs = "&".join(f"{k}={v}" for k, v in (("q", q), ("status", status), ("category", category)) if v)
    return page(request, "it_licenses.html", user=user, tab="licenses", rows=rows,
                q=q, status=status, category=category, qs=("?" + qs) if qs else "",
                today=today, renew_soon=renew_soon, n_active=n_active, due=due,
                annual=annual, seats=seats, **_license_ctx())


def _license_values(form):
    return (form["name"].strip(), form["vendor"].strip(),
            _check_enum(form["category"], LICENSE_CATEGORIES, "category"),
            _opt_int(form["seats"], "seats"), _opt_cents(form["cost"]),
            _check_enum(form["billing"], BILLING, "billing"), _opt_date(form["renewal_date"]),
            1 if form["auto_renew"] else 0, _opt_user(form["owner_id"]),
            form["login_url"].strip(), form["account"].strip(),
            _check_enum(form["status"], ("active", "cancelled"), "status"), form["notes"].strip())


@router.post("/it/licenses/add")
def license_add(user=Depends(require_it),
                name: str = Form(...), vendor: str = Form(""), category: str = Form("other"),
                seats: str = Form(""), cost: str = Form(""), billing: str = Form("annual"),
                renewal_date: str = Form(""), auto_renew: str = Form(""), owner_id: str = Form(""),
                login_url: str = Form(""), account: str = Form(""), status: str = Form("active"),
                notes: str = Form("")):
    values = _license_values(locals())
    db.execute(
        """INSERT INTO licenses (name, vendor, category, seats, cost_cents, billing, renewal_date,
                                 auto_renew, owner_id, login_url, account, status, notes, created_by)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", values + (user["id"],))
    return RedirectResponse("/it/licenses?msg=License+added", status_code=303)


@router.get("/it/licenses/{license_id}/edit")
def license_edit_form(request: Request, license_id: int, user=Depends(require_it)):
    return page(request, "license_edit.html", user=user, tab="licenses",
                lic=_get_license(license_id), **_license_ctx())


@router.post("/it/licenses/{license_id}/edit")
def license_edit(license_id: int, user=Depends(require_it),
                 name: str = Form(...), vendor: str = Form(""), category: str = Form("other"),
                 seats: str = Form(""), cost: str = Form(""), billing: str = Form("annual"),
                 renewal_date: str = Form(""), auto_renew: str = Form(""), owner_id: str = Form(""),
                 login_url: str = Form(""), account: str = Form(""), status: str = Form("active"),
                 notes: str = Form("")):
    _get_license(license_id)
    values = _license_values(locals())
    db.execute(
        """UPDATE licenses SET name = ?, vendor = ?, category = ?, seats = ?, cost_cents = ?,
           billing = ?, renewal_date = ?, auto_renew = ?, owner_id = ?, login_url = ?, account = ?,
           status = ?, notes = ?, updated_at = datetime('now') WHERE id = ?""",
        values + (license_id,))
    return RedirectResponse("/it/licenses?msg=License+saved", status_code=303)


@router.post("/it/licenses/{license_id}/delete")
def license_delete(license_id: int, user=Depends(require_it)):
    _get_license(license_id)
    db.execute("DELETE FROM licenses WHERE id = ?", (license_id,))
    return RedirectResponse("/it/licenses?msg=License+deleted", status_code=303)


@router.get("/it/licenses/export/licenses.csv")
def licenses_export(q: str = "", status: str = "", category: str = "", user=Depends(require_it)):
    where, args = _license_filters(q, status, category)
    return _csv("licenses.csv",
                ["Name", "Vendor", "Category", "Seats", "Cost", "Billing", "Renewal date",
                 "Auto-renew", "Owner", "Login URL", "Account", "Status", "Notes"],
                [[l["name"], l["vendor"], LICENSE_CAT_LABELS.get(l["category"], l["category"]),
                  l["seats"] if l["seats"] is not None else "",
                  f"{l['cost_cents'] / 100:.2f}" if l["cost_cents"] is not None else "",
                  BILLING_LABELS.get(l["billing"], l["billing"]), l["renewal_date"],
                  "yes" if l["auto_renew"] else "no", l["owner"] or "", l["login_url"],
                  l["account"], l["status"], l["notes"]]
                 for l in _license_rows(where, args)])


# ---------- vendors & support contacts ----------

def _get_vendor(vendor_id: int):
    row = db.query_one("SELECT * FROM vendors WHERE id = ?", (vendor_id,))
    if not row:
        raise HTTPException(404, "vendor not found")
    return row


@router.get("/it/vendors")
def vendors_list(request: Request, q: str = "", category: str = "", user=Depends(require_it)):
    where, args = [], []
    q = q.strip()
    if q:
        like = f"%{q}%"
        where.append("(name LIKE ? OR account_no LIKE ? OR contact_name LIKE ? OR notes LIKE ?)")
        args += [like] * 4
    if category:
        where.append("category = ?")
        args.append(_check_enum(category, VENDOR_CATEGORIES, "category"))
    rows = db.query(f"""SELECT * FROM vendors WHERE {' AND '.join(where) or '1=1'}
                        ORDER BY category, name""", tuple(args))
    today, soon, _ = _dates()
    return page(request, "it_vendors.html", user=user, tab="vendors", rows=rows, q=q,
                category=category, today=today, soon=soon,
                categories=VENDOR_CATEGORIES, cat_labels=VENDOR_CAT_LABELS)


def _vendor_values(form):
    return (form["name"].strip(), _check_enum(form["category"], VENDOR_CATEGORIES, "category"),
            form["account_no"].strip(), form["contact_name"].strip(),
            form["support_phone"].strip(), form["support_email"].strip(),
            form["portal_url"].strip(), _opt_date(form["contract_end"]), form["notes"].strip())


@router.post("/it/vendors/add")
def vendor_add(user=Depends(require_it),
               name: str = Form(...), category: str = Form("other"), account_no: str = Form(""),
               contact_name: str = Form(""), support_phone: str = Form(""),
               support_email: str = Form(""), portal_url: str = Form(""),
               contract_end: str = Form(""), notes: str = Form("")):
    db.execute(
        """INSERT INTO vendors (name, category, account_no, contact_name, support_phone,
                                support_email, portal_url, contract_end, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""", _vendor_values(locals()))
    return RedirectResponse("/it/vendors?msg=Vendor+added", status_code=303)


@router.get("/it/vendors/{vendor_id}/edit")
def vendor_edit_form(request: Request, vendor_id: int, user=Depends(require_it)):
    return page(request, "vendor_edit.html", user=user, tab="vendors",
                vendor=_get_vendor(vendor_id),
                categories=VENDOR_CATEGORIES, cat_labels=VENDOR_CAT_LABELS)


@router.post("/it/vendors/{vendor_id}/edit")
def vendor_edit(vendor_id: int, user=Depends(require_it),
                name: str = Form(...), category: str = Form("other"), account_no: str = Form(""),
                contact_name: str = Form(""), support_phone: str = Form(""),
                support_email: str = Form(""), portal_url: str = Form(""),
                contract_end: str = Form(""), notes: str = Form("")):
    _get_vendor(vendor_id)
    db.execute(
        """UPDATE vendors SET name = ?, category = ?, account_no = ?, contact_name = ?,
           support_phone = ?, support_email = ?, portal_url = ?, contract_end = ?, notes = ?,
           updated_at = datetime('now') WHERE id = ?""", _vendor_values(locals()) + (vendor_id,))
    return RedirectResponse("/it/vendors?msg=Vendor+saved", status_code=303)


@router.post("/it/vendors/{vendor_id}/delete")
def vendor_delete(vendor_id: int, user=Depends(require_it)):
    _get_vendor(vendor_id)
    db.execute("DELETE FROM vendors WHERE id = ?", (vendor_id,))
    return RedirectResponse("/it/vendors?msg=Vendor+deleted", status_code=303)
