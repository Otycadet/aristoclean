# Aristoclean Inventory - Deployment Guide

## What's in This Project

```text
aristoclean/
|-- aristoclean/              Django project config
|   |-- settings.py
|   |-- urls.py
|   `-- wsgi.py
|-- inventory/                Main app
|-- templates/                HTML templates
|-- manage.py
|-- migrate_from_sqlite.py    One-time data import from old .db file
|-- requirements.txt
`-- Procfile                  Railway / Render process file
```

## Step 1 - Run It Locally First

### 1a. Create a virtual environment

```bash
cd aristoclean
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 1b. Create a `.env` file

Do not commit this file.

```env
SECRET_KEY=any-long-random-string-here
DEBUG=True
# Leave DATABASE_URL blank to use SQLite locally
```

### 1c. Apply migrations and create your first admin user

```bash
python manage.py migrate
python manage.py createsuperuser
```

### 1d. Start the server

```bash
python manage.py runserver
```

Open `http://127.0.0.1:8000` and sign in with the superuser account.

## Step 2 - Import Existing Data (Optional)

Copy your original `.db` file into the project folder, then run:

```bash
python migrate_from_sqlite.py path/to/inventory_management_system.db
```

This imports items, locations, stock entries, issue batches, and distribution lines into the new database.

## Step 3 - Deploy to Railway

### 3a. Push the project to GitHub

```bash
git init
git add .
git commit -m "Initial commit"
git remote add origin https://github.com/YOUR_USER/aristoclean.git
git push -u origin main
```

### 3b. Create a Railway project

1. Go to `https://railway.app`.
2. Create a new project from your GitHub repo.
3. Railway will detect the `Procfile`.

### 3c. Add a PostgreSQL database

1. In Railway, add a PostgreSQL database.
2. Railway will inject `DATABASE_URL` automatically.

### 3d. Set environment variables

| Variable | Value |
|---|---|
| `SECRET_KEY` | A long random string |
| `DEBUG` | `False` |
| `ALLOWED_HOSTS` | Your Railway domain, for example `aristoclean.up.railway.app` |
| `CSRF_TRUSTED_ORIGINS` | `https://your-domain.example` |

`DATABASE_URL` and `PORT` are usually provided by the platform.

### 3e. Create your first superuser on Railway

```bash
railway run python manage.py createsuperuser
```

## Alternative - Deploy to Render

1. Create a Render account.
2. Create a new Web Service from the GitHub repo.
3. Use:
   Build command: `pip install -r requirements.txt && python manage.py collectstatic --noinput`
   Start command: `gunicorn aristoclean.wsgi --workers 2 --bind 0.0.0.0:$PORT`
4. Add a PostgreSQL database and set `DATABASE_URL`.
5. Add the same environment variables listed above.

## Step 4 - Create Additional User Accounts

### Via Django admin

Go to `/admin/`, add a user, then set the user's role under `Inventory > User profiles`.

- `Store Keeper`: can view stock, receive deliveries, issue stock, and view receipts.
- `Manager / Admin`: everything above, plus reports, exports, and management screens.

### Roles summary

| Screen | Store Keeper | Manager |
|---|---|---|
| Dashboard | Yes | Yes |
| Stock / Receive | Yes | Yes |
| Issue Stock | Yes | Yes |
| Receipts | Yes | Yes |
| Reports + CSV export | No | Yes |
| Manage Items/Locations | No | Yes |
| Django `/admin/` | No | Yes, if staff |

## Data Safety

If you use PostgreSQL on Railway or Render, your data lives in the database service, not in the app container. Redeploying the app does not wipe the database.

## Backups

Manual backup:

```bash
railway run python manage.py dumpdata --natural-foreign --indent 2 > backup.json
```

Restore:

```bash
railway run python manage.py loaddata backup.json
```

## Quick Reference

```bash
# Run locally
python manage.py runserver

# After editing models
python manage.py makemigrations
python manage.py migrate

# Static files
python manage.py collectstatic

# Django shell
python manage.py shell

# Import old SQLite data
python migrate_from_sqlite.py path/to/old.db
```
