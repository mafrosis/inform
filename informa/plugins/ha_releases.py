import logging
from dataclasses import dataclass

import bs4
import click
import requests

from informa.lib import PluginAdapter, StateBase, app, mailgun
from informa.lib.plugin import load_run_persist, load_state

logger = PluginAdapter(logging.getLogger('informa'))


TEMPLATE_NAME = 'ha.tmpl'


@dataclass
class State(StateBase):
    last_release_seen: str | None = None


@app.task('every 12 hours', name=__name__)
def run():
    load_run_persist(logger, State, main)


def main(state: State):
    ver = fetch_ha_releases(state.last_release_seen or None)
    if isinstance(ver, str):
        state.last_release_seen = ver


def fetch_ha_releases(last_release_seen: str | None):
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
                },
            )
            break

    return last_release_seen


@click.group(name=__name__[16:].replace('_', '-'))
def cli():
    'Home Assistant release tracker'


@cli.command
def current():
    'What is the current HA version?'
    state = load_state(logger, State)
    click.echo(state.last_release_seen or 'Never queried')
