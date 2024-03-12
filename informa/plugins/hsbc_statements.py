import io
import logging
import os
import tempfile
from dataclasses import dataclass

import click
import googleapiclient
from google.oauth2.service_account import Credentials

from gmsa import Gmail
from gmsa.exceptions import AttachmentSaveError
from informa.lib import PluginAdapter, StateBase, app, mailgun
from informa.lib.plugin import load_run_persist
from transto import hsbc
from transto.exceptions import MissingEnvVar

logger = PluginAdapter(logging.getLogger('informa'))


@dataclass
class State(StateBase):
    pass


@app.task('every 24 hours', name=__name__)
def run():
    load_run_persist(logger, State, main)


def main(_):
    msg = check_for_email()

    if msg and process_statement(msg):
        logger.info('Processed statement dated %s', msg.date)
        msg.mark_as_read()
    else:
        logger.info('No unread messages')


def check_for_email():
    "Fetch unread email with HSBC label"
    try:
        gmail = Gmail(
            credentials=Credentials.from_service_account_file(
                os.environ.get('GSUITE_OAUTH_CREDS'),
                scopes=['https://www.googleapis.com/auth/gmail.modify'],
                subject='m@mafro.net',
            )
        )
    except googleapiclient.errors.HttpError:
        logger.error('Failed to authenticate to Google Calendar API')
        return None

    # Fetch unread messages from HSBC/Statements
    msgs = gmail.get_messages(labels=['Label_8304695994499497474', 'UNREAD'])

    # Filter for messages with Email Statement.pdf attachment
    msgs = [m for m in msgs if m.has_attachments() and m.attachments[0].filename == 'Email Statement.pdf']
    logger.debug('Found %d messages', len(msgs))

    return msgs[-1] if len(msgs) >= 1 else None


def process_statement(msg) -> bool:
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            logger.debug('Email has %s attachments', len(msg.attachments))

            fn = os.path.join(tmpdir, msg.attachments[0].filename)

            # Save the PDF statement
            msg.attachments[0].save(filepath=fn, overwrite=True)

            logger.debug('Saved attachment %s', msg.attachments[0].filename)

            # Run transto, capturing logs
            log_stream = _capture_transto_logging()

            with open(fn, 'rb') as f:
                hsbc.cc(f)

            # Send transto logs as an email
            mailgun.send(logger, 'HSBC Statement imported', content=log_stream)

            # Mark email as read
            msg.mark_as_read()

    except (AttachmentSaveError, OSError, MissingEnvVar) as e:
        logger.error(e)
        mailgun.send(logger, 'HSBC Statement imported failed!', content=str(e))
        return False
    else:
        return True


def _capture_transto_logging() -> io.StringIO:
    'Add a handler to capture logging from transto'
    transto_logger = logging.getLogger('transto')
    log_stream = io.StringIO()
    log_handler = logging.StreamHandler(log_stream)
    log_handler.setLevel(logging.INFO)
    transto_logger.addHandler(log_handler)
    return log_stream


@click.group(name=__name__[16:].replace('_', '-'))
def cli():
    'HSBC statement loader'
