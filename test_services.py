import copy
import os
import tempfile
import unittest
from unittest.mock import patch
from fastapi import HTTPException
from services import PlaylistRequest, create_playlist_result, delete_playlist_result
from auth_manager import OctoClient


class FakeOcto:
    public_url = 'http://octo.test'
    def __init__(self):
        self.items = {}
        self.calls = []
    def call(self, endpoint, **p):
        self.calls.append((endpoint, p))
        if endpoint == 'getImports':
            return {'imports': {'lists': [copy.deepcopy(d['list']) for d in self.items.values()]}}
        if endpoint == 'importText':
            tracks = []
            for line in p['text'].splitlines():
                artist, title = line.split(' — ', 1)
                tracks.append({'artist': artist, 'title': title, 'state': 'missing'})
            self.items['file-test'] = {'list': {'id': 'file-test', 'name': p['name'],
                'total': len(tracks), 'getMissing': False, 'keepPlaylist': False}, 'tracks': tracks}
            return {'importAction': {'ok': True, 'listId': 'file-test'}}
        if endpoint == 'getImport':
            return {'import': copy.deepcopy(self.items[p['id']])}
        if endpoint == 'importAction':
            if p['action'] == 'stream':
                self.items[p['id']]['list'].update(streamWithoutDownloading=True, streamingPlaylistId='oi-test')
            elif p['action'] == 'remove':
                del self.items[p['id']]
            else:
                raise AssertionError('Download action called')
            return {'importAction': {'ok': True}}
        if endpoint == 'getPlaylist':
            return {'playlist': {'entry': self.items['file-test']['tracks']}}
        raise AssertionError(endpoint)


class VirtualPlaylistTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'OCTO_LOCK_PATH': self.temp.name+'/lock'})
        self.env.start()
        self.client = FakeOcto()
        self.payload = PlaylistRequest(name='Test', tracks=['Artist — First', 'Artist — Second', 'Artist — First'])
    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()
    def test_virtual_creation_preserves_order_and_duplicates_without_fetch(self):
        actual = create_playlist_result(self.payload, self.client)['playlist']
        self.assertTrue(actual['virtual'])
        self.assertFalse(actual['download_missing'])
        self.assertEqual([t['title'] for t in actual['tracks']], ['First', 'Second', 'First'])
        self.assertEqual([p['action'] for e,p in self.client.calls if e=='importAction'], ['stream'])
    def test_same_request_reuses_list(self):
        create_playlist_result(self.payload, self.client)
        self.assertEqual(create_playlist_result(self.payload,self.client)['status'], 'existing')
        self.assertEqual(sum(e=='importText' for e,p in self.client.calls),1)
    def test_conflicting_name_does_not_overwrite(self):
        create_playlist_result(self.payload, self.client)
        with self.assertRaises(HTTPException) as error:
            create_playlist_result(PlaylistRequest(name='Test',tracks=['Artist — Other']),self.client)
        self.assertEqual(error.exception.status_code,409)
        self.assertEqual(sum(e=='importText' for e,p in self.client.calls),1)
    def test_does_not_touch_import_with_acquisition_enabled(self):
        create_playlist_result(self.payload,self.client)
        self.client.items['file-test']['list']['getMissing']=True
        with self.assertRaises(HTTPException):
            delete_playlist_result('file-test',self.client)
        self.assertIn('file-test',self.client.items)
    def test_removes_virtual_list_only(self):
        create_playlist_result(self.payload,self.client)
        delete_playlist_result('file-test',self.client)
        self.assertFalse(self.client.items)
    def test_client_cannot_call_download_api(self):
        c=OctoClient({'url':'http://octo.test','username':'u','token':'t','salt':'s'})
        for endpoint,params in [('star',{}),('createPlaylist',{}),('importAction',{'action':'fetch'})]:
            with self.assertRaises(HTTPException):c.call(endpoint,**params)

if __name__ == '__main__':unittest.main()
