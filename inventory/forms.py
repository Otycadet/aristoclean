from django import forms
from django.contrib.auth.models import User
from django.utils import timezone
from .models import Item, Location, UserProfile


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


class UserCreateForm(forms.ModelForm):
    role = forms.ChoiceField(choices=UserProfile.ROLE_CHOICES)
    password1 = forms.CharField(widget=forms.PasswordInput, label="Password")
    password2 = forms.CharField(widget=forms.PasswordInput, label="Confirm password")
    active = forms.BooleanField(required=False, initial=True, label="Can sign in")

    class Meta:
        model = User
        fields = ["username", "first_name", "last_name", "email"]

    def clean_username(self):
        username = self.cleaned_data["username"].strip()
        if User.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError("A user with this username already exists.")
        return username

    def clean(self):
        cleaned_data = super().clean()
        if (
            cleaned_data.get("password1")
            and cleaned_data.get("password2")
            and cleaned_data["password1"] != cleaned_data["password2"]
        ):
            self.add_error("password2", "Passwords do not match.")
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
            profile.role = self.cleaned_data["role"]
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
        super().__init__(*args, **kwargs)
        profile = getattr(self.instance, "profile", None)
        self.fields["role"].initial = getattr(profile, "role", UserProfile.ROLE_STOREKEEPER)
        self.fields["active"].initial = self.instance.is_active

    def save(self, commit=True):
        user = super().save(commit=False)
        user.is_active = self.cleaned_data["active"]
        if commit:
            user.save()
            if self.cleaned_data.get("new_password"):
                user.set_password(self.cleaned_data["new_password"])
                user.save(update_fields=["password"])
            profile, _ = UserProfile.objects.get_or_create(user=user)
            profile.role = self.cleaned_data["role"]
            profile.save()
        return user
