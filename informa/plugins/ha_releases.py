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
class NewVersion:
    version: str
    url: str
    title: str | None


@dataclass
class State(StateBase):
    last_release_seen: str | None = None


@app.task('every 24 hours', name=__name__)
def run():
    load_run_persist(logger, State, main)


def main(state: State):
    nv = fetch_ha_releases(state.last_release_seen or None)
    if nv:
        notify(nv)
        state.last_release_seen = nv.version


def fetch_ha_releases(last_release_seen: str | None) -> NewVersion | None:
    "Fetch the HA release notes and parse the HTML"
    try:
        # Fetch release notes page
        resp = requests.get('https://www.home-assistant.io/blog/categories/release-notes/', timeout=5)
    except requests.RequestException as e:
        logger.error('Failed loading HA release notes: %s', e)
        return None

    soup = bs4.BeautifulSoup(resp.text, 'html.parser')

    try:
        # Extract latest version
        version = soup.select('.release-date')[0].text.strip()
    except:  # noqa: E722 bare-except
        logger.error('New HA version parse failed!')
        mailgun.send(logger, 'New HA version parse failed!')
        return None

    logger.info('Found %s', version)

    if version != last_release_seen:
        # Extract the release notes URL
        for article in soup.find_all('article'):
            for link in article.find_all('a', href=True):
                if version[0:-2] in link.text:
                    return NewVersion(version, link['href'], link.text)

    return None


def notify(nv: NewVersion):
    if nv.title:
        mailgun.send(logger, nv.title, TEMPLATE_NAME, {'version': nv.version, 'url': nv.url, 'title': nv.title})
    else:
        mailgun.send(logger, f'New HA release {nv.version}')


@click.group(name=__name__[16:].replace('_', '-'))
def cli():
    'Home Assistant release tracker'


@cli.command
def current():
    'What is the current HA version?'
    state = load_state(logger, State)
    click.echo(state.last_release_seen or 'Never queried')
