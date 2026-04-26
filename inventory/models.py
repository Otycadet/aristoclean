from decimal import Decimal

from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone


class Item(models.Model):
    name = models.CharField(max_length=255, unique=True)
    unit = models.CharField(max_length=100)
    reorder_level = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def total_received(self):
        return self.stock_entries.aggregate(total=models.Sum("quantity"))["total"] or Decimal("0.00")

    @property
    def total_issued(self):
        return (
            self.distribution_lines.filter(batch__is_voided=False)
            .aggregate(total=models.Sum("quantity"))["total"]
            or Decimal("0.00")
        )

    @property
    def total_adjustments(self):
        return self.adjustments.aggregate(total=models.Sum("quantity_delta"))["total"] or Decimal("0.00")

    @property
    def current_stock(self):
        return self.total_received - self.total_issued + self.total_adjustments

    @property
    def is_low_stock(self):
        return self.current_stock <= self.reorder_level

    @property
    def reorder_shortfall(self):
        if self.current_stock >= self.reorder_level:
            return Decimal("0.00")
        return self.reorder_level - self.current_stock


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
    quantity = models.DecimalField(max_digits=12, decimal_places=2)
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
    """One receipt / issue event covering multiple items."""

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
    is_voided = models.BooleanField(default=False)
    void_reason = models.TextField(blank=True)
    voided_at = models.DateTimeField(null=True, blank=True)
    voided_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="voided_issue_batches"
    )

    class Meta:
        ordering = ["-issued_at", "-id"]

    def __str__(self):
        return self.receipt_number or f"Batch #{self.pk}"

    def save(self, *args, **kwargs):
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
    quantity = models.DecimalField(max_digits=12, decimal_places=2)

    def __str__(self):
        return f"{self.item.name} x{self.quantity}"


class StockAdjustment(models.Model):
    REASON_DAMAGE = "damage"
    REASON_COUNT = "count"
    REASON_RETURN = "return"
    REASON_EXPIRED = "expired"
    REASON_OTHER = "other"
    REASON_CHOICES = [
        (REASON_DAMAGE, "Damage / wastage"),
        (REASON_COUNT, "Stock count correction"),
        (REASON_RETURN, "Customer / department return"),
        (REASON_EXPIRED, "Expired / unusable"),
        (REASON_OTHER, "Other"),
    ]

    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="adjustments")
    quantity_delta = models.DecimalField(max_digits=12, decimal_places=2)
    reason = models.CharField(max_length=30, choices=REASON_CHOICES)
    notes = models.TextField(blank=True)
    adjusted_at = models.DateField(default=timezone.localdate)
    created_at = models.DateTimeField(default=timezone.now)
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="stock_adjustments"
    )

    class Meta:
        ordering = ["-adjusted_at", "-id"]

    def __str__(self):
        return f"{self.item.name} {self.quantity_delta:+} ({self.get_reason_display()})"


class UserProfile(models.Model):
    ROLE_VIEWER = "viewer"
    ROLE_STOREKEEPER = "storekeeper"
    ROLE_MANAGER = "manager"
    ROLE_ADMIN = "admin"
    ROLE_CHOICES = [
        (ROLE_VIEWER, "Viewer"),
        (ROLE_STOREKEEPER, "Store Keeper"),
        (ROLE_MANAGER, "Manager"),
        (ROLE_ADMIN, "Admin"),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default=ROLE_STOREKEEPER)

    def __str__(self):
        return f"{self.user.username} ({self.get_role_display()})"

    @property
    def is_viewer(self):
        return self.role == self.ROLE_VIEWER

    @property
    def is_admin(self):
        return self.role == self.ROLE_ADMIN

    @property
    def is_manager(self):
        return self.role == self.ROLE_MANAGER

    @property
    def can_operate_stock(self):
        return self.user.is_superuser or self.role in {self.ROLE_STOREKEEPER, self.ROLE_MANAGER}

    @property
    def can_view_reports(self):
        return self.user.is_superuser or self.role == self.ROLE_MANAGER

    @property
    def can_oversee_operations(self):
        return self.user.is_superuser or self.role == self.ROLE_MANAGER

    @property
    def can_manage_settings(self):
        return self.user.is_superuser or self.role == self.ROLE_ADMIN
