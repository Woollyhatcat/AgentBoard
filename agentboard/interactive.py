"""Build isolated interactive documents with a small, versioned host protocol."""

import base64
from functools import lru_cache
import json
from pathlib import Path
import re

WEB = Path(__file__).parent / "web"
FRAME_CSP = ("sandbox allow-scripts; default-src 'none'; script-src 'unsafe-inline'; "
             "style-src 'unsafe-inline'; img-src data: blob:; font-src data:; "
             "connect-src 'none'; frame-src 'none'; worker-src 'none'; object-src 'none'; "
             "base-uri 'none'; form-action 'none'; frame-ancestors 'self'")


@lru_cache(maxsize=1)
def _libraries():
    css = (WEB / "vendor/katex/dist/katex.min.css").read_text(encoding="utf-8")
    def font(match):
        path = WEB / "vendor/katex/dist" / match[1]
        if path.suffix == ".woff2" and path.is_file():
            return "url(data:font/woff2;base64,"+base64.b64encode(path.read_bytes()).decode()+")"
        return "url(data:application/octet-stream;base64,)"
    css = re.sub(r"url\((fonts/[^)]+)\)",font,css)
    js = (WEB / "vendor/katex/dist/katex.min.js").read_text(encoding="utf-8")
    return css, js.replace("</script", "<\\/script")


def frame_document(card, key):
    if card["type"] != "interactive":
        raise ValueError("Expected interactive card")
    css, library = _libraries()
    sdk = (WEB / "interactive-sdk.js").read_text(encoding="utf-8")
    config = json.dumps({"key":key},ensure_ascii=True).replace("<","\\u003c")
    sdk = sdk.replace("__BOARD_FRAME_CONFIG__", config)
    return ("<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
        "<style>:root{font:14px/1.6 system-ui,'Microsoft YaHei',sans-serif;color-scheme:light dark}"
        "*{box-sizing:border-box}body{margin:0;padding:14px;background:var(--board-bg,#fff);color:var(--board-ink,#233538)}"
        "input,button,select,textarea{font:inherit}svg{max-width:100%}"+css+"</style>"
        "<script>"+library+"</script><script>"+sdk+"</script>"+card["props"]["source"])
