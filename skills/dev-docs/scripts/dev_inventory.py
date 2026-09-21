# -*- coding: utf-8 -*-
"""dev-docs 盘点核心：文件树扫描 / 语言指纹 / 模块划分 / 符号与端点枚举 / 确定性输出。

原则：
- 输出确定性：同一代码两次盘点结果 byte-identical（不写入时间戳；遍历顺序稳定）。
- 全语言统一走 tree-sitter（v1.5 适配器架构）：本模块只做编排，语言逻辑全部在
  dev_langs/ 适配器包中（每语言一个 LanguageAdapter 子类）。
- 该模块只做"读"，不写任何文档文件。
"""

import ast
import hashlib
import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import OrderedDict

from dev_langs import (  # noqa: F401  （EXT_LANG 对外兼容导出）
    EXT_LANG, get_adapter, identifier_counts, is_test_file, macro_defs,
)

# ---------------- 常量 ----------------

# catkin/ROS 工作空间的构建产物目录（project-type=catkin 时并入排除清单）
CATKIN_EXCLUDE = ["build", "devel", "install", "log"]
# 全文件清单的单文件大小上限：超过则跳过并记 note（防二进制/构建产物撑爆清单）
ALL_FILES_MAX_BYTES = 2 * 1024 * 1024

DEFAULT_EXCLUDE = [
    ".git", "node_modules", "__pycache__", "dist", "build", "out", "target",
    ".venv", "venv", "vendor", ".idea", ".vscode", ".codebuddy", ".specworkflow",
    "*.pyc", "*.pyo", "*.egg-info", "*.min.js", "*.map", ".d.ts",
    "migrations", "coverage", ".pytest_cache", ".tox", ".mypy_cache", "generated-images",
]

SCHEMA_VERSION = 2


# ---------------- 工具 ----------------

def eprint(*a, **k):
    print(*a, file=sys.stderr, **k)


