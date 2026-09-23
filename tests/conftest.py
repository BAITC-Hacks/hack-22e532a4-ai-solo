import os
import tempfile
from pathlib import Path

# Imports of app.main in tests must never initialize the user's working database.
_state = tempfile.TemporaryDirectory(prefix='baqbaq-tests-')
os.environ['BAQBAQ_DB_PATH'] = str(Path(_state.name) / 'test.db')
os.environ['BAQBAQ_UPLOAD_DIR'] = str(Path(_state.name) / 'uploads')
os.environ['BAQBAQ_MODEL_MODE'] = 'offline'
os.environ['BAQBAQ_SEARCH_MODE'] = 'lexical'
os.environ['BAQBAQ_AUTH_PASSWORD_HASH'] = ''
os.environ['BAQBAQ_AUTH_SECRET'] = ''
os.environ['BAQBAQ_PUBLIC_LIVE'] = '0'
