"""Octo virtual playlist REST and Streamable HTTP MCP service."""
from contextlib import asynccontextmanager
from fastapi import FastAPI, Response
from auth_manager import get_client, verify_auth
from services import (PlaylistRequest, create_playlist_result, delete_playlist_result,
                      list_playlists_result, playlist_data, run_search)
from mcp_server import mcp

mcp_http_app = mcp.streamable_http_app()

@asynccontextmanager
async def lifespan(_app):
    async with mcp.session_manager.run():
        yield

app = FastAPI(title='Octo Virtual Playlists', version='3.0.0', lifespan=lifespan)

@app.get('/health')
def health():
    return {'status': 'ok', 'backend': 'octo', 'virtual_only': True}

@app.get('/auth/check')
def check_auth():
    verify_auth(get_client())
    return {'authenticated': True, 'backend': 'octo'}

@app.post('/playlists/preview')
def preview(payload: PlaylistRequest):
    return {'tracks': run_search(payload), 'resolution': 'on_play', 'downloads': False}

@app.post('/playlists')
@app.post('/api/playlists')
def create_playlist(payload: PlaylistRequest):
    return create_playlist_result(payload)

@app.get('/api/playlists')
def list_playlists():
    return list_playlists_result()

@app.get('/api/playlists/{playlist_id}')
def read_playlist(playlist_id: str):
    return playlist_data(playlist_id)

@app.delete('/api/playlists/{playlist_id}', status_code=204)
def delete_playlist(playlist_id: str):
    delete_playlist_result(playlist_id)
    return Response(status_code=204)

app.mount('/', mcp_http_app)
