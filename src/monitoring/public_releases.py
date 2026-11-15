"""Bounded public ciphertext discovery without the shared anonymous REST quota."""
import json
import re
from urllib.parse import quote, unquote
from urllib.request import Request
import xml.etree.ElementTree as ET

from scripts.github_api import GitHubAPI, repository_path


class PublicReleases(GitHubAPI):
    def __init__(self, repository):
        super().__init__(token='')
        self.repository = repository
        self.base = repository_path(repository)
        self.public = 'https://github.com/' + repository

    def download(self, url, limit):
        if not url.startswith(self.public + '/releases'):
            raise ValueError('UNAPPROVED_PUBLIC_RELEASE_URL')
        request = Request(url, headers={'User-Agent': 'NEO-LiquidityLab-Recovery'})
        with self._opener.open(request, timeout=90) as response:
            data = response.read(limit + 1)
        if len(data) > limit:
            raise ValueError('PUBLIC_RELEASE_RESPONSE_LIMIT')
        return data

    def request(self, method, path, payload=None, **kwargs):
        if method != 'GET' or payload is not None or kwargs:
            raise ValueError('PUBLIC_RELEASE_READ_ONLY')
        prefix = self.base + '/releases/tags/'
        if path.startswith(prefix):
            tag = unquote(path[len(prefix):])
            if not re.fullmatch(r'(?:ssi-ws-|monitor-web-seed-)[a-z0-9-]{10,150}', tag):
                raise ValueError('PUBLIC_RELEASE_TAG_INVALID')
            url = self.public + '/releases/download/' + quote(tag, safe='') + '/'
            raw = self.download(url + 'manifest.json', 65536)
            outer = json.loads(raw)
            if (outer.get('format') not in ('ssi-encrypted-parquet-v1', 'liquiditylab-encrypted-web-seed-v1')
                    or outer.get('context') != self.repository + ':' + tag):
                raise ValueError('PUBLIC_RELEASE_MANIFEST_CONTEXT')
            parts = outer.get('parts')
            if not isinstance(parts, list) or not 1 <= len(parts) <= 64:
                raise ValueError('PUBLIC_RELEASE_PART_LIMIT')
            assets = [dict(name='manifest.json', size=len(raw), browser_download_url=url + 'manifest.json')]
            for index, part in enumerate(parts):
                if (not isinstance(part, dict) or part.get('name') != f'part-{index:04d}.enc'
                        or type(part.get('bytes')) is not int or not 28 <= part['bytes'] <= 16 * 1024 * 1024 + 4096
                        or not re.fullmatch('[a-f0-9]{64}', str(part.get('sha256', '')))):
                    raise ValueError('PUBLIC_RELEASE_PART_INVALID')
                assets.append(dict(name=part['name'], size=part['bytes'], browser_download_url=url + part['name']))
            return dict(assets=assets)
        if path == self.base + '/releases?per_page=100':
            raw = self.download(self.public + '/releases.atom', 1024 * 1024)
            if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
                raise ValueError('PUBLIC_RELEASE_FEED_INVALID')
            root = ET.fromstring(raw)
            namespace = '{http://www.w3.org/2005/Atom}'
            if root.tag != namespace + 'feed':
                raise ValueError('PUBLIC_RELEASE_FEED_INVALID')
            prefix = self.public + '/releases/tag/'
            releases = []
            for entry in root.findall(namespace + 'entry')[:100]:
                for link in entry.findall(namespace + 'link'):
                    href = link.get('href', '')
                    if link.get('rel', 'alternate') != 'alternate' or not href.startswith(prefix):
                        continue
                    tag = unquote(href[len(prefix):])
                    if re.fullmatch(r'ssi-ws-[a-z0-9-]{10,150}', tag):
                        releases.append(dict(tag_name=tag))
                        break
            return releases
        raise ValueError('PUBLIC_RELEASE_ROUTE_INVALID')
