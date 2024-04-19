import abc
import datetime
import inspect
import logging
import os
from collections.abc import Callable

import yaml
from dataclasses_jsonschema import JsonSchemaMixin, ValidationError
from fastapi import FastAPI
from rocketry import Rocketry
from zoneinfo import ZoneInfo

from informa.exceptions import AppError

app = Rocketry(
    config={
        'execution': 'thread',
        'timezone': ZoneInfo('Australia/Melbourne'),
        'cycle_sleep': 10,
    }
)

fastapi = FastAPI()


class PluginAdapter(logging.LoggerAdapter):
    def __init__(self, logger_):
        # Pass the plugin's name as `extra` param to the logger
        super().__init__(logger_, inspect.getmodule(inspect.stack()[1][0]).__name__.split('.')[-1].upper())

    def process(self, msg, kwargs):
        return f'[{self.extra}] {msg}', kwargs


class ConfigBase(JsonSchemaMixin, metaclass=abc.ABCMeta):
    "Base class from which plugin config classes must inherit"


def load_run_persist(
    logger: logging.Logger | logging.LoggerAdapter,
    state_cls: type[JsonSchemaMixin],
    plugin_name: str,
    main_func: Callable,
):
    """
    Load plugin state, run plugin main function via callback, persist state to disk.

    Dynamically inspects the parameter `main_func` to determine if it has a parameter derived from
    `ConfigBase`, and if so, loads a plugins config into an instance of this `ConfigBase` class.

    Params:
        logger:       Logger for the calling plugin
        state_cls:    Plugin state dataclass which is persisted between runs
        plugin_name:  Plugin unique name
        main_func:    Callback function to trigger plugin logic
    """
    try:
        # Reload config each time plugin runs
        state = load_state(logger, state_cls, plugin_name)

        plugin_config_class: type[ConfigBase] | None = None

        # Introspect the passed main_func for a Config parameter
        for param in inspect.getfullargspec(main_func).annotations.values():
            if issubclass(param, ConfigBase):
                plugin_config_class = param

        logger.info('Running')

        if plugin_config_class:
            # Reload config each time plugin runs
            config = load_config(plugin_config_class, plugin_name)

            # Call plugin with config
            main_func(state, config)
        else:
            # Call plugin without config
            main_func(state)

        # Write plugin state back to disk
        write_state(state, plugin_name)
        logger.debug('State persisted')

    except AppError as e:
        logger.error(str(e))
    except ValidationError as e:
        logger.error('State ValidationError: %s', str(e))
    except Exception:
        logger.exception('Unhandled exception')


def now_aest() -> datetime.datetime:
    "Utility function to return now as TZ-aware datetime"
    return datetime.datetime.now(ZoneInfo('Australia/Melbourne'))


def load_config(config_cls: type[ConfigBase], plugin_name: str) -> JsonSchemaMixin | None:
    return load_file('config', config_cls, plugin_name)


def load_state(
    logger: logging.Logger | logging.LoggerAdapter, state_cls: type[JsonSchemaMixin], plugin_name: str
) -> JsonSchemaMixin:
    """
    Load or initialise a plugin's state
    """
    state = load_file('state', state_cls, plugin_name)
    if not state:
        logger.debug('Empty state initialised for %s', plugin_name)
        state = state_cls()
    else:
        logger.debug('Loaded state for %s', plugin_name)

    return state


def load_file(directory: str, cls: type[JsonSchemaMixin], plugin_name: str) -> JsonSchemaMixin | None:
    """
    Utility function to load plugin config/state from a file

    Params:
        directory:    Directory path; either "config" or "state"
        cls:          Plugin's state/config class type
        plugin_name:  Plugin's name
    """
    try:
        with open(f'{directory}/{plugin_name}.yaml', encoding='utf8') as f:
            data = yaml.safe_load(f)
            if not data:
                raise FileNotFoundError
            return cls.from_dict(data)
    except FileNotFoundError:
        return None


def write_state(state_obj: JsonSchemaMixin, plugin_name: str):
    """
    Utility function to write state to a file

    Params:
        cls:          Plugin's state object
        plugin_name:  Plugin's name
    """
    if not os.path.exists('state'):
        os.mkdir('state')

    with open(f'state/{plugin_name}.yaml', 'w', encoding='utf8') as f:
        f.write(yaml.dump(state_obj.to_dict()))
