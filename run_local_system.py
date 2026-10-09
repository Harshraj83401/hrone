"""Optional desktop launcher. All API implementation remains in app/main.py."""
import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / '.env', override=False)


def find_mongod():
    configured = os.getenv('MONGOD_PATH')
    if configured:
        binary = Path(configured)
        if not binary.is_file():
            raise RuntimeError('MONGOD_PATH does not point to an existing mongod executable.')
        return binary
    found = shutil.which('mongod')
    if found:
        return Path(found)
    if os.name == 'nt':
        installed = Path(os.getenv('ProgramFiles', 'C:/Program Files')) / 'MongoDB' / 'Server'
        candidates = sorted(installed.glob('*/bin/mongod.exe'), reverse=True)
        if candidates:
            return candidates[0]
    raise RuntimeError('Install MongoDB 6.0+ and add mongod to PATH, set MONGOD_PATH, or set MONGO_URI for an existing server.')


def main():
    parser = argparse.ArgumentParser(description='Start the local attendance API and open its docs.')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--mongo-port', type=int, default=27023)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    url = f'http://{args.host}:{args.port}'
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def healthy():
        try:
            with opener.open(url + '/health', timeout=2) as response:
                if response.status != 200 or json.load(response) != {'status': 'ok'}:
                    return False
            with opener.open(url + '/openapi.json', timeout=2) as response:
                return json.load(response)['info']['title'] == 'Employee Attendance & Analytics API'
        except (OSError, ValueError, KeyError):
            return False

    def listening(port):
        with socket.socket() as connection:
            connection.settimeout(1)
            return connection.connect_ex((args.host, port)) == 0

    if healthy():
        print('The attendance API is already running:', url + '/docs', flush=True)
        if not args.no_browser:
            webbrowser.open(url + '/docs')
        return
    if listening(args.port):
        raise RuntimeError(f'Port {args.port} is occupied. Close the other server or choose --port.')

    env = dict(os.environ)
    mongo = server = None
    log = None
    try:
        if not env.get('MONGO_URI'):
            if listening(args.mongo_port):
                raise RuntimeError(f'MongoDB port {args.mongo_port} is occupied. Set MONGO_URI to use that server, or choose --mongo-port.')
            binary = find_mongod()
            directory = ROOT / '.local-data' / f'mongodb-{args.mongo_port}'
            directory.mkdir(parents=True, exist_ok=True)
            log = directory.parent / f'mongodb-{args.mongo_port}.log'
            mongo = subprocess.Popen([str(binary), '--dbpath', str(directory),
                '--bind_ip', args.host, '--port', str(args.mongo_port), '--logpath', str(log)],
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            deadline = time.monotonic() + 20
            while not listening(args.mongo_port):
                if mongo.poll() is not None:
                    raise RuntimeError(f'MongoDB failed to start. See {log}.')
                if time.monotonic() >= deadline:
                    raise RuntimeError(f'MongoDB did not start within 20 seconds. See {log}.')
                time.sleep(0.25)
            env['MONGO_URI'] = f'mongodb://{args.host}:{args.mongo_port}'
        env.setdefault('MONGO_DB', 'attendance_db')
        server = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app.main:app',
            '--host', args.host, '--port', str(args.port)], cwd=ROOT, env=env)
        deadline = time.monotonic() + 20
        while not healthy():
            if server.poll() is not None:
                raise RuntimeError('The API failed to start; see the error above.')
            if time.monotonic() >= deadline:
                raise RuntimeError('The API did not become healthy. Check MONGO_URI and the MongoDB log.')
            time.sleep(0.25)
        print('\nRUNNING: ' + url + '/docs', flush=True)
        print('MongoDB is ready. The API is ready. Keep this window open.', flush=True)
        print('Press Ctrl+C here to stop the services started by this launcher.\n', flush=True)
        if not args.no_browser:
            webbrowser.open(url + '/docs')
        server.wait()
    except KeyboardInterrupt:
        print('\nStopping the local services...', flush=True)
    finally:
        if server is not None and server.poll() is None:
            server.terminate()
            server.wait(timeout=10)
        if mongo is not None and mongo.poll() is None:
            from pymongo import MongoClient
            connection = MongoClient(env['MONGO_URI'], serverSelectionTimeoutMS=2000)
            try:
                try:
                    connection.admin.command({'shutdown': 1})
                except Exception:
                    pass  # A clean shutdown closes the connection before replying.
                mongo.wait(timeout=10)
            except Exception:
                mongo.terminate()
                mongo.wait(timeout=10)
            finally:
                connection.close()


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('\nSTARTUP ERROR:', error, flush=True)
        raise SystemExit(1)
