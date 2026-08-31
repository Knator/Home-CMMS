import os
from dotenv import load_dotenv
from app import create_app

app = create_app()

if __name__ == '__main__':

    # Get .env variables for startup
    debug = os.getenv('FLASK_DEBUG', '') in ('1', 'true', 'True', 'yes')
    host = os.getenv('HOST', '127.0.0.1')
    port = int(os.getenv('PORT', '5000'))


    # Debug is opt-in: `FLASK_DEBUG=1 python run.py`. Hardcoding it on would
    # expose the Werkzeug console to anyone who can reach the port.
    app.run(debug=debug, host=host, port=port)
