import csv
from datetime import date
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q, Sum
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
from .services import (
    annotate_item_stock,
    build_stock_rows,
    create_issue_batch,
    display_name_for_user,
    filter_report_batches,
    get_or_create_item,
    lock_items_for_update,
    log_inventory_action,
    parse_delivery_lines,
    parse_issue_lines,
    parse_reorder_lines,
    safe_csv_cell,
    serialize_items_for_js,
    serialize_reorder_lines,
)


@login_required
def dashboard(request):
    items = annotate_item_stock(Item.objects.filter(active=True)).order_by("name")
    stock_data = build_stock_rows(items)
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
    items = annotate_item_stock(Item.objects.filter(active=True)).order_by("name")
    return render(request, "inventory/stock_list.html", {"stock_data": build_stock_rows(items)})


@login_required
@manager_required
def reorder_list(request):
    items_qs = annotate_item_stock(Item.objects.filter(active=True)).order_by("name")
    reorder_lines = []
    error = None

    if request.method == "POST":
        try:
            reorder_lines = parse_reorder_lines(request.POST)
            if reorder_lines is None:
                raise ValueError("Add at least one item line.")
            messages.success(request, f"Prepared reorder list for {len(reorder_lines)} item(s).")
            log_inventory_action(
                "reorder_prepared",
                user=request.user,
                item_count=len(reorder_lines),
                items=[line["item"].name for line in reorder_lines],
            )
        except ValueError as exc:
            error = str(exc)

    return render(request, "inventory/reorder_list.html", {
        "items_data": serialize_items_for_js(items_qs),
        "reorder_lines": reorder_lines or [],
        "reorder_lines_data": serialize_reorder_lines(reorder_lines or []),
        "error": error,
    })


@login_required
@stock_operator_required
def stock_receive(request):
    received_by_name = display_name_for_user(request.user)
    header_form = DeliveryHeaderForm(request.POST or None, initial={"received_at": date.today()})
    error = None

    if request.method == "POST":
        lines = parse_delivery_lines(request.POST)
        if header_form.is_valid() and lines is not None:
            header_data = header_form.cleaned_data
            try:
                with transaction.atomic():
                    for line in lines:
                        name = line["item_name"].strip()
                        qty = line["quantity"]
                        if not name:
                            raise ValueError("Each line must have an item name.")
                        if qty <= 0:
                            raise ValueError(f"Quantity must be greater than 0 for {name}.")
                        item = get_or_create_item(name, line["unit"].strip(), line["reorder_level"])
                        StockEntry.objects.create(
                            item=item,
                            quantity=qty,
                            supplier=header_data["supplier"],
                            reference=header_data["reference"],
                            notes=header_data["notes"],
                            received_at=header_data["received_at"],
                            created_by=request.user,
                        )
                messages.success(request, f"Delivery of {len(lines)} item(s) recorded.")
                log_inventory_action(
                    "stock_received",
                    user=request.user,
                    item_count=len(lines),
                    items=[line["item_name"] for line in lines],
                    supplier=header_data["supplier"],
                    reference=header_data["reference"],
                    received_at=header_data["received_at"],
                )
                return redirect("stock_list")
            except ValueError as exc:
                error = str(exc)
        elif lines is None:
            error = "Add at least one item line."

    items_qs = annotate_item_stock(Item.objects.filter(active=True)).order_by("name")
    return render(request, "inventory/stock_receive.html", {
        "header_form": header_form,
        "items_data": serialize_items_for_js(items_qs),
        "error": error,
        "received_by_name": received_by_name,
    })


