# 文档索引

| 目录 | 用途 |
| --- | --- |
| [`technical-stack.md`](technical-stack.md) | 冻结的技术栈、版本、边界和架构决策 |
| [`product-design.md`](product-design.md) | 已评审的产品流程、AgentTeams、信息架构与验收 |
| [`architecture/`](architecture/README.md) | 系统分层、组件和运行时架构 |
| [`adr/`](adr/README.md) | 重要技术决策及其背景、取舍和后果 |
| [`api/`](api/README.md) | 唯一 `result` 接口的请求、事件和响应契约 |
| [`evaluation/`](evaluation/README.md) | 单智能体 vs 多智能体评测方案与结果 |
| [`deployment/`](deployment/README.md) | 单机部署、容器与 AgentArts 适配说明 |
| [`verification-report.md`](verification-report.md) | 质量门禁、授权联调、复现与环境限制记录 |
| [`demo-checklist.md`](demo-checklist.md) | 参赛现场演示顺序和逐项确认清单 |

## 文档约定

- 文档首部标明状态、最后更新日期和关键词。
- 经评审的架构变更先写 ADR，再更新技术栈文档。
- 评测结果保留环境、模型、数据集版本和随机种子，以便复现。

- [报告反馈 Demo](reporting-feedback-demo.md)：两 API、本地 CLI、独立目录复现。
- [反馈闭环验收](reporting-feedback-verification.md)：本轮测试、真实产物与边界。
