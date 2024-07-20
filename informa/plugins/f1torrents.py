import datetime
import logging
import math
import os
import re
import socket
import xmlrpc.client
from dataclasses import dataclass, field
from typing import cast
from urllib.parse import urlparse

import click
import feedparser
import googleapiclient
import requests
from dataclasses_json import DataClassJsonMixin
from gcsa.google_calendar import GoogleCalendar
from google.oauth2.service_account import Credentials
from rocketry.conds import cron

from informa.lib import (
    ConfigBase,
    PluginAdapter,
    StateBase,
    app,
    mailgun,
    pretty,
)
from informa.lib.plugin import load_config, load_run_persist, load_state, write_state

logger = PluginAdapter(logging.getLogger('informa'))


RTORRENT_HOST = '192.168.1.104'
TEMPLATE_NAME = 'f1torrents.tmpl'

RT_PRI_HIGH = 2
RT_PRI_NORM = 1
RT_PRI_OFF = 0


@dataclass
class Download(DataClassJsonMixin):
    key: str
    title: str
    magnet: str
    added_to_rtorrent: bool = False


@dataclass
class State(StateBase):
    races: dict[str, Download] = field(default_factory=dict)


class FailedFetchingTorrents(Exception):
    pass


@dataclass
class Race(DataClassJsonMixin):
    title: str
    start: datetime.datetime


@dataclass
class Config(ConfigBase):
    current_season: int
    calendar: list[Race] | None = None


@app.cond()
def is_f1_weekend():
    cal = fetch_f1_calendar()
    if not cal:
        return False

    today = datetime.datetime.now(tz=datetime.UTC).date()

    for race in [Race(k, v) for k, v in cal.items()]:
        if race.start.date() - datetime.timedelta(days=3) < today < race.start.date() + datetime.timedelta(days=3):
            return True

    logger.debug('Today not within F1 weekend range')
    return False


def fetch_f1_calendar() -> dict[str, datetime.datetime]:
    "Fetch current F1 calendar"
    try:
        gc = GoogleCalendar(
            credentials=Credentials.from_service_account_file(os.environ.get('GSUITE_OAUTH_CREDS'))
        )
    except googleapiclient.errors.HttpError:
        logger.error('Failed to authenticate to Google Calendar API')
        return None

    config = load_config(Config)

    events = gc.get_events(
        calendar_id='c_e615a5f4fc5d2ddb8ff9e902a50fcf0c26ffe628f149c11d13b4c51e123ce8a7@group.calendar.google.com',
        time_min=datetime.date(config.current_season, 1, 1),
        single_events=True,
        order_by='startTime',
    )
    return {e.summary: e.start for e in events if e.summary.endswith(' - Race')}


@app.task(cron('*/15 * * * *') & is_f1_weekend, name=__name__)
def run():
    load_run_persist(logger, State, main)


def main(state: State, config: Config):
    """
    Check for new F1 torrents and add to rtorrent
    """
    try:
        if check_torrentgalaxy(config.current_season, state):
            # if torrents found, try to add immediately
            add_magnet_to_rtorrent(state.races)
    except FailedFetchingTorrents as e:
        logger.error(e)


@app.task('every 5 minute')
def set_torrent_file_priorities():
    """
    Set priority high on the 02.Race.Session or 02.Qualifying.Session torrent parts
    """
    rt = RTorrent(RTORRENT_HOST, 5000)
    try:
        torrents = rt.get_torrents()
    except RtorrentError as e:
        # No error logging to save log noise when jorg is switched off
        logger.debug(e)
        return

    for hash_id, torrent_data in torrents.items():
        if 'Formula.1.' not in torrent_data['name']:
            continue

        try:
            # Set label on F1 torrents
            rt.set_tag(hash_id, 'F1')
        except RtorrentError as e:
            logger.error('Failed setting tag on %s (%s)', hash_id, e)
            continue

        for i, file_data in enumerate(torrent_data['files']):
            if '02' in file_data['filename']:
                try:
                    pri = rt.get_file_priority(hash_id, i)

                    if pri != RT_PRI_HIGH:
                        rt.set_file_priority(hash_id, i, RT_PRI_HIGH)
                        logger.debug('Set high priority on %s', torrent_data['name'])

                except RtorrentError as e:
                    logger.error('Failed setting priority on %s:%s (%s)', hash_id, i, e)
                    continue


