import functools
import importlib
import logging
from types import ModuleType
from typing import Dict

import asyncio
import uvicorn
import yaml

from informa.api import app_fastapi
from informa.lib import app as app_rocketry


logger = logging.getLogger('informa')


@functools.lru_cache()
def init_plugins() -> Dict[str, ModuleType]:
    # Load active plugins from YAML config
    with open('plugins.yaml', encoding='utf8') as f:
        plugins = yaml.safe_load(f)['plugins']

    modules = {}

    for plug in plugins:
        # Convert dashes into underscores for python imports
        module_name = plug.replace('-', '_')

        # Dynamic import to register rocketry tasks
        logger.debug('Initialising plugin %s', plug)
        modules[plug] = importlib.import_module(f'informa.plugins.{module_name}')

    return modules


class Server(uvicorn.Server):
    '''
    Customized uvicorn.Server

    Rocketry needs to shutdown when Uvicorn shuts down.
    '''
    def handle_exit(self, sig: int, frame) -> None:
        app_rocketry.session.shut_down()
        return super().handle_exit(sig, frame)


async def start(host: str, port: int):
    'Run Rocketry and FastAPI'
    server = Server(config=uvicorn.Config(app_fastapi, loop='asyncio', host=host, port=port))

    init_plugins()

    await asyncio.wait([
        asyncio.create_task(server.serve()),
        asyncio.create_task(app_rocketry.serve()),
    ])
