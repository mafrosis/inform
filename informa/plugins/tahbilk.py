import datetime
import logging
from dataclasses import dataclass, field

import bs4
import click
import requests
from dataclasses_json import DataClassJsonMixin

from informa.lib import PluginAdapter, app, load_run_persist, load_state, mailgun, now_aest

logger = PluginAdapter(logging.getLogger('informa'))


PLUGIN_NAME = __name__
TEMPLATE_NAME = 'tahbilk.tmpl'


@dataclass
class State(DataClassJsonMixin):
    last_run: datetime.date | None = None
    products_seen: set[str] = field(default_factory=set)


@app.task('every 12 hours', name=__name__)
def run():
    load_run_persist(logger, State, PLUGIN_NAME, main)


def main(state: State):
    state.last_run = now_aest()

    query_cellar_releases(state.products_seen)


def query_cellar_releases(products_seen: set[str]):
    try:
        resp = requests.get('https://www.tahbilk.com.au/cellar-release', timeout=5)
    except requests.RequestException as e:
        logger.error('Failed loading Tahbilk website: %s', e)
        return False

    soup = bs4.BeautifulSoup(resp.text, 'html.parser')

    # Iterate all products
    for product_info in soup.select('div.product-info'):
        title = product_info.select('h4')[0].text

        # Alert on anything not already seen
        if title not in products_seen:
            price = product_info.select('.old-price')[0].text
            logger.info('Found %s at %s', title, price)

            mailgun.send(
                logger,
                f'New Tahbilk release: {title}',
                TEMPLATE_NAME,
                {
                    'title': title,
                    'price': price,
                },
            )

        # Track all seen products, so they're notified only once
        products_seen.add(title)


@click.group(name=PLUGIN_NAME[16:])
def cli():
    "Tahbilk CLI"


@cli.command
def last_run():
    "When was the last run?"
    state = load_state(logger, State, PLUGIN_NAME)
    print(f'Last run: {state.last_run}')


@cli.command
def seen():
    "What products have been seen already?"
    state = load_state(logger, State, PLUGIN_NAME)
    print('\n'.join(state.products_seen))
