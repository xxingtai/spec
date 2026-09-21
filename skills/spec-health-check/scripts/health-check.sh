#!/usr/bin/env bash
# Spec 健康度检查脚本（结构层）
# 用法: bash health-check.sh <spec根目录> <名称> [--version <版本>]
# 参数与 spec-dev-workflow 的 init-spec.sh / validate-spec.sh 完全一致
# 示例1（feature 单层模式）: bash health-check.sh ./spec user-auth
# 示例2（版本/阶段模式）:   bash health-check.sh ./spec v0.0 --version V1
#
# 功能（双层检查的脚本层）:
#   1. 结构检查：文件完整性、内容量、系统占位符残留
#   2. 交叉一致性统计：阶段状态、任务勾选、AC 数、测试用例数、CR 问题数
#   3. 输出机器可读的 SUMMARY 块（供模型层语义评审使用，避免模型自己数数出错）

set -euo pipefail

if [ $# -lt 2 ]; then
    echo "用法: bash $0 <spec根目录> <名称> [--version <版本>]"
    echo "示例1（feature 单层模式）: bash $0 ./spec user-auth"
    echo "示例2（版本/阶段模式）:   bash $0 ./spec v0.0 --version V1"
    exit 1
fi

SPEC_ROOT="$1"
NAME="$2"
shift 2

VERSION=""
while [ $# -gt 0 ]; do
    case "$1" in
        --version)
            [ $# -ge 2 ] || { echo "错误: --version 需要参数"; exit 1; }
            VERSION="$2"; shift 2 ;;
        *)
            echo "错误: 未知参数: $1"; exit 1 ;;
    esac
done

# 计算目标目录
if [ -n "$VERSION" ]; then
    SPEC_DIR="${SPEC_ROOT}/${VERSION}/spec/${NAME}"
else
    SPEC_DIR="${SPEC_ROOT}/${NAME}"
fi

if [ ! -d "$SPEC_DIR" ]; then
    echo "❌ Spec 目录不存在: $SPEC_DIR"
    echo "请确认路径，或先运行: bash $(dirname "$(realpath "$0")")/../../spec-dev-workflow/scripts/init-spec.sh $SPEC_ROOT $NAME${VERSION:+ --version $VERSION}"
    exit 1
fi

echo "🏥 Spec 健康度检查: $NAME${VERSION:+（$VERSION）}"
echo "   目录: $SPEC_DIR"
echo ""

REQUIRED_FILES=(
    "00-index.md"
    "01-requirements.md"
    "02-design.md"
    "03-implementation-plan.md"
    "04-unit-test-plan.md"
    "05-integration-test-plan.md"
    "06-code-review-report.md"
    "07-docs-update-plan.md"
    "08-commit.md"
)

ERRORS=0
WARNINGS=0

file_size() {
    [ -f "$1" ] && wc -c < "$1" | tr -d ' ' || echo "0"
}

# ============================================================
# 1. 结构检查
# ============================================================
echo "--- 1. 结构检查 ---"

MISSING=0
for file in "${REQUIRED_FILES[@]}"; do
    if [ ! -f "$SPEC_DIR/$file" ]; then
        echo "  ❌ 文件缺失: $file"
        MISSING=$((MISSING + 1))
        ERRORS=$((ERRORS + 1))
    fi
done
if [ "$MISSING" -eq 0 ]; then
    echo "  ✅ 9 个文件齐全"
fi

# 空模板检测（< 100 bytes）
for file in "${REQUIRED_FILES[@]}"; do
    if [ -f "$SPEC_DIR/$file" ]; then
        size=$(file_size "$SPEC_DIR/$file")
        if [ "$size" -lt 100 ]; then
            echo "  ⚠️  $file 内容过少（$size bytes），可能未填写"
            WARNINGS=$((WARNINGS + 1))
        fi
    fi
done

# 系统占位符残留（白名单精确匹配，说明性占位符不算）
SYSTEM_PLACEHOLDERS=('{功能名称}' '{YYYYMMDD-feature-name}' '{SPEC_PATH}' '{YYYY-MM-DD}')
PLACEHOLDER_RESIDUAL=0
for file in "${REQUIRED_FILES[@]}"; do
    if [ -f "$SPEC_DIR/$file" ]; then
        found=""
        for ph in "${SYSTEM_PLACEHOLDERS[@]}"; do
            if grep -qF "$ph" "$SPEC_DIR/$file" 2>/dev/null; then
                found="$found $ph"
            fi
        done
        if [ -n "$found" ]; then
            echo "  ⚠️  $file 存在未替换的系统占位符:$found"
            PLACEHOLDER_RESIDUAL=$((PLACEHOLDER_RESIDUAL + 1))
            WARNINGS=$((WARNINGS + 1))
        fi
    fi
done

echo ""

# ============================================================
# 2. 交叉一致性统计（机器统计，供模型层使用）
# ============================================================
echo "--- 2. 交叉一致性统计 ---"

# 2.1 00-index 阶段状态
INDEX_DONE=0; INDEX_DOING=0; INDEX_TODO=0; INDEX_SKIPPED=0; INDEX_TOTAL=9
if [ -f "$SPEC_DIR/00-index.md" ]; then
    while IFS= read -r line; do
        case "$line" in
            *"✅"*) INDEX_DONE=$((INDEX_DONE + 1)) ;;
            *"🔄"*) INDEX_DOING=$((INDEX_DOING + 1)) ;;
            *"⏭️"*) INDEX_SKIPPED=$((INDEX_SKIPPED + 1)) ;;
            *"⏳"*) INDEX_TODO=$((INDEX_TODO + 1)) ;;
        esac
    done < <(grep '^| ' "$SPEC_DIR/00-index.md" | grep -v '^| 阶段' | grep -v '^|---' || true)
