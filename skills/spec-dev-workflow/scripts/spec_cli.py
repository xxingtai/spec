#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
spec-workflow 编排引擎 CLI（M0）

引擎与流程分离：本文件实现状态机 / handoff / 门禁 / resume 引擎；
流程（阶段、产物、门禁声明、确认点）来自声明式 pipeline.json。
接入其他 CLI 作为门禁检查只需改 pipeline 定义，不动引擎。

用法:
  python3 spec_cli.py init <spec-root> <feature> [--name <显示名>]
  python3 spec_cli.py phase-complete <spec-root> <feature> <phase> --handoff '<json>'
  python3 spec_cli.py phase-complete <spec-root> <feature> <phase> --handoff '<json>' \
      --user-confirmed "<用户确认说明>"   # 强制确认点阶段（require_confirm=true）必填，缺则拒绝
  python3 spec_cli.py phase-complete <spec-root> <feature> <phase> --skip <原因>
  python3 spec_cli.py gate <spec-root> <feature> [--phase <phase>]
  python3 spec_cli.py handoff read <spec-root> <feature> <phase>
  python3 spec_cli.py handoff list <spec-root> <feature>
  python3 spec_cli.py status <spec-root> <feature>
  python3 spec_cli.py restore <spec-root> <feature>

设计约定（执行纪律的机器侧）:
  - state.json 是唯一真源，只能由本 CLI 修改
  - 00-index.md 是渲染视图，任何状态变更后由本 CLI 重新生成
  - phase-complete 原子性：全部校验（顺序/门禁/handoff）通过才落盘，失败零变化

门禁三种类型（由 pipeline 声明，接第三方 CLI 不需改引擎）:
  - builtin ：内置原子规则（产物存在/非空、无占位符、无填写标记、任务勾选）
  - command ：外部命令，exit 0 通过。推荐 args 模式，占位符由引擎展开（跨平台）：
              {"type":"command","cmd":"python","args":["<脚本>","{SPEC_ROOT}","{SPEC_FEATURE}"]}
              另支持旧式 {"cmd":"<整条 shell 命令>"}；两者都注入 SPEC_*/PROJECT_ROOT/SKILL_DIR 等环境变量
  - review  ：质量门控，AI 按 spec-health-check 四维评审产出 --review-result；
              min_score 比总分，require_gate 比 Gate 红线（green<yellow<red，缺失即拒）

第四类约束 —— 强制确认点（不是门禁 check，而是阶段级开关）:
  stage.require_confirm = true 时，phase-complete 必须显式带 --user-confirmed "<说明>"，
  否则直接拒绝（fail-closed）。用于"设计、实现计划"这类**必须用户点头才能往下走**的阶段，
  防止 AI 自行认定"已确认"就流转。确认内容会写入 state.json 留痕（时间 + 原话）。
  与 confirm_point 的分工：confirm_point 只改提示文案，require_confirm 才是机器拦截。

流水线来源：<spec-root>/pipeline.json 优先（支持 {"extends":"<内置名>"} 继承
pipelines/<名>.json），否则用内置 pipelines/chained.json（**默认**）。内置可选：
  - chained（9 阶段，默认）：串联 spec-health-check 的评审/验收与 dev-docs 的对账
  - default（8 阶段）：零外部依赖的独立流程，用 {"extends":"default"} 显式退回

默认流水线会连带校验同级 skill 依赖：command 门禁里 {SKILLS_DIR}/<skill>/… 指向的文件
不存在时在**加载期**直接报错（fail-closed），并给出「补装同级 skill」与
「{"extends":"default"} 退回独立流程」两条出路 —— 避免门禁在收口时才静默失败。
"""

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import NoReturn

# ============================================================
# 常量
# ============================================================

SKILL_ROOT = Path(__file__).resolve().parent.parent          # scripts/ 的上级 = skill 目录
TEMPLATES_DIR = SKILL_ROOT / "templates"
PIPELINES_DIR = SKILL_ROOT / "pipelines"

# 默认内置流水线：串联三 skill 的 9 阶段流程。两者都按名解析，改名只需改这里。
DEFAULT_PIPELINE_NAME = "chained"        # 无 <spec根>/pipeline.json 时用它
STANDALONE_PIPELINE_NAME = "default"     # 零外部依赖的 8 阶段流程（显式退回 + 默认缺失时兜底）

MIN_ARTIFACT_BYTES = 100      # 产物"非空"阈值
MAX_HANDOFF_BYTES = 4096      # handoff 序列化上限
HANDOFF_SUMMARY_LIMIT = 300   # summary 字符上限
HANDOFF_DECISIONS_LIMIT = 5   # key_decisions 条数上限
CMD_TIMEOUT = 60              # command 门控超时（秒）
CMD_OUTPUT_LIMIT = 1024       # command 输出截断（bytes）

# 系统占位符白名单（精确匹配；说明性 {...} 占位不算）
SYSTEM_PLACEHOLDERS = ("{功能名称}", "{YYYYMMDD-feature-name}", "{SPEC_PATH}", "{YYYY-MM-DD}")

# 模板待填写标记：init 生成的产物文件末尾追加；填写完成后删除该行，
# 门禁规则 no_fill_marker 据此区分"模板未填写"与"已填写"（机器可判定的强信号）
FILL_MARKER = "SPEC_TEMPLATE_PENDING"

STAGE_STATUS_LABEL = {"pending": "⏳ 待开始", "in_progress": "🔄 进行中",
                      "completed": "✅ 已完成", "skipped": "⏭️ 已跳过"}

# review 门控的 Gate 等级（spec-health-check 口径：green < yellow < red）
GATE_ORDER = {"green": 0, "yellow": 1, "red": 2}

# 强制确认点（stage.require_confirm = true）：phase-complete 必须显式带
# --user-confirmed "<用户确认说明>"，否则拒绝收口（fail-closed）。
# 与 confirm_point 的区别 —— 两者是**不同层级的两种东西**：
#   confirm_point=true  仅提示级：影响看板 / restore 的建议文案（"先向用户展示确认"）
#   require_confirm=true 门禁级：机器会拦。防止 AI 自行认定"这看起来没问题"就直接流转
# 命名与 review 门控里的 require_gate 对齐（一个是 Gate 红线，一个是确认红线）。

# command 门禁可用的占位符（引擎侧展开，跨平台，不依赖 shell 的变量语法）
#   {SPEC_ROOT}        spec 根目录（绝对路径）
#   {SPEC_FEATURE}     当前 feature 目录名（<yyyymmddhhmm>-<slug>）
#   {SPEC_DOCS_DIR}    当前 feature 的文档目录（绝对路径）
#   {SPEC_SESSION_DIR} 当前 feature 的会话目录（含 state.json / handoff）
#   {PROJECT_ROOT}     spec 根目录的上级 = 被开发项目根（默认 <项目>/spec 时成立）
#   {SPEC_PHASE}       当前阶段 id
#   {SKILL_DIR}        本 skill 目录
#   {SKILLS_DIR}       同级 skill 的父目录（spec-health-check / dev-docs 所在处）
#   {PYTHON}           当前 Python 解释器绝对路径
# 同一批键名同时作为环境变量注入 command 门禁进程，方便脚本直接读取。
COMMAND_PLACEHOLDERS = ("SPEC_ROOT", "SPEC_FEATURE", "SPEC_DOCS_DIR", "SPEC_SESSION_DIR",
                        "PROJECT_ROOT", "SPEC_PHASE", "SKILL_DIR", "SKILLS_DIR", "PYTHON")


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def log(msg: str) -> None:
    print(msg)


def err(msg: str, code: int = 1) -> "NoReturn":
    print("❌ " + msg, file=sys.stderr)
    sys.exit(code)


def write_text_lf(path: Path, text: str) -> None:
    """跨平台固定 LF 写出：避免 Windows 文本模式把 \\n 写成 \\r\\n，保证 spec
    产物在任意平台字节一致。Python 3.7+ 通用（Path.write_text 的 newline 参数需
    3.10+，故此处用 open）。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def feature_docs_dir(spec_root: str, feature: str) -> Path:
    """文档目录：<spec根>/<feature>（spec 产物文档，人读、可提交）"""
    return Path(spec_root) / feature


