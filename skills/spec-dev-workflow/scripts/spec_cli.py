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

def load_pipeline(spec_root: str) -> dict:
    """查找顺序：<spec-root>/pipeline.json → skill 内置 pipelines/default.json"""
    project_cfg = Path(spec_root) / "pipeline.json"
    if project_cfg.is_file():
        try:
            p = json.loads(project_cfg.read_text(encoding="utf-8"))
        except Exception as e:
            err("项目 pipeline.json 解析失败: %s (%s)" % (project_cfg, e))
        p["_source"] = {"path": str(project_cfg), "kind": "project"}
    else:
        default_cfg = PIPELINES_DIR / "default.json"
        p = json.loads(default_cfg.read_text(encoding="utf-8"))
        p["_source"] = {"path": str(default_cfg), "kind": "builtin"}
    errors = validate_pipeline(p)
    if errors:
        err("pipeline 定义不合法:\n  " + "\n  ".join(errors))
    return p


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
        gate = s.get("gate", {})
        for check in gate.get("checks", []):
            ctype = check.get("type")
            if ctype not in ("builtin", "command", "review"):
                errors.append("[%s] gate.check.type 非法: %s" % (sid, ctype))
            if ctype == "command" and not check.get("cmd"):
                errors.append("[%s] command 检查缺少 cmd" % sid)
            if ctype == "review":
                ms = check.get("min_score", 80)
                if not isinstance(ms, (int, float)) or not (0 <= ms <= 100):
                    errors.append("[%s] review 的 min_score 须为 0-100 数值" % sid)
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
    print("   pipeline: %s（%s）" % (pipeline["id"], pipeline["_source"]["kind"]))
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


def run_command_check(spec_root: Path, fd: Path, stage: dict, check: dict) -> dict:
    cmd = _platform_cmd(check["cmd"])
    try:
        proc = subprocess.run(cmd, shell=True, cwd=str(spec_root), timeout=CMD_TIMEOUT,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        output = proc.stdout.decode("utf-8", errors="replace")[:CMD_OUTPUT_LIMIT]
        return {"type": "command", "cmd": cmd, "passed": proc.returncode == 0,
                "detail": output.strip() or ("exit=%d" % proc.returncode),
                "exit": proc.returncode}
    except subprocess.TimeoutExpired:
        return {"type": "command", "cmd": cmd, "passed": False,
                "detail": "超时（>%ds）" % CMD_TIMEOUT}
    except Exception as e:
        return {"type": "command", "cmd": cmd, "passed": False, "detail": "执行异常: %s" % e}


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
    """review 门控：AI 按 spec-health-check 评审产出 --review-result，引擎机器把关分数阈值"""
    min_score = check.get("min_score", 80)
    if precheck:
        return {"type": "review", "min_score": min_score, "passed": True, "skipped": True,
                "detail": "review 门控：收口时校验（gate 预检跳过，达标线 %d）" % min_score}
    if review_result is None:
        return {"type": "review", "min_score": min_score, "passed": False,
                "detail": "缺少 --review-result（须按 spec-health-check 四维评审提供 score/issues）"}
    errors = validate_review_result(review_result)
    if errors:
        return {"type": "review", "min_score": min_score, "passed": False,
                "detail": "review-result 不合法: " + "; ".join(errors)}
    score = review_result["score"]
    issues = review_result.get("issues", [])
    passed = score >= min_score
    detail = "score=%d %s 达标线 %d" % (score, "≥" if passed else "<", min_score)
    if not passed and issues:
        top = "; ".join("[%s] %s" % (i.get("severity", "?"), i.get("desc", "")) for i in issues[:3])
        detail += " | issues: " + top
    return {"type": "review", "min_score": min_score, "score": score, "passed": passed,
            "detail": detail, "issues": issues, "gate": review_result.get("gate")}


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
        if stage and stage.get("confirm_point"):
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
    pipeline = load_pipeline(args.spec_root)
    phase = args.phase or state.get("current_phase") or phase_ids(pipeline)[-1]
    if phase is None:
        err("当前无进行中阶段")
    result = run_gate(args.spec_root, docs, state, pipeline, phase, precheck=True)
    print_gate(result)
    sys.exit(0 if result["passed"] else 1)


def cmd_phase_complete(args) -> None:
    docs, sess = ensure_feature(args.spec_root, args.feature)
    state = load_state(sess)
    pipeline = load_pipeline(args.spec_root)
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
    state["phases"][phase] = {"status": "completed", "completed_at": now_str(),
                              "gate_result": gate_result}
    _advance_current(state, ids)
    save_state(sess, state)
    write_index(docs, state, pipeline)
    print("✅ %s 阶段收口完成（门禁 %s 项全过）" % (phase, len(gate_result["checks"])))
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
    pipeline = load_pipeline(args.spec_root)
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
    pipeline = load_pipeline(args.spec_root)

    # 找最近完成阶段的 handoff（按流水线顺序取最后一个 completed 且 handoff 存在的阶段）
    last_handoff = None
    for s in pipeline["stages"]:
        st = state["phases"].get(s["id"], {})
        hf = sess / "handoff" / ("%s.json" % s["id"])
        if st.get("status") == "completed" and hf.is_file():
            last_handoff = json.loads(hf.read_text(encoding="utf-8"))

    cur = state.get("current_phase")
    if cur is None:
        suggest = "全部阶段已完成；可执行 status 复查或开始新 feature"
    else:
        stage = stage_by_id(pipeline, cur)
        if stage and stage.get("confirm_point"):
            suggest = "继续 [%s]：阅读产物并完成填写后，先向用户展示确认，再 phase-complete" % cur
        else:
            suggest = "继续 [%s]：阅读上游 handoff，完成产物后执行 phase-complete" % cur

    if args.json:
        out = {
            "feature": state["feature"], "display_name": state["display_name"],
            "pipeline": state["pipeline"], "spec_root": state["spec_root"],
            "current_phase": cur,
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
