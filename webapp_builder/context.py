"""
Context data for builder templates (platform choices, toggles, permissions).
Import and pass to views instead of hardcoding in templates.
"""

PLATFORM_CHOICES = [
    ('android', 'Android', 'bi-android2', 'text-green-400'),
    ('ios',     'iOS',     'bi-apple',    'text-gray-300'),
    ('both',    'Beides',  'bi-phone',    'text-violet-400'),
]

DISPLAY_TOGGLES = [
    ('fullscreen',     'Vollbild / Fullscreen',             False),
    ('allow_zoom',     'Zoom erlauben / Allow zoom',        True),
    ('loading_spinner','Lade-Spinner / Loading spinner',    True),
    ('nav_bar',        'Navigation-Bar / Navigation bar',   False),
]

ADV_TOGGLES = [
    ('enable_js',            'JavaScript aktivieren / Enable JS',              True),
    ('enable_cookies',       'Cookies erlauben / Allow cookies',               True),
    ('enable_local_storage', 'LocalStorage aktivieren / Enable LocalStorage',  True),
    ('pull_to_refresh',      'Pull-to-Refresh',                                True),
    ('offline_page',         'Offline-Seite / Offline page',                   False),
]

PERMISSIONS = [
    ('camera',       'Kamera / Camera',       'bi-camera',         'Fotos & Videos aufnehmen'),
    ('location',     'Standort / Location',   'bi-geo-alt',        'GPS-Standort der Nutzer'),
    ('microphone',   'Mikrofon / Microphone', 'bi-mic',            'Audio aufnehmen'),
    ('notifications','Push-Benachrichtigungen', 'bi-bell',         'Push-Notifications senden'),
    ('storage',      'Speicher / Storage',    'bi-hdd',            'Dateien lesen & schreiben'),
]
