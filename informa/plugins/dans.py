import contextlib
import datetime
import decimal
import logging
from dataclasses import dataclass, field

import click
import pandas as pd
import requests

from informa.lib import (
    ConfigBase,
    PluginAdapter,
    StateBase,
    app,
    load_run_persist,
    load_state,
    mailgun,
    now_aest,
)

logger = PluginAdapter(logging.getLogger('informa'))


PLUGIN_NAME = __name__
TEMPLATE_NAME = 'dans.tmpl'


@dataclass
class Product:
    id: str
    name: str
    target: int


@dataclass
class History:
    product: Product
    price: decimal.Decimal
    ts: datetime.datetime
    alerted: bool = False


@dataclass
class State(StateBase):
    history: list[History] = field(default_factory=list)


@dataclass
class Config(ConfigBase):
    products: list[Product]


class FailedProductQuery(Exception):
    pass


class ProductNeverAlerted(Exception):
    pass


@app.task('every 12 hours', name=__name__)
def run():
    load_run_persist(logger, State, PLUGIN_NAME, main)


def main(state: State, config: Config):
    sess = requests.Session()

    history_item: History | None = None

    # Iterate configured list of Dan's products
    for product in config.products:
        with contextlib.suppress(ProductNeverAlerted):
            history_item, _ = get_last_alert(product, state.history)

        try:
            current_price = query_product(sess, product)
            alerted = False

            # Check if price within target range, and send an email if so
            if current_price <= product.target:
                # Skip product if alerted more recently than 6 days ago
                if history_item and history_item.ts > now_aest() - datetime.timedelta(days=6):
                    logger.info('Skipped alerting %s @ %s', product.name, history_item.price)
                else:
                    send_alert(product, current_price)
                    alerted = True

            # Track query results
            result = History(product, current_price, ts=now_aest(), alerted=alerted)
            add_to_history(state.history, result)

        except FailedProductQuery as e:
            logger.error(e)


def get_last_alert(product: Product, history: list[History]) -> tuple[History, int]:
    'Lookup most recent alert for this product'
    for i, history_item in enumerate(reversed(history)):
        if history_item.product == product and history_item.alerted:
            return history_item, i
    raise ProductNeverAlerted


def add_to_history(history: list[History], new_history: History):
    'Add query result to product history'
    # Remove history over 13 months old
    for i, history_item in enumerate(history):
        if history_item.ts > now_aest() + datetime.timedelta(days=400):
            del history[i]

    history.append(new_history)


def query_product(sess, product: Product) -> decimal.Decimal:
    '''
    Query Dan Murphy's API for a product's current pricing

    Params:
        sess:     Requests session
        product:  Product to query for
    Returns:
        Return the current price
    '''
    try:
        # Fetch single product from Dan's API
        resp = sess.get(f'https://api.danmurphys.com.au/apis/ui/Product/{product.id}', timeout=5)
    except requests.RequestException as e:
        raise FailedProductQuery(f'Failed loading from {product.name}: {e}') from e

    if resp.status_code != 200:  # noqa: PLR2004
        raise FailedProductQuery(f'HTTP {resp.status_code} loading {product.name}')

    try:
        # Pull out the current single bottle price
        prices = resp.json()['Products'][0]['Prices']
        prices = prices['promoprice'] if 'promoprice' in prices else prices['singleprice']

        # Continue with current price as decimal
        current_price = decimal.Decimal(str(prices['Value']))

    except (requests.exceptions.JSONDecodeError, KeyError, IndexError) as e:
        raise FailedProductQuery(f'{e.__class__.__name__} {e} parsing price for {product.name}') from e

    logger.debug('Querying %s %s for target=%s, actual=%s', product.id, product.name, product.target, current_price)

    return current_price


def send_alert(product: Product, current_price: decimal.Decimal):
    'Send alert email via Mailgun'
    logger.info('Sending email for %s @ %s', product.name, current_price)

    mailgun.send(
        logger,
        f'Good price on {product.name}',
        TEMPLATE_NAME,
        {
            'product': product.name,
            'price': current_price,
            'url': f'https://www.danmurphys.com.au/product/DM_{product.id}',
        },
    )


@click.group(name=PLUGIN_NAME[16:])
def cli():
    'Dan Murphy\'s product tracker'


def get_history() -> pd.DataFrame:
    state = load_state(logger, State, __name__)
    df = pd.DataFrame([
        {
            'id': h.product.id,
            'name': h.product.name,
            'target': h.product.target,
            'price': h.price,
            'ts': h.ts,
        }
        for h in state.history
    ])
    df['ts'] = pd.to_datetime(df.ts, utc=True)
    return df


@cli.command
def stats():
    '''
    Show product stats
    '''
    df = get_history()

    # Aggregate max/min/median prices
    price_range = df.groupby(['name']).agg({'price': ['count', 'min', 'max', 'median']})
    price_range.columns = price_range.columns.get_level_values(1)

    # Determine earliest and most recent query dates
    query_range = df.groupby(['name']).agg({'ts': ['min', 'max']})
    query_range.columns = query_range.columns.get_level_values(1)

    # Pull price from most recent query
    latest_price = df.sort_values(['ts']).groupby('name').tail(1).set_index('name').drop(columns=['id', 'ts'])

    # Smash into single dataframe
    df = pd.concat([price_range, latest_price, query_range], axis=1)
    df.columns = ['Count', 'Min', 'Max', 'Median', 'Target', 'Latest', 'First', 'Most Recent']
    df['First'] = df['First'].dt.strftime('%d-%m-%Y')
    df['Most Recent'] = df['Most Recent'].dt.strftime('%d-%m-%Y')
    print(df)
