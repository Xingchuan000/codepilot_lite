# CodePilot Issue Workflow Manual Acceptance

本文档用于手动验收第十步 issue workflow 的主链路，确保输入 issue 后能稳定生成全部产物，且不会越界执行 `git commit`、`git push` 或创建 PR。

## 1. 创建演示仓库

```bash
rm -rf /tmp/codepilot-issue-demo
mkdir -p /tmp/codepilot-issue-demo/src /tmp/codepilot-issue-demo/tests
```

写入 `src/calc.py`：

```bash
cat >/tmp/codepilot-issue-demo/src/calc.py <<'EOF'
def add(a, b):
    return a - b
EOF
```

写入 `tests/test_calc.py`：

```bash
cat >/tmp/codepilot-issue-demo/tests/test_calc.py <<'EOF'
from src.calc import add


def test_add():
    assert add(1, 2) == 3
EOF
```

初始化 git 仓库并提交基线：

```bash
cd /tmp/codepilot-issue-demo
git init
git config user.email demo@example.com
git config user.name Demo
git add .
git commit -m "init"
```

## 2. 运行 issue workflow

在项目根目录执行：

```bash
PYTHONPATH=src python -m codepilot.cli issue \
  --issue-file examples/issues/add_bug.md \
  --repo /tmp/codepilot-issue-demo \
  --run-id issue-demo-add-bug \
  --fake-actions tests/codepilot/fixtures/agent_actions_success.jsonl \
  --approve \
  --policy-mode build \
  --report \
  --json-report \
  --overwrite
```

## 3. 检查生成产物

确认以下文件存在：

```text
runs/issue-demo-add-bug/issue.json
runs/issue-demo-add-bug/trace.jsonl
runs/issue-demo-add-bug/report.md
runs/issue-demo-add-bug/report.json
runs/issue-demo-add-bug/changes.patch
runs/issue-demo-add-bug/pr_summary.md
```

## 4. 检查补丁内容

确认 `runs/issue-demo-add-bug/changes.patch` 包含如下变更：

```text
-    return a - b
+    return a + b
```

## 5. 检查安全边界

确认 workflow 运行后：

- 没有自动执行 `git commit`
- 没有自动执行 `git push`
- 没有自动创建 Pull Request

可以额外执行以下命令人工确认：

```bash
cd /tmp/codepilot-issue-demo
git status --short
git log --oneline -n 1
```

验收通过标准：

- 产物全部存在
- `changes.patch` 中确实包含 `return a - b -> return a + b`
- 仓库只有工作区改动，没有新增自动提交、推送或 PR
