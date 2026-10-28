"""Read public board client assets to inspect its documented display scales."""
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.request import urlopen
from urllib.error import HTTPError, URLError


class Scripts(HTMLParser):
    def __init__(self):
        super().__init__()
        self.sources = []

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            value = dict(attrs).get("src")
            if value:
                self.sources.append(value)


def main():
    origin = "https://kbbuddywts.kbsec.com.vn/"
    target = Path("data/raw/unit_verification")
    target.mkdir(parents=True, exist_ok=True)
    try:
        html = urlopen(origin, timeout=20).read()
    except (HTTPError,URLError) as error:
        result = {"origin":origin,"error_category":type(error).__name__,"http_status":getattr(error,"code",None),
                  "units_verified":False,"note":"Không thử né giới hạn hoặc đổi danh tính truy cập."}
        (target/"assets.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
        print(json.dumps(result,indent=2))
        return
    (target/"kbs_page.html").write_bytes(html)
    parser = Scripts()
    parser.feed(html.decode("utf-8"))
    sources = [urljoin(origin,value) for value in parser.sources]
    result = {"checked_at_utc":datetime.now(timezone.utc).isoformat(),"origin":origin,"scripts":sources,"assets":[]}
    for url in sources:
        # Inspect first-party application bundles only; no account/trading endpoints.
        name = Path(urlparse(url).path).name
        if urlparse(url).netloc == urlparse(origin).netloc and name.startswith("main") and name.endswith(".js"):
            data = urlopen(url, timeout=30).read()
            (target/name).write_bytes(data)
            result["assets"].append({"url":url,"path":str(target/name),"bytes":len(data),"sha256":hashlib.sha256(data).hexdigest()})
    (target/"assets.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
    print(json.dumps(result,indent=2))


if __name__ == "__main__":
    main()
