# -*- coding: utf-8 -*-
import os, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
if not os.environ.get('TICKFLOW_API_KEY'):
    raise RuntimeError('TICKFLOW_API_KEY environment variable is required')

import tickflow

print(dir(tickflow.client)[:30])
# Check resources
from tickflow.resources.kline import KlineResource
print("KlineResource methods:", [m for m in dir(KlineResource) if not m.startswith('_')])
