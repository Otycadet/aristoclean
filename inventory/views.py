import csv
import json
import re
from datetime import date
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.paginator import Paginator
from django.db import models as db_models
from django.db import transaction
from django.db.models import DecimalField, ExpressionWrapper, F, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .forms import (
    DeliveryHeaderForm,
    IssueBatchHeaderForm,
    ItemForm,
    LocationForm,
    ReceiptFilterForm,
    ReceiptVoidForm,
    ReportFilterForm,
    StockAdjustmentForm,
    UserCreateForm,
    UserUpdateForm,
)
from .models import DistributionLine, IssueBatch, Item, Location, StockAdjustment, StockEntry, UserProfile
from .permissions import get_user_profile, manager_required, stock_operator_required


DECIMAL_OUTPUT = DecimalField(max_digits=12, decimal_places=2)
ZERO_DECIMAL = Value(Decimal("0.00"), output_field=DECIMAL_OUTPUT)


def _annotate_item_stock(queryset=None):
    queryset = queryset or Item.objects.all()
    received_subquery = (
        StockEntry.objects.filter(item=OuterRef("pk"))
        .values("item")
        .annotate(total=Coalesce(Sum("quantity"), ZERO_DECIMAL))
        .values("total")[:1]
    )
    issued_subquery = (
        DistributionLine.objects.filter(item=OuterRef("pk"), batch__is_voided=False)
        .values("item")
        .annotate(total=Coalesce(Sum("quantity"), ZERO_DECIMAL))
        .values("total")[:1]
    )
    adjustment_subquery = (
        StockAdjustment.objects.filter(item=OuterRef("pk"))
        .values("item")
        .annotate(total=Coalesce(Sum("quantity_delta"), ZERO_DECIMAL))
        .values("total")[:1]
    )
    queryset = queryset.annotate(
        total_received_db=Coalesce(Subquery(received_subquery, output_field=DECIMAL_OUTPUT), ZERO_DECIMAL),
        total_issued_db=Coalesce(Subquery(issued_subquery, output_field=DECIMAL_OUTPUT), ZERO_DECIMAL),
        total_adjustments_db=Coalesce(Subquery(adjustment_subquery, output_field=DECIMAL_OUTPUT), ZERO_DECIMAL),
    ).annotate(
        current_stock_db=ExpressionWrapper(
            F("total_received_db") - F("total_issued_db") + F("total_adjustments_db"),
            output_field=DECIMAL_OUTPUT,
        )
    )
    return queryset


def _decimal_from_post(value, field_name):
    try:
        parsed = Decimal(str(value).strip())
    except (InvalidOperation, TypeError):
        raise ValueError(f"Enter a valid number for {field_name}.")
    return parsed.quantize(Decimal("0.01"))


def _serialize_items_for_js(items_queryset):
    return json.dumps([
        {
            "id": item.id,
            "name": item.name,
            "unit": item.unit,
            "reorder_level": float(item.reorder_level),
            "stock": float(getattr(item, "current_stock_db", item.current_stock)),
        }
        for item in items_queryset
    ])


def _display_name_for_user(user):
    return user.get_full_name().strip() or user.username


@login_required
def dashboard(request):
    items = _annotate_item_stock(Item.objects.filter(active=True)).order_by("name")
    stock_data = []
    for item in items:
        stock_data.append({
            "item": item,
            "current_stock": item.current_stock_db,
            "total_received": item.total_received_db,
            "total_issued": item.total_issued_db,
            "low": item.current_stock_db <= item.reorder_level,
            "reorder_shortfall": max(item.reorder_level - item.current_stock_db, Decimal("0.00")),
        })

    low_stock_items = [row for row in stock_data if row["low"]]
    recent_batches = (
        IssueBatch.objects.select_related("location", "voided_by")
        .prefetch_related("lines__item")
        .order_by("-issued_at", "-id")[:5]
    )
    recent_adjustments = (
        StockAdjustment.objects.select_related("item", "created_by")
        .order_by("-adjusted_at", "-id")[:5]
    )
    return render(request, "inventory/dashboard.html", {
        "stock_data": stock_data,
        "low_stock_count": len(low_stock_items),
        "low_stock_items": low_stock_items[:5],
        "recent_batches": recent_batches,
        "recent_adjustments": recent_adjustments,
        "total_items": len(stock_data),
    })


