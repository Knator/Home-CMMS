import os

from app import create_app

app = create_app()

if __name__ == '__main__':
    # Debug is opt-in: `FLASK_DEBUG=1 python run.py`. Hardcoding it on would
    # expose the Werkzeug console to anyone who can reach the port.
    app.run(debug=os.environ.get('FLASK_DEBUG') in ('1', 'true', 'True'))