def run_git(root, args):
    try:
        # 显式 UTF-8 + replace：Windows 中文环境 locale 为 GBK，git 输出含非 ASCII
        # 路径时 text=True 会按 locale 解码失败（异常被吞导致静默降级为非 git）。
        out = subprocess.run(
            ["git", "-C", root] + args,
            capture_output=True, encoding="utf-8", errors="replace", timeout=10
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:
        pass
    return None


def head_commit(root):
    return run_git(root, ["rev-parse", "--short", "HEAD"]) or ""


def is_git_root(root):
    return bool(run_git(root, ["rev-parse", "--is-inside-work-tree"]))


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def stable_dumps(obj):
    """确定性的 JSON 序列化：dict 按插入序但由构建器固定；此处排序顶层键不适用，
    改为：递归排序 dict 键，保证 byte-identical（覆盖任何插入顺序）。"""
    def _s(o):
        if isinstance(o, dict):
            return {k: _s(o[k]) for k in sorted(o)}
        if isinstance(o, (list, tuple)):
            return [_s(x) for x in o]
        return o
    return json.dumps(_s(obj), ensure_ascii=False, indent=2, sort_keys=False) + "\n"


def canon_hash(obj):
    return sha256_text(stable_dumps(obj))


def is_excluded(rel_path, extra):
    parts = rel_path.replace("\\", "/").split("/")
    name = parts[-1]
    for pat in list(DEFAULT_EXCLUDE) + (extra or []):
        if pat.startswith("*."):
            if name.endswith(pat[1:]):
                return True
        elif pat in parts or name == pat:
            return True
    return False


def iter_code_files(root, extra_exclude):
    """遍历代码文件，返回绝对路径列表（排序稳定）。"""
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        # 就地剪枝排除目录（os.walk 依赖 dirnames 修改）
        keep = []
        for d in sorted(dirnames):
            rel = os.path.relpath(os.path.join(dirpath, d), root)
            if not is_excluded(rel, extra_exclude):
                keep.append(d)
        dirnames[:] = keep
        for fn in sorted(filenames):
            rel = os.path.relpath(os.path.join(dirpath, fn), root)
            if is_excluded(rel, extra_exclude):
                continue
            ext = os.path.splitext(fn)[1].lower()
            if ext in EXT_LANG:
                out.append(os.path.join(dirpath, fn))
    return sorted(out)


def detect_langs(root, extra_exclude):
    langs = OrderedDict()
    for fp in iter_code_files(root, extra_exclude):
        ext = os.path.splitext(fp)[1].lower()
        lang = EXT_LANG.get(ext)
        if lang:
            langs.setdefault(lang, "reliable")  # tree-sitter 适配器全档 reliable
    return dict(langs)


def detect_project_type(root, extra_exclude):
    """auto 检测项目类型：目录树中发现 package.xml -> catkin，否则 generic。"""
    for dirpath, dirnames, filenames in os.walk(root):
        keep = []
        for d in sorted(dirnames):
            rel = os.path.relpath(os.path.join(dirpath, d), root)
            if not is_excluded(rel, extra_exclude):
                keep.append(d)
        dirnames[:] = keep
        if "package.xml" in filenames:
            return "catkin"
    return "generic"


def iter_all_files(root, extra_exclude):
    """全文件清单（不经 EXT_LANG 过滤；L0 防漏与漂移基线的事实源）。
    返回 (entries, hashes, notes)：
      entries=[{path, bytes, lang}]（lang=None 表示未登记语言，如 .msg/.cpp）；
      hashes={rel: sha256}（非 git 漂移基线）；
      notes=[{file, note}]（跳过的超大文件）。
    排序稳定；目录剪枝与 iter_code_files 一致。"""
    entries, hashes, notes = [], {}, []
    for dirpath, dirnames, filenames in os.walk(root):
        keep = []
        for d in sorted(dirnames):
            rel = os.path.relpath(os.path.join(dirpath, d), root)
            if not is_excluded(rel, extra_exclude):
                keep.append(d)
        dirnames[:] = keep
        for fn in sorted(filenames):
            fp = os.path.join(dirpath, fn)
            rel = _rel(root, fp)
            if is_excluded(rel, extra_exclude):
                continue
            try:
                st = os.stat(fp)
            except OSError:
                continue
            if st.st_size > ALL_FILES_MAX_BYTES:
                notes.append({"file": rel,
                              "note": "skipped_large %d bytes" % st.st_size})
                continue
            ext = os.path.splitext(fn)[1].lower()
            lang = EXT_LANG.get(ext)
            entries.append({"path": rel, "bytes": st.st_size, "lang": lang})
            hashes[rel] = sha256_file(fp)
    return entries, hashes, notes


def _rel(root, path):
    return os.path.relpath(path, root).replace("\\", "/")


# ---------------- 模块划分 ----------------

def _cmake_declared_files(pkg_dir):
    """CMakeLists 声明的消息/服务/可执行源文件（原文相对包目录）。"""
    path = os.path.join(pkg_dir, "CMakeLists.txt")
    if not os.path.isfile(path):
        return []
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return []
    out = []
    text = re.sub(r"#[^\n]*", "", text)      # 先剥掉注释：catkin 模板注释里有示例声明（Message1.msg 等）
    for m in re.finditer(r"add_(?:message|service|action)_files\s*\((.*?)\)", text, re.S):
        out.extend(re.findall(r"[\w./\-]+\.(?:msg|srv|action)", m.group(1)))
    for m in re.finditer(r"add_executable\s*\(\s*[\w\-]+\s+([^)]*)\)", text, re.S):
        out.extend(re.findall(r"[\w./\-]+\.(?:cpp|cc|cxx|c|py)", m.group(1)))
    return out


def _decl_file_exists(pkg_dir, rel):
    """声明的文件是否存在：依次尝试原路径、`msg/`、`srv/`、包根下的同名文件。"""
    base = os.path.basename(rel)
    for cand in (rel, os.path.join("msg", base), os.path.join("srv", base), base):
        if os.path.isfile(os.path.join(pkg_dir, cand)):
            return True
    return False


def build_decl_issues(root, pkgs):
    """构建声明一致性：CMakeLists 声明了但实际不存在的文件（构建风险）。

    典型实例：`src/pkg_vision/CMakeLists.txt` 声明 `CoreCmd.msg` / `TargetInfo.msg`，
    但 `src/pkg_vision/msg/` 目录缺失 —— 文档若照抄声明，读者按文档 `catkin_make` 会失败。
    这是**项目自身缺陷**（文档无法修复），故只报不拦（不纳入 check 的 FAIL 组）。"""
    issues = []
    for p in pkgs:
        pkg_dir = os.path.join(root, p["path"])
        missing = [f for f in _cmake_declared_files(pkg_dir) if not _decl_file_exists(pkg_dir, f)]
        if missing:
            issues.append({"module_path": p["path"], "name": p["name"],
                           "missing": sorted(set(missing))})
    return issues


def _catkin_packages(root, extra):
    """扫描 package.xml（catkin 包边界）：返回 [{path(相对), name}]，按路径排序。"""
    pkgs = []
    for dirpath, dirnames, filenames in os.walk(root):
        keep = []
        for d in sorted(dirnames):
            rel = os.path.relpath(os.path.join(dirpath, d), root)
            if not is_excluded(rel, extra):
                keep.append(d)
        dirnames[:] = keep
        if "package.xml" in filenames:
            rel = _rel(root, dirpath)
            name, ext_deps = None, set()
            try:
                ptree = ET.parse(os.path.join(dirpath, "package.xml"))
                name = ptree.findtext("name")
                for dep in ptree.iter():
                    if dep.tag in ("depend", "build_depend", "exec_depend", "run_depend",
                                   "build_export_depend", "test_depend", "doc_depend"):
                        if dep.text and dep.text.strip():
                            ext_deps.add(dep.text.strip())
            except (ET.ParseError, OSError):
                name = None
            pkgs.append({"path": rel, "name": (name or os.path.basename(rel)).strip(),
                         "external_deps": ext_deps})
    return sorted(pkgs, key=lambda x: x["path"])


def build_modules(root, extra_exclude, project_type=None):
    """代码文件归属划分模块。

    catkin：发现 package.xml 即以包目录为模块单元（包内子目录不拆分）；
    generic / 未找到包：原"自底向上聚合单链目录"算法（行为不变）：
    直接含代码文件的目录为候选；若某目录内只有一个候选子模块且自身无直接代码，
    则上聚到该目录，多个并列子模块则各自独立。根目录自身代码归 MOD-000-root。"""
    if project_type == "catkin":
        pkgs = _catkin_packages(root, extra_exclude)
        if pkgs:
            return _modules_from_pkgs(pkgs, root, extra_exclude)
    code_files = iter_code_files(root, extra_exclude)
    buckets = OrderedDict()  # rel_dir -> [files]
    root_files = []
    for fp in code_files:
        rel = _rel(root, fp)
        d = os.path.dirname(rel)
        if d == "":
            root_files.append(fp)
        else:
            buckets.setdefault(d, []).append(fp)

    # 直接含代码的目录 = 初始候选模块
    members = set(buckets.keys())

    def _count_inside(parent):
        return sum(1 for m in members if m.startswith(parent + "/"))

    changed = True
    while changed:
        changed = False
        for d in sorted(members, key=lambda x: x.count("/"), reverse=True):
            p = os.path.dirname(d)
            if not p or p == "." or p in members or p in buckets:
                # 已上聚到根、自身是候选/有直接代码 => 不再上聚
                continue
            # p 内成员若仅此一个且 p 无直接代码文件 => 上聚
            if _count_inside(p) == 1 and p not in buckets:
                members.discard(d)
                members.add(p)
                changed = True
                break

    modules = []
    if root_files:
        modules.append({
            "id": "MOD-000", "name": "root", "path": ".",
            "langs": _dir_langs(root_files), "kind": "root",
        })
    for i, mdir in enumerate(sorted(members), start=1):
        files = []
        for d, flist in buckets.items():
            if mdir == "." or d == mdir or d.startswith(mdir + "/"):
                files.extend(flist)
        if not files:
            files = [f for f in code_files if _rel(root, f).startswith(mdir + "/")]
        modules.append({
            "id": "MOD-%03d" % i,
            "name": os.path.basename(mdir),
            "path": mdir,
            "langs": _dir_langs(files),
            "kind": "directory",
        })
    return modules


def _modules_from_pkgs(pkgs, root, extra):
    """catkin 包 -> 模块（一包一模块；包内子目录不拆分；包外代码文件归 MOD-000 root）。
    外部依赖 = package.xml 的 depend 系标签 ∪ 包内 CMakeLists.txt 的 find_package。"""
    import re as _re
    code_files = iter_code_files(root, extra)
    rels = [_rel(root, f) for f in code_files]
    modules = []
    leftovers = []
    covered = []
    for p in pkgs:
        covered.append(p["path"] + "/")
    for rel in rels:
        if not any(rel.startswith(c) for c in covered) and "/" in rel:
            leftovers.append(rel)
    idx = 1
    if [r for r in rels if "/" not in r]:
        modules.append({"id": "MOD-000", "name": "root", "path": ".",
                        "langs": _dir_langs([f for f, r in zip(code_files, rels)
                                             if "/" not in r]), "kind": "root"})
    for p in pkgs:
        files = [f for f, r in zip(code_files, rels) if r.startswith(p["path"] + "/")]
        ext = set(p.get("external_deps") or [])
        cmake = os.path.join(root, p["path"], "CMakeLists.txt")
        if os.path.isfile(cmake):
            try:
                with open(cmake, encoding="utf-8", errors="replace") as fh:
                    for m in _re.finditer(r"(?m)^[ \t]*find_package\s*\(\s*([A-Za-z0-9_]+)",
                                          fh.read()):
                        ext.add(m.group(1))
            except OSError:
                pass
        modules.append({
            "id": "MOD-%03d" % idx, "name": p["name"], "path": p["path"],
            "langs": _dir_langs(files), "kind": "package", "deps": [],
            "external_deps": sorted(ext),
        })
        idx += 1
    if leftovers:
        modules.append({"id": "MOD-%03d" % idx, "name": "misc", "path": "",
                        "langs": _dir_langs([f for f, r in zip(code_files, rels)
                                             if r in leftovers]), "kind": "misc"})
    return modules


def _dir_langs(files):
    out = OrderedDict()
    for fp in files:
        ext = os.path.splitext(fp)[1].lower()
        if ext in EXT_LANG:
            out.setdefault(EXT_LANG[ext], None)
    return sorted(out)


def module_of(modules, root, filepath):
    rel = _rel(root, filepath)
    d = os.path.dirname(rel)
    if d == "":
        return "MOD-000"
    # 匹配最深路径前缀
    best = "MOD-000"
    best_len = -1
    for m in modules:
        if m["id"] == "MOD-000":
            continue
        p = m["path"]
        if d == p or d.startswith(p + "/"):
            if len(p) > best_len:
                best = m["id"]
                best_len = len(p)
    return best


# ---------------- 测试索引 ----------------

# ---------------- 全库引用计数（v1.5.3 refs，治 P0-4"由命名推断用途"） ----------------

def _leaf_name(qname):
    """限定名 -> 末段裸名（Foo::bar / Foo.bar / bar -> bar）。"""
    return re.split(r"[.:]+", qname or "")[-1] if qname else ""


def _is_entry_like(name):
    """入口/协议名（main、__init__ 等双下划线协议方法）：被框架/运行时隐式调用，
    源码零引用是常态——不参与零引用提示（防误报）。"""
    return name == "main" or (name.startswith("__") and name.endswith("__"))


def build_ref_counts(root, extra, symbols):
    """全库引用计数。

    口径（按"裸名"合并计数——零引用判定不受同名符号干扰）：
    - occ(name)    = 该裸名在全部代码文件的 tree-sitter 标识符出现总次数（注释/字符串天然不计）
    - decl_occ     = 声明位点数（inventory 符号定义 + C/C++ #define；每位点计 1）
    - refs(name)   = occ - decl_occ（声明之外的引用数；负值截为 0）
    返回 (refs_map, zero_refs)：zero_refs 为声明过但 refs=0 的名称（入口/协议名除外），
    按名称排序，元素 {name, kind: macro|symbol}。"""
    occ = {}
    macro_sites = set()
    for fp in iter_code_files(root, extra):
        for name, n in identifier_counts(fp).items():
            occ[name] = occ.get(name, 0) + n
        for name, _ln in macro_defs(fp):
            macro_sites.add(name)
    decl_occ = {}
    for s in symbols:
        leaf = _leaf_name(s.get("qname"))
        if leaf:
            decl_occ[leaf] = decl_occ.get(leaf, 0) + 1
    for name in macro_sites:
        decl_occ[name] = decl_occ.get(name, 0) + 1
    refs_map = {n: max(0, occ.get(n, 0) - d) for n, d in decl_occ.items()}
    zero = [{"name": n, "kind": "macro" if n in macro_sites else "symbol"}
            for n, v in refs_map.items() if v == 0 and not _is_entry_like(n)]
    zero.sort(key=lambda z: z["name"])
    return refs_map, zero


def collect_tests(root, extra_exclude):
    """测试文件与用例。返回 [{file, lang, cases:[name,...]}]。
    文件判定跨语言统一（is_test_file）；用例名仅 python 用 ast 精确提取，其它为 []（未知）。"""
    tests = []
    for fp in iter_code_files(root, extra_exclude):
        ext = os.path.splitext(fp)[1].lower()
        rel = _rel(root, fp)
        if not is_test_file(rel):
            continue
        lang = EXT_LANG.get(ext)
        cases = []
        if ext == ".py":
            try:
                with open(fp, encoding="utf-8", errors="replace") as fh:
                    tree = ast.parse(fh.read())
                for node in ast.walk(tree):
                    if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                            and node.name.startswith("test_")):
                        cases.append(node.name)
            except (SyntaxError, OSError):
                pass
        tests.append({"file": rel, "lang": lang or "", "cases": cases})
    return tests


