from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone


class Item(models.Model):
    name = models.CharField(max_length=255, unique=True)
    unit = models.CharField(max_length=100)
    reorder_level = models.FloatField(default=0)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def total_received(self):
        return self.stock_entries.aggregate(
            total=models.Sum("quantity")
        )["total"] or 0.0

    @property
    def total_issued(self):
        return self.distribution_lines.aggregate(
            total=models.Sum("quantity")
        )["total"] or 0.0

    @property
    def current_stock(self):
        return self.total_received - self.total_issued

    @property
    def is_low_stock(self):
        return self.current_stock <= self.reorder_level


class Location(models.Model):
    name = models.CharField(max_length=255, unique=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class StockEntry(models.Model):
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="stock_entries")
    quantity = models.FloatField()
    supplier = models.CharField(max_length=255, blank=True)
    reference = models.CharField(max_length=255, blank=True)
    notes = models.TextField(blank=True)
    received_at = models.DateField()
    created_at = models.DateTimeField(default=timezone.now)
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="stock_entries"
    )

    class Meta:
        ordering = ["-received_at", "-id"]

    def __str__(self):
        return f"{self.item.name} +{self.quantity} on {self.received_at}"


class IssueBatch(models.Model):
    """One 'receipt' / issue event covering multiple items."""

    location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="issue_batches")
    issued_to = models.CharField(max_length=255, blank=True)
    issued_by = models.CharField(max_length=255, blank=True)
    receipt_number = models.CharField(max_length=100, unique=True, blank=True)
    notes = models.TextField(blank=True)
    issued_at = models.DateField()
    created_at = models.DateTimeField(default=timezone.now)
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="issue_batches"
    )

    class Meta:
        ordering = ["-issued_at", "-id"]

    def __str__(self):
        return self.receipt_number or f"Batch #{self.pk}"

    def save(self, *args, **kwargs):
        # Generate receipt number after first save so we have the PK
        super().save(*args, **kwargs)
        if not self.receipt_number:
            date_str = self.issued_at.strftime("%Y%m%d")
            self.receipt_number = f"ISS-{date_str}-{self.pk:05d}"
            IssueBatch.objects.filter(pk=self.pk).update(receipt_number=self.receipt_number)

    @property
    def line_count(self):
        return self.lines.count()


class DistributionLine(models.Model):
    batch = models.ForeignKey(IssueBatch, on_delete=models.CASCADE, related_name="lines")
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="distribution_lines")
    quantity = models.FloatField()

    def __str__(self):
        return f"{self.item.name} ×{self.quantity}"


# ── Role helper ────────────────────────────────────────────────────────────

class UserProfile(models.Model):
    ROLE_STOREKEEPER = "storekeeper"
    ROLE_MANAGER = "manager"
    ROLE_CHOICES = [
        (ROLE_STOREKEEPER, "Store Keeper"),
        (ROLE_MANAGER, "Manager / Admin"),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default=ROLE_STOREKEEPER)

    def __str__(self):
        return f"{self.user.username} ({self.get_role_display()})"

    @property
    def is_manager(self):
        return self.role == self.ROLE_MANAGER
