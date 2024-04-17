import inspect
import logging
from dataclasses import dataclass
from types import ModuleType

import arrow
import click

from informa.lib import ConfigBase, PluginAdapter, StateBase, load_state


@dataclass
class Plugin:
    "Click context DTO to hold plugin global attributes"

    name: str
    logger: logging.Logger
    state_cls: type
    config_cls: type | None


click_pass_plugin = click.make_pass_decorator(Plugin)


def setup_plugin_cli(plugin: ModuleType):
    "Setup plugin CLI context for this plugin, and add common commands"

    def get_class_attr(type_):
        "Return the class type defined in the plugin, which inherits from `type_`"
        clss = [v for _, v in inspect.getmembers(plugin, inspect.isclass) if issubclass(v, type_) and v is not type_]
        return next(iter(clss), None)

    def get_plugin_logger():
        "Return the class attribute which is an instance of lib.PluginAdapter"
        return next(iter([v for _, v in inspect.getmembers(plugin) if isinstance(v, PluginAdapter)]), None)

    # If the plugin has a CLI, setup the context and add common commands
    if hasattr(plugin, 'cli'):
        plugin_config_cls = get_class_attr(ConfigBase)
        plugin_state_cls = get_class_attr(StateBase)
        plugin_logger = get_plugin_logger()

        plugin.cli.context_settings = {
            'obj': Plugin(plugin.__name__, plugin_logger, plugin_state_cls, plugin_config_cls)
        }
        plugin.cli.add_command(plugin_last_run)


@click.command('last-run')
@click_pass_plugin
def plugin_last_run(plugin: Plugin):
    "When was the last run?"
    state = load_state(plugin.logger, plugin.state_cls, plugin.name)
    last_run = arrow.get(state.last_run).humanize() if state.last_run else 'Never'
    print(f'Last run: {last_run}')
