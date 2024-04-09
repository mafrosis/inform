import logging
import re
from dataclasses import dataclass, field

import click
import feedparser
import requests
from fake_useragent import UserAgent

from informa import exceptions
from informa.lib import ConfigBase, PluginAdapter, StateBase, app, load_run_persist, load_state, mailgun

logger = PluginAdapter(logging.getLogger('informa'))


PLUGIN_NAME = __name__
TEMPLATE_NAME = 'torrentgalaxy.tmpl'


@dataclass
class State(StateBase):
    last_seen: dict[int, str] = field(default_factory=dict)


@dataclass
class Match:
    title: str
    url: str
    magnet: str


@dataclass
class Config(ConfigBase):
    users: list[int]
    terms: list[str]


@app.task('every 12 hours', name=__name__)
def run():
    load_run_persist(logger, State, PLUGIN_NAME, main)


def main(state: State, config: Config):
    sess = requests.Session()

    # Iterate configured torrentgalaxy user IDs
    for uid in config.users:
        query_torrent(sess, config, uid, state.last_seen)


def query_torrent(sess, config: Config, uid: int, last_seen: dict[int, str]):
    logger.debug('Querying user %s', uid)

    try:
        # Fetch user's RSS feed of uploads
        resp = sess.get(
            f'https://torrentgalaxy.to/rss?magnet&user={uid}',
            headers={'User-Agent': UserAgent().chrome},
            timeout=5,
        )
    except requests.RequestException as e:
        logger.error('Failed loading torrentgalaxy for %s: %s', uid, e)
        return

    feed = feedparser.parse(resp.text)

    try:
        for searchterm in config.terms:
            matches = []

            # Compile each search term as a regex
            pat = re.compile(searchterm, re.IGNORECASE)

            # Iterate the torrents in the RSS feed
            for entry in feed['entries']:
                # When we reach last seen, abort processing
                if entry['id'] == last_seen.get(uid, 0):
                    raise exceptions.ReachedLastSeen(entry['title'])

                # Pattern match terms
                match = re.search(pat, entry['title'])
                if match:
                    matches.append(Match(entry['title'], entry['comments'], entry['links'][0]['href']))

            if matches:
                # Notify all matches per search term
                mailgun.send(
                    logger,
                    f'Torrents found for {searchterm}',
                    TEMPLATE_NAME,
                    {
                        'term': searchterm,
                        'matches': matches,
                    },
                )
                logger.debug('Sent email for %s', searchterm)

    except exceptions.ReachedLastSeen as e:
        logger.debug(e)
    finally:
        # Track the first entry seen, so we can skip these next time
        logger.debug('Marking last seen: %s', feed['entries'][0]['title'])
        last_seen[uid] = feed['entries'][0]['id']


@click.group(name=PLUGIN_NAME[16:])
def cli():
    "Torrent Galaxy user tracker"


@cli.command
def last_run():
    "When was the last run?"
    state = load_state(logger, State, PLUGIN_NAME)
    print(f'Last run: {state.last_run}')
