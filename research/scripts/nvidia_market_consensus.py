# -*- coding: utf-8 -*-
"""
NVIDIA NIM Multi-Model Market Consensus Analyzer
=================================================
用法:
    python nvidia_market_consensus.py --market US
    python nvidia_market_consensus.py --market HK
    python nvidia_market_consensus.py --market A
    python nvidia_market_consensus.py --market CRYPTO
    python nvidia_market_consensus.py --market US --data '{"spy":"-1.2%","vix":"21.26"}'

工作流:
    1. 接收市场类型 + 可选实时数据
    2. 并行调集 Top 5 LLM (受 9 req/min 速率限制)
    3. 每个 LLM 输出: 情绪判断 + 核心风险 + 仓位建议 + 置信度(0-100)
    4. 共识聚合: 加权平均置信度 + 多数投票 + 分歧识别
    5. 输出结构化 JSON 报告
"""
import sys
import os
import json
import time
import argparse
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, r'C:\Users\RoyGoode\.workbuddy\skills\nvidia-api\scripts')
from nvidia_api import nvidia_llm
import config

# ============================================================
# Top 5 LLMs (速度+质量最优组合, 基于 2026-05-17 测速)
# ============================================================
TOP5_MODELS = [
    {"id": config.LLM_MODEL,                          "name": "QuantBot-Primary", "weight": 1.0, "avg_latency": 1.4},
    {"id": "qwen/qwen3-next-80b-a3b-instruct",       "name": "Qwen3-80B",    "weight": 0.95, "avg_latency": 6.5},
    {"id": "meta/llama-4-maverick-17b-128e-instruct", "name": "Llama4-17B",   "weight": 0.85, "avg_latency": 7.0},
    {"id": "meta/llama-3.3-70b-instruct",             "name": "Llama3.3-70B", "weight": 0.80, "avg_latency": 5.5},
    {"id": "mistralai/mistral-nemotron",               "name": "Nemotron",     "weight": 0.90, "avg_latency": 9.8},
]

# ============================================================
# 市场 Prompt 模板
# ============================================================
MARKET_TEMPLATES = {
    "US": {
        "label": "美股",
        "data_keys": ["spy", "qqq", "dia", "vix", "tnx", "dxy", "oil", "gold", "cpi", "fed"],
        "context": "US equity market (S&P 500, Nasdaq, Dow Jones)",
        "instruments": "SPY, QQQ, DIA, sector ETFs (XLK, XLE, XLF, XLV)",
    },
    "HK": {
        "label": "港股",
        "data_keys": ["hsi", "hscei", "hktech", "cny", "cnh", "northbound", "ah_premium"],
        "context": "Hong Kong stock market (Hang Seng Index, HSCEI, Hang Seng TECH)",
        "instruments": "00700.HK (腾讯), 09988.HK (阿里), 01810.HK (小米), 03690.HK (美团)",
    },
    "A": {
        "label": "A股",
        "data_keys": ["sh000001", "sz399001", "sz399006", "csi300", "csi500", "northbound", "margin", "pb"],
        "context": "China A-share market (Shanghai Composite, Shenzhen Component, ChiNext)",
        "instruments": "沪深300 ETF, 中证500 ETF, 北向资金, 融资融券余额",
    },
    "CRYPTO": {
        "label": "加密货币",
        "data_keys": ["btc", "eth", "sol", "xrp", "dominance", "funding", "oi", "liquidation"],
        "context": "Cryptocurrency market (Bitcoin, Ethereum, Solana)",
        "instruments": "BTC/USDT, ETH/USDT, SOL/USDT, 合约资金费率, 爆仓数据",
    },
}