@app.task('every 15 minutes')
def add_torrents():
    state = cast(State, load_state(logger, State))
    if add_magnet_to_rtorrent(state.races):
        write_state(state)


def add_magnet_to_rtorrent(races: dict[str, Download]) -> bool:
    """
    Add magnets directly to rtorrent via RPC
    """
    torrent_added = False

    for key, race_data in races.items():
        if not race_data.added_to_rtorrent:
            try:
                rt = RTorrent(RTORRENT_HOST, 5000)
                rt.add_magnet(race_data.magnet)
            except RtorrentError as e:
                if 'No route to host' in str(e):
                    # Wake jorg via wol-sender running on 3001
                    requests.get('http://locke:3001/wake/d0:50:99:c1:63:c9', timeout=3)
                    logger.info('WOL packet sent to wake rtorrent')
                    return

                logger.error('Failed adding magnet for %s (%s)', key, e)
                continue

            # parse magnet link to get torrent filename
            qs = urlparse(race_data.magnet).query.split('&')
            filename = next((p[3:] for p in qs if p.startswith('dn=')), '')

            logger.info('Added magnet for %s', filename)
            race_data.added_to_rtorrent = True

            mailgun.send(
                logger,
                f'{filename} torrent added',
                TEMPLATE_NAME,
                {
                    'filename': filename,
                },
            )
            torrent_added = True

    return torrent_added


def check_torrentgalaxy(current_season: int, state: State) -> bool:
    torrent_url = 'https://torrentgalaxy.to/rss?magnet&user=48067'

    try:
        sess = requests.Session()
        resp = sess.get(torrent_url, timeout=5)

    except requests.RequestException as e:
        raise FailedFetchingTorrents(f'Failed loading from {torrent_url}') from e

    feed = feedparser.parse(resp.text)

    ret = False

    RACE_TYPES = {'Race', 'Qualifying', 'Sprint', 'Season.Review', 'Shootout'}

    for entry in feed['entries']:
        title = entry['title']

        if 'Formula.1' in title and str(current_season) in title and 'SkyF1HD.1080p' in title:
            if not any(s in title for s in RACE_TYPES) or 'Teds' in title:
                logger.debug('Skipped: %s', title)
                continue

            try:
                # Find magnet link
                magnet = entry['links'][0]['href']
                if not magnet:
                    raise ValueError
            except (ValueError, KeyError, IndexError):
                logger.error('Failed extracting magnet: %s', entry.get('links', 'No key "links" on entry obj!'))
                continue

            # Race / Qualifying / Sprint etc
            if 'Sprint' in title:
                session_type = 'sq' if 'Qualifying' in title else 'sr'
            elif 'Qualifying' in title:
                session_type = 'qu'
            elif 'Race' in title:
                session_type = 'ra'
            else:
                session_type = 'rv'

            logger.debug('Found: %s (%s)', title, session_type)

            # Create a different key for each session type (eg. 2023x04ra)
            key = f'{title[10:17]}{session_type}'

            if key not in state.races:
                state.races[key] = Download(key=key, title=title, magnet=magnet)
                ret = True

    return ret


class RtorrentError(Exception):
    pass


