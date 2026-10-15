import os
from datetime import timedelta, timezone

# Secrets and settings come from environment variables (Railway -> Variables).
BOT_TOKEN = os.getenv("BOT_TOKEN", "PUT_YOUR_BOT_TOKEN_HERE")
ADMIN_ID = int(os.getenv("ADMIN_ID", "8206906617"))
DATABASE_URL = os.getenv("DATABASE_URL", "")

# Elo rules
START_ELO = 1000
ELO_STEP = 25            # win/lose when score gap <= BIG_GAP
ELO_STEP_BIG = 28        # win/lose when score gap > BIG_GAP
BIG_GAP = 1.0

# Matchmaking
MATCH_RANGE = 200        # initial +-range
MATCH_RANGE_STEP = 100   # widen by this much...
MATCH_RANGE_EVERY = 30   # ...every N seconds of waiting
MATCH_TIMEOUT = 300      # give up after N seconds

# Photos
PHOTOS_PER_BATTLE = 3

# Local time of the college (UTC+5), used for "top of the day/week"
LOCAL_TZ = timezone(timedelta(hours=5))

# Menu pictures: Telegram file_id or a DIRECT image link (https://i.ibb.co/...).
# Empty value = the screen is shown as plain text.
PHOTOS = {
    "welcome": "https://ibb.co.com/zHbGvmGQ",
    "main": "https://ibb.co.com/w8tPtr8",
    "battle": "https://ibb.co.com/YgKxMrL",
    "top": "https://ibb.co.com/B2vQ9tLz",
    "profile": "https://ibb.co.com/VYfRjgrr",
    "rules": "https://ibb.co.com/wFFS5xjM",
    "admin": "https://ibb.co.com/vvqXwjC5",
}
