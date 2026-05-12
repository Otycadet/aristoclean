import json
import logging
import re
from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, transaction
from django.db.models import DecimalField, ExpressionWrapper, F, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce

from .models import DistributionLine, IssueBatch, Item, StockAdjustment, StockEntry


logger = logging.getLogger("inventory.audit")

DECIMAL_OUTPUT = DecimalField(max_digits=12, decimal_places=2)
ZERO_DECIMAL = Value(Decimal("0.00"), output_field=DECIMAL_OUTPUT)


def annotate_item_stock(queryset=None):
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
    return queryset.annotate(
        total_received_db=Coalesce(Subquery(received_subquery, output_field=DECIMAL_OUTPUT), ZERO_DECIMAL),
        total_issued_db=Coalesce(Subquery(issued_subquery, output_field=DECIMAL_OUTPUT), ZERO_DECIMAL),
        total_adjustments_db=Coalesce(Subquery(adjustment_subquery, output_field=DECIMAL_OUTPUT), ZERO_DECIMAL),
    ).annotate(
        current_stock_db=ExpressionWrapper(
            F("total_received_db") - F("total_issued_db") + F("total_adjustments_db"),
            output_field=DECIMAL_OUTPUT,
        )
    )


def build_stock_rows(items):
    rows = []
    for item in items:
        rows.append({
            "item": item,
            "current_stock": item.current_stock_db,
            "total_received": item.total_received_db,
            "total_issued": item.total_issued_db,
            "total_adjustments": getattr(item, "total_adjustments_db", item.total_adjustments),
            "low": item.current_stock_db <= item.reorder_level,
            "reorder_shortfall": max(item.reorder_level - item.current_stock_db, Decimal("0.00")),
        })
    return rows


def decimal_from_post(value, field_name):
    try:
        parsed = Decimal(str(value).strip())
    except (InvalidOperation, TypeError):
        raise ValueError(f"Enter a valid number for {field_name}.")
    return parsed.quantize(Decimal("0.01"))


def whole_number_from_post(value, field_name):
    parsed = decimal_from_post(value, field_name)
    if parsed != parsed.to_integral_value():
        raise ValueError(f"Enter a whole number for {field_name}.")
    return parsed


def serialize_items_for_js(items_queryset):
    return [
        {
            "id": item.id,
            "name": item.name,
            "unit": item.unit,
            "reorder_level": float(item.reorder_level),
            "stock": float(getattr(item, "current_stock_db", item.current_stock)),
        }
        for item in items_queryset
    ]


def serialize_reorder_lines(reorder_lines):
    return [
        {
            "item_id": str(line["item"].pk),
            "quantity": float(line["quantity"]),
        }
        for line in reorder_lines
    ]


def safe_csv_cell(value):
    if value is None:
        return ""
    if not isinstance(value, str):
        return value
    if value.lstrip().startswith(("=", "+", "-", "@")):
        return f"'{value}"
    return value


def lock_items_for_update(item_ids):
    normalized_ids = sorted({int(item_id) for item_id in item_ids})
    if not normalized_ids:
        return Item.objects.none()
    return Item.objects.select_for_update().filter(pk__in=normalized_ids).order_by("pk")


def display_name_for_user(user):
    return user.get_full_name().strip() or user.username


def log_inventory_action(action, user=None, **details):
    logger.info(
        "inventory_action=%s user=%s details=%s",
        action,
        getattr(user, "username", "system"),
        json.dumps(details, sort_keys=True, default=str),
    )


def get_or_create_item(name: str, unit: str, reorder_level: Decimal) -> Item:
    normalized_name = name.strip()
    try:
        item = Item.objects.select_for_update().get(name__iexact=normalized_name)
        item.unit = unit
        item.reorder_level = reorder_level
        item.active = True
        item.save(update_fields=["unit", "reorder_level", "active"])
        return item
    except Item.DoesNotExist:
        try:
            return Item.objects.create(
                name=normalized_name,
                unit=unit,
                reorder_level=reorder_level,
            )
        except IntegrityError:
            item = Item.objects.select_for_update().get(name__iexact=normalized_name)
            item.unit = unit
            item.reorder_level = reorder_level
            item.active = True
            item.save(update_fields=["unit", "reorder_level", "active"])
            return item


def parse_delivery_lines(post_data):
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

        if not any([existing_item_id, new_name, qty]):
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
            reorder_value = decimal_from_post(reorder, f"reorder level for {item_name}")
        else:
            if not new_name:
                raise ValueError("Each delivery line must select an existing item or enter a new item name.")
            item_name = new_name
            unit = unit or "units"
            reorder_value = decimal_from_post(reorder, f"reorder level for {item_name}")

        lines.append({
            "item_name": item_name,
            "unit": unit,
            "reorder_level": reorder_value,
            "quantity": whole_number_from_post(qty, f"quantity for {item_name}"),
        })

    return lines if lines else None


