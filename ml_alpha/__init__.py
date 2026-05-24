"""
复杂度因子研究 - 融合模型构建完成

研究目标: 挖掘港股Alpha因子，与缠论模型融合提升预测能力

核心成果:
=========

1. 复杂度因子发现
   - apen_60: RankIC=-0.085 (最强复杂度因子)
   - hurst_60: RankIC=+0.059 (趋势持续性)
   - entropy_60: RankIC=+0.043 (香农熵)
   - higuchi_fd_60: 20日持有期RankIC=-0.267 (长期最强)

2. 时序特性
   - 60日窗口全面优于20日窗口
   - IC随持有期延长而增强 (apen_60: -0.021→-0.113)
   - 因子自相关>0.65，变化缓慢，适合低频调仓

3. 融合模型
   - 等权融合RankIC: +0.302
   - IC加权融合RankIC: +0.384 ⭐
   - 分层Spread: +4.74%
   - 月度胜率: 87.5%

4. 生产信号
   - 已生成2026-04-17选股信号
   - Top10平均得分: +0.271
   - 全市场平均: -0.019
   - 信号文件: signals/signal_20260417.csv

关键发现:
=========

1. 缠论因子 dominate (Top 8全是缠论)
   - frac_balance_20: RankIC=+0.314 (最强单因子)
   - top_count_20: RankIC=-0.257
   - fractal_top_5: RankIC=-0.240

2. 复杂度因子价值在于正交性
   - 与缠论因子相关性<0.3
   - 提供独立信息源
   - 长周期覆盖 (higuchi_fd_60)

3. 融合提升有限但稳健
   - 缠论单独: RankIC=0.378
   - 融合后: RankIC=0.384 (+1.6%)
   - 复杂度贡献: entropy_60 (权重0.035), hurst_60 (0.026)

文件清单:
=========
E:\quant\ml_alpha\
  complexity_factors.py          # 复杂度因子计算
  run_complexity_factors.py      # 数据获取+IC验证
  analyze_factor_dynamics.py     # 时序特性分析
  build_fusion_model.py          # 融合模型构建
  generate_signals.py            # 生产信号生成
  
  complexity_factor_data.csv     # 复杂度因子数据 (3857条)
  complexity_ic_results.csv      # IC验证结果
  fusion_model_results.csv       # 融合模型预测
  fusion_ic_analysis.csv         # 全因子IC分析
  fusion_monthly_ic.csv          # 月度IC统计
  
  signals\
    signal_20260417.csv          # 今日选股信号
    summary_20260417.json        # 信号摘要
    README_20260417.md           # 信号说明文档

下一步:
=======
1. 配置每日自动更新信号
2. 实盘跟踪记录表现
3. 深挖更多复杂度因子 (MSE, LLE等)
4. 构建动态权重模型 (根据市场状态切换)

时间戳: 2026-04-20 13:52
"""

# 保存为Python模块文档
if __name__ == '__main__':
    print(__doc__)