def build_prompt(market: str, data: dict = None) -> str:
    """构建分析 prompt"""
    cfg = MARKET_TEMPLATES.get(market.upper())
    if not cfg:
        raise ValueError(f"Unknown market: {market}. Supported: US, HK, A, CRYPTO")

    data_section = ""
    if data:
        data_section = "\n## Real-time Data\n"
        for k, v in data.items():
            data_section += f"- {k.upper()}: {v}\n"
    else:
        data_section = f"""
## Default Data Template
Please use your latest knowledge of {cfg['label']} market conditions.
Key indicators to consider: {', '.join(cfg['data_keys'])}
Key instruments: {cfg['instruments']}
"""

    prompt = f"""You are a senior quantitative analyst specializing in {cfg['context']}.

Analyze the {cfg['label']} market based on the following data:
{data_section}
## Required Output (strict JSON, no markdown, no code block)
Return EXACTLY this JSON structure in Chinese:
{{
  "sentiment": "BULL|BEAR|NEUTRAL",
  "sentiment_score": <-100 to 100>,
  "confidence": <0 to 100>,
  "key_drivers": ["driver1", "driver2", "driver3"],
  "risks": ["risk1", "risk2"],
  "positioning": "具体仓位建议",
  "time_horizon": "short|medium|long",
  "signal": "BUY|SELL|HOLD",
  "signal_strength": <0 to 100>,
  "reasoning": "50字以内的核心逻辑"
}}

IMPORTANT: Return ONLY valid JSON. No markdown, no explanation, no code block wrapper."""

    return prompt


def parse_model_output(raw: str) -> dict:
    """解析模型输出的 JSON, 容错处理"""
    text = raw.strip()

    # Remove markdown code block wrappers
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:])
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3].rstrip()

    # Try to find JSON object
    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end])
        except json.JSONDecodeError:
            pass

    # Fallback: return raw text as reasoning
    return {
        "sentiment": "NEUTRAL",
        "sentiment_score": 0,
        "confidence": 30,
        "key_drivers": [],
        "risks": [],
        "positioning": "无法解析",
        "time_horizon": "short",
        "signal": "HOLD",
        "signal_strength": 0,
        "reasoning": raw[:200],
    }


def aggregate_consensus(results: list) -> dict:
    """聚合多模型共识"""
    valid = [r for r in results if r.get("parsed") and r["status"] == "OK"]
    if not valid:
        return {"error": "No valid model outputs"}

    # Weighted sentiment score
    total_weight = 0
    weighted_sentiment = 0
    weighted_confidence = 0
    weighted_signal = 0

    sentiment_votes = {"BULL": 0, "BEAR": 0, "NEUTRAL": 0}
    signal_votes = {"BUY": 0, "SELL": 0, "HOLD": 0}
    all_drivers = []
    all_risks = []
    model_details = []

    for r in valid:
        p = r["parsed"]
        w = r["model_weight"]

        weighted_sentiment += p.get("sentiment_score", 0) * w
        weighted_confidence += p.get("confidence", 50) * w
        weighted_signal += p.get("signal_strength", 50) * w
        total_weight += w

        s = p.get("sentiment", "NEUTRAL").upper()
        sentiment_votes[s] = sentiment_votes.get(s, 0) + 1

        sig = p.get("signal", "HOLD").upper()
        signal_votes[sig] = signal_votes.get(sig, 0) + 1

        all_drivers.extend(p.get("key_drivers", []))
        all_risks.extend(p.get("risks", []))

        model_details.append({
            "model": r["model_name"],
            "sentiment": p.get("sentiment"),
            "score": p.get("sentiment_score"),
            "confidence": p.get("confidence"),
            "signal": p.get("signal"),
            "reasoning": p.get("reasoning"),
            "latency_s": r["latency_s"],
        })

    # Normalize
    if total_weight > 0:
        avg_sentiment = weighted_sentiment / total_weight
        avg_confidence = weighted_confidence / total_weight
        avg_signal = weighted_signal / total_weight
    else:
        avg_sentiment = avg_confidence = avg_signal = 0

    # Majority vote
    consensus_sentiment = max(sentiment_votes, key=sentiment_votes.get)
    consensus_signal = max(signal_votes, key=signal_votes.get)
    agreement_pct = max(sentiment_votes.values()) / len(valid) * 100

    # Deduplicate drivers/risks (simple frequency sort)
    from collections import Counter
    top_drivers = [x for x, _ in Counter(all_drivers).most_common(5)]
    top_risks = [x for x, _ in Counter(all_risks).most_common(5)]

    # Disagreement flag
    disagreement = len(set(sentiment_votes.keys()) - {k for k, v in sentiment_votes.items() if v == 0}) > 1

    return {
        "consensus": {
            "sentiment": consensus_sentiment,
            "sentiment_score": round(avg_sentiment, 1),
            "signal": consensus_signal,
            "signal_strength": round(avg_signal, 1),
            "confidence": round(avg_confidence, 1),
            "agreement_pct": round(agreement_pct, 1),
            "disagreement": disagreement,
        },
        "votes": {
            "sentiment": sentiment_votes,
            "signal": signal_votes,
        },
        "top_drivers": top_drivers,
        "top_risks": top_risks,
        "model_details": model_details,
        "models_used": len(valid),
        "models_total": len(results),
    }


