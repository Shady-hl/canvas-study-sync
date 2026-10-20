"""Stop early when this computer cannot build complete PDF/timetable context."""
import sys

try:
    import pypdf
    from zoneinfo import ZoneInfo
    ZoneInfo('Europe/Oslo')
except (ImportError, KeyError):
    print('This Python installation needs the PDF reader/timezone data.')
    setup = 'SETUP_THIS_PC.cmd' if sys.platform == 'win32' else 'SETUP_THIS_PC.command'
    print('Run ' + setup + ' once, then repeat your update.')
    sys.exit(1)
from study_setup import default_archive
print('Archive: ' + str(default_archive(__file__)))
