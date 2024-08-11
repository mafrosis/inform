import inspect
import logging
from collections.abc import Callable
from dataclasses import dataclass
from types import ModuleType

import arrow
import click
import yaml
from dataclasses_json import DataClassJsonMixin
from marshmallow.exceptions import ValidationError
from paho.mqtt import client as mqtt
from paho.mqtt.enums import CallbackAPIVersion

from informa.exceptions import AppError
from informa.lib import (
    ConfigBase,
    PluginAdapter,
    StateBase,
    _load_config,
    _load_state,
    _write_state,
    pass_plugin_name,
)
from informa.lib.utils import now_aest


@dataclass
class Plugin:
    'Click context DTO to hold plugin global attributes'

    name: str
    logger: logging.Logger
    state_cls: type
    config_cls: type | None
    main: Callable


click_pass_plugin = click.make_pass_decorator(Plugin)


def setup_plugin_cli(plugin: ModuleType):
    'Setup plugin CLI context for this plugin, and add common commands'

    def get_class_attr(type_):
        'Return the class type defined in the plugin, which inherits from `type_`'
        clss = [v for _, v in inspect.getmembers(plugin, inspect.isclass) if issubclass(v, type_) and v is not type_]
        return next(iter(clss), None)

    def get_plugin_logger():
        'Return the class attribute which is an instance of lib.PluginAdapter'
        return next(iter([v for _, v in inspect.getmembers(plugin) if isinstance(v, PluginAdapter)]), None)

    # If the plugin has a CLI, setup the context and add common commands
    if hasattr(plugin, 'cli'):
        plugin_config_cls = get_class_attr(ConfigBase)
        plugin_state_cls = get_class_attr(StateBase)
        plugin_logger = get_plugin_logger()

        plugin.cli.context_settings = {
            'obj': Plugin(plugin.__name__, plugin_logger, plugin_state_cls, plugin_config_cls, plugin.main)
        }
        plugin.cli.add_command(plugin_last_run)
        plugin.cli.add_command(plugin_run_now)


@pass_plugin_name
def load_run_persist(
    plugin_name: str, logger: logging.Logger | logging.LoggerAdapter, state_cls: type[StateBase], main_func: Callable
):
    _load_run_persist(plugin_name, logger, state_cls, main_func)


def _load_run_persist(
    plugin_name: str, logger: logging.Logger | logging.LoggerAdapter, state_cls: type[StateBase], main_func: Callable
):
    '''
    Load plugin state, run plugin main function via callback, persist state to disk.

    Dynamically inspects the parameter `main_func` to determine if it has a parameter derived from
    `ConfigBase`, and if so, loads a plugin's config into an instance of this `ConfigBase` class.

    Params:
        plugin_name:  Plugin module name, supplied by the @pass_plugin_name decorator
        logger:       Logger for the calling plugin
        state_cls:    Plugin state dataclass which is persisted between runs
        main_func:    Callback function to trigger plugin logic
    '''
    try:
        # Reload config each time plugin runs
        state = _load_state(plugin_name, logger, state_cls)

        plugin_config_class: type[ConfigBase] | None = None

        # Introspect the passed main_func for a Config parameter
        for param in inspect.getfullargspec(main_func).annotations.values():
            if issubclass(param, ConfigBase):
                plugin_config_class = param

        logger.info('Running, last run: %s', state.last_run or 'Never')

        if plugin_config_class:
            # Reload config each time plugin runs
            config = _load_config(plugin_name, plugin_config_class)

            # Call plugin with config
            main_func(state, config)
        else:
            # Call plugin without config
            main_func(state)

        # Update common plugin state attributes
        state.last_run = now_aest()

        # Persist plugin metadata
        publish_plugin_run_to_mqtt(plugin_name, state)
        _write_state(plugin_name, state)
        logger.debug('State persisted')

    except AppError as e:
        logger.error(str(e))
    except ValidationError as e:
        logger.error('State ValidationError: %s', str(e))
    except Exception:
        logger.exception('Unhandled exception')


def publish_plugin_run_to_mqtt(plugin_name: str, state: StateBase):
    "Write plugin's output to a MQTT topic"
    client = mqtt.Client(CallbackAPIVersion.VERSION2)
    client.connect('locke', 1883)
    client.publish(f'informa/{plugin_name}/last_run', state.last_run.isoformat(), retain=True)


@pass_plugin_name
def write_config(plugin_name: str, config: DataClassJsonMixin):
    '''
    Write a config file

    Params:
        plugin_name:  Plugin module name, provided by the @pass_plugin_name decorator
        config:       Plugin config to serialise into JSON
    '''
    with open(f'config/{plugin_name}.yaml', 'w', encoding='utf8') as f:
        f.write(yaml.dump(config.to_dict()))


@pass_plugin_name
def load_config(
    plugin_name: str,
    config_cls: type[ConfigBase],
) -> DataClassJsonMixin | None:
    '''
    Load plugin config

    Params:
        plugin_name:  Plugin module name, supplied by the @pass_plugin_name decorator
        cls:          Plugin's config class type
    '''
    return _load_config(plugin_name, config_cls)


@pass_plugin_name
def load_state(
    plugin_name: str, logger: logging.Logger | logging.LoggerAdapter, state_cls: type[StateBase]
) -> StateBase:
    '''
    Load or initialise plugin state

    Params:
        plugin_name:  Plugin module name, provided by the @pass_plugin_name decorator
        directory:    Directory path; either 'config' or 'state'
        cls:          Plugin's state class type
    '''
    return _load_state(plugin_name, logger, state_cls)


@pass_plugin_name
def write_state(plugin_name: str, state_obj: StateBase):
    '''
    Utility function to write state to a file

    Params:
        plugin_name:  Plugin module name, provided by the @pass_plugin_name decorator
        state_obj:    Plugin's state object
    '''
    _write_state(plugin_name, state_obj)


@click.command('last-run')
@click_pass_plugin
def plugin_last_run(plugin: Plugin):
    'When was the last run?'
    state = _load_state(plugin.name, plugin.logger, plugin.state_cls)
    last_run = arrow.get(state.last_run).humanize() if state.last_run else 'Never'
    print(f'Last run: {last_run}')


@click.command('run')
@click_pass_plugin
def plugin_run_now(plugin: Plugin):
    'Run the plugin now in the foreground'
    _load_run_persist(plugin.name, plugin.logger, plugin.state_cls, plugin.main)
