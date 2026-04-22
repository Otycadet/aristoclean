from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.core.exceptions import PermissionDenied
from django.test import RequestFactory, TestCase

from .models import DistributionLine, IssueBatch, Item, Location, StockAdjustment, StockEntry, UserProfile
from .views import issue_stock, manage_users, reports, stock_adjustment_create, stock_receive, void_receipt


class InventoryTestCase(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.viewer = self._create_user("viewer", UserProfile.ROLE_VIEWER)
        self.storekeeper = self._create_user("storekeeper", UserProfile.ROLE_STOREKEEPER)
        self.manager = self._create_user("manager", UserProfile.ROLE_MANAGER)

        self.location = Location.objects.create(name="Main Store")
        self.item = Item.objects.create(name="Soap", unit="pcs", reorder_level=Decimal("10.00"))
        StockEntry.objects.create(
            item=self.item,
            quantity=Decimal("20.00"),
            received_at=date(2026, 4, 20),
            created_by=self.manager,
        )

    def _create_user(self, username, role):
        user = User.objects.create_user(username=username, password="pass1234")
        profile, _ = UserProfile.objects.get_or_create(user=user)
        profile.role = role
        profile.save()
        return user

    def _attach_session_and_messages(self, request):
        middleware = SessionMiddleware(lambda req: None)
        middleware.process_request(request)
        request.session.save()
        setattr(request, "_messages", FallbackStorage(request))
        return request

    def test_viewer_cannot_access_stock_operations_or_reports(self):
        for view in [stock_receive, issue_stock, reports, manage_users]:
            request = self.factory.get("/")
            request.user = self.viewer
            with self.assertRaises(PermissionDenied):
                view(request)

    def test_storekeeper_can_open_issue_page_but_not_reports(self):
        request = self.factory.get("/issue/")
        request.user = self.storekeeper
        response = issue_stock(request)
        self.assertEqual(response.status_code, 200)

        request = self.factory.get("/reports/")
        request.user = self.storekeeper
        with self.assertRaises(PermissionDenied):
            reports(request)

    def test_issue_cannot_reduce_stock_below_zero(self):
        request = self.factory.post("/issue/", data={
            "location": self.location.pk,
            "issued_to": "Team A",
            "issued_by": "Store Keeper",
            "issued_at": "2026-04-22",
            "notes": "",
            "issue_item_0": str(self.item.pk),
            "issue_qty_0": "25.00",
        })
        request.user = self.storekeeper
        response = issue_stock(request)

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Only 20.00 pcs of Soap available.", response.content)
        self.assertEqual(IssueBatch.objects.count(), 0)

    def test_manager_can_void_receipt_and_restore_stock(self):
        batch = IssueBatch.objects.create(
            location=self.location,
            issued_to="Cleaning Team",
            issued_by="Manager",
            issued_at=date(2026, 4, 22),
            created_by=self.manager,
        )
        DistributionLine.objects.create(batch=batch, item=self.item, quantity=Decimal("6.00"))

        self.item.refresh_from_db()
        self.assertEqual(self.item.current_stock, Decimal("14.00"))

        request = self.factory.post(f"/receipts/{batch.receipt_number}/void/", data={"reason": "Entry duplicated"})
        request.user = self.manager
        self._attach_session_and_messages(request)
        response = void_receipt(request, batch.receipt_number)

        self.assertEqual(response.status_code, 302)
        batch.refresh_from_db()
        self.item.refresh_from_db()

        self.assertTrue(batch.is_voided)
        self.assertEqual(batch.void_reason, "Entry duplicated")
        self.assertEqual(self.item.current_stock, Decimal("20.00"))

    def test_stock_adjustment_cannot_make_stock_negative(self):
        request = self.factory.post("/stock/adjust/", data={
            "item": self.item.pk,
            "direction": "decrease",
            "quantity": "30.00",
            "reason": StockAdjustment.REASON_DAMAGE,
            "notes": "Breakage",
            "adjusted_at": "2026-04-22",
        })
        request.user = self.manager
        response = stock_adjustment_create(request)

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"would reduce Soap below zero stock", response.content)
        self.assertEqual(StockAdjustment.objects.count(), 0)

    def test_stock_adjustment_updates_current_stock(self):
        StockAdjustment.objects.create(
            item=self.item,
            quantity_delta=Decimal("-3.50"),
            reason=StockAdjustment.REASON_DAMAGE,
            adjusted_at=date(2026, 4, 22),
            created_by=self.manager,
        )
        self.item.refresh_from_db()
        self.assertEqual(self.item.current_stock, Decimal("16.50"))
