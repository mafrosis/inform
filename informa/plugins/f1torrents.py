from dataclasses import dataclass, field
import datetime
import logging
import math
import re
import socket
from socket import error as SocketError
from typing import cast, Dict, Optional
from urllib.parse import urlparse
import xmlrpc.client

import click
from dataclasses_jsonschema import JsonSchemaMixin
import feedparser
import requests
from wakeonlan import send_magic_packet


from informa.lib import (app, ConfigBase, load_run_persist, load_state, mailgun, now_aest,
                         PluginAdapter, pretty)


logger = PluginAdapter(logging.getLogger('informa'))


RTORRENT_HOST = '192.168.1.104'
PLUGIN_NAME = __name__
TEMPLATE_NAME = 'f1torrents.tmpl'


@dataclass
class Race(JsonSchemaMixin):
    key: str
    title: str
    magnet: str
    added_to_rtorrent: bool = field(default=False)

@dataclass
class State(JsonSchemaMixin):
    last_run: Optional[datetime.date] = field(default=now_aest())
    latest_race: str = field(default='')
    races: Dict[str, Race] = field(default_factory=dict)

class FailedFetchingTorrents(Exception):
    pass


@dataclass
class Config(ConfigBase):
    current_season: int = field(default=2023)


@app.task('every 15 minutes', name=__name__)
def run():
    load_run_persist(logger, State, PLUGIN_NAME, main)


def main(state: State, config: Config):
    '''
    Check for new F1 torrents and add to rtorrent
    '''
    logger.debug('Checking for torrents..')
    check_torrentgalaxy(config.current_season, state)

    # Immediately add torrents (this command also runs every 15 mins on a task)
    add_magnet_to_rtorrent(state.races)


@app.task('every 5 minute')
def set_torrent_file_priorities():
    '''
    Set priority high on the 02.Race.Session or 02.Qualifying.Session torrent parts
    '''
    rt = RTorrent(RTORRENT_HOST, 5000)
    try:
        torrents = rt.get_torrents()
    except RtorrentError as e:
        logger.error(e)
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

                    if pri != 2:
                        rt.set_file_priority(hash_id, i, 2)
                        logger.debug('Set high priority on %s', torrent_data['name'])

                except RtorrentError as e:
                    logger.error('Failed setting priority on %s:%s (%s)', hash_id, i, e)
                    continue


@app.task('every 15 minutes')
def add_torrents():
    state = cast(State, load_state(logger, State, PLUGIN_NAME))
    add_magnet_to_rtorrent(state.races)


def add_magnet_to_rtorrent(races: Dict[str, Download]):
    '''
    Add magnets directly to rtorrent via RPC
    '''
    for key, race_data in races.items():

        if not race_data.added_to_rtorrent:
            try:
                rt = RTorrent(RTORRENT_HOST, 5000)
                rt.add_magnet(race_data.magnet)
            except RtorrentError as e:
                if 'No route to host' in str(e):
                    # Wake on LAN for jorg
                    send_magic_packet('d0:50:99:c1:63:c9')
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
                }
            )


def check_torrentgalaxy(current_season: int, state: State):
    torrent_url = 'https://torrentgalaxy.to/rss?magnet&user=48067'

    try:
        sess = requests.Session()
        resp = sess.get(torrent_url, timeout=5)

    except requests.RequestException as e:
        raise FailedFetchingTorrents(f'Failed loading from {torrent_url}') from e

    feed = feedparser.parse(resp.text)

    logger.info(f'Latest race: {state.latest_race}')

    for entry in feed['entries']:
        title = entry['title']

        if 'Formula.1' in title and str(current_season) in title and 'SkyF1HD.1080p' in title:
            if not any(s in title for s in ('Race', 'Qualifying', 'Sprint', 'Season.Review')):
                logger.debug(f'Skipped: {title}')
                continue

            try:
                # Find magnet link
                magnet = entry['links'][0]['href']
                if not magnet:
                    raise ValueError
            except (ValueError, KeyError, IndexError):
                logger.error('Failed extracting magnet: %s', entry.get('links', 'No key "links" on entry obj!'))
                continue

            logger.debug(f'Found: {title}')

            # Create a different key for Race and Qualy
            if 'Race' in title:
                key = f'{title[10:17]}r'
            else:
                key = f'{title[10:17]}q'

            # Retain all data to be posted on MQTT
            if key not in state.races:
                state.races[key] = Race(key=key, title=title, magnet=magnet)

            if key > state.latest_race:
                state.latest_race = key


class RtorrentError(Exception):
    pass


class SCGITransport(xmlrpc.client.Transport):
    def single_request(self, host, handler, request_body, verbose=0):
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
    def __init__(self, uri):  # pylint: disable=super-init-not-called
        uri = urlparse(uri)
        if uri.scheme != 'scgi':
            raise IOError(f'unsupported XML-RPC protocol {uri.scheme}')

        self.__host = uri.netloc
        self.__handler = uri.path
        self.__transport = SCGITransport()
        self.__encoding = None
        self.__allow_none = None

    def __close(self):
        self.__transport.close()

    def __request(self, methodname, params):
        request = xmlrpc.client.dumps(
            params,
            methodname,
            encoding=self.__encoding,
            allow_none=self.__allow_none
        )

        response = self.__transport.request(
            self.__host,
            self.__handler,
            request,
        )

        if not response:
            return None
        elif len(response) == 1:
            response = response[0]

        return response

    def __repr__(self):
        return f'<SCGIServerProxy for {self.__host}{self.__handler}>'

    def __getattr__(self, name):
        # magic method dispatcher
        return xmlrpc.client._Method(self.__request, name)

    def __call__(self, attr):
        '''
        A workaround to get special attributes on the ServerProxy
        without interfering with the magic __getattr__
        '''
        if attr == 'close':
            return self.__close
        elif attr == 'transport':
            return self.__transport

        raise AttributeError(f'Attribute {attr} not found')