@login_required
@stock_operator_required
def issue_stock(request):
    issued_by_name = display_name_for_user(request.user)
    header_form = IssueBatchHeaderForm(
        request.POST or None,
        initial={
            "issued_at": date.today(),
            "issued_by": issued_by_name,
        },
    )
    error = None

    if request.method == "POST":
        lines = parse_issue_lines(request.POST)
        if header_form.is_valid() and lines is not None:
            header_data = header_form.cleaned_data
            header_data["issued_by"] = issued_by_name
            try:
                batch = create_issue_batch(header_data, lines, request.user)
                messages.success(request, f"Receipt {batch.receipt_number} created.")
                log_inventory_action(
                    "stock_issued",
                    user=request.user,
                    receipt_number=batch.receipt_number,
                    location=batch.location.name,
                    item_count=len(lines),
                    items=[line["item"].name for line in lines],
                    issued_to=batch.issued_to,
                    issued_at=batch.issued_at,
                )
                return redirect("receipt_detail", receipt_number=batch.receipt_number)
            except ValueError as exc:
                error = str(exc)
        elif lines is None:
            error = "Add at least one item line to the receipt."

    items_qs = annotate_item_stock(Item.objects.filter(active=True)).order_by("name")
    return render(request, "inventory/issue_stock.html", {
        "header_form": header_form,
        "items_data": serialize_items_for_js(items_qs),
        "error": error,
        "issued_by_name": issued_by_name,
    })


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
    receipt_lines = [
        {
            "item_name": line.item.name,
            "unit": line.item.unit,
            "quantity": line.quantity,
        }
        for line in batch.lines.select_related("item")
    ]

    return render(request, "inventory/receipt_detail.html", {
        "batch": batch,
        "lines": receipt_lines,
        "void_form": void_form,
    })


