"""
Addon mitmproxy pour capturer proprement le trafic Edumoov.

Usage :
    mitmdump -s capture_addon.py --set edumoov_out=../captures/edumoov_capture.jsonl

Ce que fait l'addon :
- Ne garde que les requêtes vers les domaines *.edumoov.com / *.edumoov.net
- Ignore les assets statiques (js/css/fonts/images) pour réduire le bruit
- Redige (masque) les headers sensibles (Authorization, Cookie, tokens)
- Pour les réponses JSON contenant des tableaux (ex. liste de 401 élèves),
  ne garde que les 2 premiers éléments + le nombre total, pour éviter de
  stocker des données personnelles d'élèves en clair dans la capture.
- Écrit une ligne JSON par échange requête/réponse dans le fichier de sortie.
"""
import json
import re
from datetime import datetime, timezone

from mitmproxy import http, ctx

EDUMOOV_HOST_RE = re.compile(r"(^|\.)edumoov\.(com|net)$")
STATIC_EXT_RE = re.compile(r"\.(js|css|woff2?|ttf|otf|png|jpe?g|gif|svg|ico|map)(\?|$)")
REDACT_HEADERS = {"authorization", "cookie", "set-cookie", "x-api-key"}
MAX_STRING_LEN = 2000
MAX_ARRAY_SAMPLE = 2


def redact_headers(headers):
    out = {}
    for k, v in headers.items():
        if k.lower() in REDACT_HEADERS:
            out[k] = "***REDACTED***"
        else:
            out[k] = v
    return out


def sample_json(obj, depth=0):
    """Réduit récursivement les tableaux à MAX_ARRAY_SAMPLE éléments et les
    grandes chaînes, pour ne pas stocker de gros volumes de données perso."""
    if isinstance(obj, list):
        sampled = [sample_json(x, depth + 1) for x in obj[:MAX_ARRAY_SAMPLE]]
        return {"__type": "array_sample", "total_count": len(obj), "sample": sampled}
    if isinstance(obj, dict):
        return {k: sample_json(v, depth + 1) for k, v in obj.items()}
    if isinstance(obj, str) and len(obj) > MAX_STRING_LEN:
        return obj[:MAX_STRING_LEN] + f"...[truncated, total {len(obj)} chars]"
    return obj


def body_to_record(data: bytes, content_type: str):
    if not data:
        return None
    if content_type and "json" in content_type.lower():
        try:
            parsed = json.loads(data.decode("utf-8", errors="replace"))
            return {"kind": "json", "value": sample_json(parsed)}
        except Exception:
            pass
    if content_type and ("text" in content_type.lower() or "xml" in content_type.lower()):
        text = data.decode("utf-8", errors="replace")
        if len(text) > MAX_STRING_LEN:
            text = text[:MAX_STRING_LEN] + f"...[truncated, total {len(text)} chars]"
        return {"kind": "text", "value": text}
    return {"kind": "binary", "length": len(data)}


class EdumoovCapture:
    def __init__(self):
        self.out_path = None
        self.fh = None
        self.count = 0

    def load(self, loader):
        loader.add_option(
            "edumoov_out", str, "edumoov_capture.jsonl",
            "Fichier de sortie JSONL pour la capture Edumoov",
        )

    def configure(self, updated):
        if "edumoov_out" in updated or self.fh is None:
            self.out_path = ctx.options.edumoov_out
            self.fh = open(self.out_path, "a", encoding="utf-8")
            ctx.log.info(f"[edumoov-capture] écriture dans {self.out_path}")

    def response(self, flow: http.HTTPFlow):
        host = flow.request.pretty_host
        if not EDUMOOV_HOST_RE.search(host):
            return
        path = flow.request.path
        if STATIC_EXT_RE.search(path):
            return

        req = flow.request
        resp = flow.response

        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "method": req.method,
            "host": host,
            "path": path,
            "query": dict(req.query),
            "request_headers": redact_headers(dict(req.headers)),
            "request_body": body_to_record(req.content, req.headers.get("content-type", "")),
            "status_code": resp.status_code if resp else None,
            "response_headers": redact_headers(dict(resp.headers)) if resp else None,
            "response_body": body_to_record(resp.content, resp.headers.get("content-type", "")) if resp else None,
        }
        self.fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        self.fh.flush()
        self.count += 1
        ctx.log.info(f"[edumoov-capture] #{self.count} {req.method} {host}{path} -> {record['status_code']}")

    def done(self):
        if self.fh:
            self.fh.close()


addons = [EdumoovCapture()]