def session_dir(spec_root: str, feature: str) -> Path:
    """会话状态目录（运行时）：<spec根所在目录>/.specworkflow/sessions/<feature>
    state.json 与 handoff 存这里——状态与文档分离，文档目录保持纯净"""
    root = Path(spec_root).resolve()
    return root.parent / ".specworkflow" / "sessions" / feature


def ensure_feature(spec_root: str, feature: str):
    """校验 feature 存在（文档 + 会话状态），返回 (docs_dir, session_dir)"""
    docs = feature_docs_dir(spec_root, feature)
    sess = session_dir(spec_root, feature)
    if not docs.is_dir() or not (sess / "state.json").is_file():
        err("feature 不存在: %s\n请先运行: spec_cli.py init %s %s" % (docs, spec_root, feature))
    return docs, sess


# ============================================================
# pipeline 加载与校验
# ============================================================

def _read_pipeline_file(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        err("pipeline 解析失败: %s (%s)" % (path, e))


def available_pipelines() -> str:
    """本 skill 内置流水线名列表（供 extends 取值与报错提示）"""
    if not PIPELINES_DIR.is_dir():
        return "（无）"
    return ", ".join(sorted(p.stem for p in PIPELINES_DIR.glob("*.json"))) or "（无）"


def load_builtin_pipeline(name) -> dict:
    """按名加载本 skill 内置流水线；名字做白名单校验，杜绝路径穿越"""
    safe = str(name).strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", safe):
        err("extends 名称非法（只允许字母、数字、下划线、连字符）: %r" % (name,))
    path = PIPELINES_DIR / ("%s.json" % safe)
    if not path.is_file():
        err("extends 指向的内置流水线不存在: %s\n可用: %s" % (path, available_pipelines()))
    return _read_pipeline_file(path)


def missing_skill_deps(pipeline: dict) -> list:
    """校验 command 门禁里 {SKILLS_DIR}/… 与 {SKILL_DIR}/… 指向的文件真实存在。

    这两个占位符的取值与 feature 无关（不像 {SPEC_ROOT}/{PROJECT_ROOT}），所以能在
    加载期判定。返回 [(阶段 id, 依赖标识, 缺失的绝对路径)]，空列表 = 依赖齐全。
    """
    problems = []
    bases = (("SKILLS_DIR", SKILL_ROOT.parent), ("SKILL_DIR", SKILL_ROOT))
    for s in pipeline.get("stages", []):
        sid = s.get("id")
        for check in s.get("gate", {}).get("checks", []):
            if check.get("type") != "command":
                continue
            for text in _check_command_texts(check):
                t = str(text)
                for key, base in bases:
                    prefix = "{%s}/" % key
                    if not t.startswith(prefix):
                        continue
                    rel = t[len(prefix):]
                    if not (base / rel).exists():
                        # 依赖标识：内建同级 skill 取首段目录名，本 skill 自身取自身目录名
                        dep = rel.split("/")[0] if key == "SKILLS_DIR" else SKILL_ROOT.name
                        problems.append((sid, dep, str(base / rel)))
    return problems


def verify_skill_deps(pipeline: dict) -> None:
    """默认流水线串联同级 skill，缺依赖时 fail-closed 报错并给出两条出路。

    为什么要在加载期拦：这些依赖是 command 门禁的**可执行入口**，缺失时门禁一定失败，
    但失败信息是子进程的 "can't open file"，离真正的原因（skill 没装）很远。
    """
    problems = missing_skill_deps(pipeline)
    if not problems:
        return
    seen, detail = set(), []
    for sid, dep, path in problems:
        if (sid, dep) in seen:
            continue
        seen.add((sid, dep))
        detail.append("  - 阶段 [%s] → %s：找不到 %s" % (sid, dep, path))
    err("\n".join([
        "流水线 [%s] 引用的同级 skill 依赖缺失：" % pipeline.get("id", "?"),
    ] + detail + [
        "",
        "本 skill 的默认流水线是**串联流程**（chained，9 阶段），需要 spec-health-check 与",
        "dev-docs 与本 skill 同级安装。两种解决方式：",
        "  1) 把这两个 skill 装到同一个 skills 目录下：%s" % SKILL_ROOT.parent,
        "  2) 只用本 skill 独立开发（8 阶段，零外部依赖）：在 <spec根>/pipeline.json 写",
        '     {"extends": "default"}',
    ]))


def load_pipeline(spec_root: str) -> dict:
    """查找顺序：
      1) <spec-root>/pipeline.json（项目级）。
         若其为 {"extends": "<名>"} 形式，则以本 skill 内置 pipelines/<名>.json 为基底：
         项目文件里的 id/name/version 覆盖基底，出现 stages 时整体替换基底 stages
         （用于"只想改一小部分、又不想复制全量阶段"的场景）。
      2) 本 skill 内置 pipelines/chained.json（默认）
         —— 找不到时退回 default.json，避免内置文件被误删直接崩。
    """
    project_cfg = Path(spec_root) / "pipeline.json"
    if project_cfg.is_file():
        p = _read_pipeline_file(project_cfg)
        base_name = p.get("extends")
        if base_name:
            base = load_builtin_pipeline(base_name)
            merged = dict(base)
            for k in ("id", "name", "version"):
                if p.get(k):
                    merged[k] = p[k]
            if p.get("stages"):
                merged["stages"] = p["stages"]
            p = merged
            p["_extends"] = str(base_name)
        p["_source"] = {"path": str(project_cfg), "kind": "project"}
    else:
        default_cfg = PIPELINES_DIR / ("%s.json" % DEFAULT_PIPELINE_NAME)
        if not default_cfg.is_file():
            default_cfg = PIPELINES_DIR / ("%s.json" % STANDALONE_PIPELINE_NAME)
        p = _read_pipeline_file(default_cfg)
        p["_source"] = {"path": str(default_cfg), "kind": "builtin"}
    errors = validate_pipeline(p)
    if errors:
        err("pipeline 定义不合法:\n  " + "\n  ".join(errors))
    verify_skill_deps(p)
    return p


def load_pipeline_matching(spec_root: str, state: dict) -> dict:
    """读 pipeline 并核对**阶段集合**与 state.json 记录一致（阶段集合不可中途替换）。

    流水线每次都由磁盘重新解析，而 state 是 init 时冻结的。两者阶段集合不一致时，
    state 里没有的阶段会被误显示成「待开始」、收口顺序也会错位，所以直接拦下并给修复指引。
    只比阶段 id 序列、不比 pipeline id —— 同一阶段集合下改 id 名或调门禁参数是合法的，
    只有阶段增减才是真问题。
    典型触发：改了 <spec根>/pipeline.json，或 skill 升级后默认流水线变更。
    """
    pipeline = load_pipeline(spec_root)
    recorded = list((state.get("phases") or {}).keys())
    current = phase_ids(pipeline)
    if recorded and recorded != current:
        added = [p for p in current if p not in recorded]
        gone = [p for p in recorded if p not in current]
        detail = []
        if added:
            detail.append("  本次新增阶段：%s" % ", ".join(added))
        if gone:
            detail.append("  本次缺少阶段：%s" % ", ".join(gone))
        err("\n".join([
            "流水线阶段集合与 state.json 不一致，拒绝继续（阶段集合不可中途替换）。",
            "  state 记录  ：%s" % (", ".join(recorded) or "（空）"),
            "  当前流水线  ：%s（pipeline id: %s）" % (", ".join(current), pipeline.get("id")),
        ] + detail + [
            "  常见原因：<spec根>/pipeline.json 被改动，或 skill 升级后默认流水线变更。",
            "  修复：",
            "    - 想沿用原阶段集合 → 在 <spec根>/pipeline.json 里显式写回它，",
            '      例如 {"extends": "%s"}' % STANDALONE_PIPELINE_NAME,
            "    - 确实要换流水线 → 用新流水线重新 init 一个 feature",
        ]))
    return pipeline


def _check_command_texts(check: dict) -> list:
    """command 检查里所有可能出现占位符的字符串"""
    texts = [check.get("cmd") or ""]
    args = check.get("args")
    if isinstance(args, list):
        texts += [str(a) for a in args]
    return texts


def validate_pipeline(p: dict) -> list:
    errors = []
    if not isinstance(p, dict) or not p.get("id"):
        errors.append("缺少 id")
    stages = p.get("stages", [])
    if not isinstance(stages, list) or not stages:
        errors.append("stages 不能为空")
        return errors
    ids = [s.get("id") for s in stages]
    if len(ids) != len(set(ids)):
        errors.append("stage id 必须唯一")
    for s in stages:
        sid = s.get("id")
        if not sid:
            errors.append("存在无 id 的 stage")
            continue
        if not isinstance(s.get("artifacts", []), list):
            errors.append("[%s] artifacts 必须是数组" % sid)
        tools = s.get("tools", [])
        if not isinstance(tools, list):
            errors.append("[%s] tools 必须是数组" % sid)
        else:
            for t in tools:
                if not isinstance(t, dict) or not t.get("skill"):
                    errors.append("[%s] tools 每项须为含 skill 字段的对象" % sid)
                    break
        # require_confirm：强制确认点开关（与 confirm_point 不同，这是机器门禁）
        if s.get("require_confirm") is not None and not isinstance(s.get("require_confirm"), bool):
            errors.append("[%s] require_confirm 须为布尔值 true/false" % sid)
        if s.get("confirm_point") is not None and not isinstance(s.get("confirm_point"), bool):
            errors.append("[%s] confirm_point 须为布尔值 true/false" % sid)
        gate = s.get("gate", {})
        for check in gate.get("checks", []):
            ctype = check.get("type")
            if ctype not in ("builtin", "command", "review"):
                errors.append("[%s] gate.check.type 非法: %s" % (sid, ctype))
            if ctype == "command":
                if not check.get("cmd"):
                    errors.append("[%s] command 检查缺少 cmd" % sid)
                args = check.get("args")
                if args is not None:
                    if not isinstance(args, list):
                        errors.append("[%s] command 的 args 必须是数组" % sid)
                    elif any(not isinstance(a, (str, int, float)) for a in args):
                        errors.append("[%s] command 的 args 元素须为字符串或数字" % sid)
                # 占位符白名单：拦住 {SPEC_FEATUR} 这类拼错后静默出错的情况
                for text in _check_command_texts(check):
                    for tok in re.findall(r"\{([A-Z_][A-Z0-9_]*)\}", str(text)):
                        if tok not in COMMAND_PLACEHOLDERS:
                            errors.append("[%s] command 用了未知占位符 {%s}（可用: %s）"
                                          % (sid, tok, ", ".join("{%s}" % k for k in COMMAND_PLACEHOLDERS)))
            if ctype == "review":
                ms = check.get("min_score", 80)
                if not isinstance(ms, (int, float)) or not (0 <= ms <= 100):
                    errors.append("[%s] review 的 min_score 须为 0-100 数值" % sid)
                rg = check.get("require_gate")
                if rg is not None and rg not in GATE_ORDER:
                    errors.append("[%s] review 的 require_gate 须为 green/yellow/red：%r" % (sid, rg))
            if ctype == "builtin":
                for rule in check.get("rules", []):
                    if rule not in BUILTIN_RULES:
                        errors.append("[%s] builtin 规则不存在: %s" % (sid, rule))
    return errors


def stage_by_id(pipeline: dict, phase: str) -> dict:
    for s in pipeline["stages"]:
        if s["id"] == phase:
            return s
    return None


def phase_ids(pipeline: dict) -> list:
    return [s["id"] for s in pipeline["stages"]]


def stage_requires_confirm(stage) -> bool:
    """该阶段是否为机器强制的用户确认点（require_confirm=true）"""
    return bool((stage or {}).get("require_confirm"))


def confirm_hint(stage) -> str:
    """强制确认点被拦时的修复指引（命令行示例里的阶段 id 用实际值）"""
    sid = (stage or {}).get("id", "<阶段>")
    arts = "、".join((stage or {}).get("artifacts", [])) or "本阶段产物"
    return "\n".join([
        "阶段 [%s] 是**强制确认点**，收口前必须取得用户明确确认。" % sid,
        "  这是机器门禁：不带 --user-confirmed 一律拒绝，"
        "避免把「AI 自行认为已确认」当成用户确认。",
        "",
        "  正确做法：",
        "    1) 把本阶段产物完整展示给用户：%s" % arts,
        "    2) 停下来等用户明确表态（同意 / 要改哪里），"
        "不要用「看起来没问题」代替",
        "    3) 用户认可后收口，并把确认内容带上：",
        "       phase-complete <spec根目录> <feature> %s \\" % sid,
        "         --handoff '…' --user-confirmed \"<用户确认的原话或要点>\"",
        "",
        "  若本阶段确实要整段跳过，改用 --skip \"<原因>\"（同样落盘、可审计）。",
    ])


# ============================================================
# builtin 原子规则库
# ============================================================

def rule_artifacts_exist(spec_root: Path, fd: Path, stage: dict):
    missing = [a for a in stage.get("artifacts", []) if not (fd / a).is_file()]
    return (not missing, "缺失: " + ", ".join(missing) if missing else "齐全")


def rule_artifacts_nonempty(spec_root: Path, fd: Path, stage: dict):
    small = [a for a in stage.get("artifacts", [])
             if (fd / a).is_file() and (fd / a).stat().st_size < MIN_ARTIFACT_BYTES]
    return (not small, "过小(<%dB): %s" % (MIN_ARTIFACT_BYTES, ", ".join(small)) if small else "非空")


def rule_no_placeholder(spec_root: Path, fd: Path, stage: dict):
    hit = []
    for a in stage.get("artifacts", []):
        f = fd / a
        if not f.is_file():
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        for ph in SYSTEM_PLACEHOLDERS:
            if ph in text:
                hit.append("%s:%s" % (a, ph))
    return (not hit, "残留: " + ", ".join(hit) if hit else "无占位符残留")


def rule_no_fill_marker(spec_root: Path, fd: Path, stage: dict):
    hit = []
    for a in stage.get("artifacts", []):
        f = fd / a
        if f.is_file() and FILL_MARKER in f.read_text(encoding="utf-8", errors="replace"):
            hit.append(a)
    return (not hit, "未填写（含模板标记）: " + ", ".join(hit) if hit else "模板标记已清除")


def _task_lines(fd: Path) -> list:
    """读取 08-commit.md 中的任务行（- [x]/- [ ]/- [-] T 开头），返回 [(done, line)]"""
    f = fd / "08-commit.md"
    if not f.is_file():
        return []
    result = []
    for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r"^\s*-\s*\[(.)\]\s*T?\d*", line)
        if m:
            result.append((m.group(1), line.strip()))
    return result