@login_required
def stock_list(request):
    items = _annotate_item_stock(Item.objects.filter(active=True)).order_by("name")
    stock_data = []
    for item in items:
        stock_data.append({
            "item": item,
            "current_stock": item.current_stock_db,
            "total_received": item.total_received_db,
            "total_issued": item.total_issued_db,
            "total_adjustments": item.total_adjustments_db,
            "low": item.current_stock_db <= item.reorder_level,
            "reorder_shortfall": max(item.reorder_level - item.current_stock_db, Decimal("0.00")),
        })
    return render(request, "inventory/stock_list.html", {"stock_data": stock_data})


@login_required
@stock_operator_required
def stock_receive(request):
    received_by_name = _display_name_for_user(request.user)
    header_form = DeliveryHeaderForm(
        request.POST or None,
        initial={"received_at": date.today()},
    )
    error = None

    if request.method == "POST":
        lines = _parse_delivery_lines(request.POST)
        if header_form.is_valid() and lines is not None:
            hd = header_form.cleaned_data
            try:
                with transaction.atomic():
                    for line in lines:
                        name = line["item_name"].strip()
                        unit = line["unit"].strip()
                        reorder = line["reorder_level"]
                        qty = line["quantity"]
                        if not name:
                            raise ValueError("Each line must have an item name.")
                        if qty <= 0:
                            raise ValueError(f"Quantity must be greater than 0 for {name}.")
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
            except ValueError as exc:
                error = str(exc)
        elif lines is None:
            error = "Add at least one item line."

    items_qs = _annotate_item_stock(Item.objects.filter(active=True)).order_by("name")
    return render(request, "inventory/stock_receive.html", {
        "header_form": header_form,
        "items_json": _serialize_items_for_js(items_qs),
        "error": error,
        "received_by_name": received_by_name,
    })


def _get_or_create_item(name: str, unit: str, reorder_level: Decimal) -> Item:
    try:
        item = Item.objects.get(name__iexact=name)
        item.unit = unit
        item.reorder_level = reorder_level
        item.active = True
        item.save(update_fields=["unit", "reorder_level", "active"])
        return item
    except Item.DoesNotExist:
        return Item.objects.create(
            name=name,
            unit=unit,
            reorder_level=reorder_level,
        )


def _parse_delivery_lines(post_data):
    lines = []
    line_indices = sorted({
        int(match.group(1))
        for key in post_data.keys()
        for match in [re.match(r"^line_(?:item|new_item|unit|reorder|qty)_(\d+)$", key)]
        if match
    })

    for index in line_indices:
        existing_item_id = post_data.get(f"line_item_{index}", "").strip()
        new_name = post_data.get(f"line_new_item_{index}", "").strip()
        unit = post_data.get(f"line_unit_{index}", "").strip()
        reorder = post_data.get(f"line_reorder_{index}", "0").strip() or "0"
        qty = post_data.get(f"line_qty_{index}", "").strip()

        if not any([existing_item_id, new_name, unit, reorder, qty]):
            continue
        if not qty:
            raise ValueError("Enter quantity for each delivery line you add.")

        if existing_item_id:
            try:
                item = Item.objects.get(pk=int(existing_item_id), active=True)
            except (Item.DoesNotExist, ValueError):
                raise ValueError("Select a valid active item from the dropdown.")
            item_name = item.name
            unit = unit or item.unit
            reorder_value = _decimal_from_post(reorder, f"reorder level for {item_name}")
        else:
            if not new_name:
                raise ValueError("Each delivery line must select an existing item or enter a new item name.")
            item_name = new_name
            unit = unit or "units"
            reorder_value = _decimal_from_post(reorder, f"reorder level for {item_name}")

        lines.append({
            "item_name": item_name,
            "unit": unit,
            "reorder_level": reorder_value,
            "quantity": _decimal_from_post(qty, f"quantity for {item_name}"),
        })

    return lines if lines else None