# ---------------- 主构建 ----------------

def build_inventory(root, extra_exclude=None, project_type="auto"):
    """构建 inventory 结构（不落盘）。project_type: auto|catkin|generic。"""
    extra = extra_exclude or []
    root = os.path.abspath(root)
    ptype = project_type
    if ptype == "auto":
        ptype = detect_project_type(root, extra)
    if ptype == "catkin":
        # catkin 构建产物目录并入排除（对所有扫描生效：代码/全文件/模块/依赖）
        extra = list(extra) + [p for p in CATKIN_EXCLUDE if p not in extra]
    modules = build_modules(root, extra, ptype)
    langs = detect_langs(root, extra)

    symbols = []
    endpoints = []
    interfaces = []
    code_file_hashes = {}
    confidence_notes = []
    # 构建声明一致性（catkin：CMakeLists 声明 vs 实际文件）——v1.5.2
    build_issues = build_decl_issues(root, _catkin_packages(root, extra)) if ptype == "catkin" else []
    for _it in build_issues:
        confidence_notes.append({
            "file": "%s/CMakeLists.txt" % _it["module_path"],
            "note": "build_decl_missing %s" % "、".join(_it["missing"][:5])})
    counters = {"fun": 0, "cls": 0, "api": 0, "msg": 0, "srv": 0, "top": 0, "svc": 0, "nde": 0}
    file_mod = {}          # rel -> module id（含跳过符号的文件，供 include 依赖映射）
    include_records = []   # (rel, [include 字面量]) —— C++/JS 等适配器提供

    for fp in iter_code_files(root, extra):
        ext = os.path.splitext(fp)[1].lower()
        adapter = get_adapter(ext)
        if adapter is None:
            continue
        rel = _rel(root, fp)
        mid = module_of(modules, root, fp)
        file_mod[rel] = mid
        # 测试文件不生成文档符号，只入测试索引（v1.5：跨语言统一口径）
        if is_test_file(rel):
            code_file_hashes[rel] = sha256_file(fp)
            continue
        try:
            with open(fp, "rb") as fh:
                data = fh.read()
        except OSError:
            continue
        code_file_hashes[rel] = sha256_file(fp)
        syms, eps, ifaces, notes = adapter.scan(data, rel, mid, counters)
        if hasattr(adapter, "scan_deps"):
            include_records.append((rel, adapter.scan_deps(data)))
        symbols.extend(syms)
        endpoints.extend(eps)
        interfaces.extend(ifaces)
        for n in notes:
            confidence_notes.append({"file": rel,
                                     "note": n.get("note") if isinstance(n, dict) else str(n)})
    # 模块依赖（python import 启发）
    _mod_deps(modules, root, extra)

    # include 依赖映射：C++ 本地头（"pkg/path.h"）→ 项目内文件 → 目标模块
    if include_records:
        include_deps = {}
        for rel, incs in include_records:
            src_mod = file_mod.get(rel)
            if not src_mod:
                continue
            for inc in incs:
                inc = inc.strip("<>\"'").strip()
                if not inc:
                    continue
                for f_rel, f_mod in file_mod.items():
                    if f_mod != src_mod and (f_rel == inc or f_rel.endswith("/" + inc)):
                        include_deps.setdefault(src_mod, set()).add(f_mod)
        for m in modules:
            inc_deps = include_deps.get(m["id"])
            if inc_deps:
                m["deps"] = sorted((set(m.get("deps") or []) | inc_deps) - {m["id"]})

    tests = collect_tests(root, extra)

    # 全库引用计数（v1.5.3 refs）：符号挂 refs（声明外引用数），零引用名称单独成表。
    # refs=0 = 机器可知的"死代码/仅框架回调"事实——文档叙事不得凭命名臆造其用途（P0-4）。
    refs_map, zero_refs = build_ref_counts(root, extra, symbols)
    for s in symbols:
        s["refs"] = refs_map.get(_leaf_name(s.get("qname")), 0)

    # L0 全文件清单（含未登记语言，如 .cpp/.msg/.srv）+ 非 git 漂移基线
    files, files_hashes, file_notes = iter_all_files(root, extra)
    confidence_notes.extend(file_notes)

    # 为符号稳定 ID：扫描顺序已稳定（排序 + 文件内 AST 顺序），但多文件时 n 跨文件计数需要全局。
    # 处理：python 文件内 ID 是每文件局部；改成全局分配会破坏已生成文档锚点。
    # 结论：ID 采用 "<MOD>:<seq>" 稳定于排序文件顺序；为兼容锚点简单化，此处保留文件内局部 ID，
    # 但为确定性需在排序文件序列中固定（已稳定）。重复风险：同一 qname 跨文件 ID 可能重复——
    # 允许，check 以 (id, file) 判别；文档登记以标题锚点（含 file 名）避免歧义。
    inventory = {
        "schema_version": SCHEMA_VERSION,
        "project_type": ptype,
        "langs": langs,
        "source_commit": head_commit(root),
        "is_git": is_git_root(root),
        "root": root,
        "excluded": list(DEFAULT_EXCLUDE) + list(extra),
        "modules": modules,
        "symbols": symbols,
        "endpoints": endpoints,
        "interfaces": interfaces,
        "tests": tests,
        "code_file_hashes": code_file_hashes,
        "files": files,
        "files_hashes": files_hashes,
        "build_issues": build_issues,
        "zero_refs": zero_refs,
        "confidence": {"notes": confidence_notes},
    }
    return inventory


def _mod_deps(modules, root, extra):
    """python 模块间 import 启发依赖。"""
    by_path = {m["path"]: m["id"] for m in modules}
    for m in modules:
        if "python" not in m["langs"]:
            continue
        dirpath = os.path.join(root, m["path"]) if m["path"] != "." else root
        deps = set()
        for dirpath2, _, files in os.walk(dirpath):
            if is_excluded(os.path.relpath(dirpath2, root), extra):
                continue
            for fn in files:
                if not fn.endswith(".py"):
                    continue
                p = os.path.join(dirpath2, fn)
                try:
                    with open(p, encoding="utf-8", errors="replace") as fh:
                        src = fh.read()
                    nodes = ast.parse(src).body
                except (OSError, SyntaxError):
                    continue
                for node in nodes:
                    if isinstance(node, ast.Import):
                        for a in node.names:
                            _map_import_dep(deps, a.name, by_path)
                    elif isinstance(node, ast.ImportFrom) and node.module:
                        _map_import_dep(deps, node.module, by_path)
        m["deps"] = sorted(deps - {m["id"]})


def _map_import_dep(deps, imp, by_path):
    for top in by_path:
        if imp == top or imp.startswith(top + "."):
            deps.add(by_path[top])
