from config import settings

import logging


logging.basicConfig(
    level=logging.INFO, 
    format="%(asctime)s | %(levelname)s | %(message)s"
)

app_name = settings.APP_NAME.replace(' ', '')
logger = logging.getLogger(app_name+'Logger')