@login_required
@stock_operator_required
def issue_stock(request):
    issued_by_name = _display_name_for_user(request.user)
    header_form = IssueBatchHeaderForm(
        request.POST or None,
        initial={
            "issued_at": date.today(),
            "issued_by": issued_by_name,
        },
    )
    error = None

    if request.method == "POST":
        lines = _parse_issue_lines(request.POST)
        if header_form.is_valid() and lines is not None:
            hd = header_form.cleaned_data
            hd["issued_by"] = issued_by_name
            try:
                batch = _create_issue_batch(hd, lines, request.user)
                messages.success(request, f"Receipt {batch.receipt_number} created.")
                return redirect("receipt_detail", receipt_number=batch.receipt_number)
            except ValueError as exc:
                error = str(exc)
        elif lines is None:
            error = "Add at least one item line to the receipt."

    items_qs = _annotate_item_stock(Item.objects.filter(active=True)).order_by("name")
    return render(request, "inventory/issue_stock.html", {
        "header_form": header_form,
        "items_json": _serialize_items_for_js(items_qs),
        "error": error,
        "issued_by_name": issued_by_name,
    })


def _parse_issue_lines(post_data):
    lines = []
    index = 0
    while f"issue_item_{index}" in post_data:
        item_id = post_data.get(f"issue_item_{index}", "").strip()
        qty = post_data.get(f"issue_qty_{index}", "").strip()
        if item_id and qty:
            try:
                item = Item.objects.get(pk=int(item_id), active=True)
            except (Item.DoesNotExist, ValueError):
                raise ValueError("Select valid items from the list of active stock items.")
            lines.append({"item": item, "quantity": _decimal_from_post(qty, f"quantity for {item.name}")})
        index += 1
    return lines if lines else None


@transaction.atomic
def _create_issue_batch(hd: dict, lines: list, user) -> IssueBatch:
    location = hd["location"]
    if not location.active:
        raise ValueError("Select an active location.")

    grouped = {}
    for line in lines:
        item = line["item"]
        grouped[item.pk] = grouped.get(item.pk, Decimal("0.00")) + line["quantity"]

    annotated_items = {
        item.pk: item
        for item in _annotate_item_stock(Item.objects.filter(pk__in=grouped.keys()))
    }

    validated = []
    for item_id, qty in grouped.items():
        item = annotated_items[item_id]
        if qty <= 0:
            raise ValueError(f"Quantity must be greater than 0 for {item.name}.")
        if qty > item.current_stock_db:
            raise ValueError(f"Only {item.current_stock_db:,.2f} {item.unit} of {item.name} available.")
        validated.append((item, qty))

    batch = IssueBatch.objects.create(
        location=location,
        issued_to=hd.get("issued_to", ""),
        issued_by=hd.get("issued_by", ""),
        notes=hd.get("notes", ""),
        issued_at=hd["issued_at"],
        created_by=user,
    )
    for item, qty in validated:
        DistributionLine.objects.create(batch=batch, item=item, quantity=qty)
    return batch


@login_required
def receipts_list(request):
    form = ReceiptFilterForm(request.GET or None)
    batches = IssueBatch.objects.select_related("location", "voided_by").prefetch_related("lines__item")

    if form.is_valid():
        q = form.cleaned_data.get("q")
        location = form.cleaned_data.get("location")
        status = form.cleaned_data.get("status")
        date_from = form.cleaned_data.get("date_from")
        date_to = form.cleaned_data.get("date_to")

        if q:
            batches = batches.filter(
                Q(receipt_number__icontains=q)
                | Q(issued_to__icontains=q)
                | Q(issued_by__icontains=q)
                | Q(location__name__icontains=q)
                | Q(lines__item__name__icontains=q)
            ).distinct()
        if location:
            batches = batches.filter(location__name__iexact=location)
        if status == "active":
            batches = batches.filter(is_voided=False)
        elif status == "voided":
            batches = batches.filter(is_voided=True)
        if date_from:
            batches = batches.filter(issued_at__gte=date_from)
        if date_to:
            batches = batches.filter(issued_at__lte=date_to)

    paginator = Paginator(batches.order_by("-issued_at", "-id"), 25)
    page_obj = paginator.get_page(request.GET.get("page"))
    return render(request, "inventory/receipts_list.html", {
        "form": form,
        "page_obj": page_obj,
        "batches": page_obj.object_list,
    })


