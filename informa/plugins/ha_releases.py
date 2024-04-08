import datetime
import logging
from dataclasses import dataclass, field
from typing import Optional

import bs4
import click
import requests
from dataclasses_jsonschema import JsonSchemaMixin

from informa.lib import PluginAdapter, app, load_run_persist, load_state, mailgun, now_aest

logger = PluginAdapter(logging.getLogger('informa'))


PLUGIN_NAME = __name__
TEMPLATE_NAME = 'ha.tmpl'


@dataclass
class State(JsonSchemaMixin):
    last_run: Optional[datetime.date] = field(default=None)
    last_release_seen: Optional[str] = field(default=None)


@app.task('every 12 hours', name=__name__)
def run():
    load_run_persist(logger, State, PLUGIN_NAME, main)


def main(state: State):
    state.last_run = now_aest()

    ver = fetch_ha_releases(state.last_release_seen or None)
    if isinstance(ver, str):
        state.last_release_seen = ver


def fetch_ha_releases(last_release_seen: Optional[str]):
    try:
        # Fetch release notes page
        resp = requests.get('https://www.home-assistant.io/blog/categories/release-notes/', timeout=5)
    except requests.RequestException as e:
        logger.error('Failed loading HA release notes: %s', e)
        return False

    soup = bs4.BeautifulSoup(resp.text, 'html.parser')

    # Iterate release news page
    for release in soup.select('h1.gamma a'):
        try:
            # Parse release title (this is likely to break at some point)
            version = release.text.split(':')[0]
        except IndexError:
            continue

        # Abort when we reach the most recently seen release
        if version == last_release_seen:
            logger.debug('Stopping at %s', version)
            break

        # Send an email for a more recent HA release
        if version > (last_release_seen or '0'):
            last_release_seen = version
            logger.info('Found %s', last_release_seen)

            mailgun.send(
                logger,
                f'New HA release {last_release_seen}',
                TEMPLATE_NAME,
                {
                    'version': last_release_seen,
                }
            )
            break

    return last_release_seen


@click.group(name=PLUGIN_NAME[16:].replace('_', '-'))
def cli():
    'Home Assistant release tracker'

@cli.command
def last_run():
    'When was the last run?'
    state = load_state(logger, State, PLUGIN_NAME)
    print(f'Last run: {state.last_run}')

@cli.command
def current():
    'What is the current HA version?'
    state = load_state(logger, State, PLUGIN_NAME)
    click.echo(state.last_release_seen or 'Never queried')
