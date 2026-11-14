web: python -m app.seed --if-empty && gunicorn -w 2 -b 0.0.0.0:$PORT --timeout 120 "app:create_app()"
