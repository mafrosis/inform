from fastapi import APIRouter
from fastapi.middleware.cors import CORSMiddleware

from informa.lib import app as app_rocketry
from informa.lib import fastapi as app_fastapi

# CORS support for React frontends
app_fastapi.add_middleware(
    CORSMiddleware,
    allow_origins=['https://informa.mafro.net', 'http://localhost:3000'],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)


######
# Session Config API
#
router_config = APIRouter(tags=['config'])

@router_config.get('/session/config')
def get_session_config():
    return {
        'execution': app_rocketry.session.config.execution,
        'timezone': str(app_rocketry.session.config.timezone),
        'cycle_sleep': app_rocketry.session.config.cycle_sleep,
        'task_priority': app_rocketry.session.config.task_priority,
        'timeout': app_rocketry.session.config.cycle_sleep,
        'restarting': app_rocketry.session.config.restarting,
        'instant_shutdown': app_rocketry.session.config.instant_shutdown,
        'max_process_count': app_rocketry.session.config.max_process_count,
        'multilaunch': app_rocketry.session.config.multilaunch,
        'debug': app_rocketry.session.config.debug,
    }

@router_config.patch('/session/config')
def patch_session_config(values: dict):
    for key, val in values.items():
        setattr(app_rocketry.session.config, key, val)


app_fastapi.include_router(router_config)
