"""Code Review Workflow CLI。

用法:
  python run.py                          # 真实模型，REPL
  python run.py --demo                   # 离线演示（无需 Key，含并行+回环）
  python run.py --demo --once "实现..."   # 单次运行
  python run.py --resume <run_id>        # 从检查点恢复失败/中断的运行
  python run.py --list                   # 查看历史运行
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

def print_result(state: WorkflowState) -> None:
    banner(f"运行 {state.run_id} | 状态 {state.status} | 审查轮次 {state.round}",
           "green" if state.status == "success" else "red")
    cprint(f"\n审查结论: {state.review_summary}", "yellow")
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

    if args.list:
        for r in store.list_runs():
            print(f"{r['run_id']}  {r['status']:<8} 轮次{r['round']}  {r['requirement']}")
        return 0

    print(BANNER)

    # 恢复模式
    if args.resume:
        state = store.load(args.resume)
        if state.status == "success":
            print("该运行已成功，无需恢复。")
            print_result(state)
            return 0
        engine = make_engine(config, demo=args.demo)
        state = engine.run(state)
        print_result(state)
        return 0 if state.status == "success" else 1

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
        return 0 if state.status == "success" else 1

    # REPL
    cprint("输入需求交给 Workflow；命令: :list / :resume <id> / :clear? / :exit", "gray")
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
