#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Spec 健康度检查脚本（结构层）——health-check.sh 的跨平台等价实现。

用法: python health-check.py <spec根目录> <名称> [--version <版本>]
示例1（feature 单层模式）: python health-check.py ./spec user-auth
示例2（版本/阶段模式）:   python health-check.py ./spec v0.0 --version V1

与 health-check.sh 逐行同逻辑：结构检查 + 交叉一致性统计 + SUMMARY 块，
输出与退出码语义一致（ERROR>0 → exit 1）。
选择依据：Windows/Linux/macOS 只要装了 Python 3.7+ 即可运行（原生）；
bash 版 health-check.sh 需 Git Bash/WSL。
"""
import os
import re
import sys

REQUIRED_FILES = [
    "00-index.md",
    "01-requirements.md",
    "02-design.md",
    "03-implementation-plan.md",
    "04-unit-test-plan.md",
    "05-integration-test-plan.md",
    "06-code-review-report.md",
    "07-docs-update-plan.md",
    "08-commit.md",
]

SYSTEM_PLACEHOLDERS = ["{功能名称}", "{YYYYMMDD-feature-name}", "{SPEC_PATH}", "{YYYY-MM-DD}"]

LINE_TASKS_03 = re.compile(r"^\| *T[0-9]+ *\|")          # bash: ^\| *T[0-9]+ *\|
LINE_TASKS_08 = re.compile(r"^- \[[ x!-]\] T[0-9]+")     # bash: ^- \[[ x!-]\] T[0-9]+
LINE_TASKS_08_DONE = re.compile(r"^- \[x\] T[0-9]+")      # bash: ^- \[x\] T[0-9]+
LINE_AC = re.compile(r"^\| *AC-[0-9]+")                   # bash: ^\| *AC-[0-9]+
LINE_TC = re.compile(r"^\| *TC-[0-9]+")                   # bash: ^\| *TC-[0-9]+
LINE_CR = re.compile(r"^\| *[SPCMT][0-9]+ *\|")           # bash: ^\| *[SPCMT][0-9]+ *\|
CASE_MARK = re.compile(r"\[[ x-]\] *(\||$)")              # bash: \[[ x-]\] *(\||$)


def file_size(path):
    return os.path.getsize(path) if os.path.isfile(path) else 0


def read_text(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def count_lines(text, regex, needle=None):
    """按 bash grep -cE 语义：对逐行匹配 regex 的行计数；needle 提供则再过滤含子串。"""
    n = 0
    for line in text.splitlines():
        if regex.match(line) and (needle is None or needle in line):
            n += 1
    return n


def index_status(path):
    """00-index 阶段状态：bash 对 '^| ' 且非 '| 阶段'/'|---' 的行做 emoji 分类。"""
    done = doing = todo = skipped = 0
    for line in read_text(path).splitlines():
        if not line.startswith("| "):
            continue
        if line.startswith("| 阶段") or line.startswith("|---"):
            continue
        # bash case 首个匹配即归类，与下面 if/elif 链顺序一致
        if "✅" in line:
            done += 1
        elif "🔄" in line:
            doing += 1
        elif "⏭️" in line:
            skipped += 1
        elif "⏳" in line:
            todo += 1
    return done, doing, todo, skipped


def _force_utf8_output():
    """Windows 控制台/管道默认 GBK：输出 emoji（🏥✅❌⚠️）会触发 UnicodeEncodeError。
    统一把 stdout/stderr 切到 UTF-8（Python 3.7+）；异常时静默降级，不影响主流程。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def main(argv=None):
    _force_utf8_output()
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) < 2:
        print("用法: python health-check.py <spec根目录> <名称> [--version <版本>]")
        print("示例1（feature 单层模式）: python health-check.py ./spec user-auth")
        print("示例2（版本/阶段模式）:   python health-check.py ./spec v0.0 --version V1")
        return 1

    spec_root, name = argv[0], argv[1]
    rest = argv[2:]
    version = ""
    while rest:
        a = rest.pop(0)
        if a == "--version":
            if not rest:
                print("错误: --version 需要参数")
                return 1
            version = rest.pop(0)
        else:
            print("错误: 未知参数: %s" % a)
            return 1

    if version:
        spec_dir = os.path.join(spec_root, version, "spec", name)
    else:
        spec_dir = os.path.join(spec_root, name)

    if not os.path.isdir(spec_dir):
        print("❌ Spec 目录不存在: %s" % spec_dir)
        print("请确认路径，或先运行初始化脚本创建 spec 目录")
        return 1

    print("🏥 Spec 健康度检查: %s%s" % (name, "（%s）" % version if version else ""))
    print("   目录: %s" % spec_dir)
    print("")

    errors = 0
    warnings = 0

    # ============ 1. 结构检查 ============
    print("--- 1. 结构检查 ---")

    missing = 0
    for fn in REQUIRED_FILES:
        if not os.path.isfile(os.path.join(spec_dir, fn)):
            print("  ❌ 文件缺失: %s" % fn)
            missing += 1
            errors += 1
    if missing == 0:
        print("  ✅ 9 个文件齐全")

    # 空模板检测（< 100 bytes）
    for fn in REQUIRED_FILES:
        p = os.path.join(spec_dir, fn)
        if os.path.isfile(p):
            size = file_size(p)
            if size < 100:
                print("  ⚠️  %s 内容过少（%d bytes），可能未填写" % (fn, size))
                warnings += 1

    # 系统占位符残留
    placeholder_residual = 0
    for fn in REQUIRED_FILES:
        p = os.path.join(spec_dir, fn)
        if not os.path.isfile(p):
            continue
        text = read_text(p)
        found = [ph for ph in SYSTEM_PLACEHOLDERS if ph in text]
        if found:
            print("  ⚠️  %s 存在未替换的系统占位符:%s" % (fn, "".join(" %s" % x for x in found)))
            placeholder_residual += 1
            warnings += 1

    print("")

    # ============ 2. 交叉一致性统计 ============
    print("--- 2. 交叉一致性统计 ---")

    idx_path = os.path.join(spec_dir, "00-index.md")
    index_done = index_doing = index_todo = index_skipped = 0
    if os.path.isfile(idx_path):
        index_done, index_doing, index_todo, index_skipped = index_status(idx_path)
    print("  00-index 状态: 完成 %d/9 | 进行中 %d | 待开始 %d | 已跳过 %d"
          % (index_done, index_doing, index_todo, index_skipped))

    p03 = os.path.join(spec_dir, "03-implementation-plan.md")
    p08 = os.path.join(spec_dir, "08-commit.md")
    tasks_total_03 = tasks_done_03 = tasks_total_08 = tasks_done_08 = 0
    if os.path.isfile(p03):
        text03 = read_text(p03)
        tasks_total_03 = count_lines(text03, LINE_TASKS_03)
        tasks_done_03 = sum(1 for line in text03.splitlines()
                            if LINE_TASKS_03.match(line) and ("[x]" in line or "✅" in line))
    if os.path.isfile(p08):
        text08 = read_text(p08)
        tasks_total_08 = count_lines(text08, LINE_TASKS_08)
        tasks_done_08 = count_lines(text08, LINE_TASKS_08_DONE)
    print("  03 实现计划任务: %d/%d 已勾选" % (tasks_done_03, tasks_total_03))
    print("  08 完成记录任务: %d/%d 已勾选" % (tasks_done_08, tasks_total_08))

    ac_count = 0
    p01 = os.path.join(spec_dir, "01-requirements.md")
    if os.path.isfile(p01):
        ac_count = count_lines(read_text(p01), LINE_AC)
    print("  01 验收标准（AC）: %d 条" % ac_count)

    test_cases = 0
    p04 = os.path.join(spec_dir, "04-unit-test-plan.md")
    if os.path.isfile(p04):
        text04 = read_text(p04)
        cases_template = sum(1 for line in text04.splitlines()
                             if line.startswith("|") and CASE_MARK.search(line))
        cases_tc = count_lines(text04, LINE_TC)
        test_cases = cases_template if cases_template >= cases_tc else cases_tc
    print("  04 单元测试用例: %d 条" % test_cases)

    cr_issues = 0
    p06 = os.path.join(spec_dir, "06-code-review-report.md")
    if os.path.isfile(p06):
        cr_issues = count_lines(read_text(p06), LINE_CR)
    print("  06 CR 问题记录: %d 条" % cr_issues)

    doc_07_bytes = 0
    p07 = os.path.join(spec_dir, "07-docs-update-plan.md")
    if os.path.isfile(p07):
        doc_07_bytes = file_size(p07)
    print("  07 文档更新文件: %d bytes" % doc_07_bytes)

    # ============ 3. 一致性告警 ============
    print("")
    print("--- 3. 一致性告警 ---")

    consistency_warn = 0

    def warn(msg):
        nonlocal consistency_warn, warnings
        print("  ⚠️  %s" % msg)
        consistency_warn += 1
        warnings += 1

    if index_done >= 8 and tasks_total_03 > 0 and tasks_done_03 < tasks_total_03:
        warn("00-index 声称 8 阶段完成，但 03 任务仅勾选 %d/%d" % (tasks_done_03, tasks_total_03))
    if tasks_total_03 > 0 and tasks_total_08 > 0 and tasks_done_03 != tasks_done_08:
        warn("03（%d/%d）与 08（%d/%d）任务勾选数不一致"
             % (tasks_done_03, tasks_total_03, tasks_done_08, tasks_total_08))
    if index_done >= 1 and ac_count == 0:
        warn("需求阶段已开始/完成，但 01 中无编号验收标准（AC-xx）")
    if ac_count > 0 and test_cases == 0 and index_done >= 5:
        warn("测试阶段声称完成，但 04 中无测试用例记录")
    if index_done >= 8 and doc_07_bytes > 0 and doc_07_bytes < 500:
        warn("文档更新阶段声称完成，但 07 仅 %d bytes，疑似流于形式" % doc_07_bytes)
    if index_done >= 7 and cr_issues == 0:
        warn("CR 报告零问题记录，真实审查通常至少有 MINOR/NIT 发现，留痕真实性存疑")

    if consistency_warn == 0:
        print("  ✅ 脚本层未发现一致性矛盾")

    # ============ 4. 机器可读摘要 ============
    has_report = int(os.path.isfile(os.path.join(spec_dir, "health-report.md")))

    print("")
    print("=== SUMMARY ===")
    print("spec_dir=%s" % spec_dir)
    print("spec_name=%s" % name)
    print("version=%s" % (version or "none"))
    print("has_report=%d" % has_report)
    print("file_missing=%d" % missing)
    print("placeholder_residual=%d" % placeholder_residual)
    print("index_done=%d" % index_done)
    print("index_doing=%d" % index_doing)
    print("index_todo=%d" % index_todo)
    print("index_skipped=%d" % index_skipped)
    print("tasks_total_03=%d" % tasks_total_03)
    print("tasks_done_03=%d" % tasks_done_03)
    print("tasks_total_08=%d" % tasks_total_08)
    print("tasks_done_08=%d" % tasks_done_08)
    print("acceptance_criteria=%d" % ac_count)
    print("unit_test_cases=%d" % test_cases)
    print("review_issues=%d" % cr_issues)
    print("doc_07_bytes=%d" % doc_07_bytes)
    print("structure_errors=%d" % errors)
    print("structure_warnings=%d" % warnings)
    print("consistency_warnings=%d" % consistency_warn)
    print("=== END SUMMARY ===")

    if errors == 0:
        print("✅ 结构检查完成（%d 个警告）" % warnings)
    else:
        print("❌ 结构检查发现 %d 个错误" % errors)
    if has_report:
        print("检测到已有 health-report.md → 回归检查：按 SKILL.md 核对遗留问题修复状态后更新同一份报告")
    else:
        print("首次检查：按 SKILL.md 四维评审框架生成 health-report.md")

    return 1 if errors > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
