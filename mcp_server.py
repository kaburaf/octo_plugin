"""MCP tools for private virtual Octo playlists, never download operations."""
from typing import Literal
from fastapi import HTTPException
from mcp.server.fastmcp import FastMCP
from pydantic import ValidationError
from auth_manager import get_client, verify_auth
from services import (Track, PlaylistRequest, create_playlist_result, delete_playlist_result,
                      list_playlists_result, playlist_data, run_search)

# Keep the existing server identity for installed clients and the existing tunnel.
mcp = FastMCP('ytmusic-playlists', host='0.0.0.0', stateless_http=True, json_response=True)


def error_result(exc):
    if isinstance(exc, HTTPException):
        return {'status': 'error', 'code': exc.status_code, 'detail': exc.detail}
    if isinstance(exc, ValidationError):
        return {'status': 'error', 'code': 422, 'detail': exc.errors(include_input=False, include_context=False)}
    return {'status': 'error', 'code': 502, 'detail': f'Octo request failed ({type(exc).__name__})'}


@mcp.tool()
def check_connection() -> dict:
    """Check authentication with Octo/Navidrome. This server creates virtual playlists only."""
    try:
        client = get_client()
        verify_auth(client)
        return {'status': 'ok', 'authenticated': True, 'backend': 'octo', 'virtual_only': True,
                'playlists': list_playlists_result(client)}
    except Exception as exc:
        return error_result(exc)


@mcp.tool()
def preview_tracks(tracks: list[str]) -> dict:
    """Validate Artist — Title entries for Octo without changing lists or downloading. This does not confirm a recording match; Octo resolves online tracks when played."""
    try:
        results = run_search(PlaylistRequest(name='Preview', tracks=tracks))
        return {'status': 'ok', 'tracks': results, 'resolution': 'on_play', 'downloads': False}
    except Exception as exc:
        return error_result(exc)


@mcp.tool()
def create_playlist(name: str, tracks: list[Track], description: str = '',
                    privacy_status: Literal['PUBLIC', 'PRIVATE', 'UNLISTED'] = 'PRIVATE') -> dict:
    """Create a PRIVATE virtual Octo playlist of artist/title entries, preserving order and duplicates. Missing tracks stream online, never queued for downloading. Reuse an identical list, refuse overwriting a different one. privacy_status is legacy: all Octo imports are private. Description is accepted for compatibility but Octo imports do not store descriptions."""
    try:
        return create_playlist_result(PlaylistRequest(name=name, tracks=tracks, description=description))
    except Exception as exc:
        return error_result(exc)


@mcp.tool()
def list_playlists() -> dict:
    """List the current user's virtual Octo playlists."""
    try:
        return {'status': 'ok', 'playlists': list_playlists_result()}
    except Exception as exc:
        return error_result(exc)


@mcp.tool()
def read_playlist(playlist_id: str) -> dict:
    """Read an Octo import by its file-... ID, including ordered tracks and virtual/download settings."""
    try:
        return {'status': 'ok', 'playlist': playlist_data(playlist_id)}
    except Exception as exc:
        return error_result(exc)


@mcp.tool()
def delete_playlist(playlist_id: str) -> dict:
    """Remove the explicitly requested virtual Octo import. Does not delete any audio files. Refuses imports with fetching or local-playlist settings enabled."""
    try:
        delete_playlist_result(playlist_id)
        return {'status': 'deleted', 'playlist_id': playlist_id}
    except Exception as exc:
        return error_result(exc)


if __name__ == '__main__':
    mcp.run(transport='stdio')
