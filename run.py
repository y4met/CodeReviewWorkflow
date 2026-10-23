"""Code Review Workflow CLI。

用法:
  python run.py                          # 真实模型，REPL
  python run.py --demo                   # 离线演示（无需 Key，含并行+回环）
  python run.py --demo --once "实现..."   # 单次运行
  python run.py --resume <run_id>        # 从检查点恢复失败/中断的运行
  python run.py --list                   # 查看历史运行
  python run.py --clear [--yes]          # 清除全部历史运行记录
"""
from __future__ import annotations

import argparse
import sys

from common.config import WorkflowConfig
from common.llm import build_llm
from common.logging_utils import cprint, banner
from workflow.engine import WorkflowEngine
from workflow.review_flow import build_pieces
from workflow.state import WorkflowState
from workflow.trace import RunStore

BANNER = r"""
   3-Agent 代码审查 Workflow | 并行 · 回环 · 检查点
---------------------------------------------------
"""

def make_engine(config: WorkflowConfig, demo: bool) -> WorkflowEngine:
    llm = build_llm(config, demo=demo)
    nodes, routing = build_pieces(config, llm)
    store = RunStore(config.runs_dir)
    return WorkflowEngine(nodes, routing, config, store)

def exit_code(state: WorkflowState) -> int:
    """只有「流程成功 + 审查通过」才算成功退出。"""
    return 0 if state.status == "success" and state.review_passed else 1


def print_result(state: WorkflowState) -> None:
    ok = state.status == "success" and state.review_passed
    banner(f"运行 {state.run_id} | 状态 {state.status} | 审查轮次 {state.round}",
           "green" if ok else "red")
    if state.review_passed:
        conclusion = "审查通过，已交付"
    elif state.outcome == "max_rounds":
        conclusion = "达到最大轮次，未通过审查"
    elif state.status == "failed":
        conclusion = "运行失败"
    else:
        conclusion = "未通过审查"
    cprint(f"\n交付结论: {conclusion}", "yellow")
    cprint(f"审查结论: {state.review_summary}", "yellow")
    if state.issues:
        cprint("\n最后一轮问题:", "yellow")
        for i in state.issues:
            cprint(f"  - [{i.get('severity')}] {i.get('desc')}", "gray")
    cprint("\n最终代码:", "cyan")
    print(state.code)
    if state.error:
        cprint(f"\n错误: {state.error}", "red")

def main() -> int:
    p = argparse.ArgumentParser(description="3-Agent 代码审查 Workflow")
    p.add_argument("--demo", action="store_true", help="离线演示模式")
    p.add_argument("--once", type=str, help="单次需求运行后退出")
    p.add_argument("--resume", type=str, help="从指定 run_id 的检查点恢复")
    p.add_argument("--list", action="store_true", help="列出历史运行")
    p.add_argument("--clear", action="store_true",
                   help="清除全部历史运行记录（请先停止 Web 服务）")
    p.add_argument("--yes", "-y", action="store_true", help="清除时跳过确认")
    p.add_argument("--no-parallel", action="store_true", help="关闭并行审查")
    p.add_argument("--rounds", type=int, help="覆盖最大审查轮次")
    args = p.parse_args()

    config = WorkflowConfig.load()
    config.ensure_dirs()
    if args.no_parallel:
        config.enable_parallel = False
    if args.rounds:
        config.max_rounds = args.rounds

    store = RunStore(config.runs_dir)

    if args.clear:
        if not args.yes:
            answer = input(
                f"将删除 {config.runs_dir} 下全部历史运行记录，输入 yes 确认: ")
            if answer.strip().lower() not in ("yes", "y"):
                print("已取消。")
                return 1
        result = store.clear()
        print(f"已清除 {result['deleted']} 条历史运行记录。")
        if result["failed"]:
            for item in result["failed"]:
                print(f"  [失败] {item['run_id']}: {item['error']}")
            return 1
        return 0

    if args.list:
        for r in store.list_runs():
            print(f"{r['run_id']}  {r['status']:<8} 轮次{r['round']}  {r['requirement']}")
        return 0

    print(BANNER)

    # 恢复模式
    if args.resume:
        state = store.load(args.resume)
        if state.status == "success":
            print("该运行已结束，无需恢复。")
            print_result(state)
            return exit_code(state)
        engine = make_engine(config, demo=args.demo)
        state = engine.run(state)
        print_result(state)
        return exit_code(state)

    try:
        engine = make_engine(config, demo=args.demo)
    except ValueError as e:
        cprint(f"[配置错误] {e}", "red")
        return 1

    mode = "离线演示" if args.demo else f"真实模型({config.model_name})"
    cprint(f"模式: {mode} | 并行: {config.enable_parallel} | 最大轮次: {config.max_rounds}", "cyan")

    # 单次
    if args.once:
        state = WorkflowState(requirement=args.once,
                              enable_parallel=config.enable_parallel)
        state = engine.run(state)
        print_result(state)
        return exit_code(state)

    # REPL
    cprint("输入需求交给 Workflow；命令: :list / :resume <id> / :clear / :exit", "gray")
    while True:
        try:
            text = input("\n需求> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见！")
            break
        if not text:
            continue
        if text in (":exit", ":quit"):
            print("再见！")
            break
        if text == ":list":
            for r in store.list_runs():
                print(f"{r['run_id']}  {r['status']:<8} 轮次{r['round']}  {r['requirement']}")
            continue
        if text == ":clear":
            answer = input("确认清除全部历史运行记录？输入 yes: ")
            if answer.strip().lower() in ("yes", "y"):
                result = store.clear()
                print(f"已清除 {result['deleted']} 条历史运行记录。")
            else:
                print("已取消。")
            continue
        if text.startswith(":resume "):
            rid = text.split(maxsplit=1)[1]
            state = engine.run(store.load(rid))
            print_result(state)
            continue

        state = WorkflowState(requirement=text, enable_parallel=config.enable_parallel)
        state = engine.run(state)
        print_result(state)

    return 0

if __name__ == "__main__":
    sys.exit(main())
