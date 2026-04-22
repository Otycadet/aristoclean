import csv
import json
from datetime import date

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import models as db_models
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views import View

from .forms import (
    DeliveryHeaderForm,
    IssueBatchHeaderForm,
    IssueLineForm,
    ItemForm,
    LocationForm,
    ReportFilterForm,
    StockEntryLineForm,
)
from .models import DistributionLine, IssueBatch, Item, Location, StockEntry, UserProfile
from .permissions import ManagerRequiredMixin, manager_required


# ── Dashboard ──────────────────────────────────────────────────────────────

@login_required
def dashboard(request):
    items = Item.objects.filter(active=True)
    stock_data = []
    low_stock_count = 0
    for item in items:
        cs = item.current_stock
        low = cs <= item.reorder_level
        if low:
            low_stock_count += 1
        stock_data.append({
            "item": item,
            "current_stock": cs,
            "total_received": item.total_received,
            "total_issued": item.total_issued,
            "low": low,
        })

    recent_batches = IssueBatch.objects.select_related("location").prefetch_related("lines")[:5]
    return render(request, "inventory/dashboard.html", {
        "stock_data": stock_data,
        "low_stock_count": low_stock_count,
        "recent_batches": recent_batches,
        "total_items": len(stock_data),
    })


# ── Stock (incoming deliveries) ────────────────────────────────────────────

@login_required
def stock_list(request):
    items = Item.objects.filter(active=True)
    stock_data = []
    for item in items:
        cs = item.current_stock
        stock_data.append({
            "item": item,
            "current_stock": cs,
            "total_received": item.total_received,
            "total_issued": item.total_issued,
            "low": cs <= item.reorder_level,
        })
    return render(request, "inventory/stock_list.html", {"stock_data": stock_data})


@login_required
def stock_receive(request):
    """Multi-item incoming delivery form."""
    header_form = DeliveryHeaderForm(request.POST or None, initial={"received_at": date.today()})
    error = None

    if request.method == "POST":
        # Parse dynamic line data from POST
        lines = _parse_delivery_lines(request.POST)
        if header_form.is_valid() and lines is not None:
            hd = header_form.cleaned_data
            try:
                with transaction.atomic():
                    for line in lines:
                        name = line["item_name"].strip()
                        unit = line["unit"].strip()
                        reorder = float(line["reorder_level"])
                        qty = float(line["quantity"])
                        if not name:
                            raise ValueError("Each line must have an item name.")
                        if qty <= 0:
                            raise ValueError(f"Quantity must be > 0 for {name}.")
                        item, _ = Item.objects.update_or_create(
                            name__iexact=name,
                            defaults={"name": name, "unit": unit,
                                      "reorder_level": reorder, "active": True},
                        )
                        # update_or_create with iexact needs a workaround:
                        item = _get_or_create_item(name, unit, reorder)
                        StockEntry.objects.create(
                            item=item,
                            quantity=qty,
                            supplier=hd["supplier"],
                            reference=hd["reference"],
                            notes=hd["notes"],
                            received_at=hd["received_at"],
                            created_by=request.user,
                        )
                messages.success(request, f"Delivery of {len(lines)} item(s) recorded.")
                return redirect("stock_list")
            except ValueError as e:
                error = str(e)
        else:
            if lines is None:
                error = "Add at least one item line."

    items_json = json.dumps([
        {"name": i.name, "unit": i.unit, "reorder_level": i.reorder_level}
        for i in Item.objects.filter(active=True)
    ])
    return render(request, "inventory/stock_receive.html", {
        "header_form": header_form,
        "items_json": items_json,
        "error": error,
    })


def _get_or_create_item(name: str, unit: str, reorder_level: float) -> Item:
    """Case-insensitive get-or-create for Item."""
    try:
        item = Item.objects.get(name__iexact=name)
        item.unit = unit
        item.reorder_level = reorder_level
        item.active = True
        item.save(update_fields=["unit", "reorder_level", "active"])
        return item
    except Item.DoesNotExist:
        return Item.objects.create(
            name=name, unit=unit, reorder_level=reorder_level
        )


def _parse_delivery_lines(post_data):
    """Extract delivery line dicts from flat POST keys: line_item_0, line_unit_0, …"""
    lines = []
    i = 0
    while f"line_item_{i}" in post_data:
        name = post_data.get(f"line_item_{i}", "").strip()
        unit = post_data.get(f"line_unit_{i}", "").strip()
        reorder = post_data.get(f"line_reorder_{i}", "0").strip() or "0"
        qty = post_data.get(f"line_qty_{i}", "").strip()
        if name and qty:
            try:
                lines.append({
                    "item_name": name,
                    "unit": unit or "units",
                    "reorder_level": float(reorder),
                    "quantity": float(qty),
                })
            except ValueError:
                pass
        i += 1
    return lines if lines else None


# ── Issue stock ────────────────────────────────────────────────────────────

