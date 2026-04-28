from decimal import Decimal

from django import forms
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.utils import timezone

from .models import Item, Location, StockAdjustment, UserProfile


class StockEntryLineForm(forms.Form):
    item_name = forms.CharField(max_length=255, label="Item")
    unit = forms.CharField(max_length=100)
    reorder_level = forms.DecimalField(min_value=0, initial=Decimal("0.00"), label="Reorder level")
    quantity = forms.DecimalField(min_value=Decimal("0.01"), decimal_places=2, max_digits=12, label="Quantity received")


class DeliveryHeaderForm(forms.Form):
    supplier = forms.CharField(max_length=255, required=False, label="Supplier")
    reference = forms.CharField(max_length=255, required=False, label="Reference / PO#")
    notes = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), required=False)
    received_at = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date"}),
        initial=timezone.localdate,
    )


class IssueBatchHeaderForm(forms.Form):
    location = forms.ModelChoiceField(
        queryset=Location.objects.none(),
        label="Location / Department",
        empty_label="Select location",
    )
    issued_to = forms.CharField(max_length=255, required=False, label="Issued to")
    issued_by = forms.CharField(max_length=255, required=False, label="Issued by")
    notes = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), required=False)
    issued_at = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date"}),
        initial=timezone.localdate,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["location"].queryset = Location.objects.filter(active=True).order_by("name")


class IssueLineForm(forms.Form):
    item_name = forms.CharField(max_length=255, label="Item")
    quantity = forms.DecimalField(min_value=Decimal("0.01"), decimal_places=2, max_digits=12)


class ItemForm(forms.ModelForm):
    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        queryset = Item.objects.filter(name__iexact=name)
        if self.instance.pk:
            queryset = queryset.exclude(pk=self.instance.pk)
        if queryset.exists():
            raise forms.ValidationError("An item with this name already exists.")
        return name

    class Meta:
        model = Item
        fields = ["name", "unit", "reorder_level", "active"]


class LocationForm(forms.ModelForm):
    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        queryset = Location.objects.filter(name__iexact=name)
        if self.instance.pk:
            queryset = queryset.exclude(pk=self.instance.pk)
        if queryset.exists():
            raise forms.ValidationError("A location with this name already exists.")
        return name

    class Meta:
        model = Location
        fields = ["name", "active"]


class ReportFilterForm(forms.Form):
    MONTH_CHOICES = [
        (1, "January"), (2, "February"), (3, "March"), (4, "April"),
        (5, "May"), (6, "June"), (7, "July"), (8, "August"),
        (9, "September"), (10, "October"), (11, "November"), (12, "December"),
    ]

    year = forms.IntegerField(min_value=2000, max_value=2100, initial=timezone.localdate().year)
    month = forms.ChoiceField(choices=MONTH_CHOICES, initial=timezone.localdate().month)
    location = forms.ChoiceField(required=False)
    query = forms.CharField(required=False, max_length=255, label="Search")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        location_choices = [("", "All locations")] + [
            (loc.name, loc.name)
            for loc in Location.objects.filter(active=True).order_by("name")
        ]
        self.fields["location"].choices = location_choices


class ReceiptFilterForm(forms.Form):
    q = forms.CharField(required=False, max_length=255, label="Search")
    item = forms.ModelChoiceField(
        queryset=Item.objects.none(),
        required=False,
        label="Item",
        empty_label="All items",
    )
    location = forms.ChoiceField(required=False)
    status = forms.ChoiceField(
        required=False,
        choices=[
            ("", "All receipts"),
            ("active", "Active only"),
            ("voided", "Voided only"),
        ],
    )
    date_from = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
        label="From",
    )
    date_to = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
        label="To",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["item"].queryset = Item.objects.filter(active=True).order_by("name")
        self.fields["location"].choices = [("", "All locations")] + [
            (loc.name, loc.name)
            for loc in Location.objects.order_by("name")
        ]


