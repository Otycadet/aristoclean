from django.urls import path
from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),

    # Stock
    path("stock/", views.stock_list, name="stock_list"),
    path("stock/receive/", views.stock_receive, name="stock_receive"),
    path("stock/receive/receipts/", views.delivery_receipts_list, name="delivery_receipts_list"),
    path("stock/receive/receipts/<str:receipt_number>/",
         views.delivery_receipt_detail, name="delivery_receipt_detail"),
    path("stock/reorder/", views.reorder_list, name="stock_reorder"),
    path("stock/adjust/", views.stock_adjustment_create,
         name="stock_adjustment_create"),
    path("stock/low/", views.low_stock_report, name="low_stock_report"),
    path("stock/low/export/", views.export_low_stock_csv,
         name="export_low_stock_csv"),
    path("stock/export/", views.export_csv, name="export_stock_csv"),

    # Issue
    path("issue/", views.issue_stock, name="issue_stock"),

    # Receipts
    path("receipts/", views.receipts_list, name="receipts_list"),
    path("receipts/<str:receipt_number>/",
         views.receipt_detail, name="receipt_detail"),
    path("receipts/<str:receipt_number>/void/",
         views.void_receipt, name="void_receipt"),

    # Reports
    path("reports/", views.reports, name="reports"),
    path("reports/export/", views.export_report_csv, name="export_report_csv"),

    # Management (admin only)
    path("manage/items/", views.manage_items, name="manage_items"),
    path("manage/locations/", views.manage_locations, name="manage_locations"),
    path("manage/users/", views.manage_users, name="manage_users"),
    path("manage/sign-ins/", views.sign_in_logs, name="sign_in_logs"),

    # API helpers
    path("api/item-stock/<str:item_name>/",
         views.item_stock_api, name="item_stock_api"),
]
