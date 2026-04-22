# Aristoclean Inventory — Deployment Guide

## What's in this project

```
aristoclean/
├── aristoclean/          ← Django project config
│   ├── settings.py
│   ├── urls.py
│   └── wsgi.py
├── inventory/            ← Your app (models, views, forms, …)
├── templates/            ← All HTML templates
├── manage.py
├── migrate_from_sqlite.py   ← One-time data import from old .db file
├── requirements.txt
└── Procfile              ← For Railway / Render
```

---

## Step 1 — Run it locally first

### 1a. Create a virtual environment

```bash
cd aristoclean
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 1b. Create a `.env` file (do NOT commit this)

```
SECRET_KEY=any-long-random-string-here
DEBUG=True
# Leave DATABASE_URL blank to use SQLite locally
```

### 1c. Apply migrations and create your first admin user

```bash
python manage.py migrate
python manage.py createsuperuser
# Follow prompts: enter username, email, password
```

This first user will have no role profile yet — assign one in the next step.

### 1d. Assign the manager role to your admin user

```bash
python manage.py shell
```

```python
from django.contrib.auth.models import User
from inventory.models import UserProfile
u = User.objects.get(username='your_username')
p, _ = UserProfile.objects.get_or_create(user=u)
p.role = 'manager'
p.save()
exit()
```

### 1e. Start the server

```bash
python manage.py runserver
```

Open http://127.0.0.1:8000 — log in with the superuser account.

---

## Step 2 — Import your existing data (optional)

Copy your original `.db` file into the project folder, then:

```bash
python migrate_from_sqlite.py path/to/inventory_management_system.db
```

This migrates all items, locations, stock entries, issue batches, and
distribution lines into the new database. It is safe to re-run (it skips
already-imported records).

---

## Step 3 — Deploy to Railway (recommended, free tier available)

Railway is the fastest option: push to GitHub, connect, done.

### 3a. Push your project to GitHub

```bash
git init
git add .
git commit -m "Initial commit"
# Create a repo on github.com, then:
git remote add origin https://github.com/YOUR_USER/aristoclean.git
git push -u origin main
```

### 3b. Create a Railway project

1. Go to https://railway.app and sign up / log in.
2. Click **New Project → Deploy from GitHub repo**.
3. Select your `aristoclean` repository.
4. Railway will detect the `Procfile` automatically.

### 3c. Add a PostgreSQL database

1. In your Railway project, click **+ New → Database → PostgreSQL**.
2. Railway automatically injects `DATABASE_URL` into your service environment.

### 3d. Set environment variables

In your Railway service → **Variables**, add:

| Variable | Value |
|---|---|
| `SECRET_KEY` | A long random string (generate one at https://djecrety.ir/) |
| `DEBUG` | `False` |
| `ALLOWED_HOSTS` | Your Railway domain, e.g. `aristoclean.up.railway.app` |
| `CSRF_TRUSTED_ORIGINS` | `https://aristoclean.up.railway.app` |

Railway injects `DATABASE_URL` and `PORT` automatically — no need to add those.

### 3e. Deploy

Railway redeploys automatically on every `git push`. The `Procfile` runs
`migrate` on each deploy before starting the server.

### 3f. Create your first superuser on Railway

```bash
# Install Railway CLI: https://docs.railway.app/develop/cli
railway run python manage.py createsuperuser
```

Or use the Railway shell in the web dashboard.

Then assign the manager role via the shell (same commands as Step 1d).

---

## Alternative: Deploy to Render (also free tier)

1. Create account at https://render.com.
2. **New → Web Service → Connect GitHub repo**.
3. Set:
   - **Build command:** `pip install -r requirements.txt && python manage.py collectstatic --noinput`
   - **Start command:** `gunicorn aristoclean.wsgi --workers 2 --bind 0.0.0.0:$PORT`
4. Add a **PostgreSQL** database (New → PostgreSQL), copy the `Internal Database URL`.
5. In your web service **Environment**, add all variables from the table above, plus `DATABASE_URL`.

---

## Step 4 — Create additional user accounts

### Via Django admin panel

Go to `/admin/` → **Users → Add user**.

After creating each user, go to **Inventory → User profiles** and set their role:

- **Store Keeper** — can view stock, receive deliveries, issue stock, view receipts and reports
- **Manager / Admin** — everything above + manage items/locations

### Roles summary

| Screen | Store Keeper | Manager |
|---|---|---|
| Dashboard | ✓ | ✓ |
| Stock / Receive | ✓ | ✓ |
| Issue Stock | ✓ | ✓ |
| Receipts | ✓ | ✓ |
| Reports + CSV export | ✓ | ✓ |
| Manage Items/Locations | ✗ | ✓ |
| Django /admin panel | ✗ | ✓ (if staff) |

---

## Keeping your data when redeploying

Because you're using PostgreSQL on Railway/Render, your data lives in the
hosted database — not in the app container. Redeployments never touch the
database. Your data is safe.

## Backing up

Railway and Render both offer database backups in their dashboards.
For manual backup:

```bash
railway run python manage.py dumpdata --natural-foreign --indent 2 > backup.json
```

To restore:

```bash
railway run python manage.py loaddata backup.json
```

---

## Quick reference — useful management commands

```bash
# Run locally
python manage.py runserver

# Create new Django migrations after editing models.py
python manage.py makemigrations
python manage.py migrate

# Collect static files (done automatically in Procfile)
python manage.py collectstatic

# Open a Django shell
python manage.py shell

# Import old SQLite data
python migrate_from_sqlite.py path/to/old.db
```