@login_required
def receipt_detail(request, receipt_number):
    batch = get_object_or_404(
        IssueBatch.objects.select_related("location", "created_by", "voided_by").prefetch_related("lines__item"),
        receipt_number=receipt_number,
    )
    void_form = ReceiptVoidForm()
    receipt_lines = []
    for line in batch.lines.select_related("item"):
        receipt_lines.append({
            "item_name": line.item.name,
            "unit": line.item.unit,
            "quantity": line.quantity,
        })

    return render(request, "inventory/receipt_detail.html", {
        "batch": batch,
        "lines": receipt_lines,
        "void_form": void_form,
    })


@login_required
@manager_required
@transaction.atomic
def void_receipt(request, receipt_number):
    batch = get_object_or_404(IssueBatch, receipt_number=receipt_number)
    if request.method != "POST":
        return redirect("receipt_detail", receipt_number=receipt_number)
    if batch.is_voided:
        messages.warning(request, f"Receipt {batch.receipt_number} is already voided.")
        return redirect("receipt_detail", receipt_number=receipt_number)

    form = ReceiptVoidForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Enter a reason before voiding a receipt.")
        return redirect("receipt_detail", receipt_number=receipt_number)

    batch.is_voided = True
    batch.void_reason = form.cleaned_data["reason"]
    batch.voided_at = timezone.now()
    batch.voided_by = request.user
    batch.save(update_fields=["is_voided", "void_reason", "voided_at", "voided_by"])
    messages.success(request, f"Receipt {batch.receipt_number} has been voided and stock has been restored.")
    return redirect("receipt_detail", receipt_number=receipt_number)


@login_required
@manager_required
def reports(request):
    form = ReportFilterForm(request.GET or None)
    summary_rows = []
    detail_page = None
    ran = False

    if form.is_valid():
        ran = True
        year = int(form.cleaned_data["year"])
        month = int(form.cleaned_data["month"])
        location_name = form.cleaned_data.get("location") or None
        query = form.cleaned_data.get("query") or ""

        batch_qs = IssueBatch.objects.filter(
            issued_at__year=year,
            issued_at__month=month,
            is_voided=False,
        )
        if location_name:
            batch_qs = batch_qs.filter(location__name__iexact=location_name)
        if query:
            batch_qs = batch_qs.filter(
                Q(receipt_number__icontains=query)
                | Q(issued_to__icontains=query)
                | Q(issued_by__icontains=query)
                | Q(location__name__icontains=query)
                | Q(lines__item__name__icontains=query)
            ).distinct()

        summary_qs = (
            DistributionLine.objects.filter(batch__in=batch_qs)
            .values("item__name", "item__unit", "batch__location__name")
            .annotate(total=Sum("quantity"))
            .order_by("batch__location__name", "item__name")
        )
        summary_rows = [
            {
                "location": row["batch__location__name"],
                "item": row["item__name"],
                "unit": row["item__unit"],
                "total": row["total"],
            }
            for row in summary_qs
        ]

        detail_qs = (
            DistributionLine.objects.filter(batch__in=batch_qs)
            .select_related("batch__location", "item")
            .order_by("-batch__issued_at", "-batch__id", "item__name")
        )
        paginator = Paginator(detail_qs, 25)
        detail_page = paginator.get_page(request.GET.get("page"))

    return render(request, "inventory/reports.html", {
        "form": form,
        "summary_rows": summary_rows,
        "detail_page": detail_page,
        "ran": ran,
    })


