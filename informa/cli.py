import asyncio
import inspect
import logging
import os

import click

from informa.exceptions import PluginNotFound
from informa.lib import load_run_persist
from informa.main import init_plugins
from informa.main import start as start_app

logger = logging.getLogger('informa')
sh = logging.StreamHandler()
logger.addHandler(sh)
sh.setFormatter(logging.Formatter('%(asctime)s - %(message)s'))
logger.setLevel(logging.INFO)


@click.group()
@click.option('--debug', is_flag=True, default=False)
def cli(debug):
    # Set DEBUG logging based on ENV or --debug CLI flag
    if debug or os.environ.get('DEBUG'):
        logger.setLevel(logging.DEBUG)


@cli.group('plugin')
def plugin_():
    "Invoke a plugin's CLI"


# Load all the plugins at import time
PLUGINS = init_plugins()

# Iterate plugins, looking for Click Groups to include in the CLI
for plugin_module in PLUGINS.values():
    for _, member in inspect.getmembers(plugin_module):
        if isinstance(member, click.core.Group):
            plugin_.add_command(member)


@cli.command
@click.option('--host', help='Bind FastAPI server to hostname', default='127.0.0.1', type=str)
@click.option('--port', help='Bind FastAPI server to port', default=3000, type=int)
def start(host: str, port: int):
    "Start the async workers for each plugin, and the API server"
    logger.info('Starting FastAPI and Rocketry workers')
    asyncio.run(start_app(host, port))


@cli.command
def list_plugins():
    """
    List configured plugins
    """
    for plug in PLUGINS:
        print(plug)


def get_plugin(command: str):
    "Attempt to convert a string into a valid plugin name"
    # Cleanup the passed string
    command = command.lower().replace('_', '-')

    if command in PLUGINS:
        # Found plugin by name
        return PLUGINS[command]

    matches = [p for p in PLUGINS if p.startswith(command)]
    if len(matches) == 1:
        # Only one plugin exists with passed prefix, so assume a match
        return PLUGINS[matches[0]]

    raise PluginNotFound


@cli.command
@click.argument('plugin')
def call(plugin: str):
    """
    Run a single plugin synchronously

    PLUGIN - Name of the plugin to run synchronously on the CLI
    """
    plug = get_plugin(plugin)
    load_run_persist(plug.logger, plug.State, plug.PLUGIN_NAME, plug.main)