class SCGITransport(xmlrpc.client.Transport):
    def single_request(self, host, handler, request_body, verbose=0):  # noqa: ARG002
        # Create SCGI header
        header = f'CONTENT_LENGTH\x00{len(request_body)}\x00SCGI\x001\x00'
        request_body = f'{len(header)}:{header},{request_body}'
        sock = None

        try:
            if host:
                host, port = host.split(':')
                addrinfo = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)
                sock = socket.socket(*addrinfo[0][:3])
                sock.connect(addrinfo[0][4])
            else:
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.connect(handler)

            sock.send(request_body.encode('ascii'))
            return self.parse_response(sock.makefile())

        finally:
            if sock:
                sock.close()

    def parse_response(self, response):
        p, u = self.getparser()

        response_body = ''
        while True:
            data = response.read(1024)
            if not data:
                break
            response_body += data

        try:
            # Remove SCGI headers from the response
            _, response_body = re.split(r'\n\s*?\n', response_body, maxsplit=1)
            p.feed(response_body)
            p.close()
            return u.close()

        except ValueError as e:
            raise RtorrentError('Failed parsing SCGI response!') from e


class SCGIServerProxy(xmlrpc.client.ServerProxy):
    def __init__(self, uri):
        uri = urlparse(uri)
        if uri.scheme != 'scgi':
            raise OSError(f'unsupported XML-RPC protocol {uri.scheme}')

        self.__host = uri.netloc
        self.__handler = uri.path
        self.__transport = SCGITransport()
        self.__encoding = None
        self.__allow_none = None

    def __close(self):
        self.__transport.close()

    def __request(self, methodname, params):
        request = xmlrpc.client.dumps(params, methodname, encoding=self.__encoding, allow_none=self.__allow_none)

        response = self.__transport.request(
            self.__host,
            self.__handler,
            request,
        )

        if not response:
            return None
        if len(response) == 1:
            response = response[0]

        return response

    def __repr__(self):
        return f'<SCGIServerProxy for {self.__host}{self.__handler}>'

    def __getattr__(self, name):
        return xmlrpc.client._Method(self.__request, name)  # noqa: SLF001

    def __call__(self, attr):
        """
        A workaround to get special attributes on the ServerProxy
        without interfering with the magic __getattr__
        """
        if attr == 'close':
            return self.__close
        if attr == 'transport':
            return self.__transport

        raise AttributeError(f'Attribute {attr} not found')


