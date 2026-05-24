# -*- coding: utf-8 -*-
import os, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
os.environ['TICKFLOW_API_KEY'] = 'REDACTED_NAMED_CREDENTIAL'
import tickflow

print(dir(tickflow.client)[:30])
# Check resources
from tickflow.resources.kline import KlineResource
print("KlineResource methods:", [m for m in dir(KlineResource) if not m.startswith('_')])