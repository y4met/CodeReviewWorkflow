"""审查 Agent：并行执行【静态 LLM 审查】与【动态运行自检】，合并出裁决。

- 两个分支并发（LLM 调用 与 子进程执行互不阻塞）；
- 分支级容错：某分支失败不拖垮整体，错误计入结论；
- 合并规则：动态自检必须通过，且静态无 major 问题 -> review_passed=True。
"""
from __future__ import annotations

from common.llm import request_json
from workflow.base_node import BaseNode, ParallelNode
from workflow.state import WorkflowState
from .prompts import review_messages

class StaticReviewBranch(BaseNode):
    name = "static_review"
    label = "静态审查(LLM)"

    def __init__(self, llm):
        self._llm = llm

    def run(self, state: WorkflowState) -> dict:
        data = request_json(self._llm, review_messages(state.code, state.round))
        return {"static_issues": data.get("issues", [])}

class RuntimeTestBranch(BaseNode):
    name = "runtime_test"
    label = "动态运行测试"

    def __init__(self, run_python):
        self._run_python = run_python

    def run(self, state: WorkflowState) -> dict:
        full = state.code
        if state.self_check:
            full = full.rstrip() + "\n\n# ---- self check ----\n" + state.self_check
        out = self._run_python.run({"code": full})
        # 通过判定：出现成功标记且没有错误/异常痕迹
        passed = ("all tests passed" in out
                  and "Traceback" not in out
                  and "Error" not in out
                  and "失败" not in out)
        return {"test_output": out, "tests_passed": passed}

class ReviewerAgent(BaseNode):
    name = "review"
    label = "审查Agent"

    def __init__(self, llm, run_python):
        static = StaticReviewBranch(llm)
        runtime = RuntimeTestBranch(run_python)
        self._parallel = ParallelNode(self.name, [static, runtime],
                                      merge=self._merge, label=self.label)

    def run(self, state: WorkflowState) -> dict:
        if state.enable_parallel:
            patch = self._parallel.run(state)
        else:
            patch = self._parallel.run_sequential(state)
        patch["round"] = state.round + 1
        return patch

    @staticmethod
    def _merge(results: dict, state: WorkflowState) -> dict:
        # 静态分支
        spatch, sstatus, serr = results.get("static_review", (None, "failed", "无结果"))
        static_issues = []
        if sstatus == "success" and spatch:
            static_issues = spatch.get("static_issues", [])
        else:
            static_issues.append({"severity": "minor",
                                  "desc": f"静态审查分支执行异常：{serr}"})

        # 动态分支
        rpatch, rstatus, rerr = results.get("runtime_test", (None, "failed", "无结果"))
        test_output, tests_passed = "", False
        if rstatus == "success" and rpatch:
            test_output = rpatch.get("test_output", "")
            tests_passed = bool(rpatch.get("tests_passed", False))
        else:
            test_output = f"动态测试分支异常：{rerr}"

        # 汇总问题
        issues: list = list(static_issues)
        if not tests_passed:
            issues.append({"severity": "major",
                           "desc": "动态自检未通过，运行输出如下：\n" + test_output})

        has_major_static = any(i.get("severity") == "major" for i in static_issues)
        passed = tests_passed and not has_major_static

        summary = (
            f"第 {state.round + 1} 轮审查：动态测试{'通过' if tests_passed else '未通过'}，"
            f"静态发现 {len(static_issues)} 个问题；"
            + ("审查通过。" if passed else "需要修复。")
        )
        return {
            "static_issues": static_issues,
            "test_output": test_output,
            "tests_passed": tests_passed,
            "issues": issues,
            "review_passed": passed,
            "review_summary": summary,
        }