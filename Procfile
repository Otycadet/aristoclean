web: python manage.py migrate --noinput && python manage.py setup_admin && gunicorn aristoclean.wsgi --workers 2 --bind 0.0.0.0:$PORT --log-file -
release: python manage.py collectstatic --noinput
