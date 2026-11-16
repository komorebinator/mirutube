"""A small Chrome DevTools Protocol client on the standard library alone, for
tests#browser: a WebSocket of its own (RFC 6455, the client's half), pages in
browser contexts of their own — each a device with its own storage — and
what the tests do with a page: evaluate script, wait for it to be true, click
as a user does, and choose files."""

import base64
import http.client
import json
import os
import queue
import socket
import struct
import threading
import time
from urllib.parse import urlsplit


class WebSocket:
    """One WebSocket connection, text messages only."""

    def __init__(self, url, timeout=30):
        u = urlsplit(url)
        self.sock = socket.create_connection((u.hostname, u.port), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        path = u.path + ("?" + u.query if u.query else "")
        self.sock.sendall(("GET %s HTTP/1.1\r\nHost: %s:%d\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                           "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n"
                           % (path, u.hostname, u.port, key)).encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(1)
            if not chunk:
                raise ConnectionError("the browser closed the connection")
            head += chunk
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise ConnectionError("no WebSocket: %r" % head[:200])
        self.sock.settimeout(None)
        self._send_lock = threading.Lock()

    def _read(self, n):
        data = b""
        while len(data) < n:
            chunk = self.sock.recv(n - len(data))
            if not chunk:
                raise ConnectionError("closed")
            data += chunk
        return data

    def send(self, text, opcode=0x1):
        payload = text.encode() if isinstance(text, str) else text
        n = len(payload)
        header = bytes([0x80 | opcode])
        if n < 126:
            header += bytes([0x80 | n])
        elif n < 1 << 16:
            header += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            header += bytes([0x80 | 127]) + struct.pack(">Q", n)
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        with self._send_lock:
            self.sock.sendall(header + mask + masked)

    def recv(self):
        """The next text message; answers pings, ends on close."""
        message = b""
        while True:
            b1, b2 = self._read(2)
            opcode, n = b1 & 0x0F, b2 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            payload = self._read(n)
            if opcode == 0x8:
                raise ConnectionError("closed")
            if opcode == 0x9:
                self.send(payload, 0xA)
                continue
            if opcode in (0x0, 0x1, 0x2):
                message += payload
                if b1 & 0x80:
                    return message.decode("utf-8", "replace")

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


class Session:
    """A DevTools connection: commands answered by id, events queued."""

    def __init__(self, url):
        self.ws = WebSocket(url)
        self._id = 0
        self._lock = threading.Lock()
        self._waiting = {}
        self.events = queue.Queue()
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self):
        try:
            while True:
                msg = json.loads(self.ws.recv())
                if "id" in msg:
                    q = self._waiting.pop(msg["id"], None)
                    if q is not None:
                        q.put(msg)
                else:
                    self.events.put(msg)
        except (OSError, ConnectionError, ValueError):
            for q in list(self._waiting.values()):
                q.put({"error": {"message": "the browser went away"}})

    def send(self, method, timeout=30, **params):
        with self._lock:
            self._id += 1
            i = self._id
            q = queue.Queue()
            self._waiting[i] = q
        self.ws.send(json.dumps({"id": i, "method": method, "params": params}))
        try:
            msg = q.get(timeout=timeout)
        except queue.Empty:
            raise TimeoutError("%s got no answer" % method) from None
        if "error" in msg:
            raise RuntimeError("%s: %s" % (method, msg["error"].get("message")))
        return msg.get("result", {})

    def close(self):
        self.ws.close()


class Browser:
    """A Chromium with its DevTools on `port`."""

    def __init__(self, port, timeout=60):
        self.port = port
        deadline = time.monotonic() + timeout
        while True:
            try:
                version = self._json("/json/version")
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.3)
        self.session = Session(version["webSocketDebuggerUrl"])

    def _json(self, path):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request("GET", path)
            return json.loads(conn.getresponse().read())
        finally:
            conn.close()

    def new_device(self, width=1280, height=720):
        """A page in a browser context of its own: storage of its own, so it
        is a device of its own, and never touched until a test touches it."""
        context = self.session.send("Target.createBrowserContext")["browserContextId"]
        target = self.session.send("Target.createTarget", url="about:blank", browserContextId=context,
                                   width=width, height=height)["targetId"]
        page = Page(Session("ws://127.0.0.1:%d/devtools/page/%s" % (self.port, target)))
        page.send("Page.enable")
        page.send("Runtime.enable")
        page.send("Emulation.setDeviceMetricsOverride", width=width, height=height, deviceScaleFactor=1,
                  mobile=False)
        return page

    def close(self):
        self.session.close()


class Page:
    def __init__(self, session):
        self.session = session

    def send(self, method, **params):
        return self.session.send(method, **params)

    def goto(self, url):
        self.send("Page.navigate", url=url)
        self.wait("document.readyState === 'complete' && !!document.getElementById('now-playing')")

    def eval(self, expression):
        """The value of a script, awaited when it is a promise."""
        r = self.send("Runtime.evaluate", expression=expression, returnByValue=True, awaitPromise=True)
        if "exceptionDetails" in r:
            raise RuntimeError("script failed: %s" % json.dumps(r["exceptionDetails"])[:800])
        return r["result"].get("value")

    def wait(self, expression, timeout=20, message=None):
        """Waits until a script is true, and returns its value."""
        deadline = time.monotonic() + timeout
        while True:
            value = self.eval(expression)
            if value:
                return value
            if time.monotonic() > deadline:
                raise AssertionError(message or "never true: %s" % expression)
            time.sleep(0.2)

    def center(self, selector):
        box = self.eval("(() => { const e = document.querySelector(%s); if (!e) return null;"
                        " const r = e.getBoundingClientRect(); return [r.x + r.width / 2, r.y + r.height / 2]; })()"
                        % json.dumps(selector))
        if not box:
            raise AssertionError("nothing to click: " + selector)
        return box

    def click(self, selector):
        """A click as a user's: it counts as a touch of the page, which lets it
        start sound and go full screen."""
        x, y = self.center(selector)
        self.mouse(x, y)
        for kind in ("mousePressed", "mouseReleased"):
            self.send("Input.dispatchMouseEvent", type=kind, x=x, y=y, button="left", clickCount=1)

    def mouse(self, x, y):
        self.send("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y)

    def set_files(self, selector, paths):
        """Chooses files in a file input, as its picker does."""
        root = self.send("DOM.getDocument")["root"]["nodeId"]
        node = self.send("DOM.querySelector", nodeId=root, selector=selector)["nodeId"]
        self.send("DOM.setFileInputFiles", files=list(paths), nodeId=node)

    def close(self):
        self.session.close()
