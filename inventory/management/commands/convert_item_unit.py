from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from inventory.models import DistributionLine, Item, StockAdjustment, StockEntry


class Command(BaseCommand):
    help = "Convert an existing item's historical quantities into a new base unit."

    def add_arguments(self, parser):
        parser.add_argument("item", help="Item name or database id to convert.")
        parser.add_argument("factor", help="How many new base units each old unit contains, e.g. 48.")
        parser.add_argument("--new-unit", required=True, help="New base unit label, e.g. pieces.")
        parser.add_argument("--pack-size", help="Optional pack size in the new base unit.")
        parser.add_argument("--carton-size", help="Optional carton size in the new base unit.")
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Actually save the conversion. Without this flag, only a dry-run summary is shown.",
        )

    def handle(self, *args, **options):
        factor = self._decimal(options["factor"], "factor")
        if factor <= 0:
            raise CommandError("Factor must be greater than 0.")

        pack_size = self._optional_decimal(options.get("pack_size"), "pack size")
        carton_size = self._optional_decimal(options.get("carton_size"), "carton size")
        item = self._get_item(options["item"])

        stock_entries = StockEntry.objects.filter(item=item)
        distribution_lines = DistributionLine.objects.filter(item=item)
        adjustments = StockAdjustment.objects.filter(item=item)
        current_stock_before = item.current_stock

        self.stdout.write(f"Item: {item.name}")
        self.stdout.write(f"Old base unit: {item.unit}")
        self.stdout.write(f"New base unit: {options['new_unit']}")
        self.stdout.write(f"Conversion factor: 1 {item.unit} = {factor} {options['new_unit']}")
        self.stdout.write(f"Current stock: {current_stock_before} {item.unit} -> {current_stock_before * factor} {options['new_unit']}")
        self.stdout.write(f"Stock entries to update: {stock_entries.count()}")
        self.stdout.write(f"Issue lines to update: {distribution_lines.count()}")
        self.stdout.write(f"Adjustments to update: {adjustments.count()}")
        self.stdout.write(f"Reorder level: {item.reorder_level} -> {item.reorder_level * factor}")

        if not options["apply"]:
            self.stdout.write(self.style.WARNING("Dry run only. Add --apply to save these changes."))
            return

        with transaction.atomic():
            for entry in stock_entries.select_for_update():
                entry.quantity = (entry.quantity * factor).quantize(Decimal("0.01"))
                entry.save(update_fields=["quantity"])

            for line in distribution_lines.select_for_update():
                line.quantity = (line.quantity * factor).quantize(Decimal("0.01"))
                line.save(update_fields=["quantity"])

            for adjustment in adjustments.select_for_update():
                adjustment.quantity_delta = (adjustment.quantity_delta * factor).quantize(Decimal("0.01"))
                adjustment.save(update_fields=["quantity_delta"])

            item = Item.objects.select_for_update().get(pk=item.pk)
            item.unit = options["new_unit"].strip()
            item.reorder_level = (item.reorder_level * factor).quantize(Decimal("0.01"))
            item.pack_size = pack_size
            item.carton_size = carton_size
            item.save(update_fields=["unit", "reorder_level", "pack_size", "carton_size"])

        item.refresh_from_db()
        self.stdout.write(self.style.SUCCESS(f"Converted {item.name}. New current stock: {item.current_stock} {item.unit}"))

    def _get_item(self, value):
        if value.isdigit():
            try:
                return Item.objects.get(pk=int(value))
            except Item.DoesNotExist as exc:
                raise CommandError(f"No item found with id {value}.") from exc
        try:
            return Item.objects.get(name__iexact=value.strip())
        except Item.DoesNotExist as exc:
            raise CommandError(f"No item found named {value}.") from exc

    def _decimal(self, value, label):
        try:
            return Decimal(str(value).strip()).quantize(Decimal("0.01"))
        except (InvalidOperation, AttributeError) as exc:
            raise CommandError(f"Enter a valid number for {label}.") from exc

    def _optional_decimal(self, value, label):
        if value in (None, ""):
            return None
        parsed = self._decimal(value, label)
        if parsed <= 0:
            raise CommandError(f"{label.capitalize()} must be greater than 0.")
        return parsed