@login_required
@manager_required
def export_csv(request):
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = (
        f'attachment; filename="stock_snapshot_{date.today().strftime("%Y%m%d")}.csv"'
    )
    writer = csv.writer(response)
    writer.writerow([
        "Item", "Unit", "Current Stock", "Reorder Level", "Shortfall",
        "Total Received", "Total Issued", "Total Adjustments", "Status",
    ])
    for item in _annotate_item_stock(Item.objects.filter(active=True)).order_by("name"):
        status = "LOW STOCK" if item.current_stock_db <= item.reorder_level else "OK"
        shortfall = max(item.reorder_level - item.current_stock_db, Decimal("0.00"))
        writer.writerow([
            item.name,
            item.unit,
            f"{item.current_stock_db:,.2f}",
            f"{item.reorder_level:,.2f}",
            f"{shortfall:,.2f}",
            f"{item.total_received_db:,.2f}",
            f"{item.total_issued_db:,.2f}",
            f"{item.total_adjustments_db:,.2f}",
            status,
        ])
    return response


@login_required
@manager_required
def export_report_csv(request):
    form = ReportFilterForm(request.GET)
    if not form.is_valid():
        return redirect("reports")

    year = int(form.cleaned_data["year"])
    month = int(form.cleaned_data["month"])
    location_name = form.cleaned_data.get("location") or None
    query = form.cleaned_data.get("query") or ""

    batch_qs = IssueBatch.objects.filter(
        issued_at__year=year,
        issued_at__month=month,
        is_voided=False,
    )
    if location_name:
        batch_qs = batch_qs.filter(location__name__iexact=location_name)
    if query:
        batch_qs = batch_qs.filter(
            Q(receipt_number__icontains=query)
            | Q(issued_to__icontains=query)
            | Q(issued_by__icontains=query)
            | Q(location__name__icontains=query)
            | Q(lines__item__name__icontains=query)
        ).distinct()

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = (
        f'attachment; filename="report_{year:04d}_{month:02d}.csv"'
    )
    writer = csv.writer(response)
    writer.writerow(["Receipt", "Date", "Location", "Item", "Quantity", "Unit", "Issued To", "Issued By", "Notes"])
    qs = (
        DistributionLine.objects.filter(batch__in=batch_qs)
        .select_related("batch__location", "item")
        .order_by("-batch__issued_at", "item__name")
    )
    for line in qs:
        writer.writerow([
            line.batch.receipt_number,
            line.batch.issued_at,
            line.batch.location.name,
            line.item.name,
            f"{line.quantity:,.2f}",
            line.item.unit,
            line.batch.issued_to,
            line.batch.issued_by,
            line.batch.notes,
        ])
    return response


@login_required
def low_stock_report(request):
    items = _annotate_item_stock(Item.objects.filter(active=True)).order_by("name")
    low_stock_items = [item for item in items if item.current_stock_db <= item.reorder_level]
    return render(request, "inventory/low_stock_report.html", {
        "low_stock_items": low_stock_items,
    })


@login_required
def export_low_stock_csv(request):
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = (
        f'attachment; filename="low_stock_{date.today().strftime("%Y%m%d")}.csv"'
    )
    writer = csv.writer(response)
    writer.writerow(["Item", "Unit", "Current Stock", "Reorder Level", "Shortfall"])
    for item in _annotate_item_stock(Item.objects.filter(active=True)).order_by("name"):
        if item.current_stock_db <= item.reorder_level:
            writer.writerow([
                item.name,
                item.unit,
                f"{item.current_stock_db:,.2f}",
                f"{item.reorder_level:,.2f}",
                f"{max(item.reorder_level - item.current_stock_db, Decimal('0.00')):,.2f}",
            ])
    return response


@login_required
@manager_required
def stock_adjustment_create(request):
    form = StockAdjustmentForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        adjustment = form.save(commit=False)
        adjustment.quantity_delta = form.cleaned_data["quantity_delta"]
        adjustment.created_by = request.user

        item_with_stock = _annotate_item_stock(Item.objects.filter(pk=adjustment.item_id)).get()
        projected_stock = item_with_stock.current_stock_db + adjustment.quantity_delta
        if projected_stock < Decimal("0.00"):
            form.add_error("quantity", f"This adjustment would reduce {adjustment.item.name} below zero stock.")
        else:
            adjustment.save()
            messages.success(request, f"Stock adjustment recorded for {adjustment.item.name}.")
            return redirect("stock_list")

    adjustments = StockAdjustment.objects.select_related("item", "created_by").order_by("-adjusted_at", "-id")[:20]
    return render(request, "inventory/stock_adjustment_form.html", {
        "form": form,
        "adjustments": adjustments,
    })


