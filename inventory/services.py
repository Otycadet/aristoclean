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
        current_stock = item.current_stock_db
        reorder_shortfall = max(item.reorder_level - current_stock, Decimal("0.00"))
        rows.append({
            "item": item,
            "current_stock": current_stock,
            "total_received": item.total_received_db,
            "total_issued": item.total_issued_db,
            "total_adjustments": getattr(item, "total_adjustments_db", item.total_adjustments),
            "low": current_stock <= item.reorder_level,
            "reorder_shortfall": reorder_shortfall,
            "current_stock_display": format_display_quantity(item, current_stock),
            "reorder_level_display": format_display_quantity(item, item.reorder_level),
            "reorder_shortfall_display": format_display_quantity(item, reorder_shortfall),
        })
    return rows


def format_display_quantity(item: Item, quantity: Decimal) -> str:
    quantity = Decimal(str(quantity or "0")).quantize(Decimal("0.01"))
    unit = (item.unit or "").strip()
    unit_lower = unit.lower()
    is_piece_unit = unit_lower in {"piece", "pieces", "pcs"}
    has_pack_size = item.pack_size and item.pack_size > 0
    has_carton_size = item.carton_size and item.carton_size > 0
    if not is_piece_unit or quantity <= 0 or not (has_pack_size or has_carton_size):
        return f"{format_quantity_number(quantity)} {unit}".strip()

    remaining = quantity
    parts = []
    carton_size = item.carton_size if has_carton_size else None
    pack_size = item.pack_size if has_pack_size else None

    if carton_size and remaining >= carton_size:
        cartons = int(remaining // carton_size)
        remaining = (remaining - (carton_size * cartons)).quantize(Decimal("0.01"))
        parts.append(f"{cartons:,} carton{'s' if cartons != 1 else ''}")

    if pack_size and remaining >= pack_size:
        packs = int(remaining // pack_size)
        remaining = (remaining - (pack_size * packs)).quantize(Decimal("0.01"))
        parts.append(f"{packs:,} pack{'s' if packs != 1 else ''}")

    if not parts or remaining > 0:
        parts.append(f"{format_quantity_number(remaining)} {unit}".strip())

    return " + ".join(parts)


def format_quantity_number(value: Decimal) -> str:
    value = Decimal(str(value or "0")).quantize(Decimal("0.01"))
    if value == value.to_integral_value():
        return f"{int(value):,}"
    return f"{value:,.2f}"


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
            "pack_size": float(item.pack_size) if item.pack_size else None,
            "carton_size": float(item.carton_size) if item.carton_size else None,
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


def converted_quantity_for_item(item: Item, quantity: Decimal, measure: str) -> Decimal:
    stock_unit = item.unit.strip().lower()
    stock_is_pack = stock_unit in {"pack", "packs"}
    stock_is_piece = stock_unit in {"piece", "pieces", "pcs"}

    if measure == "piece":
        if not item.pack_size:
            raise ValueError(f"Set how many pieces are in one {item.unit} for {item.name}.")
        if stock_is_pack:
            return (quantity / item.pack_size).quantize(Decimal("0.01"))
        return quantity.quantize(Decimal("0.01"))
    if measure == "pack":
        if not item.pack_size:
            raise ValueError(f"Set how many pieces are in one pack for {item.name}.")
        if stock_is_piece:
            return (quantity * item.pack_size).quantize(Decimal("0.01"))
        return quantity.quantize(Decimal("0.01"))
    if measure == "carton":
        if not item.carton_size:
            raise ValueError(f"Set how many pieces are in one carton for {item.name}.")
        if stock_is_pack and item.pack_size:
            return (quantity * item.carton_size / item.pack_size).quantize(Decimal("0.01"))
        return (quantity * item.carton_size).quantize(Decimal("0.01"))
    return quantity.quantize(Decimal("0.01"))


def conversion_label_for_item(item: Item, quantity: Decimal, measure: str) -> str:
    if measure in {"piece", "pack", "carton"}:
        unit_label = item.label_for_measure(measure)
        stock_quantity = converted_quantity_for_item(item, quantity, measure)
        return f"{quantity:,.0f} {unit_label} ({stock_quantity:,.2f} {item.unit})"
    return f"{quantity:,.0f} {item.unit}"


def convert_existing_item_quantities(item: Item, factor: Decimal):
    factor = Decimal(str(factor)).quantize(Decimal("0.01"))
    if factor <= 0:
        raise ValueError("Conversion factor must be greater than 0.")

    for entry in StockEntry.objects.select_for_update().filter(item=item):
        entry.quantity = (entry.quantity * factor).quantize(Decimal("0.01"))
        entry.save(update_fields=["quantity"])

    for line in DistributionLine.objects.select_for_update().filter(item=item):
        line.quantity = (line.quantity * factor).quantize(Decimal("0.01"))
        line.save(update_fields=["quantity"])

    for adjustment in StockAdjustment.objects.select_for_update().filter(item=item):
        adjustment.quantity_delta = (adjustment.quantity_delta * factor).quantize(Decimal("0.01"))
        adjustment.save(update_fields=["quantity_delta"])

    item.reorder_level = (item.reorder_level * factor).quantize(Decimal("0.01"))
    item.save(update_fields=["reorder_level"])


def parse_delivery_lines(post_data):
    lines = []
    line_indices = sorted({
        int(match.group(1))
        for key in post_data.keys()
        for match in [re.match(r"^line_(?:item|new_item|unit|reorder|measure|qty)_(\d+)$", key)]
        if match
    })

    for index in line_indices:
        existing_item_id = post_data.get(f"line_item_{index}", "").strip()
        new_name = post_data.get(f"line_new_item_{index}", "").strip()
        unit = post_data.get(f"line_unit_{index}", "").strip()
        reorder = post_data.get(f"line_reorder_{index}", "0").strip() or "0"
        measure = post_data.get(f"line_measure_{index}", "base").strip() or "base"
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
            entered_quantity = whole_number_from_post(qty, f"quantity for {item_name}")
            quantity = converted_quantity_for_item(item, entered_quantity, measure)
            quantity_label = conversion_label_for_item(item, entered_quantity, measure)
        else:
            if not new_name:
                raise ValueError("Each delivery line must select an existing item or enter a new item name.")
            item_name = new_name
            unit = unit or "units"
            reorder_value = decimal_from_post(reorder, f"reorder level for {item_name}")
            entered_quantity = whole_number_from_post(qty, f"quantity for {item_name}")
            quantity = entered_quantity
            quantity_label = f"{entered_quantity:,.0f} {unit}"

        lines.append({
            "item_name": item_name,
            "unit": unit,
            "reorder_level": reorder_value,
            "quantity": quantity,
            "quantity_label": quantity_label,
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
        for match in [re.match(r"^issue_(?:item|measure|qty)_(\d+)$", key)]
        if match
    })

    for index in line_indices:
        item_id = post_data.get(f"issue_item_{index}", "").strip()
        measure = post_data.get(f"issue_measure_{index}", "base").strip() or "base"
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
        entered_quantity = whole_number_from_post(qty, f"quantity for {item.name}")
        lines.append({
            "item": item,
            "quantity": converted_quantity_for_item(item, entered_quantity, measure),
            "quantity_label": conversion_label_for_item(item, entered_quantity, measure),
        })
    return lines if lines else None


def collect_delivery_line_inputs(post_data):
    lines = []
    line_indices = sorted({
        int(match.group(1))
        for key in post_data.keys()
        for match in [re.match(r"^line_(?:item|new_item|unit|reorder|measure|qty)_(\d+)$", key)]
        if match
    })
    for index in line_indices:
        lines.append({
            "item_id": post_data.get(f"line_item_{index}", "").strip(),
            "new_name": post_data.get(f"line_new_item_{index}", "").strip(),
            "unit": post_data.get(f"line_unit_{index}", "").strip(),
            "reorder": post_data.get(f"line_reorder_{index}", "").strip(),
            "measure": post_data.get(f"line_measure_{index}", "").strip(),
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
        for match in [re.match(r"^issue_(?:item|measure|qty)_(\d+)$", key)]
        if match
    })
    for index in line_indices:
        lines.append({
            "item_id": post_data.get(f"issue_item_{index}", "").strip(),
            "measure": post_data.get(f"issue_measure_{index}", "").strip(),
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