@login_required
@manager_required
@transaction.atomic
def void_receipt(request, receipt_number):
    batch = get_object_or_404(
        IssueBatch.objects.select_for_update().prefetch_related("lines"),
        receipt_number=receipt_number,
    )
    if request.method != "POST":
        return redirect("receipt_detail", receipt_number=receipt_number)
    if batch.is_voided:
        messages.warning(request, f"Receipt {batch.receipt_number} is already voided.")
        return redirect("receipt_detail", receipt_number=receipt_number)

    lock_items_for_update(batch.lines.values_list("item_id", flat=True))

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
    log_inventory_action(
        "receipt_voided",
        user=request.user,
        receipt_number=batch.receipt_number,
        reason=batch.void_reason,
    )
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

        batch_qs = filter_report_batches(year, month, location_name, query)

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
    response["Content-Disposition"] = f'attachment; filename="stock_snapshot_{date.today().strftime("%Y%m%d")}.csv"'
    writer = csv.writer(response)
    writer.writerow([
        "Item", "Unit", "Current Stock", "Reorder Level", "Shortfall",
        "Total Received", "Total Issued", "Total Adjustments", "Status",
    ])
    for item in annotate_item_stock(Item.objects.filter(active=True)).order_by("name"):
        status = "LOW STOCK" if item.current_stock_db <= item.reorder_level else "OK"
        shortfall = max(item.reorder_level - item.current_stock_db, Decimal("0.00"))
        writer.writerow([
            safe_csv_cell(item.name),
            safe_csv_cell(item.unit),
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
    batch_qs = filter_report_batches(year, month, location_name, query)

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="report_{year:04d}_{month:02d}.csv"'
    writer = csv.writer(response)
    writer.writerow(["Receipt", "Date", "Location", "Item", "Quantity", "Unit", "Issued To", "Issued By", "Notes"])
    qs = (
        DistributionLine.objects.filter(batch__in=batch_qs)
        .select_related("batch__location", "item")
        .order_by("-batch__issued_at", "item__name")
    )
    for line in qs:
        writer.writerow([
            safe_csv_cell(line.batch.receipt_number),
            line.batch.issued_at,
            safe_csv_cell(line.batch.location.name),
            safe_csv_cell(line.item.name),
            f"{line.quantity:,.2f}",
            safe_csv_cell(line.item.unit),
            safe_csv_cell(line.batch.issued_to),
            safe_csv_cell(line.batch.issued_by),
            safe_csv_cell(line.batch.notes),
        ])
    return response


@login_required
@manager_required
def low_stock_report(request):
    items = annotate_item_stock(Item.objects.filter(active=True)).order_by("name")
    low_stock_items = [row for row in build_stock_rows(items) if row["low"]]
    return render(request, "inventory/low_stock_report.html", {
        "low_stock_items": low_stock_items,
    })


@login_required
@manager_required
def export_low_stock_csv(request):
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="low_stock_{date.today().strftime("%Y%m%d")}.csv"'
    writer = csv.writer(response)
    writer.writerow(["Item", "Unit", "Current Stock", "Reorder Level", "Shortfall"])
    for item in annotate_item_stock(Item.objects.filter(active=True)).order_by("name"):
        if item.current_stock_db <= item.reorder_level:
            writer.writerow([
                safe_csv_cell(item.name),
                safe_csv_cell(item.unit),
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

        with transaction.atomic():
            item_with_stock = annotate_item_stock(lock_items_for_update([adjustment.item_id])).get()
            projected_stock = item_with_stock.current_stock_db + adjustment.quantity_delta
            if projected_stock < Decimal("0.00"):
                form.add_error("quantity", f"This adjustment would reduce {adjustment.item.name} below zero stock.")
            else:
                adjustment.save()
                messages.success(request, f"Stock adjustment recorded for {adjustment.item.name}.")
                log_inventory_action(
                    "stock_adjusted",
                    user=request.user,
                    item=adjustment.item.name,
                    quantity_delta=adjustment.quantity_delta,
                    reason=adjustment.reason,
                    adjusted_at=adjustment.adjusted_at,
                )
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
        item_id = None
        form = ItemForm(instance=editing_item)

    if request.method == "POST" and form.is_valid():
        item = form.save()
        action = "Updated" if item_id else "Added"
        messages.success(request, f"{action} {item.name}.")
        log_inventory_action(
            "item_saved",
            user=request.user,
            mode="update" if item_id else "create",
            item_id=item.pk,
            item_name=item.name,
            unit=item.unit,
            reorder_level=item.reorder_level,
            active=item.active,
        )
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
        location_id = None
        form = LocationForm(instance=editing_location)

    if request.method == "POST" and form.is_valid():
        location = form.save()
        action = "Updated" if location_id else "Added"
        messages.success(request, f"{action} {location.name}.")
        log_inventory_action(
            "location_saved",
            user=request.user,
            mode="update" if location_id else "create",
            location_id=location.pk,
            location_name=location.name,
            active=location.active,
        )
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
                log_inventory_action(
                    "user_created",
                    user=request.user,
                    target_user=user.username,
                    role=get_user_profile(user).role,
                    active=user.is_active,
                )
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
                    updated_user = target_form.save()
                    messages.success(request, f"Updated user {target_user.username}.")
                    log_inventory_action(
                        "user_updated",
                        user=request.user,
                        target_user=updated_user.username,
                        role=get_user_profile(updated_user).role,
                        active=updated_user.is_active,
                        password_reset=bool(target_form.cleaned_data.get("new_password")),
                    )
                    return redirect("manage_users")

    user_rows = []
    for user in User.objects.select_related("profile").order_by("username"):
        form = (
            target_form
            if target_form is not None and str(user.pk) == str(target_user_id)
            else UserUpdateForm(instance=user, prefix=f"user-{user.pk}")
        )
        user_rows.append({"user": user, "form": form})

    return render(request, "inventory/manage_users.html", {
        "create_form": create_form,
        "user_rows": user_rows,
    })


@login_required
def item_stock_api(request, item_name):
    try:
        item = annotate_item_stock(Item.objects.filter(name__iexact=item_name)).get()
        return JsonResponse({"stock": float(item.current_stock_db), "unit": item.unit})
    except Item.DoesNotExist:
        return JsonResponse({"stock": None, "unit": None})
