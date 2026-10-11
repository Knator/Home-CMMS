"""A small SMTP server for the mail tests, so sending is exercised over a real
socket — greeting, EHLO, STARTTLS, AUTH, MAIL, RCPT, DATA — rather than against
a mock that agrees with whatever the code assumed.

Speaks just enough of RFC 5321 for smtplib: plain, STARTTLS, or TLS from the
first byte (what port 465 means), with AUTH PLAIN and LOGIN checked against one
username and password. Every accepted message is kept on `.messages`.
"""
import base64
import socketserver
import ssl
import threading
from email import message_from_bytes, policy


class _Handler(socketserver.StreamRequestHandler):
    timeout = 10

    def setup(self):
        server = self.server
        if server.mode == 'ssl':
            self.request = server.tls.wrap_socket(self.request, server_side=True)
        super().setup()

    def send(self, line):
        self.wfile.write(line.encode() + b'\r\n')
        self.wfile.flush()

    def readline(self):
        line = self.rfile.readline()
        if not line:
            raise ConnectionError('client went away')
        return line.decode(errors='replace').rstrip('\r\n')

    def handle(self):
        server = self.server
        secure = server.mode == 'ssl'
        authed = False
        sender, recipients = None, []
        self.send('220 test.local ESMTP ready')
        while True:
            try:
                line = self.readline()
            except (ConnectionError, OSError):
                return
            verb, _, arg = line.partition(' ')
            verb = verb.upper()

            if verb in ('EHLO', 'HELO'):
                extensions = ['test.local']
                if server.mode == 'starttls' and not secure and server.offer_starttls:
                    extensions.append('STARTTLS')
                if server.username and (secure or server.mode == 'plain'):
                    extensions.append('AUTH PLAIN LOGIN')
                for extension in extensions[:-1]:
                    self.send(f'250-{extension}')
                self.send(f'250 {extensions[-1]}')
            elif verb == 'STARTTLS':
                self.send('220 go ahead')
                self.request = server.tls.wrap_socket(self.request, server_side=True)
                self.rfile = self.request.makefile('rb')
                self.wfile = self.request.makefile('wb')
                secure = True
            elif verb == 'AUTH':
                mechanism, _, initial = arg.partition(' ')
                if mechanism.upper() == 'PLAIN':
                    if not initial:
                        self.send('334 ')
                        initial = self.readline()
                    parts = base64.b64decode(initial).split(b'\0')
                    user, password = parts[1].decode(), parts[2].decode()
                else:   # LOGIN
                    self.send('334 VXNlcm5hbWU6')
                    user = base64.b64decode(self.readline()).decode()
                    self.send('334 UGFzc3dvcmQ6')
                    password = base64.b64decode(self.readline()).decode()
                server.logins.append((user, password))
                if (user, password) == (server.username, server.password):
                    authed = True
                    self.send('235 authenticated')
                else:
                    self.send('535 5.7.8 authentication failed')
            elif verb == 'MAIL':
                if server.username and not authed:
                    self.send('530 5.7.0 authentication required')
                    continue
                sender = arg.partition(':')[2].strip().strip('<>')
                if server.refuse_sender and sender == server.refuse_sender:
                    self.send('553 5.7.1 sender not allowed')
                    sender = None
                    continue
                self.send('250 ok')
            elif verb == 'RCPT':
                recipients.append(arg.partition(':')[2].strip().strip('<>'))
                self.send('250 ok')
            elif verb == 'DATA':
                self.send('354 end with .')
                lines = []
                while True:
                    data = self.rfile.readline()
                    if data in (b'.\r\n', b'.\n', b''):
                        break
                    lines.append(data[1:] if data.startswith(b'..') else data)
                server.messages.append({
                    'from': sender, 'to': list(recipients), 'secure': secure,
                    'message': message_from_bytes(b''.join(lines), policy=policy.default),
                })
                recipients = []
                self.send('250 queued')
            elif verb in ('RSET', 'NOOP'):
                self.send('250 ok')
            elif verb == 'QUIT':
                self.send('221 bye')
                return
            else:
                self.send('502 not implemented')


class SMTPTestServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, mode='plain', username=None, password=None, certfile=None,
                 keyfile=None, offer_starttls=True, refuse_sender=None):
        super().__init__(('127.0.0.1', 0), _Handler)
        self.mode = mode
        self.username, self.password = username, password
        self.offer_starttls = offer_starttls
        self.refuse_sender = refuse_sender
        self.messages, self.logins = [], []
        self.tls = None
        if certfile:
            self.tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            self.tls.load_cert_chain(certfile, keyfile)
        self._thread = threading.Thread(target=self.serve_forever, daemon=True)

    @property
    def port(self):
        return self.server_address[1]

    def handle_error(self, request, client_address):
        """Quiet: the tests provoke failed handshakes and dropped connections on
        purpose, and the client's side of each is what they check."""

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self.shutdown()
        self.server_close()
