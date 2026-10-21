# Dota 2 异常账号分析与举报

一个 Codex 技能：通过 OpenDota 公开数据调查疑似故意掉分、炸鱼和操纵比赛，并生成有来源、可核验的中英文 Steam 举报信及 TXT 证据附件。

先分析证据，再决定结论。高胜率、连败或五排本身不等于违规；聊天归属、固定队伍和比赛表现需要逐项核验，最终处罚由 Valve 决定。

## 安装到 Codex

需要 Git；运行数据脚本需要 Python 3.10 或更高版本，仅使用标准库，无需 OpenDota API 密钥。

将仓库克隆到 Codex 的个人技能目录。Windows PowerShell 默认路径：

```powershell
git clone https://github.com/xsrkt26/dota2-account-report.git "$env:USERPROFILE/.codex/skills/dota2-account-report"
```

macOS / Linux 默认路径：

```sh
git clone https://github.com/xsrkt26/dota2-account-report.git ~/.codex/skills/dota2-account-report
```

如已自定义 `CODEX_HOME`，使用其下的 `skills/dota2-account-report` 目录。已有同名技能时先保留自己的修改，再更新文件。安装后重新启动 Codex 以加载技能。

## 使用示例

在 Codex 中输入，将方括号内容替换为实际信息：

```text
使用 $dota2-account-report 分析账号 [Dota Account ID 或 SteamID64]，
调查是否存在故意掉分、炸鱼或操纵比赛，并生成中英文 Steam 客服信及 TXT 证据附件。
我在 [比赛 ID] 中使用 [英雄]，是该玩家的对手。
```

也可以只要求分析、补充证据或修改既有文案。没有直接交手经历时省略最后一句。

技能会清洗公开战绩并说明覆盖范围，比较同一时期的英雄胜率与组队表现，核对聊天说话人和跨局队伍成员，保留反例及数据缺失说明。证据不足时明确指出，不自动提交举报。

## 独立运行数据脚本

在仓库目录运行，替换 `ACCOUNT_ID` 和比赛 ID：

```sh
python scripts/opendota_audit.py collect --account ACCOUNT_ID --out work/case
python scripts/opendota_audit.py analyze --history work/case/history.json --out work/case/analysis
python scripts/opendota_audit.py details --account ACCOUNT_ID --matches MATCH_ID_1 MATCH_ID_2 --out work/case
```

使用 `--help` 查看参数。`analyze` 支持 `--from`、`--until` 和 `--utc-offset`；默认时区为 UTC+8。`collect` 和 `details` 默认复用缓存，用 `--refresh` 刷新；每次新调查使用新目录。

脚本保存原始数据、请求来源与哈希，导出统计和文本证据；完整的中英文客服信由 Codex 根据技能说明起草。OpenDota 的公开覆盖、解析状态和限流会影响可用数据，脚本不能恢复私密战绩，也不能单凭统计证明脚本或主观意图。

## 文件说明

| 文件 | 用途 |
| --- | --- |
| [SKILL.md](SKILL.md) | 技能入口与工作流程 |
| [scripts/opendota_audit.py](scripts/opendota_audit.py) | 采集、清洗、统计和证据导出 |
| [references/analysis.md](references/analysis.md) | 分析口径与核验要求 |
| [references/reporting.md](references/reporting.md) | 中英文文案与附件规范 |
| [references/steam.md](references/steam.md) | Steam 官方入口、规则及附件注意事项 |
| [agents/openai.yaml](agents/openai.yaml) | Codex 技能显示信息 |

客服附件优先使用真正的 TXT 文件和原始截图；实际表单接受的格式与限制以当前页面为准。调查数据应留在本地，本仓库不包含真实玩家的举报材料。

## English overview

A Codex skill for investigating suspicious Dota 2 accounts using public OpenDota data. It cleans ranked match history, compares hero and party performance within consistent time windows, verifies chat attribution and recurring party rosters, and drafts concise English and Chinese Steam reports with traceable TXT evidence.

The bundled Python script uses only the standard library. It collects and summarizes evidence; it does not automatically determine misconduct or submit reports. Missing data, counterexamples, and alternative explanations must remain visible. Public statistics alone do not establish intentional losing, scripting, or account sharing.
