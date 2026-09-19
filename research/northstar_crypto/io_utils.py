import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + uuid4().hex + '.tmp')
    temp.write_text(canonical(value) + '\n', encoding='utf-8')
    os.replace(temp, path)


def verify_vendor():
    folder = Path(__file__).parent / 'vendor'
    manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
    for row in manifest:
        if hashlib.sha256((folder / row['file']).read_bytes()).hexdigest() != row['sha256']:
            raise ValueError('Pinned Northstar source changed: ' + row['file'])
    return digest(manifest)