@login_required
def issue_stock(request):
    header_form = IssueBatchHeaderForm(request.POST or None, initial={"issued_at": date.today()})
    error = None

    if request.method == "POST":
        lines = _parse_issue_lines(request.POST)
        if header_form.is_valid() and lines is not None:
            hd = header_form.cleaned_data
            try:
                batch = _create_issue_batch(hd, lines, request.user)
                messages.success(request, f"Receipt {batch.receipt_number} created.")
                return redirect("receipt_detail", receipt_number=batch.receipt_number)
            except ValueError as e:
                error = str(e)
        else:
            if lines is None:
                error = "Add at least one item line to the receipt."

    items_json = json.dumps([
        {"name": i.name, "unit": i.unit, "stock": i.current_stock}
        for i in Item.objects.filter(active=True)
    ])
    locations_json = json.dumps([
        loc.name for loc in Location.objects.filter(active=True)
    ])
    return render(request, "inventory/issue_stock.html", {
        "header_form": header_form,
        "items_json": items_json,
        "locations_json": locations_json,
        "error": error,
    })


def _parse_issue_lines(post_data):
    lines = []
    i = 0
    while f"issue_item_{i}" in post_data:
        name = post_data.get(f"issue_item_{i}", "").strip()
        qty = post_data.get(f"issue_qty_{i}", "").strip()
        if name and qty:
            try:
                lines.append({"item_name": name, "quantity": float(qty)})
            except ValueError:
                pass
        i += 1
    return lines if lines else None


@transaction.atomic
def _create_issue_batch(hd: dict, lines: list, user) -> IssueBatch:
    location_name = hd["location"].strip()
    if not location_name:
        raise ValueError("Location is required.")

    try:
        location = Location.objects.get(name__iexact=location_name)
        location.active = True
        location.save(update_fields=["active"])
    except Location.DoesNotExist:
        location = Location.objects.create(name=location_name)

    # Group duplicate items
    grouped: dict[str, float] = {}
    for line in lines:
        key = line["item_name"].strip().lower()
        grouped[key] = grouped.get(key, 0.0) + line["quantity"]

    validated = []
    for name_lower, qty in grouped.items():
        try:
            item = Item.objects.get(name__iexact=name_lower)
        except Item.DoesNotExist:
            raise ValueError(f"'{name_lower}' does not exist. Add it via stock receive first.")
        if qty > item.current_stock:
            raise ValueError(
                f"Only {item.current_stock:,.2f} {item.unit} of {item.name} available."
            )
        validated.append((item, qty))

    batch = IssueBatch.objects.create(
        location=location,
        issued_to=hd.get("issued_to", ""),
        issued_by=hd.get("issued_by", ""),
        notes=hd.get("notes", ""),
        issued_at=hd["issued_at"],
        created_by=user,
    )
    # receipt_number is generated by model.save()

    for item, qty in validated:
        DistributionLine.objects.create(batch=batch, item=item, quantity=qty)

    return batch


# ── Receipts ───────────────────────────────────────────────────────────────

@login_required
def receipts_list(request):
    batches = (
        IssueBatch.objects
        .select_related("location")
        .prefetch_related("lines__item")
        .order_by("-issued_at", "-id")[:200]
    )
    return render(request, "inventory/receipts_list.html", {"batches": batches})


@login_required
def receipt_detail(request, receipt_number):
    batch = get_object_or_404(
        IssueBatch.objects.select_related("location").prefetch_related("lines__item"),
        receipt_number=receipt_number,
    )
    # Compute balance_after_issue per line (same logic as original)
    lines_with_balance = []
    for line in batch.lines.select_related("item"):
        # Sum all issues of this item up to and including this batch
        issued_up_to = (
            DistributionLine.objects
            .filter(
                item=line.item,
            )
            .filter(
                db_models.Q(batch__issued_at__lt=batch.issued_at) |
                db_models.Q(batch__issued_at=batch.issued_at, batch__id__lte=batch.id)
            )
            .aggregate(total=db_models.Sum("quantity"))["total"] or 0
        )
        total_received = line.item.total_received
        balance = total_received - issued_up_to
        lines_with_balance.append({
            "item_name": line.item.name,
            "unit": line.item.unit,
            "quantity": line.quantity,
            "balance_after_issue": balance,
        })

    return render(request, "inventory/receipt_detail.html", {
        "batch": batch,
        "lines": lines_with_balance,
    })


# ── Reports ────────────────────────────────────────────────────────────────

