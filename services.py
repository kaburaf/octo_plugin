"""Virtual Octo imports only. Never enables fetching, liking or download on play."""
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import re
import unicodedata

from fastapi import HTTPException
from pydantic import BaseModel, Field, model_validator

from auth_manager import get_client, verify_auth


class Track(BaseModel):
    artist: str = Field(min_length=1, max_length=500)
    title: str = Field(min_length=1, max_length=500)

    @model_validator(mode='after')
    def valid_text(self):
        for value in (self.artist, self.title):
            if not value.strip() or any(ord(c) < 32 for c in value) or any(c in value for c in ('\u2028', '\u2029')):
                raise ValueError('Artist and title must be non-empty single lines')
        return self


class PlaylistRequest(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    description: str = Field(default='', max_length=5000)
    tracks: list[Track | str] = Field(min_length=1, max_length=500)

    @model_validator(mode='after')
    def valid_name(self):
        if not self.name.strip() or any(ord(c)<32 for c in self.name):
            raise ValueError('Playlist name must be a non-empty single line')
        return self


def normalize(value):
    text = unicodedata.normalize('NFKC', value).casefold()
    return re.sub(r'\s+', ' ', text).strip()


def parse_track(raw):
    if isinstance(raw, Track):
        return raw
    parts = re.split(r'\s+[—–-]\s+', raw.strip(), maxsplit=1)
    if len(parts) != 2:
        raise HTTPException(422, "Use 'Artist — Title' or an artist/title object")
    return Track(artist=parts[0].strip(), title=parts[1].strip())


@contextmanager
def playlist_lock():
    path = Path(os.environ.get('OCTO_LOCK_PATH', '/data/octo-playlists.lock'))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def overview(client):
    return client.call('getImports')['imports']


def detail(client, playlist_id):
    if not re.fullmatch(r'file-[A-Za-z0-9_-]+', playlist_id):
        raise HTTPException(422, 'Expected an Octo file import ID')
    return client.call('getImport', id=playlist_id)['import']


def require_virtual_safe(item):
    if item.get('getMissing') or item.get('keepPlaylist'):
        raise HTTPException(409, 'This import has download or local-playlist settings; it will not be changed')


def list_playlists_result(client=None):
    client = client or get_client()
    return [summary(item, client) for item in overview(client).get('lists', [])
            if item.get('streamWithoutDownloading')]


def summary(item, client):
    return {'playlist_id': item['id'], 'streaming_playlist_id': item.get('streamingPlaylistId'),
            'title': item['name'], 'privacy': 'PRIVATE', 'virtual': bool(item.get('streamWithoutDownloading')),
            'download_missing': bool(item.get('getMissing')), 'track_count': item.get('total', 0),
            'dashboard_url': client.public_url+'/admin/'}


def playlist_data(playlist_id, client=None):
    client = client or get_client()
    data = detail(client, playlist_id)
    return {**summary(data['list'], client),
            'tracks': [{'artist': t['artist'], 'title': t['title'], 'state': t['state'],
                        'library_id': t.get('libraryId')} for t in data['tracks']]}


def lookup_tracks(client, tracks):
    # Validate names as names: online matching happens in Octo, not in YouTube Music.
    return [{'requested': parse_track(t).model_dump(), 'status': 'ready',
             'resolution': 'Octo resolves missing tracks when played'} for t in tracks]


def run_search(payload, client=None):
    client = client or get_client()
    verify_auth(client)
    return lookup_tracks(client, payload.tracks)


def same_tracks(actual, expected):
    return [(normalize(t['artist']), normalize(t['title'])) for t in actual] == [
        (normalize(t.artist), normalize(t.title)) for t in expected]


def create_playlist_result(payload, client=None):
    client = client or get_client()
    tracks = [parse_track(t) for t in payload.tracks]
    name = payload.name.strip()
    with playlist_lock():
        existing = [item for item in overview(client).get('lists', [])
                    if item['name'].strip().casefold() == name.casefold()]
        if len(existing) > 1:
            raise HTTPException(409, 'More than one import has that name; choose a new name')
        reused = bool(existing)
        if existing:
            playlist_id = existing[0]['id']
            data = detail(client, playlist_id)
            require_virtual_safe(data['list'])
            if not same_tracks(data['tracks'], tracks):
                raise HTTPException(409, 'A different playlist already has that name; choose a new name')
        else:
            text = '\n'.join(t.artist+' — '+t.title for t in tracks)
            result = client.call('importText', name=name, text=text)['importAction']
            playlist_id = result.get('listId')
            if not playlist_id:
                raise HTTPException(502, 'Octo did not return an import ID; inspect imports before retrying')
            data = detail(client, playlist_id)
            require_virtual_safe(data['list'])
            if not same_tracks(data['tracks'], tracks):
                raise HTTPException(502, {'message': 'Octo parsed tracks differently; the import remains hidden',
                                          'playlist_id': playlist_id})
        if not data['list'].get('streamWithoutDownloading'):
            client.call('importAction', id=playlist_id, action='stream', on='true')
        actual = playlist_data(playlist_id, client)
        if not actual['virtual'] or actual['download_missing'] or not same_tracks(actual['tracks'], tracks):
            raise HTTPException(502, {'message': 'Virtual playlist verification failed', 'playlist_id': playlist_id})
        # Verify the list is really published to music clients, without playing a track.
        virtual_id = actual['streaming_playlist_id']
        if not virtual_id:
            raise HTTPException(502, 'Octo did not publish a streaming playlist ID')
        published = client.call('getPlaylist', id=virtual_id).get('playlist', {})
        if len(published.get('entry', [])) != len(tracks):
            raise HTTPException(502, {'message': 'Published playlist track count differs', 'playlist_id': playlist_id})
        return {'status': 'existing' if reused else 'created', 'playlist': actual,
                'note': 'Private virtual playlist; missing tracks are not queued for download'}


def delete_playlist_result(playlist_id, client=None):
    client = client or get_client()
    with playlist_lock():
        data = detail(client, playlist_id)
        require_virtual_safe(data['list'])
        if not data['list'].get('streamWithoutDownloading'):
            raise HTTPException(409, 'This is not a virtual playlist')
        client.call('importAction', action='remove', id=playlist_id)
        if any(item['id'] == playlist_id for item in overview(client).get('lists', [])):
            raise HTTPException(502, 'Octo did not confirm playlist removal')
