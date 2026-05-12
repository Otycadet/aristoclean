from datetime import date
from decimal import Decimal

from django.contrib.auth.signals import user_logged_in
from django.contrib.auth.models import User
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core.management import call_command
from django.contrib.sessions.middleware import SessionMiddleware
from django.core.exceptions import PermissionDenied
from django.test import RequestFactory, TestCase, override_settings

from .forms import UserCreateForm, UserUpdateForm
from .models import DistributionLine, IssueBatch, Item, Location, SignInLog, StockAdjustment, StockEntry, UserProfile
from .views import (
    export_csv,
    issue_stock,
    low_stock_report,
    manage_items,
    manage_locations,
    manage_users,
    receipts_list,
    receipt_detail,
    reorder_list,
    reports,
    sign_in_logs,
    stock_adjustment_create,
    stock_receive,
    void_receipt,
)


class InventoryTestCase(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.viewer = self._create_user("viewer", UserProfile.ROLE_VIEWER)
        self.storekeeper = self._create_user("storekeeper", UserProfile.ROLE_STOREKEEPER)
        self.manager = self._create_user("manager", UserProfile.ROLE_MANAGER)
        self.admin = self._create_user("adminrole", UserProfile.ROLE_ADMIN)
        self.superuser = User.objects.create_superuser("admin", "admin@example.com", "pass1234")

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

        request = self.factory.get("/stock/reorder/")
        request.user = self.storekeeper
        with self.assertRaises(PermissionDenied):
            reorder_list(request)

        request = self.factory.get("/stock/low/")
        request.user = self.storekeeper
        with self.assertRaises(PermissionDenied):
            low_stock_report(request)

    def test_manager_can_open_operational_oversight_pages(self):
        request = self.factory.get("/stock/reorder/")
        request.user = self.manager
        response = reorder_list(request)
        self.assertEqual(response.status_code, 200)

        request = self.factory.get("/stock/low/")
        request.user = self.manager
        response = low_stock_report(request)
        self.assertEqual(response.status_code, 200)

        request = self.factory.get("/reports/")
        request.user = self.manager
        response = reports(request)
        self.assertEqual(response.status_code, 200)

        request = self.factory.get("/manage/items/")
        request.user = self.manager
        with self.assertRaises(PermissionDenied):
            manage_items(request)

    def test_admin_can_open_management_pages_but_not_operational_pages(self):
        request = self.factory.get("/manage/items/")
        request.user = self.admin
        response = manage_items(request)
        self.assertEqual(response.status_code, 200)

        request = self.factory.get("/manage/locations/")
        request.user = self.admin
        response = manage_locations(request)
        self.assertEqual(response.status_code, 200)

        request = self.factory.get("/manage/users/")
        request.user = self.admin
        response = manage_users(request)
        self.assertEqual(response.status_code, 200)

        request = self.factory.get("/reports/")
        request.user = self.admin
        with self.assertRaises(PermissionDenied):
            reports(request)

        request = self.factory.get("/issue/")
        request.user = self.admin
        with self.assertRaises(PermissionDenied):
            issue_stock(request)

    @override_settings(DEBUG=False)
    def test_custom_404_page_is_rendered(self):
        response = self.client.get("/missing-page/")

        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "Page Not Found")

    @override_settings(DEBUG=False)
    def test_custom_403_page_is_rendered(self):
        self.client.force_login(self.storekeeper)

        response = self.client.get("/reports/")

        self.assertEqual(response.status_code, 403)
        self.assertContains(response, "Access Denied")

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

    def test_issue_rejects_decimal_quantity(self):
        request = self.factory.post("/issue/", data={
            "location": self.location.pk,
            "issued_to": "Team A",
            "issued_by": "Store Keeper",
            "issued_at": "2026-04-22",
            "notes": "",
            "issue_item_0": str(self.item.pk),
            "issue_qty_0": "2.50",
        })
        request.user = self.storekeeper

        response = issue_stock(request)

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Enter a whole number for quantity for Soap.", response.content)

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

    def test_receipt_detail_shows_only_issued_quantities(self):
        batch = IssueBatch.objects.create(
            location=self.location,
            issued_to="Cleaning Team",
            issued_by="Store Keeper",
            issued_at=date(2026, 4, 22),
            created_by=self.storekeeper,
        )
        DistributionLine.objects.create(batch=batch, item=self.item, quantity=Decimal("6.00"))

        request = self.factory.get(f"/receipts/{batch.receipt_number}/")
        request.user = self.storekeeper
        response = receipt_detail(request, batch.receipt_number)
        content = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn("Quantity Issued", content)
        self.assertIn("6.00", content)
        self.assertNotIn("Balance After Issue", content)

    def test_receipts_list_shows_item_disbursal_summary_across_locations(self):
        second_location = Location.objects.create(name="Branch Store")
        first_batch = IssueBatch.objects.create(
            location=self.location,
            issued_to="Cleaning Team",
            issued_by="Store Keeper",
            issued_at=date(2026, 4, 22),
            created_by=self.storekeeper,
        )
        second_batch = IssueBatch.objects.create(
            location=second_location,
            issued_to="Branch Team",
            issued_by="Store Keeper",
            issued_at=date(2026, 4, 23),
            created_by=self.storekeeper,
        )
        DistributionLine.objects.create(batch=first_batch, item=self.item, quantity=Decimal("6.00"))
        DistributionLine.objects.create(batch=second_batch, item=self.item, quantity=Decimal("4.00"))

        request = self.factory.get("/receipts/", data={"item": str(self.item.pk), "status": "active"})
        request.user = self.storekeeper
        response = receipts_list(request)
        content = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn("Item Disbursal Summary", content)
        self.assertIn("10.00", content)
        self.assertIn("Main Store", content)
        self.assertIn("Branch Store", content)

    def test_successful_sign_in_creates_log_entry(self):
        request = self.factory.get("/accounts/login/", HTTP_USER_AGENT="InventoryBrowser/1.0", REMOTE_ADDR="127.0.0.1")
        request.user = self.storekeeper

        user_logged_in.send(sender=self.storekeeper.__class__, request=request, user=self.storekeeper)

        log = SignInLog.objects.get(user=self.storekeeper)
        self.assertEqual(log.username_snapshot, self.storekeeper.username)
        self.assertEqual(log.ip_address, "127.0.0.1")
        self.assertEqual(log.user_agent, "InventoryBrowser/1.0")

    def test_admin_can_open_sign_in_logs(self):
        SignInLog.objects.create(user=self.storekeeper, username_snapshot=self.storekeeper.username)
        request = self.factory.get("/manage/sign-ins/")
        request.user = self.admin

        response = sign_in_logs(request)

        self.assertEqual(response.status_code, 200)
        self.assertIn("Sign-in Logs", response.content.decode())

    def test_manager_cannot_open_sign_in_logs(self):
        request = self.factory.get("/manage/sign-ins/")
        request.user = self.manager

        with self.assertRaises(PermissionDenied):
            sign_in_logs(request)

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

    def test_stock_receive_can_use_existing_item_dropdown(self):
        request = self.factory.post("/stock/receive/", data={
            "supplier": "Local Supplier",
            "reference": "PO-100",
            "notes": "",
            "received_at": "2026-04-22",
            "line_item_0": str(self.item.pk),
            "line_new_item_0": "",
            "line_unit_0": "pcs",
            "line_reorder_0": "12.00",
            "line_qty_0": "5.00",
        })
        request.user = self.storekeeper
        self._attach_session_and_messages(request)

        response = stock_receive(request)

        self.assertEqual(response.status_code, 302)
        self.item.refresh_from_db()
        self.assertEqual(Item.objects.count(), 1)
        self.assertEqual(self.item.reorder_level, Decimal("12.00"))
        self.assertEqual(self.item.current_stock, Decimal("25.00"))

    def test_stock_receive_ignores_extra_blank_line(self):
        data = {
            "supplier": "Local Supplier",
            "reference": "PO-102",
            "notes": "",
            "received_at": "2026-04-22",
        }
        for index in range(5):
            data.update({
                f"line_item_{index}": str(self.item.pk),
                f"line_new_item_{index}": "",
                f"line_unit_{index}": "pcs",
                f"line_reorder_{index}": "10.00",
                f"line_qty_{index}": "1",
            })
        data.update({
            "line_item_5": "",
            "line_new_item_5": "",
            "line_unit_5": "",
            "line_reorder_5": "0",
            "line_qty_5": "",
        })
        request = self.factory.post("/stock/receive/", data=data)
        request.user = self.storekeeper
        self._attach_session_and_messages(request)

        response = stock_receive(request)

        self.assertEqual(response.status_code, 302)
        self.item.refresh_from_db()
        self.assertEqual(self.item.current_stock, Decimal("25.00"))
        self.assertEqual(StockEntry.objects.filter(item=self.item).count(), 6)

    def test_issue_stock_ignores_extra_blank_line(self):
        data = {
            "location": self.location.pk,
            "issued_to": "Team A",
            "issued_by": "Store Keeper",
            "issued_at": "2026-04-22",
            "notes": "",
        }
        for index in range(5):
            data.update({
                f"issue_item_{index}": str(self.item.pk),
                f"issue_qty_{index}": "1",
            })
        data.update({
            "issue_item_5": "",
            "issue_qty_5": "",
        })
        request = self.factory.post("/issue/", data=data)
        request.user = self.storekeeper
        self._attach_session_and_messages(request)

        response = issue_stock(request)

        self.assertEqual(response.status_code, 302)
        batch = IssueBatch.objects.get()
        self.assertEqual(batch.lines.count(), 1)
        self.assertEqual(batch.lines.get().quantity, Decimal("5.00"))
        self.item.refresh_from_db()
        self.assertEqual(self.item.current_stock, Decimal("15.00"))

    def test_stock_receive_rejects_decimal_quantity(self):
        request = self.factory.post("/stock/receive/", data={
            "supplier": "Local Supplier",
            "reference": "PO-100",
            "notes": "",
            "received_at": "2026-04-22",
            "line_item_0": str(self.item.pk),
            "line_new_item_0": "",
            "line_unit_0": "pcs",
            "line_reorder_0": "12.00",
            "line_qty_0": "5.50",
        })
        request.user = self.storekeeper

        response = stock_receive(request)

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Enter a whole number for quantity for Soap.", response.content)

    def test_stock_receive_can_create_new_item_from_typed_name(self):
        request = self.factory.post("/stock/receive/", data={
            "supplier": "Fresh Supplier",
            "reference": "PO-101",
            "notes": "",
            "received_at": "2026-04-22",
            "line_item_0": "",
            "line_new_item_0": "Detergent",
            "line_unit_0": "bottles",
            "line_reorder_0": "8.00",
            "line_qty_0": "4.00",
        })
        request.user = self.storekeeper
        self._attach_session_and_messages(request)

        response = stock_receive(request)

        self.assertEqual(response.status_code, 302)
        new_item = Item.objects.get(name="Detergent")
        self.assertEqual(new_item.unit, "bottles")
        self.assertEqual(new_item.reorder_level, Decimal("8.00"))
        self.assertEqual(new_item.current_stock, Decimal("4.00"))

    def test_stock_receive_converts_packs_to_base_unit(self):
        self.item.unit = "pieces"
        self.item.pack_size = Decimal("12.00")
        self.item.save(update_fields=["unit", "pack_size"])

        request = self.factory.post("/stock/receive/", data={
            "supplier": "Local Supplier",
            "reference": "PO-103",
            "notes": "",
            "received_at": "2026-04-22",
            "line_item_0": str(self.item.pk),
            "line_new_item_0": "",
            "line_unit_0": "pieces",
            "line_reorder_0": "10.00",
            "line_measure_0": "pack",
            "line_qty_0": "2",
        })
        request.user = self.storekeeper
        self._attach_session_and_messages(request)

        response = stock_receive(request)

        self.assertEqual(response.status_code, 302)
        self.item.refresh_from_db()
        self.assertEqual(self.item.current_stock, Decimal("44.00"))

    def test_issue_stock_converts_packs_to_base_unit(self):
        self.item.unit = "pieces"
        self.item.pack_size = Decimal("12.00")
        self.item.save(update_fields=["unit", "pack_size"])

        request = self.factory.post("/issue/", data={
            "location": self.location.pk,
            "issued_to": "Team A",
            "issued_by": "Store Keeper",
            "issued_at": "2026-04-22",
            "notes": "",
            "issue_item_0": str(self.item.pk),
            "issue_measure_0": "pack",
            "issue_qty_0": "1",
        })
        request.user = self.storekeeper
        self._attach_session_and_messages(request)

        response = issue_stock(request)

        self.assertEqual(response.status_code, 302)
        batch = IssueBatch.objects.get()
        self.assertEqual(batch.lines.get().quantity, Decimal("12.00"))
        self.item.refresh_from_db()
        self.assertEqual(self.item.current_stock, Decimal("8.00"))

    def test_convert_item_unit_command_updates_existing_quantities(self):
        batch = IssueBatch.objects.create(
            location=self.location,
            issued_to="Cleaning Team",
            issued_by="Store Keeper",
            issued_at=date(2026, 4, 22),
            created_by=self.storekeeper,
        )
        DistributionLine.objects.create(batch=batch, item=self.item, quantity=Decimal("2.00"))
        StockAdjustment.objects.create(
            item=self.item,
            quantity_delta=Decimal("1.00"),
            reason=StockAdjustment.REASON_COUNT,
            adjusted_at=date(2026, 4, 23),
            created_by=self.manager,
        )
        self.item.unit = "packs"
        self.item.reorder_level = Decimal("5.00")
        self.item.save(update_fields=["unit", "reorder_level"])

        call_command(
            "convert_item_unit",
            str(self.item.pk),
            "48",
            "--new-unit",
            "pieces",
            "--pack-size",
            "48",
            "--apply",
            verbosity=0,
        )

        self.item.refresh_from_db()
        self.assertEqual(self.item.unit, "pieces")
        self.assertEqual(self.item.pack_size, Decimal("48.00"))
        self.assertEqual(self.item.reorder_level, Decimal("240.00"))
        self.assertEqual(StockEntry.objects.get(item=self.item).quantity, Decimal("960.00"))
        self.assertEqual(DistributionLine.objects.get(item=self.item).quantity, Decimal("96.00"))
        self.assertEqual(StockAdjustment.objects.get(item=self.item).quantity_delta, Decimal("48.00"))
        self.assertEqual(self.item.current_stock, Decimal("912.00"))

    def test_superuser_can_open_manage_pages(self):
        request = self.factory.get("/manage/items/")
        request.user = self.superuser
        response = manage_items(request)
        self.assertEqual(response.status_code, 200)

        request = self.factory.get("/manage/locations/")
        request.user = self.superuser
        response = manage_locations(request)
        self.assertEqual(response.status_code, 200)

        request = self.factory.get("/manage/users/")
        request.user = self.superuser
        response = manage_users(request)
        self.assertEqual(response.status_code, 200)

    def test_admin_can_create_user(self):
        request = self.factory.post("/manage/users/", data={
            "action": "create",
            "create-username": "newadmin",
            "create-first_name": "New",
            "create-last_name": "Admin",
            "create-email": "newadmin@example.com",
            "create-role": UserProfile.ROLE_MANAGER,
            "create-password1": "Pass1234!!",
            "create-password2": "Pass1234!!",
            "create-active": "on",
        })
        request.user = self.admin
        self._attach_session_and_messages(request)

        response = manage_users(request)

        self.assertEqual(response.status_code, 302)
        new_user = User.objects.get(username="newadmin")
        self.assertEqual(new_user.profile.role, UserProfile.ROLE_MANAGER)

    def test_admin_cannot_see_superadmin_in_manage_users_list(self):
        hidden_superuser = User.objects.create_superuser("chiefroot", "chief@example.com", "pass1234")
        request = self.factory.get("/manage/users/")
        request.user = self.admin

        response = manage_users(request)
        content = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("chiefroot", content)
        self.assertNotIn(hidden_superuser.email, content)

    def test_admin_cannot_change_own_role(self):
        request = self.factory.post("/manage/users/", data={
            "action": "update",
            "user_id": str(self.admin.pk),
            f"user-{self.admin.pk}-first_name": "Admin",
            f"user-{self.admin.pk}-last_name": "User",
            f"user-{self.admin.pk}-email": "adminrole@example.com",
            f"user-{self.admin.pk}-role": UserProfile.ROLE_MANAGER,
            f"user-{self.admin.pk}-active": "on",
            f"user-{self.admin.pk}-new_password": "",
        })
        request.user = self.admin
        self._attach_session_and_messages(request)

        response = manage_users(request)

        self.assertEqual(response.status_code, 302)
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.profile.role, UserProfile.ROLE_ADMIN)

    def test_admin_cannot_change_superadmin_role(self):
        request = self.factory.post("/manage/users/", data={
            "action": "update",
            "user_id": str(self.superuser.pk),
            f"user-{self.superuser.pk}-first_name": "Chief",
            f"user-{self.superuser.pk}-last_name": "Admin",
            f"user-{self.superuser.pk}-email": "chief@example.com",
            f"user-{self.superuser.pk}-role": UserProfile.ROLE_MANAGER,
            f"user-{self.superuser.pk}-active": "on",
            f"user-{self.superuser.pk}-new_password": "",
        })
        request.user = self.admin
        self._attach_session_and_messages(request)

        with self.assertRaises(PermissionDenied):
            manage_users(request)

        self.superuser.refresh_from_db()
        self.assertTrue(self.superuser.is_superuser)

    def test_superuser_can_promote_manager_to_admin(self):
        request = self.factory.post("/manage/users/", data={
            "action": "update",
            "user_id": str(self.manager.pk),
            f"user-{self.manager.pk}-username": self.manager.username,
            f"user-{self.manager.pk}-first_name": self.manager.first_name,
            f"user-{self.manager.pk}-last_name": self.manager.last_name,
            f"user-{self.manager.pk}-email": self.manager.email,
            f"user-{self.manager.pk}-role": UserProfile.ROLE_ADMIN,
            f"user-{self.manager.pk}-active": "on",
            f"user-{self.manager.pk}-new_password": "",
        })
        request.user = self.superuser
        self._attach_session_and_messages(request)

        response = manage_users(request)

        self.assertEqual(response.status_code, 302)
        self.manager.refresh_from_db()
        self.assertEqual(self.manager.profile.role, UserProfile.ROLE_ADMIN)

    def test_superuser_can_change_username(self):
        request = self.factory.post("/manage/users/", data={
            "action": "update",
            "user_id": str(self.manager.pk),
            f"user-{self.manager.pk}-username": "operationslead",
            f"user-{self.manager.pk}-first_name": self.manager.first_name,
            f"user-{self.manager.pk}-last_name": self.manager.last_name,
            f"user-{self.manager.pk}-email": self.manager.email,
            f"user-{self.manager.pk}-role": UserProfile.ROLE_MANAGER,
            f"user-{self.manager.pk}-active": "on",
            f"user-{self.manager.pk}-new_password": "",
        })
        request.user = self.superuser
        self._attach_session_and_messages(request)

        response = manage_users(request)

        self.assertEqual(response.status_code, 302)
        self.manager.refresh_from_db()
        self.assertEqual(self.manager.username, "operationslead")

    def test_manager_cannot_access_manage_users_or_create_user(self):
        request = self.factory.get("/manage/users/")
        request.user = self.manager
        with self.assertRaises(PermissionDenied):
            manage_users(request)

        request = self.factory.post("/manage/users/", data={
            "action": "create",
            "create-username": "newuser",
            "create-first_name": "New",
            "create-last_name": "User",
            "create-email": "new@example.com",
            "create-role": UserProfile.ROLE_MANAGER,
            "create-password1": "Pass1234!!",
            "create-password2": "Pass1234!!",
            "create-active": "on",
        })
        request.user = self.manager
        self._attach_session_and_messages(request)

        with self.assertRaises(PermissionDenied):
            manage_users(request)

        self.assertFalse(User.objects.filter(username="newuser").exists())

    def test_manager_cannot_change_role_via_forged_post(self):
        request = self.factory.post("/manage/users/", data={
            "action": "update",
            "user_id": str(self.storekeeper.pk),
            f"user-{self.storekeeper.pk}-first_name": "Updated",
            f"user-{self.storekeeper.pk}-last_name": "",
            f"user-{self.storekeeper.pk}-email": "updated@example.com",
            f"user-{self.storekeeper.pk}-role": UserProfile.ROLE_MANAGER,
            f"user-{self.storekeeper.pk}-active": "on",
            f"user-{self.storekeeper.pk}-new_password": "",
        })
        request.user = self.manager
        self._attach_session_and_messages(request)

        with self.assertRaises(PermissionDenied):
            manage_users(request)

        self.storekeeper.refresh_from_db()
        self.assertEqual(self.storekeeper.profile.role, UserProfile.ROLE_STOREKEEPER)

    def test_manager_cannot_edit_admin_account(self):
        request = self.factory.post("/manage/users/", data={
            "action": "update",
            "user_id": str(self.admin.pk),
            f"user-{self.admin.pk}-first_name": "Nope",
            f"user-{self.admin.pk}-last_name": "",
            f"user-{self.admin.pk}-email": "nope@example.com",
            f"user-{self.admin.pk}-active": "on",
            f"user-{self.admin.pk}-new_password": "",
        })
        request.user = self.manager
        self._attach_session_and_messages(request)

        with self.assertRaises(PermissionDenied):
            manage_users(request)

    def test_admin_cannot_change_username_via_forged_post(self):
        request = self.factory.post("/manage/users/", data={
            "action": "update",
            "user_id": str(self.manager.pk),
            f"user-{self.manager.pk}-username": "renamedbyadmin",
            f"user-{self.manager.pk}-first_name": "Managed",
            f"user-{self.manager.pk}-last_name": "",
            f"user-{self.manager.pk}-email": "managed@example.com",
            f"user-{self.manager.pk}-role": UserProfile.ROLE_MANAGER,
            f"user-{self.manager.pk}-active": "on",
            f"user-{self.manager.pk}-new_password": "",
        })
        request.user = self.admin
        self._attach_session_and_messages(request)

        response = manage_users(request)

        self.assertEqual(response.status_code, 302)
        self.manager.refresh_from_db()
        self.assertEqual(self.manager.username, "manager")
        self.assertEqual(self.manager.first_name, "Managed")

    def test_superuser_can_edit_item_and_reorder_level(self):
        request = self.factory.post("/manage/items/", data={
            "item_id": str(self.item.pk),
            "name": "Liquid Soap",
            "unit": "bottles",
            "reorder_level": "15.00",
            "active": "on",
        })
        request.user = self.superuser
        self._attach_session_and_messages(request)

        response = manage_items(request)

        self.assertEqual(response.status_code, 302)
        self.item.refresh_from_db()
        self.assertEqual(self.item.name, "Liquid Soap")
        self.assertEqual(self.item.unit, "bottles")
        self.assertEqual(self.item.reorder_level, Decimal("15.00"))

    def test_manage_item_can_convert_existing_stock_to_new_base_unit(self):
        request = self.factory.post("/manage/items/", data={
            "item_id": str(self.item.pk),
            "name": self.item.name,
            "unit": "pieces",
            "pack_size": "48.00",
            "carton_size": "",
            "reorder_level": "10.00",
            "active": "on",
            "convert_existing_stock": "1",
            "conversion_factor": "48",
        })
        request.user = self.superuser
        self._attach_session_and_messages(request)

        response = manage_items(request)

        self.assertEqual(response.status_code, 302)
        self.item.refresh_from_db()
        self.assertEqual(self.item.unit, "pieces")
        self.assertEqual(self.item.pack_size, Decimal("48.00"))
        self.assertEqual(self.item.reorder_level, Decimal("480.00"))
        self.assertEqual(StockEntry.objects.get(item=self.item).quantity, Decimal("960.00"))
        self.assertEqual(self.item.current_stock, Decimal("960.00"))

    def test_superuser_can_edit_location(self):
        request = self.factory.post("/manage/locations/", data={
            "location_id": str(self.location.pk),
            "name": "Head Office Store",
            "active": "on",
        })
        request.user = self.superuser
        self._attach_session_and_messages(request)

        response = manage_locations(request)

        self.assertEqual(response.status_code, 302)
        self.location.refresh_from_db()
        self.assertEqual(self.location.name, "Head Office Store")

    def test_user_create_form_rejects_weak_password(self):
        form = UserCreateForm(data={
            "username": "newuser",
            "first_name": "New",
            "last_name": "User",
            "email": "new@example.com",
            "role": UserProfile.ROLE_STOREKEEPER,
            "password1": "password123",
            "password2": "password123",
            "active": "on",
        }, acting_user=self.superuser)

        self.assertFalse(form.is_valid())
        self.assertIn("password1", form.errors)

    def test_user_update_form_rejects_weak_password_reset(self):
        form = UserUpdateForm(data={
            "first_name": self.storekeeper.first_name,
            "last_name": self.storekeeper.last_name,
            "email": self.storekeeper.email,
            "role": UserProfile.ROLE_STOREKEEPER,
            "active": "on",
            "new_password": "password123",
        }, instance=self.storekeeper, acting_user=self.superuser)

        self.assertFalse(form.is_valid())
        self.assertIn("new_password", form.errors)

    def test_export_csv_sanitizes_formula_like_values(self):
        dangerous_item = Item.objects.create(name="=2+3", unit="@pcs", reorder_level=Decimal("1.00"))
        StockEntry.objects.create(
            item=dangerous_item,
            quantity=Decimal("5.00"),
            received_at=date(2026, 4, 22),
            created_by=self.manager,
        )
        request = self.factory.get("/stock/export/")
        request.user = self.manager

        response = export_csv(request)
        content = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn("'=2+3", content)
        self.assertIn("'@pcs", content)