fi
echo "  00-index 状态: 完成 ${INDEX_DONE}/9 | 进行中 ${INDEX_DOING} | 待开始 ${INDEX_TODO} | 已跳过 ${INDEX_SKIPPED}"

# 2.2 任务勾选（03 实现计划 vs 08 完成记录）
TASKS_TOTAL_03=0; TASKS_DONE_03=0; TASKS_TOTAL_08=0; TASKS_DONE_08=0
if [ -f "$SPEC_DIR/03-implementation-plan.md" ]; then
    TASKS_TOTAL_03=$(grep -cE '^\| *T[0-9]+ *\|' "$SPEC_DIR/03-implementation-plan.md" || true)
    # 勾选状态兼容两种写法：模板格式 [x] / 自定义格式 ✅
    TASKS_DONE_03=$(grep -E '^\| *T[0-9]+ *\|' "$SPEC_DIR/03-implementation-plan.md" 2>/dev/null | grep -cE '\[x\]|✅' || true)
fi
if [ -f "$SPEC_DIR/08-commit.md" ]; then
    TASKS_TOTAL_08=$(grep -cE '^- \[[ x!-]\] T[0-9]+' "$SPEC_DIR/08-commit.md" || true)
    TASKS_DONE_08=$(grep -cE '^- \[x\] T[0-9]+' "$SPEC_DIR/08-commit.md" || true)
fi
echo "  03 实现计划任务: ${TASKS_DONE_03}/${TASKS_TOTAL_03} 已勾选"
echo "  08 完成记录任务: ${TASKS_DONE_08}/${TASKS_TOTAL_08} 已勾选"

# 2.3 验收标准与测试用例
AC_COUNT=0
if [ -f "$SPEC_DIR/01-requirements.md" ]; then
    AC_COUNT=$(grep -cE '^\| *AC-[0-9]+' "$SPEC_DIR/01-requirements.md" || true)
fi
echo "  01 验收标准（AC）: ${AC_COUNT} 条"

TEST_CASES=0
if [ -f "$SPEC_DIR/04-unit-test-plan.md" ]; then
    # 兼容两种写法：模板格式（状态列含 [ ]/[x]）/ 自定义格式（TC-xx 编号行）
    CASES_TEMPLATE=$(grep -E '^\|' "$SPEC_DIR/04-unit-test-plan.md" 2>/dev/null | grep -cE '\[[ x-]\] *(\||$)' || true)
    CASES_TC=$(grep -cE '^\| *TC-[0-9]+' "$SPEC_DIR/04-unit-test-plan.md" || true)
    if [ "$CASES_TEMPLATE" -ge "$CASES_TC" ]; then
        TEST_CASES=$CASES_TEMPLATE
    else
        TEST_CASES=$CASES_TC
    fi
fi
echo "  04 单元测试用例: ${TEST_CASES} 条"

# 2.4 Code Review 问题数
CR_ISSUES=0
if [ -f "$SPEC_DIR/06-code-review-report.md" ]; then
    CR_ISSUES=$(grep -cE '^\| *[SPCMT][0-9]+ *\|' "$SPEC_DIR/06-code-review-report.md" || true)
fi
echo "  06 CR 问题记录: ${CR_ISSUES} 条"

# 2.5 文档更新文件大小（薄文件检测）
DOC_07_SIZE=0
if [ -f "$SPEC_DIR/07-docs-update-plan.md" ]; then
    DOC_07_SIZE=$(file_size "$SPEC_DIR/07-docs-update-plan.md")
fi
echo "  07 文档更新文件: ${DOC_07_SIZE} bytes"

# ============================================================
# 3. 硬性一致性告警（脚本层能判定的矛盾）
# ============================================================
echo ""
echo "--- 3. 一致性告警 ---"

