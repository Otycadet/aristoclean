web: python manage.py collectstatic --noinput && python manage.py migrate --noinput && gunicorn aristoclean.wsgi --workers 2 --bind 0.0.0.0:$PORT --log-file -
