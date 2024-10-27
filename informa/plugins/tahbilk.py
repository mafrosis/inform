import logging
from dataclasses import dataclass, field

import bs4
import click
import requests

from informa.lib import PluginAdapter, StateBase, app, mailgun
from informa.lib.plugin import load_run_persist, load_state

logger = PluginAdapter(logging.getLogger('informa'))


TEMPLATE_NAME = 'tahbilk.tmpl'


@dataclass
class State(StateBase):
    products_seen: set[str] = field(default_factory=set)

@dataclass
class NewRelease:
    title: str
    price: str


@app.task('every 12 hours', name=__name__)
def run():
    load_run_persist(logger, State, main)


def main(state: State) -> int:
    if nr := query_cellar_releases(state.products_seen):
        notify(nr)

    return len(state.products_seen)


def query_cellar_releases(products_seen: set[str]) -> NewRelease | None:
    try:
        resp = requests.get('https://www.tahbilk.com.au/cellar-release', timeout=5)
    except requests.RequestException as e:
        logger.error('Failed loading Tahbilk website: %s', e)
        return None

    soup = bs4.BeautifulSoup(resp.text, 'html.parser')

    # Iterate all products
    for product_info in soup.select('div.product-info'):
        title = product_info.select('h4')[0].text

        # Track all seen products, so they're notified only once
        products_seen.add(title)

        # Alert on anything not already seen
        if title not in products_seen:
            price = product_info.select('.old-price')[0].text
            logger.info('Found %s at %s', title, price)
            return NewRelease(title, price)

    return None


def notify(nr: NewRelease):
    mailgun.send(
        logger,
        f'New Tahbilk release: {nr.title}',
        TEMPLATE_NAME,
        {
            'title': nr.title,
            'price': nr.price,
        },
    )


@click.group(name=__name__[16:])
def cli():
    'Tahbilk CLI'


@cli.command
def seen():
    'What products have been seen already?'
    state = load_state(logger, State)
    print('\n'.join(state.products_seen))
