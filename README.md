# Jev Model Router

用 [TypeSafe Jev](https://typesafe.ai) 为 Codex 执行子代理选择模型与推理强度，减少每个任务都要手动选模型的负担。

[English](README.en.md) · [技能指令](SKILL.md) · [MIT License](LICENSE)

父会话负责理解需求和最终整合，Jev 只推荐执行子代理的配置。它不能切换已经运行的父会话模型，也不会自动授予执行权限。

## 工作方式

```text
用户任务 → 父会话提炼最小任务摘要 → Jev 推荐档位
                                    ↓
                             本地策略校验
                            ↙           ↘
                    采用推荐             保留当前模型
                    创建执行子代理       继续完成任务
```

- 只有非轻量执行任务才路由；普通聊天、状态查询和用户已指定模型的任务跳过。
- Python 3.10+，仅使用标准库，无第三方运行依赖。
- 路由脚本只输出一个 JSON 决策，不直接创建子代理。Codex 按 `SKILL.md` 执行后续委派。
- API 不可用、超时、输入或响应不合法时，调用方保留当前模型继续任务。
- 本仓库在已有本地技能基础上整理，新增可移植说明、离线测试和 CI，并补充输入校验、错误脱敏及重定向保护。

## 安装

前提：已有支持 skills、子代理和所选模型的 Codex 环境，以及 TypeSafe API key。

```bash
mkdir -p "${CODEX_HOME:-$HOME/.codex}/skills"
git clone https://github.com/mrhsiung2015/jev-model-router.git \
  "${CODEX_HOME:-$HOME/.codex}/skills/jev-model-router"
```

目录已存在时，先检查其来源并自行备份；不要直接覆盖本地修改。安装后开启一个新 Codex 会话，让技能被发现。

脚本从环境变量 `TYPESAFE_API_KEY`（优先）或 `JEV_API_KEY` 读取密钥。可由密钥管理器注入；也可在当前终端临时输入，避免把真实密钥写进命令历史：

```bash
read -r -s TYPESAFE_API_KEY
export TYPESAFE_API_KEY
```

运行第一行后输入密钥并回车。此设置仅对当前终端及其后续子进程有效；已启动的桌面应用不会自动获得它。请按所用客户端的环境变量配置方式注入，勿提交密钥。脚本不会自动加载 `.env`。

## 使用

在 Codex 中明确调用：

```text
使用 $jev-model-router 选择执行子代理，然后完成这个任务：……
```

需要全局自动路由时，把 [examples/AGENTS.md](examples/AGENTS.md) 中的规则合并到自己的全局或项目 `AGENTS.md`，保留已有规则。无需修改 Codex 的默认模型配置。

直接运行脚本也可以验证策略。以下命令完全离线，不需要密钥、不发送网络请求：

```bash
printf '%s\n' '{"task":"Implement a contained feature","current_model":"gpt-6-luna","current_reasoning_effort":"low","risk":"normal","expected_scope":"contained"}' \
  | python3 scripts/route.py --offline-choice balanced --offline-confidence 0.92
```

示例输出（模拟决策，不代表线上 Jev 的真实推荐）：

```json
{"status":"routed","tier":"balanced","model":"gpt-6-luna","reasoning_effort":"medium","confidence":0.92,"probabilities":{"balanced":1.0},"policy":["jev_recommendation"]}
```

真实请求：删除两个 `--offline-*` 参数即可。**在受限 Codex 环境里，必须仅将路由脚本进程通过权限工具运行在沙箱外**，任务 JSON 经 stdin 输入，或从权限安全的临时文件读取。终端管道示例不会自行解除沙箱。后续子代理、Git 和项目命令仍遵循各自权限边界。

## 路由策略

| 档位 | 默认执行模型 | 推理强度 |
| --- | --- | --- |
| Fast | `gpt-6-luna` | `low` |
| Balanced | `gpt-6-luna` | `medium` |
| Strong | `gpt-6-sol` | `high` |
| Long | `gpt-6-astra` | `max` |

这些是原使用环境中的模型 ID，并非通用模型可用性承诺。使用前确认自己的执行工具支持对应 ID 和推理强度；不支持时保留当前模型并报告原因。

| 条件 | 行为 |
| --- | --- |
| 置信度 ≥ 0.80 | 接受推荐，仍应用风险下限和 Long 开关 |
| 0.60 ≤ 置信度 < 0.80 | 当前档位可识别时，允许升级或同档，拒绝降级 |
| 置信度 < 0.60，普通任务 | 保留当前模型 |
| 高风险任务收到合法推荐 | 最低 Strong；低置信度也应用此下限 |
| Long 未启用 | Long 推荐转为 Strong |
| API / 网络 / 响应错误 | 输出错误，调用方保留当前模型，包括高风险任务 |

`risk` 必须由调用方正确判断：安全敏感、破坏性操作、生产变更、部署和数据库迁移使用 `high`。高风险下限只作用于合法推荐，不能在 API 故障时保证升级。

Fast 和 Balanced 共用模型 ID，因此应提供 `current_reasoning_effort`。缺少或无法匹配推理强度时，脚本使用该模型匹配的最高档位，避免误降级。当前模型未知或不在配置中时，脚本无法比较升降级；调用方应提供准确上下文并审核中置信度推荐。

## 输入、输出与配置

stdin 必须是一个 JSON 对象：

| 字段 | 要求 |
| --- | --- |
| `task` | 必填，非空任务摘要 |
| `current_model` | 可选字符串，仅供本地策略比较 |
| `current_reasoning_effort` | 可选字符串，仅供本地策略比较 |
| `risk` | `low` / `normal` / `high`，默认 `normal` |
| `expected_scope` | `single-step` / `contained` / `multi-step` / `long-running`，默认 `contained` |

stdout 状态：`routed` 采用推荐；`keep_current` 策略拒绝推荐；`error` 请求或输入错误。前两者退出码为 0，错误为 2。调用方应解析 `status`，不能仅看退出码。CLI 参数语法错误由 argparse 输出到 stderr。

| 配置 | 默认值 / 用途 |
| --- | --- |
| `TYPESAFE_API_KEY` / `JEV_API_KEY` | 必需的 API key；离线模式除外 |
| `JEV_MODEL` | `jev-latest`，可固定 Jev 版本 |
| `JEV_ROUTER_ALLOW_LONG` | 仅设为 `1` 才允许 Long |
| `JEV_CODEX_FAST_MODEL` | 覆盖 Fast 模型 |
| `JEV_CODEX_BALANCED_MODEL` | 覆盖 Balanced 模型 |
| `JEV_CODEX_STRONG_MODEL` | 覆盖 Strong 模型 |
| `JEV_CODEX_LONG_MODEL` | 覆盖 Long 模型 |
| `--timeout` | 默认 10 秒，必须为正有限数 |

模型覆盖值仅接受 `gpt-6-luna`、`gpt-6-sol`、`gpt-6-astra`；其他值回退到该档默认值。覆盖模型不改变档位对应的推理强度。

## 隐私与权限

实际发送到 `https://api.typesafe.ai/v1/systemone` 的任务状态只有 `task`、`risk` 和 `expected_scope`，另包含固定的档位说明和 Jev 模型名。当前模型、当前推理强度和输入中的其他字段不发送给 Jev。

任务摘要会离开本机；调用方负责排除密钥、源码、个人信息和无关对话。脚本不写入任务、响应或密钥文件，不输出密钥，拒绝 HTTP 重定向，并只返回结构化决策或脱敏错误。终端、客户端或服务提供方可能另有日志或保留策略；本仓库不对外部服务的数据处理作承诺。

路由建议不等于执行授权。沙箱外例外仅适用于路由 API 进程，不扩展到执行子代理或后续命令。

## 测试与贡献

```bash
python3 -m unittest discover -s tests -v
```

测试使用模拟 HTTP 响应及离线 CLI，覆盖策略边界、输入/响应校验、密钥不回显、请求字段最小化、网络回退和重定向保护，不需要真实 key 或访问 TypeSafe。GitHub Actions 在 Python 3.10、3.12、3.14 上运行同一测试套件。

欢迎提交 issue 或 PR。修改策略时请补充相应行为测试；不要提交真实任务摘要、密钥或私有工作区资料。

MIT 许可仅覆盖本仓库内容。Jev / TypeSafe 是外部服务，其条款和商标权利属于各自权利人；本项目为独立集成。
