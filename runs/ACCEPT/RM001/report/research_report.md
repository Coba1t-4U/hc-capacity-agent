# 主配微协同优化研究报告

研究号：STUDY-20261016-230654
生成时间：2026-10-16T23:06:55+08:00

## 1 研究范围与控制模式

- 研究范围模式：dm_equivalent
- 控制模式：atc
- 电气计算全部由 OpenDSS 完成；数学优化器仅生成候选调度，不替代电气仿真
- 暂不覆盖：暂态稳定、保护配合、谐波、频率全过程和现场控制闭环
- 上级网络为批准的主配接口限额与 OpenDSS 电源等值；本结论不构成完整主网优化结论

## 2 区域与接口

- 区域数：5
- 接口数：4

## 3 容量边界结果

- 最大可行 λ：1.40625 MW
- 可行点 / 不可行点 / unknown：3 / 6 / 0
- 总成本：0.0 元

## 4 一致性检查

- 非预见性：通过
- 储能能量守恒：通过

## 5 输出文件

- `results/capacity_frontier.csv`
- `results/interface_trace.csv`
- `results/probability_trace.csv`
- `results/dispatch.csv`
- `results/research_metrics.csv`

> 仅已验证点标记为 feasible；unknown 不计为不可行，也不计为容量不可行。