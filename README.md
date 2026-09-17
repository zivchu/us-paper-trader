# us-paper-trader

美股 1% 目标模拟盘 · 云端自动交易系统（模拟盘实验，非真实资金）

- 本金：¥12,000 ≈ $1,789.44（USDCNY 6.706）
- 目标：日收益 +1%（极激进的实验目标，全程如实记录）
- 架构：GitHub Actions 云端保底执行（cloud_engine.py，纯标准库零依赖）+ 本地 WorkBuddy AI 分析增强层
- 定时（UTC）：intraday 14:00 周一至五（北京时间 22:00，美股盘中）；close 21:20 周一至五（北京时间次日 05:20，收盘后）
- 数据源：腾讯行情（qt.gtimg.cn 实时报价）+ stockanalysis.com 日K + CNBC 报价交叉验证
- 策略：momentum_breakout v1（见 config.json）——出场：止损 -6% / 止盈 +9% / 时间止损 5 日 / 移动止盈 5%；入场：20日新高 + 5日动量 + RSI + 量比
- 风控：单仓 ≤35%、最多 4 仓、现金 ≥5%、净值回撤 >20% 触发 kill-switch 强制清仓
- 状态文件：state.json / history.csv / trades.csv / strategy_log.json（本仓库为唯一事实源；本地副本运行时先同步）
- 报告：reports/YYYY-MM-DD_decision.html（盘中决策）与 reports/YYYY-MM-DD_pnl.html（收盘收益）；收盘后自动创建 GitHub Issue 摘要

⚠️ 模拟盘实验，仅供学习研究，不构成任何投资建议。
