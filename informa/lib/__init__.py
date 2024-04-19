import abc
import datetime
import inspect
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import cast

import yaml
from dataclasses_json import DataClassJsonMixin
from fastapi import FastAPI
from marshmallow.exceptions import ValidationError
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


class ConfigBase(DataClassJsonMixin, metaclass=abc.ABCMeta):
    "Base class from which plugin Config classes must inherit"


@dataclass
class StateBase(DataClassJsonMixin):
    "Base class from which plugin State classes must inherit"

    last_run: datetime.date | None = None


def pass_plugin_name(func):
    "Lookup the name of the calling module, and pass as the first parameter to `func`"

    def inner(*args, **kwargs):
        frame = inspect.stack()[1]
        plugin_name = inspect.getmodule(frame[0]).__name__
        return func(plugin_name, *args, **kwargs)

    return inner


@pass_plugin_name
def load_run_persist(
    plugin_name: str, logger: logging.Logger | logging.LoggerAdapter, state_cls: type[StateBase], main_func: Callable
):
    """
    Load plugin state, run plugin main function via callback, persist state to disk.

    Dynamically inspects the parameter `main_func` to determine if it has a parameter derived from
    `ConfigBase`, and if so, loads a plugins config into an instance of this `ConfigBase` class.

    Params:
        plugin_name:  Plugin module name, supplied by the @pass_plugin_name decorator
        logger:       Logger for the calling plugin
        state_cls:    Plugin state dataclass which is persisted between runs
        main_func:    Callback function to trigger plugin logic
    """
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

        state.last_run = now_aest()

        # Write plugin state back to disk
        _write_state(plugin_name, state)
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


@pass_plugin_name
def load_config(
    plugin_name: str,
    config_cls: type[ConfigBase],
) -> DataClassJsonMixin | None:
    """
    Load plugin config

    Params:
        plugin_name:  Plugin module name, supplied by the @pass_plugin_name decorator
        cls:          Plugin's config class type
    """
    return _load_config(plugin_name, config_cls)


def _load_config(
    plugin_name: str,
    config_cls: type[ConfigBase],
) -> DataClassJsonMixin | None:
    """
    Load plugin config
    Doesn't use @pass_plugin_name magic
    """
    return cast(ConfigBase, _load_file(plugin_name, 'config', config_cls))


@pass_plugin_name
def load_state(
    plugin_name: str, logger: logging.Logger | logging.LoggerAdapter, state_cls: type[StateBase]
) -> StateBase:
    """
    Load or initialise plugin state

    Params:
        plugin_name:  Plugin module name, provided by the @pass_plugin_name decorator
        directory:    Directory path; either "config" or "state"
        cls:          Plugin's state class type
    """
    return _load_state(plugin_name, logger, state_cls)


def _load_state(
    plugin_name: str, logger: logging.Logger | logging.LoggerAdapter, state_cls: type[StateBase]
) -> StateBase:
    """
    Load or initialise plugin state
    Doesn't use @pass_plugin_name magic
    """
    state = _load_file(plugin_name, 'state', state_cls)
    if not state:
        logger.debug('Empty state initialised for %s', plugin_name)
        state = state_cls()
    else:
        logger.debug('Loaded state for %s', plugin_name)

    return cast(StateBase, state)


def _load_file(plugin_name: str, directory: str, cls: type[DataClassJsonMixin]) -> DataClassJsonMixin | None:
    """
    Utility function to load plugin config/state from a file

    Params:
        plugin_name:  Plugin's name
        directory:    Directory path; either "config" or "state"
        cls:          Plugin's state/config class type
    """
    try:
        with open(f'{directory}/{plugin_name}.yaml', encoding='utf8') as f:
            data = yaml.load(f, Loader=yaml.Loader)  # noqa: S506
            if not data:
                raise FileNotFoundError
            return cls.from_dict(data)
    except FileNotFoundError:
        return None


@pass_plugin_name
def write_state(plugin_name: str, state_obj: StateBase):
    """
    Utility function to write state to a file

    Params:
        plugin_name:  Plugin module name, provided by the @pass_plugin_name decorator
        state_obj:    Plugin's state object
    """
    _write_state(plugin_name, state_obj)


def _write_state(plugin_name: str, state_obj: StateBase):
    """
    Utility function to write state to a file
    Doesn't use @pass_plugin_name magic
    """
    if not os.path.exists('state'):
        os.mkdir('state')

    with open(f'state/{plugin_name}.yaml', 'w', encoding='utf8') as f:
        f.write(yaml.dump(state_obj.to_dict()))
