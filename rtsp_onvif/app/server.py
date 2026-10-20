"""One HTTP server per camera: Protect treats each ONVIF server as a single camera."""

import http.client
import json
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse

from .onvif import SOAP, event_reply, fault, operation, patch


def proxy(port, method, path, body, headers):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
    try:
        conn.request(method, path, body, headers)
        resp = conn.getresponse()
        return resp.status, resp.getheader("Content-Type", "application/octet-stream"), resp.read()
    finally:
        conn.close()


def make_handler(cam):
    port = cam.cfg["onvif_port"]
    model = cam.cfg.get("model") or cam.name

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def reply(self, code, data, ctype):
            data = data.encode() if isinstance(data, str) else data
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def host(self):
            return self.headers.get("Host") or f"127.0.0.1:{port}"

        def go2rtc(self, method, path, body=None):
            headers = {"Host": self.host()}  # go2rtc builds its URLs from it
            if body is not None:
                headers["Content-Type"] = self.headers.get("Content-Type", "application/soap+xml")
            try:
                return proxy(cam.api_port, method, path, body, headers)
            except OSError as e:
                cam.log("go2rtc unreachable:", e)
                return 502, "text/plain", b"go2rtc unreachable"

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/status":
                return self.reply(200, json.dumps(cam.status()), "application/json")
            if path == "/api/frame.jpeg":
                if jpeg := cam.snapshot():
                    return self.reply(200, jpeg, "image/jpeg")
                return self.reply(*self._swap(self.go2rtc("GET", f"/api/frame.jpeg?src={cam.name}")))
            self.reply(404, "not found", "text/plain")

        @staticmethod
        def _swap(resp):
            code, ctype, data = resp
            return code, data, ctype

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode("utf-8", "replace")
            op = operation(body)
            url = urlparse(self.path)
            if url.path == "/onvif/event_service":
                xml = event_reply(op, body, url.query, self.host(), cam.subs, cam.name)
                if xml is None:
                    cam.log("unsupported event operation", op)
                    return self.reply(400, fault(f"{op} not supported"), SOAP)
                return self.reply(200, xml, SOAP)
            code, ctype, data = self.go2rtc("POST", self.path, body.encode())
            xml = patch(op, data.decode("utf-8", "replace"), self.host(), cam.name, model, cam.mac, cam.cfg["audio"])
            self.reply(code, xml, ctype)

    return Handler
