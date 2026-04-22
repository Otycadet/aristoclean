from django import forms
from django.utils import timezone
from .models import Item, Location, StockEntry, IssueBatch, DistributionLine


# ── Stock / Incoming delivery ──────────────────────────────────────────────

class StockEntryLineForm(forms.Form):
    item_name = forms.CharField(max_length=255, label="Item")
    unit = forms.CharField(max_length=100)
    reorder_level = forms.FloatField(min_value=0, initial=0, label="Reorder level")
    quantity = forms.FloatField(min_value=0.001, label="Quantity received")


class DeliveryHeaderForm(forms.Form):
    supplier = forms.CharField(max_length=255, required=False, label="Supplier")
    reference = forms.CharField(max_length=255, required=False, label="Reference / PO#")
    notes = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), required=False)
    received_at = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date"}),
        initial=timezone.now().date,
    )


# ── Issue stock ────────────────────────────────────────────────────────────

class IssueBatchHeaderForm(forms.Form):
    location = forms.CharField(max_length=255, label="Location / Department")
    issued_to = forms.CharField(max_length=255, required=False, label="Issued to")
    issued_by = forms.CharField(max_length=255, required=False, label="Issued by")
    notes = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), required=False)
    issued_at = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date"}),
        initial=timezone.now().date,
    )


class IssueLineForm(forms.Form):
    item_name = forms.CharField(max_length=255, label="Item")
    quantity = forms.FloatField(min_value=0.001)


# ── Admin / management ─────────────────────────────────────────────────────

class ItemForm(forms.ModelForm):
    class Meta:
        model = Item
        fields = ["name", "unit", "reorder_level", "active"]


class LocationForm(forms.ModelForm):
    class Meta:
        model = Location
        fields = ["name", "active"]


# ── Reports ────────────────────────────────────────────────────────────────

class ReportFilterForm(forms.Form):
    MONTH_CHOICES = [
        (1, "January"), (2, "February"), (3, "March"), (4, "April"),
        (5, "May"), (6, "June"), (7, "July"), (8, "August"),
        (9, "September"), (10, "October"), (11, "November"), (12, "December"),
    ]
    year = forms.IntegerField(min_value=2000, max_value=2100, initial=timezone.now().year)
    month = forms.ChoiceField(choices=MONTH_CHOICES, initial=timezone.now().month)
    location = forms.ChoiceField(required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        location_choices = [("", "All locations")] + [
            (loc.name, loc.name)
            for loc in Location.objects.filter(active=True).order_by("name")
        ]
        self.fields["location"].choices = location_choices