def rule_tasks_all_checked(spec_root: Path, fd: Path, stage: dict):
    tasks = _task_lines(fd)
    unchecked = [t for (s, t) in tasks if s not in ("x", "-", "!")]
    return (not unchecked, "未处理: %d 条" % len(unchecked) if unchecked else "全部勾选(%d)" % len(tasks))


def rule_tasks_any_checked(spec_root: Path, fd: Path, stage: dict):
    tasks = _task_lines(fd)
    done = [t for (s, t) in tasks if s == "x"]
    return (bool(done), "已勾选 %d/%d" % (len(done), len(tasks)) if done else "任务清单无勾选")


BUILTIN_RULES = {
    "artifacts_exist": rule_artifacts_exist,
    "artifacts_nonempty": rule_artifacts_nonempty,
    "no_placeholder": rule_no_placeholder,
    "no_fill_marker": rule_no_fill_marker,
    "tasks_all_checked": rule_tasks_all_checked,
    "tasks_any_checked": rule_tasks_any_checked,
}

# ============================================================
# state 读写与渲染
# ============================================================

def new_state(spec_root: str, feature: str, display_name: str, pipeline: dict) -> dict:
    return {
        "version": "1.0",
        "feature": feature,
        "display_name": display_name,
        "spec_root": spec_root,
        "pipeline": {"id": pipeline["id"], "source": pipeline["_source"]["kind"]},
        "created_at": now_str(),
        "updated_at": now_str(),
        "current_phase": phase_ids(pipeline)[0],
        "phases": {sid: {"status": "pending", "completed_at": ""} for sid in phase_ids(pipeline)},
        "decisions": [],
    }


