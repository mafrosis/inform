# vim: set ft=jinja:

DEBUG = {{ pillar['flask_debug'] }}
SECRET_KEY = '{{ pillar['secret_key'] }}'

# Celery config
BROKER_URL = 'sqla+sqlite:////srv/informa/informa.sqlitedb'
CELERY_DEFAULT_QUEUE = 'informa'
CELERY_DEFAULT_EXCHANGE = 'informa'
CELERY_DEFAULT_EXCHANGE_TYPE = 'direct'
CELERY_DEFAULT_ROUTING_KEY = 'informa'

{% if 'zapier_email_webhook_id' in pillar %}
ZAPIER_EMAIL_WEBHOOK_ID = '{{ pillar['zapier_email_webhook_id'] }}'
ZAPIER_EMAIL_HEARTBEAT = True
{% endif %}

SQLALCHEMY_DATABASE_URI = 'sqlite:////srv/informa/informa.sqlitedb'
