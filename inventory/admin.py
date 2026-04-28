from django.contrib import admin

from .models import DistributionLine, IssueBatch, Item, Location, SignInLog, StockAdjustment, StockEntry, UserProfile


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ["user", "role"]
    list_editable = ["role"]


@admin.register(Item)
class ItemAdmin(admin.ModelAdmin):
    list_display = ["name", "unit", "reorder_level", "active"]
    list_filter = ["active"]
    search_fields = ["name"]


@admin.register(Location)
class LocationAdmin(admin.ModelAdmin):
    list_display = ["name", "active"]
    list_filter = ["active"]
    search_fields = ["name"]


@admin.register(StockEntry)
class StockEntryAdmin(admin.ModelAdmin):
    list_display = ["item", "quantity", "supplier", "reference", "received_at", "created_by"]
    list_filter = ["received_at"]
    search_fields = ["item__name", "supplier", "reference"]


@admin.register(StockAdjustment)
class StockAdjustmentAdmin(admin.ModelAdmin):
    list_display = ["item", "quantity_delta", "reason", "adjusted_at", "created_by"]
    list_filter = ["reason", "adjusted_at"]
    search_fields = ["item__name", "notes"]


@admin.register(SignInLog)
class SignInLogAdmin(admin.ModelAdmin):
    list_display = ["username_snapshot", "ip_address", "signed_in_at"]
    list_filter = ["signed_in_at"]
    search_fields = ["username_snapshot", "user__username", "ip_address", "user_agent"]


class DistributionLineInline(admin.TabularInline):
    model = DistributionLine
    extra = 0


@admin.register(IssueBatch)
class IssueBatchAdmin(admin.ModelAdmin):
    list_display = ["receipt_number", "location", "issued_at", "issued_to", "issued_by", "is_voided"]
    list_filter = ["issued_at", "location", "is_voided"]
    search_fields = ["receipt_number", "issued_to", "issued_by"]
    inlines = [DistributionLineInline]