def parse_reorder_lines(post_data):
    lines = []
    line_indices = sorted({
        int(match.group(1))
        for key in post_data.keys()
        for match in [re.match(r"^line_(?:item|qty)_(\d+)$", key)]
        if match
    })

    for index in line_indices:
        item_id = post_data.get(f"line_item_{index}", "").strip()
        qty = post_data.get(f"line_qty_{index}", "").strip()

        if not any([item_id, qty]):
            continue
        if not item_id:
            raise ValueError("Select a valid item for each reorder line.")
        if not qty:
            raise ValueError("Enter quantity for each reorder line you add.")

        try:
            item = annotate_item_stock(Item.objects.filter(pk=int(item_id), active=True)).get()
        except (Item.DoesNotExist, ValueError):
            raise ValueError("Select a valid active item from the dropdown.")

        quantity = decimal_from_post(qty, f"quantity for {item.name}")
        if quantity <= 0:
            raise ValueError(f"Quantity must be greater than 0 for {item.name}.")

        lines.append({
            "item": item,
            "quantity": quantity,
            "stock": item.current_stock_db,
        })

    return lines if lines else None


def parse_issue_lines(post_data):
    lines = []
    line_indices = sorted({
        int(match.group(1))
        for key in post_data.keys()
        for match in [re.match(r"^issue_(?:item|qty)_(\d+)$", key)]
        if match
    })

    for index in line_indices:
        item_id = post_data.get(f"issue_item_{index}", "").strip()
        qty = post_data.get(f"issue_qty_{index}", "").strip()
        if not any([item_id, qty]):
            continue
        if not item_id:
            raise ValueError("Select an item for each issue line you add.")
        if not qty:
            raise ValueError("Enter quantity for each issue line you add.")

        try:
            item = Item.objects.get(pk=int(item_id), active=True)
        except (Item.DoesNotExist, ValueError):
            raise ValueError("Select valid items from the list of active stock items.")
        lines.append({"item": item, "quantity": whole_number_from_post(qty, f"quantity for {item.name}")})
    return lines if lines else None


def collect_delivery_line_inputs(post_data):
    lines = []
    line_indices = sorted({
        int(match.group(1))
        for key in post_data.keys()
        for match in [re.match(r"^line_(?:item|new_item|unit|reorder|qty)_(\d+)$", key)]
        if match
    })
    for index in line_indices:
        lines.append({
            "item_id": post_data.get(f"line_item_{index}", "").strip(),
            "new_name": post_data.get(f"line_new_item_{index}", "").strip(),
            "unit": post_data.get(f"line_unit_{index}", "").strip(),
            "reorder": post_data.get(f"line_reorder_{index}", "").strip(),
            "quantity": post_data.get(f"line_qty_{index}", "").strip(),
        })
    return [
        line for line in lines
        if any([line["item_id"], line["new_name"], line["quantity"]])
    ]


def collect_reorder_line_inputs(post_data):
    lines = []
    line_indices = sorted({
        int(match.group(1))
        for key in post_data.keys()
        for match in [re.match(r"^line_(?:item|qty)_(\d+)$", key)]
        if match
    })
    for index in line_indices:
        lines.append({
            "item_id": post_data.get(f"line_item_{index}", "").strip(),
            "quantity": post_data.get(f"line_qty_{index}", "").strip(),
        })
    return [line for line in lines if any(line.values())]


def collect_issue_line_inputs(post_data):
    lines = []
    line_indices = sorted({
        int(match.group(1))
        for key in post_data.keys()
        for match in [re.match(r"^issue_(?:item|qty)_(\d+)$", key)]
        if match
    })
    for index in line_indices:
        lines.append({
            "item_id": post_data.get(f"issue_item_{index}", "").strip(),
            "quantity": post_data.get(f"issue_qty_{index}", "").strip(),
        })
    return [line for line in lines if any(line.values())]


@transaction.atomic
def create_issue_batch(header_data: dict, lines: list, user) -> IssueBatch:
    location = header_data["location"]
    if not location.active:
        raise ValueError("Select an active location.")

    grouped = {}
    for line in lines:
        item = line["item"]
        grouped[item.pk] = grouped.get(item.pk, Decimal("0.00")) + line["quantity"]

    annotated_items = {
        item.pk: item
        for item in annotate_item_stock(lock_items_for_update(grouped.keys()))
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
        issued_to=header_data.get("issued_to", ""),
        issued_by=header_data.get("issued_by", ""),
        notes=header_data.get("notes", ""),
        issued_at=header_data["issued_at"],
        created_by=user,
    )
    for item, qty in validated:
        DistributionLine.objects.create(batch=batch, item=item, quantity=qty)
    return batch


def filter_report_batches(year, month, location_name=None, query=""):
    batches = IssueBatch.objects.filter(
        issued_at__year=year,
        issued_at__month=month,
        is_voided=False,
    )
    if location_name:
        batches = batches.filter(location__name__iexact=location_name)
    if query:
        batches = batches.filter(
            Q(receipt_number__icontains=query)
            | Q(issued_to__icontains=query)
            | Q(issued_by__icontains=query)
            | Q(location__name__icontains=query)
            | Q(lines__item__name__icontains=query)
        ).distinct()
    return batches