@login_required
def reports(request):
    form = ReportFilterForm(request.GET or None)
    summary_rows = []
    detail_rows = []
    ran = False

    if form.is_valid():
        ran = True
        year = int(form.cleaned_data["year"])
        month = int(form.cleaned_data["month"])
        location_name = form.cleaned_data.get("location") or None
        month_key = f"{year:04d}-{month:02d}"

        batch_qs = IssueBatch.objects.filter(
            issued_at__year=year, issued_at__month=month
        )
        if location_name:
            batch_qs = batch_qs.filter(location__name__iexact=location_name)

        # Summary: group by location + item
        from django.db.models import Sum
        summary_qs = (
            DistributionLine.objects
            .filter(batch__in=batch_qs)
            .values("item__name", "item__unit", "batch__location__name")
            .annotate(total=Sum("quantity"))
            .order_by("batch__location__name", "item__name")
        )
        summary_rows = [
            {
                "location": r["batch__location__name"],
                "item": r["item__name"],
                "unit": r["item__unit"],
                "total": r["total"],
            }
            for r in summary_qs
        ]

        detail_qs = (
            DistributionLine.objects
            .filter(batch__in=batch_qs)
            .select_related("batch__location", "item")
            .order_by("-batch__issued_at", "-batch__id", "item__name")
        )
        detail_rows = [
            {
                "receipt_number": dl.batch.receipt_number,
                "issued_at": dl.batch.issued_at,
                "location": dl.batch.location.name,
                "item": dl.item.name,
                "quantity": dl.quantity,
                "unit": dl.item.unit,
                "issued_to": dl.batch.issued_to,
                "issued_by": dl.batch.issued_by,
                "notes": dl.batch.notes,
            }
            for dl in detail_qs
        ]

    return render(request, "inventory/reports.html", {
        "form": form,
        "summary_rows": summary_rows,
        "detail_rows": detail_rows,
        "ran": ran,
    })


@login_required
def export_csv(request):
    """Export stock snapshot as CSV."""
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = (
        f'attachment; filename="stock_snapshot_{date.today().strftime("%Y%m%d")}.csv"'
    )
    writer = csv.writer(response)
    writer.writerow(["Item", "Unit", "Current Stock", "Reorder Level",
                     "Total Received", "Total Issued", "Status"])
    for item in Item.objects.filter(active=True):
        cs = item.current_stock
        status = "LOW STOCK" if cs <= item.reorder_level else "OK"
        writer.writerow([
            item.name, item.unit, f"{cs:,.2f}",
            f"{item.reorder_level:,.2f}",
            f"{item.total_received:,.2f}",
            f"{item.total_issued:,.2f}",
            status,
        ])
    return response


@login_required
def export_report_csv(request):
    """Export monthly distribution detail as CSV."""
    form = ReportFilterForm(request.GET)
    if not form.is_valid():
        return redirect("reports")
    year = int(form.cleaned_data["year"])
    month = int(form.cleaned_data["month"])
    location_name = form.cleaned_data.get("location") or None

    batch_qs = IssueBatch.objects.filter(issued_at__year=year, issued_at__month=month)
    if location_name:
        batch_qs = batch_qs.filter(location__name__iexact=location_name)

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = (
        f'attachment; filename="report_{year:04d}_{month:02d}.csv"'
    )
    writer = csv.writer(response)
    writer.writerow(["Receipt", "Date", "Location", "Item",
                     "Quantity", "Unit", "Issued To", "Issued By", "Notes"])
    qs = (
        DistributionLine.objects
        .filter(batch__in=batch_qs)
        .select_related("batch__location", "item")
        .order_by("-batch__issued_at", "item__name")
    )
    for dl in qs:
        writer.writerow([
            dl.batch.receipt_number, dl.batch.issued_at,
            dl.batch.location.name, dl.item.name,
            f"{dl.quantity:,.2f}", dl.item.unit,
            dl.batch.issued_to, dl.batch.issued_by, dl.batch.notes,
        ])
    return response


# ── Admin / management (manager only) ─────────────────────────────────────

@login_required
@manager_required
def manage_items(request):
    items = Item.objects.all().order_by("name")
    form = ItemForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        name = form.cleaned_data["name"].strip()
        try:
            item = Item.objects.get(name__iexact=name)
            item.unit = form.cleaned_data["unit"]
            item.reorder_level = form.cleaned_data["reorder_level"]
            item.active = form.cleaned_data["active"]
            item.save()
            messages.success(request, f"Updated {item.name}.")
        except Item.DoesNotExist:
            form.save()
            messages.success(request, f"Added {name}.")
        return redirect("manage_items")
    return render(request, "inventory/manage_items.html", {"items": items, "form": form})


@login_required
@manager_required
def manage_locations(request):
    locations = Location.objects.all().order_by("name")
    form = LocationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        name = form.cleaned_data["name"].strip()
        try:
            loc = Location.objects.get(name__iexact=name)
            loc.active = form.cleaned_data["active"]
            loc.save()
            messages.success(request, f"Updated {loc.name}.")
        except Location.DoesNotExist:
            form.save()
            messages.success(request, f"Added {name}.")
        return redirect("manage_locations")
    return render(request, "inventory/manage_locations.html", {
        "locations": locations, "form": form
    })


# ── AJAX helpers ───────────────────────────────────────────────────────────

@login_required
def item_stock_api(request, item_name):
    """Return current stock for autocomplete / validation."""
    try:
        item = Item.objects.get(name__iexact=item_name)
        return JsonResponse({"stock": item.current_stock, "unit": item.unit})
    except Item.DoesNotExist:
        return JsonResponse({"stock": None, "unit": None})