def load_state(sess: Path) -> dict:
    try:
        return json.loads((sess / "state.json").read_text(encoding="utf-8"))
    except Exception as e:
        err("state.json 读取失败: %s (%s)" % (sess / "state.json", e))


def save_state(sess: Path, state: dict) -> None:
    state["updated_at"] = now_str()
    write_text_lf(sess / "state.json",
                  json.dumps(state, ensure_ascii=False, indent=2) + "\n")


def render_index(fd: Path, state: dict, pipeline: dict) -> str:
    """00-index.md：从 state 渲染的视图（机器是源，本文件是视图）"""
    total = len(pipeline["stages"])
    done = sum(1 for s in pipeline["stages"] if state["phases"].get(s["id"], {}).get("status") == "completed")
    lines = [
        "# %s — 进度索引" % state["display_name"],
        "",
        "> Spec: `%s`" % state["feature"],
        "> 路径: `%s`" % (Path(state["spec_root"]) / state["feature"]),
        "> Pipeline: `%s`" % state["pipeline"]["id"],
        "",
        "## 重要说明",
        "",
        "**开发以 spec 文档为准**：",
        "- 每完成一个阶段，必须通过 `phase-complete` 收口（门禁达标才流转）",
        "- 代码实现必须与 spec 文档保持一致",
        "- 如需变更，先更新 spec 文档，再修改代码",
        "- spec 文档与 state.json 是项目的唯一真实来源（本文件为自动生成视图，请勿手改）",
        "",
        "## 阶段进度",
        "",
        "| 阶段 | 产物 | 状态 | 完成时间 |",
        "|------|------|------|----------|",
    ]
    for s in pipeline["stages"]:
        st = state["phases"].get(s["id"], {})
        status = st.get("status", "pending")
        if status == "pending" and state.get("current_phase") == s["id"]:
            status = "in_progress"
        marker = {"pending": "⏳ 待开始", "in_progress": "🔄 进行中",
                  "completed": "✅ 已完成", "skipped": "⏭️ 已跳过"}[status]
        completed_at = st.get("completed_at", "")
        if status == "skipped" and st.get("skip_reason"):
            completed_at = st["skip_reason"]
        lines.append("| %s | %s | %s | %s |" % (s["id"], ", ".join(s.get("artifacts", [])), marker, completed_at))
    lines += [
        "",
        "## 总体状态",
        "",
        "- 当前阶段：%s" % (state.get("current_phase") or "全部完成"),
        "- 整体进度：%d/%d" % (done, total),
        "",
    ]
    return "\n".join(lines)


def write_index(fd: Path, state: dict, pipeline: dict) -> None:
    write_text_lf(fd / "00-index.md", render_index(fd, state, pipeline))


# ============================================================
# 模板填充
# ============================================================

_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)


def render_template(tpl_text: str, feature: str, display_name: str, spec_root: str) -> str:
    text = _HTML_COMMENT_RE.sub("", tpl_text)
    replacements = {
        "{功能名称}": display_name,
        "{YYYYMMDD-feature-name}": feature,
        "{SPEC_PATH}": str(Path(spec_root) / feature),
        "{YYYY-MM-DD}": today_str(),
    }
    for k, v in replacements.items():
        text = text.replace(k, v)
    return text


# ============================================================
# 子命令：init
# ============================================================

def ts_dirname(slug: str) -> str:
    """目录名 = <yyyymmddhhmm>-<slug>：按创建时间排序且保留语义可读"""
    ts = datetime.now().strftime("%Y%m%d%H%M")
    return "%s-%s" % (ts, slug)


