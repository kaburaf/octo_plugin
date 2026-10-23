"""Octo Subsonic client. Credentials never enter URLs or error messages."""
import json
import os
from pathlib import Path
from urllib import error, parse, request

from fastapi import HTTPException

AUTH_FILE = Path(os.environ.get('OCTO_AUTH_PATH', '/data/octo-auth.json'))


class OctoClient:
    def __init__(self, config=None):
        if config is None:
            try:
                config = json.loads(AUTH_FILE.read_text())
            except (OSError, ValueError):
                raise HTTPException(503, 'Octo credentials are not configured') from None
        if not all(config.get(k) for k in ('url', 'username', 'token', 'salt')):
            raise HTTPException(503, 'Octo credentials are incomplete')
        self.config = config
        self.base_url = os.environ.get('OCTO_URL', config['url']).rstrip('/')
        if parse.urlsplit(self.base_url).scheme not in ('http', 'https'):
            raise HTTPException(503, 'Invalid Octo URL')
        self.public_url = os.environ.get('OCTO_PUBLIC_URL', 'http://octo.arr.home').rstrip('/')
        self.opener = request.build_opener(request.ProxyHandler({}))

    def call(self, endpoint, **values):
        # This integration has no route to acquisition, stars or physical playlists.
        allowed = {'ping', 'getOpenSubsonicExtensions', 'getImports', 'getImport',
                   'getPlaylists', 'getPlaylist', 'importText', 'importAction', 'search3'}
        if endpoint not in allowed:
            raise HTTPException(400, 'Unsupported Octo operation')
        if endpoint == 'importAction' and values.get('action') not in ('stream', 'remove'):
            raise HTTPException(400, 'Download operations are not supported')
        params = dict(u=self.config['username'], t=self.config['token'], s=self.config['salt'],
                      v='1.16.1', c='octo-playlists', f='json', **values)
        req = request.Request(self.base_url+'/rest/'+endpoint,
                              data=parse.urlencode(params).encode(),
                              headers={'Content-Type': 'application/x-www-form-urlencoded'})
        try:
            with self.opener.open(req, timeout=90) as response:
                answer = json.load(response)['subsonic-response']
        except (OSError, ValueError, KeyError, error.URLError):
            raise HTTPException(502, 'Octo request failed; inspect playlists before retrying a write') from None
        if answer.get('status') != 'ok':
            code = answer.get('error', {}).get('code')
            raise HTTPException(401 if code == 40 else 502, f'Octo rejected the request (code {code})')
        if endpoint in ('importText', 'importAction') and not answer.get('importAction', {}).get('ok'):
            raise HTTPException(502, 'Octo did not confirm the playlist operation')
        return answer


def get_client():
    return OctoClient()


def verify_auth(client):
    client.call('ping')
