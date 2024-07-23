import abc
import datetime
import decimal
import inspect
import logging
import os
from dataclasses import dataclass
from typing import cast

import orjson
import yaml
from dataclasses_json import DataClassJsonMixin
from fastapi import FastAPI
from rocketry import Rocketry
from zoneinfo import ZoneInfo

app = Rocketry(
    config={
        'execution': 'thread',
        'timezone': ZoneInfo('Australia/Melbourne'),
        'cycle_sleep': 10,
    }
)

fastapi = FastAPI()


class PluginAdapter(logging.LoggerAdapter):
    def __init__(self, logger_, plugin_name: str | None = None):
        if plugin_name is None:
            # Automatically determine plugin name from calling class
            plugin_name = inspect.getmodule(inspect.stack()[1][0]).__name__.split('.')[-1]

        super().__init__(logger_, plugin_name.upper())

    def process(self, msg, kwargs):
        return f'[{self.extra}] {msg}', kwargs


class ConfigBase(DataClassJsonMixin, abc.ABC):
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


def _load_config(
    plugin_name: str,
    config_cls: type[ConfigBase],
) -> DataClassJsonMixin | None:
    """
    Load plugin config
    """
    return cast(ConfigBase, _load_file(plugin_name, 'config', config_cls))


def _load_state(
    plugin_name: str, logger: logging.Logger | logging.LoggerAdapter, state_cls: type[StateBase]
) -> StateBase:
    """
    Load or initialise plugin state
    """
    state = _load_file(plugin_name, 'state', state_cls)
    _write_state(plugin_name, state)
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
        #with open(f'{directory}/{plugin_name}.json', encoding='utf8') as f:
        #    data = orjson.loads(f.read())
        #    if not data:
        #        raise FileNotFoundError
        #    data = cls.from_dict(data)
        #    _write_state(plugin_name, data)
        #    return data

        with open(f'{directory}/{plugin_name}.yaml', encoding='utf8') as f:
            data = yaml.load(f, Loader=yaml.Loader)  # noqa: S506
            if not data:
                raise FileNotFoundError
            data = cls.from_dict(data)
            _write_state(plugin_name, data)
            return data
    except FileNotFoundError:
        return None


def _write_state(plugin_name: str, state_obj: StateBase):
    """
    Utility function to write state to a file
    """
    if not os.path.exists('state'):
        os.mkdir('state')

    def default(obj):
        'Handler for types unknown to orjson'
        if isinstance(obj, decimal.Decimal):
            return str(obj)
        raise TypeError

    with open(f'state/{plugin_name}.json', 'w', encoding='utf8') as f:
        f.write(orjson.dumps(state_obj, default=default).decode())

    #with open(f'state/{plugin_name}.yaml', 'w', encoding='utf8') as f:
    #    f.write(yaml.dump(state_obj.to_dict()))