def cmd_init(args) -> None:
    spec_root = args.spec_root
    slug = args.feature
    display_name = args.name or slug

    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]*$", slug):
        err("feature 名称不合法（仅字母数字._-且非空）: %s" % slug)

    pipeline = load_pipeline(spec_root)
    feature = ts_dirname(slug)          # 实际目录名带时间戳前缀，天然按时间排序
    docs = feature_docs_dir(spec_root, feature)
    sess = session_dir(spec_root, feature)
    if docs.exists():
        err("文档目录已存在，拒绝覆盖: %s" % docs)
    if sess.exists():
        err("会话状态已存在，拒绝覆盖: %s" % sess)

    docs.mkdir(parents=True)
    sess.mkdir(parents=True)

    # 复制模板（pipeline 各阶段 artifacts 的并集，保持模板原有顺序）到文档目录
    wanted = []
    seen = set()
    for s in pipeline["stages"]:
        for a in s.get("artifacts", []):
            if a not in seen:
                seen.add(a)
                wanted.append(a)

    for a in wanted:
        tpl = TEMPLATES_DIR / a
        if tpl.is_file():
            content = render_template(tpl.read_text(encoding="utf-8"), feature, display_name, spec_root)
        else:
            content = "> 本产物由 pipeline 声明生成，按需填写。\n"
        # 追加模板待填写标记（填写完成后删除该行，供门禁 no_fill_marker 判定）
        content += "\n<!-- %s: 本产物为模板生成，填写完成后请删除本行 -->\n" % FILL_MARKER
        write_text_lf(docs / a, content)

    # 状态与视图分离：state 落运行时会话目录，00-index 视图落文档目录
    state = new_state(spec_root, feature, display_name, pipeline)
    save_state(sess, state)
    write_index(docs, state, pipeline)

    print("✅ init 完成")
    print("   目录: %s（时间戳 %s + slug %s）" % (feature, feature[:12], slug))
    print("   文档: %s" % docs)
    print("   会话: %s" % sess)
    src = pipeline["_source"]
    print("   pipeline: %s · %s（%d 阶段，%s）" % (
        pipeline["id"], pipeline.get("name", ""), len(pipeline["stages"]),
        "内置默认" if src["kind"] == "builtin" else "项目配置"))
    if src["kind"] == "builtin":
        print('   （内置默认流水线；若想退回零外部依赖的 8 阶段独立流程，'
              '在 %s/pipeline.json 写 {"extends": "default"}）' % spec_root)
    print("   下一步: 进入「%s」阶段（产物：%s）" %
          (pipeline["stages"][0]["id"], ", ".join(pipeline["stages"][0].get("artifacts", []))))


# ============================================================
# 门禁引擎（三类检查）
# ============================================================

def run_builtin_check(spec_root: Path, fd: Path, stage: dict, check: dict) -> dict:
    results = []
    passed = True
    for rule in check.get("rules", []):
        fn = BUILTIN_RULES[rule]
        ok, detail = fn(spec_root, fd, stage)
        if not ok:
            passed = False
        results.append({"rule": rule, "passed": ok, "detail": detail})
    return {"type": "builtin", "rules": check.get("rules", []), "passed": passed,
            "items": results}


def _platform_cmd(cmd, is_win=None):
    """command 门禁命令的跨平台归一（仅影响声明式 command 门禁，不影响其它）：
    - POSIX（默认/非 Windows）：原样交给 /bin/sh。
    - Windows（cmd.exe）：只做保守替换——前导 `python3 `/`python ` 换成当前解释器
      （Windows 常无 python3 启动名）；其余命令原样交给 cmd.exe。
      POSIX 专用工具（bash/test/…）cmd.exe 无法执行时自然失败，detail 会给出输出提示。
    """
    if is_win is None:
        is_win = (os.name == "nt")
    if not is_win:
        return cmd
    t = cmd.strip()
    if t in ("python3", "python"):
        return '"%s"' % sys.executable
    for prefix in ("python3 ", "python "):
        if t.startswith(prefix):
            rest = t[len(prefix):]
            return '"%s" %s' % (sys.executable, rest)
    return cmd


def _platform_argv0(token) -> str:
    """args 模式的首元素归一：python/python3 → 当前解释器（Windows 常无 python3 启动名）"""
    return sys.executable if str(token).strip() in ("python", "python3") else str(token)


def _cmd_context(spec_root, fd: Path, stage: dict) -> dict:
    """command 门禁的占位符 / 环境变量上下文（见 COMMAND_PLACEHOLDERS 注释）"""
    root = Path(spec_root)
    feature = fd.name
    return {
        "SPEC_ROOT": str(root),
        "SPEC_FEATURE": feature,
        "SPEC_DOCS_DIR": str(fd),
        "SPEC_SESSION_DIR": str(session_dir(str(root), feature)),
        "PROJECT_ROOT": str(root.parent),
        "SPEC_PHASE": stage.get("id", ""),
        "SKILL_DIR": str(SKILL_ROOT),
        "SKILLS_DIR": str(SKILL_ROOT.parent),
        "PYTHON": sys.executable,
    }


def _expand_placeholders(text, ctx: dict) -> str:
    """展开 {KEY}；未知占位符原样保留（validate_pipeline 已在加载期拦截）"""
    return re.sub(r"\{([A-Z_][A-Z0-9_]*)\}",
                  lambda m: ctx.get(m.group(1), m.group(0)), str(text))


