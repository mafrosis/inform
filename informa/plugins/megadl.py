import logging
import os
import socket
import warnings
from dataclasses import dataclass, field
from typing import List

import click
from cryptography.utils import CryptographyDeprecationWarning

with warnings.catch_warnings(action='ignore', category=CryptographyDeprecationWarning):
    import paramiko
import yaml

from informa.lib import PluginAdapter, StateBase, app
from informa.lib.plugin import load_run_persist, load_state

logger = PluginAdapter(logging.getLogger('informa'))


@dataclass
class State(StateBase):
    completed: List[str] = field(default_factory=list)


@app.task('every 1 hours', name=__name__)
def run():
    load_run_persist(logger, State, main)


def main(state: State) -> int:
    '''
    Trigger downloads from MEGA via SSH to jorg
    '''
    client = paramiko.client.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())  # noqa: S507
    try:
        client.connect(
            'jorg',
            username='mafro',
            key_filename=os.environ.get('JORG_SSH_KEY'),
            look_for_keys=False,
            allow_agent=False,
        )

        # Run blocking command over SSH
        _, stdout, _ = client.exec_command('cd {} && megadlz\n'.format(os.environ.get('MEGADLZ_DIR')))
        stdout.channel.set_combine_stderr(True)
        output = stdout.readlines()

    except socket.gaierror:
        logger.error('Socket error on SSH connect')
        return 0
    except paramiko.ssh_exception.NoValidConnectionsError:
        # Server jorg is sleeping
        return 0
    except paramiko.ssh_exception.SSHException:
        logger.error('Bad SSH private key defined in JORG_SSH_KEY')
        return 0
    finally:
        client.close()

    try:
        # Extract name of completed download
        file = next(iter([line[11:] for line in output if line.startswith('Downloaded ')])).strip()
        logger.info('Downloaded %s', file)

        # Persist something
        state.completed.append(file)

    except StopIteration:
        return 0
    else:
        # Plugin only ever triggers a single download
        return 1


@click.group(name=__name__[16:].replace('_', '-'))
def cli():
    'MEGA zip downloader'


@cli.command
def completed():
    'Print completed MEGA downloads'
    state = load_state(logger, State)
    print(yaml.dump(state.completed))
