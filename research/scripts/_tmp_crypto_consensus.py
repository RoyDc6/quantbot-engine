# -*- coding: utf-8 -*-
import sys, os
sys.path.insert(0, r'C:\Users\RoyGoode\.workbuddy\skills\nvidia-api\scripts')
os.chdir(r'E:\quant\research')
sys.path.insert(0, r'E:\quant\research\scripts')
from nvidia_market_consensus import run_consensus
import json

data = {
    "btc": "$78,337 (24h +0.26%, 7d -2.89%)",
    "eth": "$2,189 (24h +0.49%, 7d -5.89%)",
    "sol": "$86.83 (24h +0.71%, 7d -6.77%)",
    "xrp": "$1.42 (24h +0.75%, 7d +0.13%)",
    "doge": "$0.1113 (24h +1.89%, 7d +2.42%)",
    "total_market_cap": "$2.586T",
    "btc_dominance": "60.5%",
    "eth_dominance": "10.4%",
    "volume_24h": "$66.5B (down 43.75%)",
    "market_cap_change": "-$40.6B (-1.54%)",
    "sector_top": "DeFAI +11.83%, Recruitment +11.85%",
    "sector_bottom": "TON -19.88% weekly, ZEC -13.87% weekly",
    "notable": "BTC dominance strong 60.5%, exchange reserves 7-year low, BlackRock holds $62B BTC"
}

result = run_consensus("CRYPTO", data)
output_path = r'E:\quant\research\output\consensus_CRYPTO_20260517.json'
with open(output_path, 'w', encoding='utf-8') as f:
    json.dump(result, f, ensure_ascii=False, indent=2)
print(f"\nSaved: {output_path}")