def run_consensus(market: str, data: dict = None) -> dict:
    """主执行流程"""
    print(f"\n{'='*60}")
    print(f"  NVIDIA NIM Multi-Model Consensus: {MARKET_TEMPLATES.get(market, {}).get('label', market)}")
    print(f"  Models: {len(TOP5_MODELS)} | Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}\n")

    prompt = build_prompt(market, data)
    results = []

    for i, model in enumerate(TOP5_MODELS, 1):
        model_id = model["id"]
        model_name = model["name"]

        print(f"[{i}/{len(TOP5_MODELS)}] {model_name} ({model_id})...", end=" ", flush=True)

        nvidia_llm.model = model_id
        start = time.time()

        try:
            raw = nvidia_llm.chat(prompt, max_tokens=800)
            latency = time.time() - start
            parsed = parse_model_output(raw)
            status = "OK"
            print(f"OK ({latency:.1f}s) sentiment={parsed.get('sentiment','?')} conf={parsed.get('confidence','?')}")
        except Exception as e:
            latency = time.time() - start
            parsed = None
            raw = str(e)
            status = f"FAIL"
            print(f"FAIL ({latency:.1f}s) {str(e)[:60]}")

        results.append({
            "model_id": model_id,
            "model_name": model_name,
            "model_weight": model["weight"],
            "status": status,
            "latency_s": round(latency, 2),
            "raw_output": raw,
            "parsed": parsed,
        })

    # Aggregate
    consensus = aggregate_consensus(results)
    consensus["market"] = market
    consensus["market_label"] = MARKET_TEMPLATES.get(market, {}).get("label", market)
    consensus["timestamp"] = datetime.now().isoformat()

    # Print summary
    c = consensus["consensus"]
    print(f"\n{'='*60}")
    print(f"  CONSENSUS RESULT")
    print(f"{'='*60}")
    print(f"  Sentiment:    {c['sentiment']} (score: {c['sentiment_score']})")
    print(f"  Signal:       {c['signal']} (strength: {c['signal_strength']})")
    print(f"  Confidence:   {c['confidence']}%")
    print(f"  Agreement:    {c['agreement_pct']}%")
    print(f"  Disagreement: {'YES' if c['disagreement'] else 'NO'}")
    print(f"  Votes:        {consensus['votes']['sentiment']}")
    print(f"  Top Drivers:  {consensus['top_drivers'][:3]}")
    print(f"  Top Risks:    {consensus['top_risks'][:3]}")
    print(f"{'='*60}\n")

    return consensus


def main():
    parser = argparse.ArgumentParser(description="NVIDIA NIM Multi-Model Market Consensus")
    parser.add_argument("--market", required=True, choices=["US", "HK", "A", "CRYPTO"],
                        help="Market type: US, HK, A, CRYPTO")
    parser.add_argument("--data", type=str, default=None,
                        help='JSON string of market data, e.g. \'{"spy":"-1.2%","vix":"21.26"}\'')
    parser.add_argument("--output", type=str, default=None,
                        help="Output JSON file path")
    args = parser.parse_args()

    data = None
    if args.data:
        try:
            data = json.loads(args.data)
        except json.JSONDecodeError:
            print(f"ERROR: --data must be valid JSON, got: {args.data}")
            sys.exit(1)

    result = run_consensus(args.market, data)

    # Save output
    if args.output:
        output_path = args.output
    else:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = os.path.join(r"E:\quant\research\output", f"consensus_{args.market}_{ts}.json")

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"Results saved: {output_path}")
    return result


if __name__ == "__main__":
    main()
