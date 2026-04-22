web: python manage.py migrate --noinput && gunicorn aristoclean.wsgi --workers 2 --bind 0.0.0.0:$PORT --log-file -
release: python manage.py collectstatic --noinput