class RTorrent():
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
                'd.custom1='
            )
            if downloads is None:
                raise RtorrentError('Failed to load from rtorrent SCGI')

        except ConnectionRefusedError as e:
            raise RtorrentError('Rtorrent is down') from e
        except (xmlrpc.client.Fault, SocketError) as e:
            raise RtorrentError(f'Failed to load from rtorrent SCGI: {e}') from e

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
                    raise RtorrentError('Rtorrent is down') from e
                except (xmlrpc.client.Fault, SocketError) as e:
                    raise RtorrentError(f'Failed to load d.files from rtorrent SCGI: {e}') from e

                for f in files:
                    data[d[0]]['files'].append({
                        'filename': f[0],
                        'size': format_size(f[1]),
                        'progress': '{0:.1f}%'.format(float(f[3]) / float(f[2]) * 100) if f[2] else 0,  # pylint: disable=consider-using-f-string
                        'priority': 'skip' if f[4] == 0 else 'high' if f[4] == 2 else 'normal',
                    })

                try:
                    # torrent total progress based on each file's progress, ignoring "skipped" files
                    torrent_progress = sum((f[3] for f in files if f[4] > 0)) / sum((f[2] for f in files if f[4] > 0)) * 100
                except ZeroDivisionError:
                    # all files are "skip"
                    torrent_progress = 0

                data[d[0]]['progress'] = f'{torrent_progress:.1f}%'
                data[d[0]]['complete'] = torrent_progress == 100

        return data


    def add_magnet(self, magnet_url):
        '''
        Add a magnet URL

        Params:
            magnet_url (str):   duh
        '''
        try:
            self.server.load.start_verbose('', magnet_url)

        except ConnectionRefusedError as e:
            raise RtorrentError('Rtorrent is down') from e
        except (xmlrpc.client.Fault, SocketError) as e:
            raise RtorrentError(f'Failed to add magnet: {e}') from e


    def set_tag(self, hash_id, tag_name):
        '''
        Set tag in custom1 field on a torrent

        Params:
            hash_id (str):      download hash_id
            tag_name (str):     tag text
        '''
        try:
            self.server.d.custom1.set(hash_id, tag_name)

        except ConnectionRefusedError as e:
            raise RtorrentError('Rtorrent is down') from e
        except (xmlrpc.client.Fault, SocketError) as e:
            raise RtorrentError(f'Failed to load from rtorrent SCGI: {e}') from e


    def set_file_priority(self, hash_id, file_index, priority):
        '''
        Set priority of a file in a torrent

        Params:
            hash_id (str):     download hash_id
            file_index (int):  position in download.files[] from get_torrents()
            priority (int):    0: skip, 1: normal, 2: high
        '''
        try:
            self.server.f.priority.set(f'{hash_id}:f{file_index}', priority)

        except ConnectionRefusedError as e:
            raise RtorrentError('Rtorrent is down') from e
        except (xmlrpc.client.Fault, SocketError) as e:
            raise RtorrentError(f'Failed to load from rtorrent SCGI: {e}') from e


    def get_file_priority(self, hash_id, file_index):
        '''
        Set priority of a file in a torrent

        Params:
            hash_id (str):     download hash_id
            file_index (int):  position in download.files[] from get_torrents()
        Returns:
            priority (int):    0: skip, 1: normal, 2: high
        '''
        try:
            return self.server.f.priority(f'{hash_id}:f{file_index}')

        except ConnectionRefusedError as e:
            raise RtorrentError('Rtorrent is down') from e
        except (xmlrpc.client.Fault, SocketError) as e:
            raise RtorrentError(f'Failed to load from rtorrent SCGI: {e}') from e


def format_size(size):
    if size <= 0:
        return '0B'

    i = int(math.floor(math.log(size, 1024)))
    s = round(size / math.pow(1024, i), 2)

    return '{}{}'.format(s, ('B', 'KB', 'MB', 'GB', 'TB', 'PB')[i])  # pylint: disable=consider-using-f-string


@click.group(name=PLUGIN_NAME[16:])
def cli():
    'F1 torrent downloader'

@cli.command
def last_run():
    'When was the last run?'
    state = load_state(logger, State, PLUGIN_NAME)
    print(f'Last run: {state.last_run}')

@cli.command
def found():
    'What races have we found already?'
    state = load_state(logger, State, PLUGIN_NAME)
    for race in state.races.values():
        added = 'added  ' if race.added_to_rtorrent is True else 'pending'
        print(f'{added} {race.title}')

@cli.command
def get_torrents():
    'Load the current torrents from rtorrent'
    rt = RTorrent(RTORRENT_HOST, 5000)
    try:
        torrents = rt.get_torrents()
        pretty.table(torrents.values(), columns=('progress', 'name'))
    except RtorrentError as e:
        logger.error(e)