@login_required
@manager_required
def manage_items(request):
    items = Item.objects.all().order_by("name")
    editing_item = None
    edit_item_id = request.GET.get("edit")
    if edit_item_id:
        editing_item = get_object_or_404(Item, pk=edit_item_id)

    if request.method == "POST":
        item_id = request.POST.get("item_id")
        if item_id:
            editing_item = get_object_or_404(Item, pk=item_id)
        form = ItemForm(request.POST, instance=editing_item)
    else:
        form = ItemForm(instance=editing_item)

    if request.method == "POST" and form.is_valid():
        item = form.save()
        action = "Updated" if item_id else "Added"
        messages.success(request, f"{action} {item.name}.")
        return redirect("manage_items")
    return render(request, "inventory/manage_items.html", {
        "items": items,
        "form": form,
        "editing_item": editing_item,
    })


@login_required
@manager_required
def manage_locations(request):
    locations = Location.objects.all().order_by("name")
    editing_location = None
    edit_location_id = request.GET.get("edit")
    if edit_location_id:
        editing_location = get_object_or_404(Location, pk=edit_location_id)

    if request.method == "POST":
        location_id = request.POST.get("location_id")
        if location_id:
            editing_location = get_object_or_404(Location, pk=location_id)
        form = LocationForm(request.POST, instance=editing_location)
    else:
        form = LocationForm(instance=editing_location)

    if request.method == "POST" and form.is_valid():
        location = form.save()
        action = "Updated" if location_id else "Added"
        messages.success(request, f"{action} {location.name}.")
        return redirect("manage_locations")
    return render(request, "inventory/manage_locations.html", {
        "locations": locations,
        "form": form,
        "editing_location": editing_location,
    })


@login_required
@manager_required
def manage_users(request):
    create_form = UserCreateForm(prefix="create")
    target_user_id = None
    target_form = None

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "create":
            create_form = UserCreateForm(request.POST, prefix="create")
            if create_form.is_valid():
                user = create_form.save()
                messages.success(request, f"Added user {user.username}.")
                return redirect("manage_users")
        elif action == "update":
            target_user_id = request.POST.get("user_id")
            target_user = get_object_or_404(User, pk=target_user_id)
            target_form = UserUpdateForm(
                request.POST,
                instance=target_user,
                prefix=f"user-{target_user.pk}",
            )
            if target_form.is_valid():
                new_role = target_form.cleaned_data["role"]
                new_active = target_form.cleaned_data["active"]
                active_manager_count = UserProfile.objects.filter(
                    role=UserProfile.ROLE_MANAGER,
                    user__is_active=True,
                ).count()

                if target_user == request.user and (new_role != UserProfile.ROLE_MANAGER or not new_active):
                    target_form.add_error(None, "You cannot remove your own manager access or deactivate yourself.")
                elif (
                    get_user_profile(target_user).is_manager
                    and active_manager_count == 1
                    and (new_role != UserProfile.ROLE_MANAGER or not new_active)
                ):
                    target_form.add_error(None, "At least one active manager must remain in the system.")
                else:
                    target_form.save()
                    messages.success(request, f"Updated user {target_user.username}.")
                    return redirect("manage_users")

    user_rows = []
    for user in User.objects.select_related("profile").order_by("username"):
        form = target_form if target_form is not None and str(user.pk) == str(target_user_id) else UserUpdateForm(
            instance=user,
            prefix=f"user-{user.pk}",
        )
        user_rows.append({"user": user, "form": form})

    return render(request, "inventory/manage_users.html", {
        "create_form": create_form,
        "user_rows": user_rows,
    })


@login_required
def item_stock_api(request, item_name):
    try:
        item = _annotate_item_stock(Item.objects.filter(name__iexact=item_name)).get()
        return JsonResponse({"stock": float(item.current_stock_db), "unit": item.unit})
    except Item.DoesNotExist:
        return JsonResponse({"stock": None, "unit": None})
