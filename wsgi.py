"""WSGI entry point (gunicorn wsgi:app, or point PythonAnywhere's WSGI file here)."""
from app import create_app

app = create_app()

if __name__ == "__main__":
    app.run(debug=True)
