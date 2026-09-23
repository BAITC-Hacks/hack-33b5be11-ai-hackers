"""Launch the supported web app; optionally ask for a key without saving it."""
import argparse
import getpass
import importlib.util
import os
import runpy
from pathlib import Path

if __name__ == '__main__':
    parser=argparse.ArgumentParser(description='Career Quest web launcher')
    parser.add_argument('--ask-key',action='store_true',help='Read an API key invisibly; keep only in process memory')
    parser.add_argument('--port',type=int,default=int(os.environ.get('PORT','8000')))
    args=parser.parse_args()
    if args.ask_key:
        if importlib.util.find_spec('openai') is None:
            parser.error('Install dependencies in this Python environment: python -m pip install -r requirements.txt')
        key=getpass.getpass('OpenAI API key (hidden, not saved): ').strip()
        if not key: parser.error('Empty key')
        os.environ['OPENAI_API_KEY']=key
    os.environ['PORT']=str(args.port)
    runpy.run_path(str(Path(__file__).resolve().parent/'server.py'),run_name='__main__')
