# -*- coding: utf-8 -*-
"""cli.misc_cmds — 覆盖率/推送/LOF/自检类命令（W7b 自 main.py 原样搬出）。"""
import sys

from cli._shared import _banner


def cmd_check_coverage(args):
    """数据覆盖体检（可转债 + ETF/LOF + 基金历史起点对账）"""
    from tools.check_coverage import run
    history = getattr(args, "history", False)
    strict = getattr(args, "strict", False)
    scope = getattr(args, "scope", "full")
    _banner("check-coverage 数据覆盖体检" + ("（历史起点对账）" if history else ""))
    return run(args.date, history=history, strict=strict, scope=scope)

def cmd_check_consumer(args):
    """决策侧消费标的覆盖体检（511180/511380 教训的系统性防线）"""
    from tools.consumer_coverage import main as cc_main
    _banner("check-consumer 决策侧消费标的覆盖体检")
    cc_main()

def cmd_doctor(args):
    """环境自检"""
    from tools.doctor import run_doctor
    _banner("doctor 环境自检")
    r = run_doctor(network=args.network, as_json=args.json)
    if r.n_fail:
        raise SystemExit(1)

def cmd_push_test(args):
    """PushPlus 推送测试 / 发送"""
    from push.pushplus import run_push
    _banner("push-test PushPlus推送")
    # 未提供 --content 时按测试消息发送
    run_push(
        token=args.token,
        channel=args.channel,
        template=args.template,
        title=args.title,
        content=args.content,
        test=args.test or not args.content,
    )

def cmd_lof_list(args):
    """场内ETF/LOF清单统计"""
    from tools.export.list_lof import run
    _banner("lof-list 场内ETF/LOF清单统计")
    run()

def cmd_lof_export(args):
    """场内LOF导出Excel"""
    from tools.export.export_lof import run
    _banner("lof-export 场内LOF导出Excel")
    out_path = run(args.out)
    print(f"\n输出文件: {out_path}")

def cmd_config_check(args):
    """config.py 完整性检查（与 doctor / regress 共用同一份校验逻辑）"""
    from tools.config_check import main as cc_main
    _banner("config-check config.py 完整性检查")
    # 返回非 0 = 检查未通过，须让退出码传播（脚本/CI 依赖此语义）
    sys.exit(cc_main())