CONSISTENCY_WARN=0
# 告警1: index 全部完成，但 03 任务未全部勾选
if [ "$INDEX_DONE" -ge 8 ] && [ "$TASKS_TOTAL_03" -gt 0 ] && [ "$TASKS_DONE_03" -lt "$TASKS_TOTAL_03" ]; then
    echo "  ⚠️  00-index 声称 8 阶段完成，但 03 任务仅勾选 ${TASKS_DONE_03}/${TASKS_TOTAL_03}"
    CONSISTENCY_WARN=$((CONSISTENCY_WARN + 1)); WARNINGS=$((WARNINGS + 1))
fi
# 告警2: 03 与 08 勾选数不一致
if [ "$TASKS_TOTAL_03" -gt 0 ] && [ "$TASKS_TOTAL_08" -gt 0 ] && [ "$TASKS_DONE_03" -ne "$TASKS_DONE_08" ]; then
    echo "  ⚠️  03（${TASKS_DONE_03}/${TASKS_TOTAL_03}）与 08（${TASKS_DONE_08}/${TASKS_TOTAL_08}）任务勾选数不一致"
    CONSISTENCY_WARN=$((CONSISTENCY_WARN + 1)); WARNINGS=$((WARNINGS + 1))
fi
# 告警3: 需求阶段完成但 AC 为 0
if [ "$INDEX_DONE" -ge 1 ] && [ "$AC_COUNT" -eq 0 ]; then
    echo "  ⚠️  需求阶段已开始/完成，但 01 中无编号验收标准（AC-xx）"
    CONSISTENCY_WARN=$((CONSISTENCY_WARN + 1)); WARNINGS=$((WARNINGS + 1))
fi
# 告警4: AC 数 > 0 但测试用例为 0 且测试阶段完成
if [ "$AC_COUNT" -gt 0 ] && [ "$TEST_CASES" -eq 0 ] && [ "$INDEX_DONE" -ge 5 ]; then
    echo "  ⚠️  测试阶段声称完成，但 04 中无测试用例记录"
    CONSISTENCY_WARN=$((CONSISTENCY_WARN + 1)); WARNINGS=$((WARNINGS + 1))
fi
# 告警5: 文档更新阶段完成但 07 过薄
if [ "$INDEX_DONE" -ge 8 ] && [ "$DOC_07_SIZE" -gt 0 ] && [ "$DOC_07_SIZE" -lt 500 ]; then
    echo "  ⚠️  文档更新阶段声称完成，但 07 仅 ${DOC_07_SIZE} bytes，疑似流于形式"
    CONSISTENCY_WARN=$((CONSISTENCY_WARN + 1)); WARNINGS=$((WARNINGS + 1))
fi
# 告警6: CR 零发现且审查阶段完成（全绿可疑）
if [ "$INDEX_DONE" -ge 7 ] && [ "$CR_ISSUES" -eq 0 ]; then
    echo "  ⚠️  CR 报告零问题记录，真实审查通常至少有 MINOR/NIT 发现，留痕真实性存疑"
    CONSISTENCY_WARN=$((CONSISTENCY_WARN + 1)); WARNINGS=$((WARNINGS + 1))
fi

if [ "$CONSISTENCY_WARN" -eq 0 ]; then
    echo "  ✅ 脚本层未发现一致性矛盾"
fi

# ============================================================
# 4. 机器可读摘要（模型层评审的输入，避免模型自己数数）
# ============================================================
REPORT_EXISTS=0
if [ -f "$SPEC_DIR/health-report.md" ]; then
    REPORT_EXISTS=1
fi

echo ""
echo "=== SUMMARY ==="
cat <<EOF
spec_dir=${SPEC_DIR}
spec_name=${NAME}
version=${VERSION:-none}
has_report=${REPORT_EXISTS}
file_missing=${MISSING}
placeholder_residual=${PLACEHOLDER_RESIDUAL}
index_done=${INDEX_DONE}
index_doing=${INDEX_DOING}
index_todo=${INDEX_TODO}
index_skipped=${INDEX_SKIPPED}
tasks_total_03=${TASKS_TOTAL_03}
tasks_done_03=${TASKS_DONE_03}
tasks_total_08=${TASKS_TOTAL_08}
tasks_done_08=${TASKS_DONE_08}
acceptance_criteria=${AC_COUNT}
unit_test_cases=${TEST_CASES}
review_issues=${CR_ISSUES}
doc_07_bytes=${DOC_07_SIZE}
structure_errors=${ERRORS}
structure_warnings=${WARNINGS}
consistency_warnings=${CONSISTENCY_WARN}
=== END SUMMARY ===
EOF

if [ "$ERRORS" -eq 0 ]; then
    echo "✅ 结构检查完成（${WARNINGS} 个警告）"
else
    echo "❌ 结构检查发现 ${ERRORS} 个错误"
fi
if [ "$REPORT_EXISTS" -eq 1 ]; then
    echo "检测到已有 health-report.md → 回归检查：按 SKILL.md 核对遗留问题修复状态后更新同一份报告"
else
    echo "首次检查：按 SKILL.md 四维评审框架生成 health-report.md"
fi
if [ "$ERRORS" -gt 0 ]; then
    exit 1
fi
