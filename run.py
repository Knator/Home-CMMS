import os
import sys

from app import create_app

app = create_app()

LOOPBACK = {'127.0.0.1', 'localhost', '::1'}


def main():
    debug = os.getenv('FLASK_DEBUG', '') in ('1', 'true', 'True', 'yes')
    host = os.getenv('HOST', '127.0.0.1')
    port = int(os.getenv('PORT', '5000'))

    # The Werkzeug debugger executes arbitrary Python from the browser. On
    # loopback that is a developer convenience; on any other interface it is
    # remote code execution for anyone who can reach the port, so refuse rather
    # than start something that looks fine and is not.
    if debug and host not in LOOPBACK:
        sys.exit(
            f'Refusing to start: FLASK_DEBUG is on and HOST is {host!r}.\n'
            'The debugger runs arbitrary code for anyone who can reach the port.\n'
            'Use HOST=127.0.0.1 to debug, or unset FLASK_DEBUG to serve on a network.'
        )

    if host not in LOOPBACK:
        print(f'Serving on {host}:{port} with the development server. '
              'Use a production server (gunicorn -w 1 "run:app") for anything '
              'long-running.', file=sys.stderr)

    app.run(debug=debug, host=host, port=port)


if __name__ == '__main__':
    main()