class RTorrent:
    def __init__(self, host, port):
        self.server = SCGIServerProxy(f'scgi://{host}:{port}/')

    def get_torrents(self, tag_filter=None):
        try:
            downloads = self.server.d.multicall2(
                '',  # empty target
                'main',
                'd.hash=',
                'd.name=',
                'd.completed_bytes=',
                'd.custom1=',
            )
            if downloads is None:
                raise RtorrentError('get_torrents: Failed to load from rtorrent SCGI')

        except ConnectionRefusedError as e:
            raise RtorrentError('get_torrents: Rtorrent is down') from e
        except (OSError, xmlrpc.client.Fault) as e:
            raise RtorrentError(f'get_torrents: Failed to load from rtorrent SCGI: {e}') from e

        data = {}

        for d in downloads:
            if not tag_filter or (tag_filter and d[4] == tag_filter):
                # calculate % done
                data[d[0]] = {
                    'name': d[1],
                    'size': format_size(d[2]),
                    'tag': d[3],
                    'files': [],
                }

                try:
                    files = self.server.f.multicall(
                        d[0],
                        '',
                        'f.path=',
                        'f.size_bytes=',
                        'f.size_chunks=',
                        'f.completed_chunks=',
                        'f.priority=',
                    )
                except ConnectionRefusedError as e:
                    raise RtorrentError('get_torrents: Rtorrent is down') from e
                except (OSError, xmlrpc.client.Fault) as e:
                    raise RtorrentError(f'get_torrents: Failed to load d.files from rtorrent SCGI: {e}') from e

                for f in files:
                    data[d[0]]['files'].append({
                        'filename': f[0],
                        'size': format_size(f[1]),
                        'progress': f'{float(f[3]) / float(f[2]) * 100:.1f}%' if f[2] else 0,
                        'priority': 'skip' if f[4] == RT_PRI_OFF else 'high' if f[4] == RT_PRI_HIGH else 'normal',
                    })

                try:
                    # torrent total progress based on each file's progress, ignoring "skipped" files
                    torrent_progress = sum(f[3] for f in files if f[4] > 0) / sum(f[2] for f in files if f[4] > 0) * 100
                except ZeroDivisionError:
                    # all files are "skip"
                    torrent_progress = 0

                data[d[0]]['progress'] = f'{torrent_progress:.1f}%'
                data[d[0]]['complete'] = torrent_progress == 100  # noqa: PLR2004

        return data

    def add_magnet(self, magnet_url):
        """
        Add a magnet URL

        Params:
            magnet_url (str):   duh
        """
        try:
            self.server.load.start_verbose('', magnet_url)

        except ConnectionRefusedError as e:
            raise RtorrentError('add_magnet: Rtorrent is down') from e
        except (OSError, xmlrpc.client.Fault) as e:
            raise RtorrentError(f'add_magnet: Failed to add magnet: {e}') from e

    def set_tag(self, hash_id, tag_name):
        """
        Set tag in custom1 field on a torrent

        Params:
            hash_id (str):      download hash_id
            tag_name (str):     tag text
        """
        try:
            self.server.d.custom1.set(hash_id, tag_name)

        except ConnectionRefusedError as e:
            raise RtorrentError('set_tag: Rtorrent is down') from e
        except (OSError, xmlrpc.client.Fault) as e:
            raise RtorrentError(f'set_tag: Failed to load from rtorrent SCGI: {e}') from e

    def set_file_priority(self, hash_id, file_index, priority):
        """
        Set priority of a file in a torrent

        Params:
            hash_id (str):     download hash_id
            file_index (int):  position in download.files[] from get_torrents()
            priority (int):    0: skip, 1: normal, 2: high
        """
        try:
            self.server.f.priority.set(f'{hash_id}:f{file_index}', priority)

        except ConnectionRefusedError as e:
            raise RtorrentError('set_file_priority: Rtorrent is down') from e
        except (OSError, xmlrpc.client.Fault) as e:
            raise RtorrentError(f'set_file_priority: Failed to load from rtorrent SCGI: {e}') from e

    def get_file_priority(self, hash_id, file_index):
        """
        Set priority of a file in a torrent

        Params:
            hash_id (str):     download hash_id
            file_index (int):  position in download.files[] from get_torrents()
        Returns:
            priority (int):    0: skip, 1: normal, 2: high
        """
        try:
            return self.server.f.priority(f'{hash_id}:f{file_index}')

        except ConnectionRefusedError as e:
            raise RtorrentError('get_file_priority: Rtorrent is down') from e
        except (OSError, xmlrpc.client.Fault) as e:
            raise RtorrentError(f'get_file_priority: Failed to load from rtorrent SCGI: {e}') from e


def format_size(size):
    if size <= 0:
        return '0B'

    i = int(math.floor(math.log(size, 1024)))
    s = round(size / math.pow(1024, i), 2)

    return '{}{}'.format(s, ('B', 'KB', 'MB', 'GB', 'TB', 'PB')[i])


@click.group(name=__name__[16:])
def cli():
    "F1 torrent downloader"


@cli.command
def found():
    "What races have we found already?"
    state = load_state(logger, State)
    for race in state.races.values():
        added = 'added  ' if race.added_to_rtorrent is True else 'pending'
        print(f'{added} {race.title}')


@cli.command
def get_torrents():
    "Load the current torrents from rtorrent"
    rt = RTorrent(RTORRENT_HOST, 5000)
    try:
        torrents = rt.get_torrents()
        pretty.table([t for t in torrents.values() if 'Formula.1' in t['name']], columns=('progress', 'name'))
    except RtorrentError as e:
        logger.error(e)


@cli.command
def calendar():
    "Fetch F1 calendar for the current configured year, and write to config"
    cal = fetch_f1_calendar()
    if not cal:
        return False

    for r in [Race(k, v) for k, v in cal.items()]:
        print(f'{r.title}')
        print(f'   {r.start}')
