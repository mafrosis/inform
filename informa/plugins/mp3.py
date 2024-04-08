import logging
import os
import pathlib

from fastapi import APIRouter

from informa.lib import PluginAdapter
from informa.lib import fastapi as app_fastapi

logger = PluginAdapter(logging.getLogger('informa'))


MP3HOME = os.environ.get('MP3HOME', '/home/mafro/music/')


router = APIRouter(prefix='/mp3')


@router.get('/info/{identifier}')
def get_mp3_album_info(identifier: str):
    def count_mp3s(dirpath: str):
        return sum(1 for f in os.listdir(dirpath) if f.endswith('mp3'))

    def find_album_path(albumartist: str):
        for path in pathlib.Path(MP3HOME).glob(f'**/{albumartist}'):
            if path.is_dir():
                return str(path)

    logger.debug('Lookup: %s', identifier)

    path = find_album_path(identifier)

    return {
        'identifier': identifier,
        'path': path,
        'track_count': count_mp3s(path),
    }


app_fastapi.include_router(router)