class StockAdjustmentForm(forms.ModelForm):
    DIRECTION_INCREASE = "increase"
    DIRECTION_DECREASE = "decrease"
    DIRECTION_CHOICES = [
        (DIRECTION_INCREASE, "Increase stock"),
        (DIRECTION_DECREASE, "Decrease stock"),
    ]

    direction = forms.ChoiceField(choices=DIRECTION_CHOICES)
    quantity = forms.DecimalField(min_value=Decimal("0.01"), decimal_places=2, max_digits=12)

    class Meta:
        model = StockAdjustment
        fields = ["item", "reason", "notes", "adjusted_at"]
        widgets = {
            "adjusted_at": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["item"].queryset = Item.objects.filter(active=True).order_by("name")
        self.fields["adjusted_at"].initial = timezone.localdate

    def clean(self):
        cleaned_data = super().clean()
        quantity = cleaned_data.get("quantity")
        direction = cleaned_data.get("direction")
        if quantity is not None and direction:
            cleaned_data["quantity_delta"] = quantity if direction == self.DIRECTION_INCREASE else -quantity
        return cleaned_data


class ReceiptVoidForm(forms.Form):
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), label="Void reason")


class UserCreateForm(forms.ModelForm):
    role = forms.ChoiceField(choices=UserProfile.ROLE_CHOICES)
    password1 = forms.CharField(widget=forms.PasswordInput, label="Password")
    password2 = forms.CharField(widget=forms.PasswordInput, label="Confirm password")
    active = forms.BooleanField(required=False, initial=True, label="Can sign in")

    class Meta:
        model = User
        fields = ["username", "first_name", "last_name", "email"]

    def __init__(self, *args, **kwargs):
        self.acting_user = kwargs.pop("acting_user", None)
        role_choices = kwargs.pop("role_choices", None)
        super().__init__(*args, **kwargs)
        if role_choices is not None:
            self.fields["role"].choices = role_choices
        if "role" in self.fields and not self.fields["role"].choices:
            self.fields.pop("role", None)

    def clean_username(self):
        username = self.cleaned_data["username"].strip()
        if User.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError("A user with this username already exists.")
        return username

    def clean(self):
        cleaned_data = super().clean()
        password = cleaned_data.get("password1")
        if (
            password
            and cleaned_data.get("password2")
            and password != cleaned_data["password2"]
        ):
            self.add_error("password2", "Passwords do not match.")
        elif password:
            user = User(
                username=cleaned_data.get("username", "").strip(),
                first_name=cleaned_data.get("first_name", ""),
                last_name=cleaned_data.get("last_name", ""),
                email=cleaned_data.get("email", ""),
            )
            try:
                validate_password(password, user=user)
            except ValidationError as exc:
                self.add_error("password1", exc)
        return cleaned_data

    def save(self, commit=True):
        user = super().save(commit=False)
        user.username = self.cleaned_data["username"].strip()
        user.is_active = self.cleaned_data["active"]
        user.is_staff = False
        user.is_superuser = False
        user.set_password(self.cleaned_data["password1"])
        if commit:
            user.save()
            profile, _ = UserProfile.objects.get_or_create(user=user)
            profile.role = (
                self.cleaned_data["role"]
                if "role" in self.cleaned_data
                else UserProfile.ROLE_STOREKEEPER
            )
            profile.save()
        return user


class UserUpdateForm(forms.ModelForm):
    role = forms.ChoiceField(choices=UserProfile.ROLE_CHOICES)
    active = forms.BooleanField(required=False, label="Can sign in")
    new_password = forms.CharField(
        required=False,
        widget=forms.PasswordInput(render_value=False),
        label="Reset password",
    )

    class Meta:
        model = User
        fields = ["first_name", "last_name", "email"]

    def __init__(self, *args, **kwargs):
        self.acting_user = kwargs.pop("acting_user", None)
        role_choices = kwargs.pop("role_choices", None)
        super().__init__(*args, **kwargs)
        profile = getattr(self.instance, "profile", None)
        if role_choices is not None:
            self.fields["role"].choices = role_choices
        if "role" in self.fields and self.fields["role"].choices:
            self.fields["role"].initial = getattr(profile, "role", UserProfile.ROLE_STOREKEEPER)
        else:
            self.fields.pop("role", None)
        self.fields["active"].initial = self.instance.is_active

    def save(self, commit=True):
        user = super().save(commit=False)
        user.is_active = self.cleaned_data["active"]
        password = self.cleaned_data.get("new_password")
        if commit:
            user.save()
            if password:
                user.set_password(password)
                user.save(update_fields=["password"])
            profile, _ = UserProfile.objects.get_or_create(user=user)
            if "role" in self.cleaned_data:
                profile.role = self.cleaned_data["role"]
            profile.save()
        return user

    def clean_new_password(self):
        password = self.cleaned_data.get("new_password")
        if not password:
            return password
        validate_password(password, user=self.instance)
        return password
