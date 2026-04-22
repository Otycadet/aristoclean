from django.contrib import admin
from .models import Item, Location, StockEntry, IssueBatch, DistributionLine, UserProfile


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


@admin.register(StockEntry)
class StockEntryAdmin(admin.ModelAdmin):
    list_display = ["item", "quantity", "supplier", "received_at", "created_by"]
    list_filter = ["received_at"]
    search_fields = ["item__name", "supplier"]


class DistributionLineInline(admin.TabularInline):
    model = DistributionLine
    extra = 0


@admin.register(IssueBatch)
class IssueBatchAdmin(admin.ModelAdmin):
    list_display = ["receipt_number", "location", "issued_at", "issued_to", "issued_by"]
    list_filter = ["issued_at", "location"]
    search_fields = ["receipt_number", "issued_to", "issued_by"]
    inlines = [DistributionLineInline]
