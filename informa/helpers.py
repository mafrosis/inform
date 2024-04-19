import yaml
from dataclasses_jsonschema import JsonSchemaMixin


def write_config(config: JsonSchemaMixin, plugin_name: str):
    """
    Helper function to write a config file from code

        from informa.helpers import write_config
        write_config(Config(PRODUCTS), __name__)

    Params:
        config:       Plugin config to serialise into JSON
        plugin_name:  Plugin's name
    """
    with open(f'config/{plugin_name}.yaml', 'w', encoding='utf8') as f:
        f.write(yaml.dump(config.to_dict()))
