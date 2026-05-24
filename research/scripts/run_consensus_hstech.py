#!/usr/bin/env python3
"""
恒生科技指数成分股多模型共识分析
使用NVIDIA NIM API分析30只成分股
"""
import sys
import os
import json
sys.path.insert(0, r'E:\quant\research\scripts')
from nvidia_market_consensus import run_consensus

# 恒生科技指数成分股数据（来自Futu API快照）
hstech_data = {
    "index": "恒生科技指数 (HSTECH)",
    "index_level": "4842.11 (-2.00%)",
    "analysis_time": "2026-05-18 11:03",
    "constituents_count": 30,
    "market_sentiment": "整体承压，科技股多数下跌",
    
    # 权重股数据（按市值排序）
    "top_weighted_stocks": [
        {
            "code": "HK.09988",
            "name": "阿里巴巴-W",
            "price": 129.6,
            "change_pct": -2.04,
            "pe_ratio": 20.736,
            "pb_ratio": 2.07,
            "market_cap_billion": 2487.3,
            "turnover_rate": 0.183,
            "volume_ratio": 1.229
        },
        {
            "code": "HK.00700",
            "name": "腾讯控股",
            "price": 396.2,
            "change_pct": -1.49,
            "pe_ratio": 23.91,
            "pb_ratio": 6.06,
            "market_cap_billion": 3682.0,
            "turnover_rate": 0.067,
            "volume_ratio": 0.889
        },
        {
            "code": "HK.01810",
            "name": "小米集团-W",
            "price": 38.0,
            "change_pct": -3.06,
            "pe_ratio": 22.61,
            "pb_ratio": 4.70,
            "market_cap_billion": 948.9,
            "turnover_rate": 0.145,
            "volume_ratio": 0.892
        },
        {
            "code": "HK.03690",
            "name": "美团-W",
            "price": 136.4,
            "change_pct": -2.43,
            "pe_ratio": 34.06,
            "pb_ratio": 6.66,
            "market_cap_billion": 829.8,
            "turnover_rate": 0.109,
            "volume_ratio": 0.808
        },
        {
            "code": "HK.09618",
            "name": "京东集团-SW",
            "price": 132.9,
            "change_pct": -2.63,
            "pe_ratio": 9.37,
            "pb_ratio": 1.53,
            "market_cap_billion": 391.4,
            "turnover_rate": 0.118,
            "volume_ratio": 1.099
        }
    ],
    
    # 板块表现
    "sector_performance": {
        "互联网平台": "普遍下跌，京东(-2.63%)、快手(-1.76%)、美团(-2.43%)",
        "新能源汽车": "理想(-3.37%)、小鹏(-2.52%)、零跑(-3.32%)",
        "半导体": "中芯国际(-0.40%)、华虹半导体(-2.40%)",
        "消费电子": "小米(-3.06%)、比亚迪电子(-3.39%)",
        "云计算/AI": "百度(-0.59%)、金山软件(-1.79%)"
    },
    
    # 资金流向
    "capital_flow": {
        "northbound": "净流出45亿元（5月第二周）",
        "ah_premium": "118.1（下降）",
        "cny_usd": "7.2495"
    },
    
    # 关键事件
    "key_events": [
        "阿里巴巴财报后调整，AI业务增长强劲",
        "北向资金持续流出，外资情绪谨慎",
        "恒生科技指数跌2%，领跌港股",
        "新能源汽车板块集体回调"
    ]
}

if __name__ == "__main__":
    result = run_consensus("HK", hstech_data)
    output_file = r'E:\quant\research\output\consensus_HSTECH_20260518.json'
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"共识分析结果已保存至: {output_file}")