def run_command_check(spec_root: Path, fd: Path, stage: dict, check: dict) -> dict:
    """command 门禁：把任意 CLI 接成门禁（exit 0 通过）。两种写法：

    - **args 模式（推荐）**：
        {"type":"command","cmd":"python","args":["<脚本>","{SPEC_ROOT}","{SPEC_FEATURE}"]}
      shell=False 逐参数传递 → 路径含空格/中文安全；占位符由引擎展开，跨平台一致。
    - **旧式 shell 模式**：{"type":"command","cmd":"<整条 shell 命令>"}
      交给 cmd.exe(Windows) / /bin/sh，保持向后兼容。

    两种模式都会：cwd = spec 根目录；注入 SPEC_ROOT / SPEC_FEATURE / SPEC_DOCS_DIR /
    SPEC_SESSION_DIR / PROJECT_ROOT / SPEC_PHASE / SKILL_DIR / SKILLS_DIR / PYTHON 环境变量。
    """
    ctx = _cmd_context(spec_root, fd, stage)
    env = dict(os.environ)
    env.update(ctx)
    args = check.get("args")
    if args is not None:
        argv = [_platform_argv0(_expand_placeholders(check["cmd"], ctx))]
        argv += [_expand_placeholders(a, ctx) for a in args]
        shown = " ".join(argv)
    else:
        argv = None
        shown = _expand_placeholders(_platform_cmd(check["cmd"]), ctx)
    try:
        if argv is not None:
            proc = subprocess.run(argv, shell=False, cwd=str(spec_root), timeout=CMD_TIMEOUT,
                                  env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        else:
            proc = subprocess.run(shown, shell=True, cwd=str(spec_root), timeout=CMD_TIMEOUT,
                                  env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        output = proc.stdout.decode("utf-8", errors="replace")[:CMD_OUTPUT_LIMIT]
        return {"type": "command", "cmd": shown, "passed": proc.returncode == 0,
                "detail": output.strip() or ("exit=%d" % proc.returncode),
                "exit": proc.returncode}
    except subprocess.TimeoutExpired:
        return {"type": "command", "cmd": shown, "passed": False,
                "detail": "超时（>%ds）" % CMD_TIMEOUT}
    except Exception as e:
        return {"type": "command", "cmd": shown, "passed": False, "detail": "执行异常: %s" % e}


def validate_review_result(payload: dict) -> list:
    """校验 --review-result（spec-health-check 评审产物）；空列表 = 合法"""
    errors = []
    if not isinstance(payload, dict):
        return ["review-result 必须是 JSON 对象"]
    sc = payload.get("score")
    if isinstance(sc, bool) or not isinstance(sc, (int, float)) or not (0 <= sc <= 100):
        errors.append("score 须为 0-100 数值")
    dims = payload.get("dimensions")
    if not isinstance(dims, list) or not dims:
        errors.append("dimensions 须为非空数组（建议四项：A需求/B一致性/C真实性/D设计计划）")
    elif any(not isinstance(d, dict) or not d.get("id")
             or not isinstance(d.get("score"), (int, float)) for d in dims):
        errors.append("dimensions 每项须含 id 与数值 score")
    if payload.get("issues") is not None and not isinstance(payload["issues"], list):
        errors.append("issues 须为数组")
    if payload.get("gate") not in (None, "green", "yellow", "red"):
        errors.append("gate 取值须为 green/yellow/red")
    return errors


def run_review_check(check: dict, review_result: dict, precheck: bool, phase: str) -> dict:
    """review 门控：AI 按 spec-health-check 评审产出 --review-result，引擎机器把关

    - `min_score`   ：总分下限（默认 80）
    - `require_gate`：可选。要求 review-result 的 `gate` 达到该等级（green < yellow < red）。
      与 spec-health-check 的 Gate 条件集对齐，避免"总分够但实际红灯"被放行。
      **fail-closed**：`gate` 缺失或非法时判不合格，不许只报分数不报 Gate。
    """
    min_score = check.get("min_score", 80)
    require_gate = check.get("require_gate")
    if precheck:
        extra = "，Gate 须 %s" % require_gate if require_gate else ""
        return {"type": "review", "min_score": min_score, "require_gate": require_gate,
                "passed": True, "skipped": True,
                "detail": "review 门控：收口时校验（gate 预检跳过，达标线 %d%s）" % (min_score, extra)}
    if review_result is None:
        return {"type": "review", "min_score": min_score, "require_gate": require_gate, "passed": False,
                "detail": "缺少 --review-result（须按 spec-health-check 四维评审提供 score/gate/issues）"}
    errors = validate_review_result(review_result)
    if errors:
        return {"type": "review", "min_score": min_score, "require_gate": require_gate, "passed": False,
                "detail": "review-result 不合法: " + "; ".join(errors)}
    score = review_result["score"]
    gate = review_result.get("gate")
    issues = review_result.get("issues", [])
    passed = score >= min_score
    detail = "score=%d %s 达标线 %d" % (score, "≥" if passed else "<", min_score)
    if require_gate:
        if gate not in GATE_ORDER:
            passed = False
            detail += " | gate=%r 缺失或非法（require_gate=%s，必须显式给出）" % (gate, require_gate)
        elif GATE_ORDER[gate] > GATE_ORDER[require_gate]:
            passed = False
            detail += " | gate=%s 未达要求 %s" % (gate, require_gate)
        else:
            detail += " | gate=%s ✓（要求 %s）" % (gate, require_gate)
    if not passed and issues:
        top = "; ".join("[%s] %s" % (i.get("severity", "?"), i.get("desc", "")) for i in issues[:3])
        detail += " | issues: " + top
    return {"type": "review", "min_score": min_score, "require_gate": require_gate,
            "score": score, "passed": passed, "detail": detail, "issues": issues, "gate": gate}


def run_gate(spec_root: str, fd: Path, state: dict, pipeline: dict, phase: str,
             review_result: dict = None, precheck: bool = False) -> dict:
    """执行指定阶段 gate.checks 的全部检查，返回结果 dict（不落盘）
    review 门控：precheck(预检)=跳过展示；收口时传入 review_result 严格判定"""
    stage = stage_by_id(pipeline, phase)
    if stage is None:
        err("阶段不存在于流水线: %s" % phase)
    checks = stage.get("gate", {}).get("checks", [])
    result = {"phase": phase, "passed": True, "checks": []}
    root = Path(spec_root)
    for check in checks:
        ctype = check.get("type")
        if ctype == "builtin":
            r = run_builtin_check(root, fd, stage, check)
        elif ctype == "command":
            r = run_command_check(root, fd, stage, check)
        elif ctype == "review":
            r = run_review_check(check, review_result, precheck, phase)
        else:
            continue
        result["checks"].append(r)
        if not r["passed"]:
            result["passed"] = False
    return result


def print_gate(result: dict) -> None:
    print("--- 门禁结果 [%s] ---" % result["phase"])
    for c in result["checks"]:
        mark = "✅" if c["passed"] else "❌"
        if c["type"] == "builtin":
            detail = "; ".join("%s:%s" % (i["rule"], "PASS" if i["passed"] else "FAIL") for i in c["items"])
        else:
            detail = c.get("detail", "")
        print("  %s [%s] %s" % (mark, c["type"], detail))
        if not c["passed"] and c["type"] == "builtin":
            for i in c["items"]:
                if not i["passed"]:
                    print("      ↳ %s: %s" % (i["rule"], i["detail"]))
    print(">>> %s" % ("门禁通过 ✅" if result["passed"] else "门禁未通过 ❌，禁止流转"))


# ============================================================
# handoff
# ============================================================

def validate_handoff_payload(payload: dict) -> list:
    """返回错误列表；空列表 = 合法"""
    errors = []
    if not isinstance(payload, dict):
        return ["handoff 必须是 JSON 对象"]
    for field in ("summary", "key_decisions", "artifacts", "next_inputs"):
        if field not in payload:
            errors.append("缺少必填字段: %s" % field)
    if isinstance(payload.get("summary"), str) and len(payload["summary"]) > HANDOFF_SUMMARY_LIMIT:
        errors.append("summary 超长（>%d 字）" % HANDOFF_SUMMARY_LIMIT)
    kd = payload.get("key_decisions")
    if kd is not None and (not isinstance(kd, list) or len(kd) > HANDOFF_DECISIONS_LIMIT):
        errors.append("key_decisions 须为数组且 ≤%d 条" % HANDOFF_DECISIONS_LIMIT)
    for field in ("artifacts", "next_inputs"):
        v = payload.get(field)
        if v is not None and not isinstance(v, dict):
            errors.append("%s 须为对象" % field)
    size = len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    if size > MAX_HANDOFF_BYTES:
        errors.append("handoff 超限（%d > %d bytes），请压缩 summary/删减决策" % (size, MAX_HANDOFF_BYTES))
    return errors


def write_handoff(sess: Path, phase: str, payload: dict) -> None:
    hd = sess / "handoff"
    hd.mkdir(parents=True, exist_ok=True)
    doc = dict(payload)
    doc.setdefault("phase", phase)
    doc["written_at"] = now_str()
    write_text_lf(hd / ("%s.json" % phase),
                  json.dumps(doc, ensure_ascii=False, indent=2) + "\n")


def read_handoff(sess: Path, phase: str) -> dict:
    f = sess / "handoff" / ("%s.json" % phase)
    if not f.is_file():
        err("handoff 不存在: %s（先完成该阶段）" % f)
    return json.loads(f.read_text(encoding="utf-8"))


# ============================================================
# 看板与状态推进
# ============================================================

def stage_icon(st: dict, is_current: bool) -> str:
    status = st.get("status", "pending")
    if status == "completed":
        return "✅"
    if status == "skipped":
        return "⏭️"
    if is_current or status == "in_progress":
        return "🔄"
    return "⏳"


def render_board(state: dict, pipeline: dict) -> str:
    lines = ["📊 %s  [%s]  %s" % (state["feature"], state["pipeline"]["id"], state["display_name"]),
             "━━━━━━━━━━━━━━━━━━━━━━━━━━━"]
    cur = state.get("current_phase")
    for s in pipeline["stages"]:
        st = state["phases"].get(s["id"], {})
        icon = stage_icon(st, s["id"] == cur)
        label = {"completed": "完成", "skipped": "跳过",
                 "in_progress": "进行中", "pending": "未开始"}[st.get("status", "pending")]
        if s["id"] == cur and st.get("status") == "pending":
            label = "进行中"
        extra = ""
        if st.get("status") == "skipped" and st.get("skip_reason"):
            extra = "（%s）" % st["skip_reason"]
        lines.append("%s %-16s %s%s" % (icon, s["id"], label, extra))
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    if cur is None:
        lines.append("全部阶段已完成 ✅  可执行 status / gate 复查")
    else:
        stage = stage_by_id(pipeline, cur)
        next_hint = "完成产物后执行 phase-complete 收口"
        if stage_requires_confirm(stage):
            next_hint = "🔒 强制确认点：先展示产物取得用户确认，收口须带 --user-confirmed"
        elif stage and stage.get("confirm_point"):
            next_hint = "完成产物后先向用户展示确认，再 phase-complete 收口"
        lines.append("当前阶段：%s　下一步：%s" % (cur, next_hint))
    return "\n".join(lines)


# ============================================================
# 子命令实现：phase-complete / gate / handoff / status / restore
# ============================================================

def cmd_gate(args) -> None:
    docs, _ = ensure_feature(args.spec_root, args.feature)
    sess = session_dir(args.spec_root, args.feature)
    state = load_state(sess)
    pipeline = load_pipeline_matching(args.spec_root, state)
    phase = args.phase or state.get("current_phase") or phase_ids(pipeline)[-1]
    if phase is None:
        err("当前无进行中阶段")
    result = run_gate(args.spec_root, docs, state, pipeline, phase, precheck=True)
    print_gate(result)
    if stage_requires_confirm(stage_by_id(pipeline, phase)):
        print("🔒 本阶段为强制确认点：收口必须带 --user-confirmed \"<用户确认说明>\"，"
              "否则 phase-complete 会被拒绝")
    sys.exit(0 if result["passed"] else 1)


def cmd_phase_complete(args) -> None:
    docs, sess = ensure_feature(args.spec_root, args.feature)
    state = load_state(sess)
    pipeline = load_pipeline_matching(args.spec_root, state)
    phase = args.phase
    ids = phase_ids(pipeline)
    if phase not in ids:
        err("阶段不存在: %s（流水线含: %s）" % (phase, ", ".join(ids)))
    cur = state.get("current_phase")
    if phase != cur:
        hint = "全部完成" if cur is None else "当前应完成阶段: %s" % cur
        err("越级收口被拒：期望 [%s]，收到 [%s]（%s）" % (cur, phase, hint))

    # ---- 跳过分支 ----
    if args.skip is not None:
        reason = args.skip.strip()
        if not reason:
            err("--skip 必须提供原因")
        state["phases"][phase] = {"status": "skipped", "completed_at": "", "skip_reason": reason}
        _advance_current(state, ids)
        save_state(sess, state)
        write_index(docs, state, pipeline)
        print("⏭️ 已跳过 %s：%s" % (phase, reason))
        print(render_board(state, pipeline))
        return

    # ---- 0. 强制确认点（机器门禁；先于门禁/handoff 校验，避免白跑一遍检查）----
    stage = stage_by_id(pipeline, phase)
    confirm_note = (args.user_confirmed or "").strip()
    if stage_requires_confirm(stage) and not confirm_note:
        err(confirm_hint(stage))

    # ---- 1. 门禁 ----
    # 解析 review-result（挂 review 门控的阶段收口需提供；解析失败直接拒绝）
    review_result = None
    if args.review_result is not None:
        try:
            review_result = json.loads(args.review_result)
        except json.JSONDecodeError as e:
            err("--review-result 不是合法 JSON: %s" % e)
    gate_result = run_gate(args.spec_root, docs, state, pipeline, phase,
                           review_result=review_result, precheck=False)
    print_gate(gate_result)
    if not gate_result["passed"]:
        err("门禁未通过，状态未变更。修复后重试 phase-complete")

    # ---- 2. handoff ----
    if args.handoff is None:
        err("缺少 --handoff '<json>'（该阶段产物摘要，四字段：summary/key_decisions/artifacts/next_inputs）")
    try:
        payload = json.loads(args.handoff)
    except json.JSONDecodeError as e:
        err("--handoff 不是合法 JSON: %s" % e)
    hf_errors = validate_handoff_payload(payload)
    if hf_errors:
        err("handoff 不合法:\n  " + "\n  ".join(hf_errors))

    # ---- 3. 决策 ----
    if args.decision:
        state["decisions"].append("%s %s: %s" % (today_str(), phase, args.decision))

    # ---- 4. 全部校验通过，落盘（原子性：以上任何失败都不改状态）----
    write_handoff(sess, phase, payload)
    completed = {"status": "completed", "completed_at": now_str(),
                 "gate_result": gate_result}
    if confirm_note:
        # 确认留痕：谁在什么时候确认的、确认了什么（用于事后审计"是不是真的用户点的头"）
        completed["user_confirmed"] = {"at": now_str(), "note": confirm_note}
    state["phases"][phase] = completed
    _advance_current(state, ids)
    save_state(sess, state)
    write_index(docs, state, pipeline)
    print("✅ %s 阶段收口完成（门禁 %s 项全过）" % (phase, len(gate_result["checks"])))
    if confirm_note:
        print("🔒 用户确认已留痕：%s" % confirm_note)
    print(render_board(state, pipeline))


def _advance_current(state: dict, ids: list) -> None:
    cur = state.get("current_phase")
    if cur is None:
        return
    idx = ids.index(cur)
    state["current_phase"] = ids[idx + 1] if idx + 1 < len(ids) else None


def cmd_handoff(args) -> None:
    ensure_feature(args.spec_root, args.feature)
    sess = session_dir(args.spec_root, args.feature)
    if args.action == "read":
        print(json.dumps(read_handoff(sess, args.phase), ensure_ascii=False, indent=2))
    elif args.action == "list":
        hd = sess / "handoff"
        files = sorted(hd.glob("*.json")) if hd.is_dir() else []
        if not files:
            print("（尚无任何阶段 handoff）")
        for f in files:
            print(f.stem)


def cmd_status(args) -> None:
    ensure_feature(args.spec_root, args.feature)
    sess = session_dir(args.spec_root, args.feature)
    state = load_state(sess)
    pipeline = load_pipeline_matching(args.spec_root, state)
    print(render_board(state, pipeline))
    total = len(pipeline["stages"])
    done = sum(1 for s in pipeline["stages"]
               if state["phases"].get(s["id"], {}).get("status") == "completed")
    skipped = sum(1 for s in pipeline["stages"]
                  if state["phases"].get(s["id"], {}).get("status") == "skipped")
    print("进度: %d/%d 完成%s" % (done, total, "，%d 跳过" % skipped if skipped else ""))


def cmd_restore(args) -> None:
    ensure_feature(args.spec_root, args.feature)
    sess = session_dir(args.spec_root, args.feature)
    state = load_state(sess)
    pipeline = load_pipeline_matching(args.spec_root, state)

    # 找最近完成阶段的 handoff（按流水线顺序取最后一个 completed 且 handoff 存在的阶段）
    last_handoff = None
    for s in pipeline["stages"]:
        st = state["phases"].get(s["id"], {})
        hf = sess / "handoff" / ("%s.json" % s["id"])
        if st.get("status") == "completed" and hf.is_file():
            last_handoff = json.loads(hf.read_text(encoding="utf-8"))

    cur = state.get("current_phase")
    stage = stage_by_id(pipeline, cur) if cur else None
    cur_tools = (stage or {}).get("tools") or []
    if cur is None:
        suggest = "全部阶段已完成；可执行 status 复查或开始新 feature"
    elif stage_requires_confirm(stage):
        suggest = ("继续 [%s]：阅读产物并完成填写后，**必须先展示给用户并取得明确确认**，"
                   "再带 --user-confirmed \"<确认说明>\" 收口（缺此项会被机器拒绝）" % cur)
    elif stage and stage.get("confirm_point"):
        suggest = "继续 [%s]：阅读产物并完成填写后，先向用户展示确认，再 phase-complete" % cur
    else:
        suggest = "继续 [%s]：阅读上游 handoff，完成产物后执行 phase-complete" % cur
    if cur_tools:
        names = "、".join(str(t.get("skill", "?")) for t in cur_tools)
        roles = "；".join("%s：%s" % (t.get("skill", "?"), t.get("role", ""))
                         for t in cur_tools if t.get("role"))
        suggest += "｜本阶段绑定 skill: %s%s" % (names, ("（%s）" % roles) if roles else "")

    if args.json:
        out = {
            "feature": state["feature"], "display_name": state["display_name"],
            "pipeline": state["pipeline"], "spec_root": state["spec_root"],
            "current_phase": cur,
            "current_stage_tools": cur_tools,
            "current_stage_require_confirm": stage_requires_confirm(stage),
            "phases": {sid: {"status": p["status"], "completed_at": p.get("completed_at", ""),
                             **({"skip_reason": p["skip_reason"]} if p.get("skip_reason") else {})}
                       for sid, p in state["phases"].items()},
            "decisions": state["decisions"],
            "last_handoff": last_handoff,
            "suggest": suggest,
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(render_board(state, pipeline))
        print("Pipeline: %s（%s）" % (state["pipeline"]["id"], state["pipeline"].get("source", "")))
        if last_handoff:
            print("--- 最近 handoff [%s] ---" % last_handoff.get("phase", "?"))
            print("summary: %s" % last_handoff.get("summary", ""))
            print("key_decisions:")
            for d in last_handoff.get("key_decisions", []):
                print("  - %s" % d)
        print("建议: %s" % suggest)


# ============================================================
# 主入口
# ============================================================

def _force_utf8_output() -> None:
    """Windows 控制台/管道默认 GBK：输出 emoji（✅🔄❌）会触发 UnicodeEncodeError。
    统一把 stdout/stderr 切到 UTF-8（Python 3.7+）；异常时静默降级，不影响主流程。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def main() -> None:
    _force_utf8_output()
    parser = argparse.ArgumentParser(prog="spec_cli.py", description="spec-workflow 编排引擎")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="初始化一个 feature（按流水线定义生成模板与 state）")
    p.add_argument("spec_root", help="spec 根目录（如 ./spec）")
    p.add_argument("feature", help="feature 名，如 user-auth")
    p.add_argument("--name", default=None, help="显示名（默认用 feature 名）")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("phase-complete", help="阶段收口（门禁达标才流转）")
    p.add_argument("spec_root")
    p.add_argument("feature")
    p.add_argument("phase", help="当前应完成的阶段 id")
    p.add_argument("--handoff", default=None, help="handoff JSON（四字段）")
    p.add_argument("--skip", default=None, metavar="原因", help="跳过当前阶段并注明原因")
    p.add_argument("--decision", default=None, help="可选：追加一条决策记录")
    p.add_argument("--review-result", default=None,
                   help="review 门控阶段必填：spec-health-check 评审 JSON（score/issues）")
    p.add_argument("--user-confirmed", default=None, metavar="说明",
                   help="强制确认点阶段（require_confirm=true）必填：用户明确确认的原话或要点。"
                        "缺此项直接拒绝收口")
    p.set_defaults(func=cmd_phase_complete)

    p = sub.add_parser("gate", help="门禁检查（可独立预检）")
    p.add_argument("spec_root")
    p.add_argument("feature")
    p.add_argument("--phase", default=None, help="指定阶段（默认当前阶段）")
    p.set_defaults(func=cmd_gate)

    p = sub.add_parser("handoff", help="阶段数据交接")
    p.add_argument("action", choices=("read", "list"))
    p.add_argument("spec_root")
    p.add_argument("feature")
    p.add_argument("phase", nargs="?", default=None, help="read 时的阶段 id")
    p.set_defaults(func=cmd_handoff)

    p = sub.add_parser("status", help="进度看板")
    p.add_argument("spec_root")
    p.add_argument("feature")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("restore", help="断点续传（输出状态与最近 handoff）")
    p.add_argument("spec_root")
    p.add_argument("feature")
    p.add_argument("--json", action="store_true", help="机器可读 JSON 输出")
    p.set_defaults(func=cmd_restore)

    args = parser.parse_args()
    if hasattr(args, "func"):
        args.func(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
