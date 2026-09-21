# -*- coding: utf-8 -*-
"""dev-docs CLI：inventory / extract / promote / check / report。

依赖：同目录 dev_inventory.py（Python 3.7+，零第三方依赖）。
产物落位：<目标项目>/docs/<out>/（out 默认 dev-docs）。

本工具只读写 <out>/ 下的文档与 JSON（inventory.json/.baseline.json），
不修改目标项目任何代码文件。语义由 AI 按 SKILL.md 填写，机器只做骨架与门禁。
"""
import datetime
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dev_inventory as inv   # noqa: E402
from dev_langs import docstrings_by_def_line, is_decl_line, line_kind_ts, macro_defs  # noqa: E402

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATES = os.path.join(SKILL_DIR, "templates")
BEG = "<!-- AI-GEN:BEGIN -->"
END = "<!-- AI-GEN:END -->"
ANCHOR = re.compile(r"<!--\s*@([A-Z]+-\d+)\s*-->")   # v1.3：kind 前缀放开（FUN/API/SYM/EPT/ITF…）
MARK = "<!-- TODO AI 依源码填写"   # 语义未填占位（填充度报告用）
# v1.4.1：「相关源文件」行虽位于 AI-GEN 区外，但由机器维护（随扫描/语义地图刷新）
_SRC_LINE_RE = re.compile(r"^\*\*相关源文件\*\*：.*$", re.M)
# v1.5.5：「页面导航」行同为机器维护（随页面树刷新：父链/兄弟页/标题变化）
_NAV_LINE_RE = re.compile(r"^\*\*页面导航\*\*：.*$", re.M)
_MACHINE_LINE_RES = (_SRC_LINE_RE, _NAV_LINE_RE)
SYM_TODO = "<!-- TODO AI 依源码填写（evidence: 推断/假设需注明） -->"
EP_TODO = "<!-- TODO AI 依源码填写；示例取 tested_by 对应测试 -->"


# ---------------- 通用 ----------------

def eprint(*a):
    print(*a, file=sys.stderr)


def now_iso():
    return datetime.datetime.now().isoformat(timespec="seconds")


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # newline="\n"：跨平台固定 LF，避免 Windows 文本模式把 \n 写成 \r\n，
    # 保证生成的 md/json 在任何平台字节一致（确定性 / git diff / CI 漂移检测依赖）
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def json_load(path):
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def json_save(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # 同 write：固定 LF，跨平台字节一致
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(inv.stable_dumps(obj))


def resolve_root(root):
    """git 仓库内则取仓库根；否则用给定目录。"""
    root = os.path.abspath(root)
    toplevel = inv.run_git(root, ["rev-parse", "--show-toplevel"])
    if toplevel:
        return os.path.abspath(toplevel)
    return root


def outdir(root, sub):
    return os.path.join(root, "docs", sub or "dev-docs")


_SLUG_RE = re.compile(r"[^A-Za-z0-9_-]+")


def module_slug(m):
    """文档文件名用语义化 slug（而非 MOD-编号），编号保留在 frontmatter/doc_id。"""
    path = (m.get("path") or "").strip().strip("/")
    if path in ("", "."):
        return "root"
    slug = _SLUG_RE.sub("-", path).strip("-")
    return slug or (m.get("id") or "mod").lower()


def list_md(out):
    files = []
    if os.path.isdir(out):
        for dp, _, fns in os.walk(out):
            for fn in sorted(fns):
                if fn.endswith(".md") and not fn.endswith(".draft.md"):
                    files.append(os.path.join(dp, fn))
    return sorted(files)


def list_drafts(out):
    files = []
    if os.path.isdir(out):
        for dp, _, fns in os.walk(out):
            for fn in sorted(fns):
                if fn.endswith(".draft.md"):
                    files.append(os.path.join(dp, fn))
    return sorted(files)


# ---------------- frontmatter ----------------

def parse_frontmatter(text):
    """返回 (meta, rest)。无 frontmatter 时 meta=None。"""
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        if end != -1:
            block = text[4:end]
            meta = {}
            for line in block.splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip()] = v.strip()
            rest = text[end + 4:]
            if rest.startswith("\n"):
                rest = rest[1:]
            return meta, rest
    return None, text


def render_frontmatter(meta):
    lines = ["---"]
    for k in ("doc_id", "type", "module_id", "source_commit", "generated_at",
              "inventory_hash", "status"):
        if k in meta and meta[k]:
            lines.append("%s: %s" % (k, meta[k]))
    lines.append("---")
    return "\n".join(lines) + "\n"


# ---------------- 渲染 ----------------

class _Safe(dict):
    def __missing__(self, key):
        return ""


def render(tpl_name, values):
    path = os.path.join(TEMPLATES, tpl_name)
    if not os.path.exists(path):
        raise SystemExit("模板缺失: %s" % path)
    return read(path).format_map(_Safe(values))


def meta_for(inv_data, doc_id, typ, module_id=None, status="draft"):
    return {
        "doc_id": doc_id, "type": typ,
        "module_id": module_id or "",
        "source_commit": inv_data.get("source_commit") or "",
        "generated_at": now_iso(),
        "inventory_hash": inv_data.get("_inventory_hash") or "",
        "status": status,
    }


def _sym_card(s, qualified=False):
    """v1.2 符号卡片：语义名标题 + 隐藏 ID 锚点。
    v1.4.1：同模块内存在同名符号（如三个脚本都有 build_arg_parser）时，
    qualified=True 给标题加文件限定，避免重名标题造成锚点歧义。
    v1.5.3：refs=0 的符号插入引用计数行——机器可知事实，填卡者不得凭命名臆造用途。"""
    title = s["qname"] if not qualified else "%s（%s）" % (s["qname"], s["file"])
    refs_note = ""
    if s.get("refs") == 0:
        refs_note = ("- 引用计数：refs=0（全库源码零引用——疑似死代码或仅供框架回调；"
                     "叙事不得凭命名推断用途）\n")
    return ("### %s\n\n"
            "- 签名：`%s`（file %s:%s, evidence: 事实）<!-- @%s -->\n"
            "%s"
            "- 用途 / 参数 / 返回 / 错误：%s\n"
            % (title, s.get("signature") or "?", s["file"],
               s.get("line", 0), s["id"], refs_note, SYM_TODO))


def _ep_card(e):
    methods = ",".join(e["method"]) if isinstance(e["method"], list) else (e["method"] or "*")
    title = ("%s %s" % (methods, e.get("path") or "")).strip()
    return ("### %s\n\n"
            "- handler：`%s`（file %s:%s, evidence: 事实）<!-- @%s -->\n"
            "- 参数 / 响应 / 错误 / 示例：%s\n"
            % (title, e.get("handler") or "", e["file"], e.get("line", 0), e["id"], EP_TODO))


def _ep_title(e):
    methods = ",".join(e["method"]) if isinstance(e["method"], list) else (e["method"] or "*")
    return ("%s %s" % (methods, e.get("path") or "")).strip()


def gh_anchor(title):
    """GitHub 标题锚 slug：小写；字母数字（含 unicode）与 - _ 保留；空格转 -；其余标点丢弃。
    与 `### <标题>` 渲染后的页内锚一致，供索引表跳转到卡片（不同查看器略有差异，失效无害）。"""
    out = []
    for ch in (title or "").strip().lower():
        if ch.isalnum() or ch in "_-":
            out.append(ch)
        elif ch == " ":
            out.append("-")
    return "".join(out)


def ref_index_rows(inv_data, module_id):
    """符号索引表行：机器全量生成（ID + 可跳转符号名 + 类型 + 位置文件 + 说明）。
    v1.5.5：符号名带页内锚链接（浏览器一键跳卡片）；新增「位置」列（basename，
    刻意不含 `:行号` 以避开 REF_RE 的引用校验——行号引用只属于卡片签名行）。"""
    rows = []
    for s in inv_data["symbols"]:
        if s["module"] != module_id:
            continue
        note = "refs:0（源码零引用）" if s.get("refs") == 0 else "—"
        loc = os.path.basename(s.get("file") or "—") or "—"
        name = "[%s](#%s)" % (s["qname"], gh_anchor(s.get("qname") or ""))
        rows.append("| @%s | %s | %s | %s | %s |"
                    % (s["id"], name, s.get("kind") or "函数", loc, note))
    for e in inv_data["endpoints"]:
        if e["module"] != module_id:
            continue
        loc = os.path.basename(e.get("file") or "—") or "—"
        rows.append("| @%s | %s | 端点 | %s | — |" % (e["id"], _ep_title(e), loc))
    for i in inv_data.get("interfaces", []) or []:
        if i["module"] != module_id:
            continue
        loc = os.path.basename(i.get("file") or "—") or "—"
        rows.append("| @%s | %s | %s | %s | — |" % (i["id"], i["name"], i.get("kind") or "接口", loc))
    return rows


_KIND_CN = {"class": "类", "method": "方法", "function": "函数"}


def ref_summary(inv_data, module_id):
    """本页速览（v1.5.5，机器渲染）：符号构成 + 零引用数——读者 10 秒建立模块全局感。
    空模块返回空串（模板行塌缩为空行，无碍）。"""
    syms = [s for s in inv_data.get("symbols", []) if s.get("module") == module_id]
    eps = [e for e in inv_data.get("endpoints", []) if e.get("module") == module_id]
    ifaces = [i for i in inv_data.get("interfaces", []) or [] if i.get("module") == module_id]
    if not (syms or eps or ifaces):
        return ""
    kinds = {}
    for s in syms:
        k = _KIND_CN.get(s.get("kind"), s.get("kind") or "其他")
        kinds[k] = kinds.get(k, 0) + 1
    parts = ["%s" % "、".join("%s %d" % (k, n) for k, n in kinds.items()) or "符号 0"]
    if eps:
        parts.append("端点 %d" % len(eps))
    if ifaces:
        parts.append("接口/绑定 %d" % len(ifaces))
    zero = len([s for s in syms if s.get("refs") == 0])
    if zero:
        parts.append("**零引用 %d**（见索引表 refs:0 标注）" % zero)
    return "符号面：%s" % "；".join(parts)


def ref_detail_sections(inv_data, module_id):
    """详细契约：按 模块级函数 / 类 / HTTP 端点 分节（分级组织）。"""
    syms = [s for s in inv_data["symbols"] if s["module"] == module_id]
    eps = [e for e in inv_data["endpoints"] if e["module"] == module_id]
    sections = []
    # v1.4.1：同模块内同名符号（多文件同名函数）→ 标题加文件限定，避免锚点歧义
    counts = {}
    for s in syms:
        counts[s.get("qname")] = counts.get(s.get("qname"), 0) + 1

    def card(s):
        return _sym_card(s, counts.get(s.get("qname"), 0) > 1)

    funcs = [s for s in syms if s.get("kind") == "function"]
    if funcs:
        sections.append("## 模块级函数\n\n" +
                        "\n".join(card(s) for s in funcs))
    by_cls = {}
    order = []
    for s in syms:
        if s.get("kind") != "method":
            continue
        cls = s.get("cls") or "(未归类方法)"
        if cls not in by_cls:
            by_cls[cls] = []
            order.append(cls)
        by_cls[cls].append(s)
    for cls in order:
        sections.append("## 类：%s\n\n" % cls +
                        "\n".join(card(s) for s in by_cls[cls]))
    others = [s for s in syms if s.get("kind") not in ("function", "method")]
    if others:
        sections.append("## 其他符号\n\n" +
                        "\n".join(card(s) for s in others))
    if eps:
        sections.append("## HTTP 端点\n\n" +
                        "\n".join(_ep_card(e) for e in eps))
    ifaces = [i for i in inv_data.get("interfaces", []) or [] if i["module"] == module_id]
    if ifaces:
        sections.append("## ROS 接口定义\n\n" +
                        "\n".join(_iface_card(i) for i in ifaces))
    return "\n\n".join(sections) if sections else "（本模块无公开符号；或符号尚未被盘点识别）"


def _iface_field(f):
    """接口字段 -> `类型[数组] 名字`（常量附 =值）。"""
    t = "%s%s" % (f.get("type", "?"), f.get("array") or "")
    if f.get("constant") is not None:
        return "`%s %s=%s`" % (t, f.get("name"), f.get("constant"))
    return "`%s %s`" % (t, f.get("name"))


def _iface_card(i):
    """ROS 接口/绑定卡片：标题写语义名 + 隐藏 ID 锚点（纳入对账）。
    kind=msg/srv 为接口定义（字段/请求响应）；topic/service 为绑定条目（pub/sub/server/client）；
    node 为节点入口。"""
    if i.get("kind") == "srv":
        req = "、".join(_iface_field(f) for f in i.get("request") or []) or "—"
        rsp = "、".join(_iface_field(f) for f in i.get("response") or []) or "—"
        detail = "- 请求：%s\n- 响应：%s（file %s:%s, evidence: 事实）<!-- @%s -->" % (
            req, rsp, i["file"], i.get("line", 0), i["id"])
    elif i.get("kind") == "topic":
        detail = "- %s 消息 `%s`（file %s:%s, evidence: 事实）<!-- @%s -->" % (
            {"pub": "发布", "sub": "订阅"}.get(i.get("role"), i.get("role", "?")),
            i.get("msg") or "（动态：运行时确定）", i["file"], i.get("line", 0), i["id"])
    elif i.get("kind") == "service":
        detail = "- %s 服务 `%s`（file %s:%s, evidence: 事实）<!-- @%s -->" % (
            {"server": "提供服务", "client": "调用"}.get(i.get("role"), i.get("role", "?")),
            i.get("srv") or "（类型待查）", i["file"], i.get("line", 0), i["id"])
    elif i.get("kind") == "node":
        detail = "- 节点名：`%s`（file %s:%s, evidence: 事实）<!-- @%s -->" % (
            i["name"], i["file"], i.get("line", 0), i["id"])
    else:
        fields = "、".join(_iface_field(f) for f in i.get("fields") or []) or "—"
        detail = "- 字段：%s（file %s:%s, evidence: 事实）<!-- @%s -->" % (
            fields, i["file"], i.get("line", 0), i["id"])
    title = "%s（%s）" % (i["name"], i["kind"])
    return ("### %s\n\n"
            "%s\n"
            "- 用途 / 语义：%s\n"
            % (title, detail, SYM_TODO))


def _deps_display(m):
    """模块依赖展示：内部 MOD-id + 外部生态依赖（external_deps）。"""
    parts = []
    if m.get("deps"):
        parts.append("内部: " + ", ".join(m["deps"]))
    if m.get("external_deps"):
        parts.append("外部: " + ", ".join(m["external_deps"]))
    return "；".join(parts) or "（无内部依赖或待确认）"


def reference_tpl_values(inv_data, m):
    return {
        "MOD-id": m["id"], "module_name": m["name"], "path": m["path"],
        "lang": ",".join(m.get("langs") or []),
        "n_symbols": len([s for s in inv_data["symbols"] if s["module"] == m["id"]]) +
                     len([e for e in inv_data["endpoints"] if e["module"] == m["id"]]),
        "generated_at": now_iso(),
        "inventory_hash": inv_data.get("_inventory_hash") or "",
        "status": "draft",
        "deps": _deps_display(m),
        "index_rows": "\n".join(ref_index_rows(inv_data, m["id"])) or "| — | （本模块无公开符号） | — | — | — |",
        "ref_summary": ref_summary(inv_data, m["id"]),
        "detail_rows": ref_detail_sections(inv_data, m["id"]),
    }


def arch_module_rows(inv_data, out=None):
    """架构页模块清单表（机器渲染**整表**：表头 + 行）。
    v1.5.5：职责列来源标注收敛到表头一次（此前每行都拖「AI 断言·待核」，重复噪音）；
    表内无语义地图数据时表头退回「职责」。行：编号/名称/路径/职责(语义地图优先)/依赖。"""
    used_smap = False
    rows = []
    for m in inv_data.get("modules", []):
        resp = "（待补：职责）"
        if out:
            sm = smap_for_module(out, m)
            if sm and sm.get("responsibility"):
                resp = sm["responsibility"]
                used_smap = True
        rows.append("| %s | %s | %s | %s | %s |"
                    % (m["id"], m["name"], m["path"], resp,
                       _deps_display(m)))
    if not rows:
        rows = ["| — | — | — | — | — |"]
    header = "职责（语义地图·AI 断言·待核）" if used_smap else "职责"
    table = ["| MOD-id | 模块 | 路径 | %s | 主要依赖 |" % header,
             "|:---|:---|:---|:---|:---|"] + rows
    return "\n".join(table)


def arch_values(inv_data):
    entries = []
    for lang in inv_data.get("langs", {}):
        entries.append("- %s：<!-- TODO AI：入口文件/启动命令/路由注册 -->" % lang)
    return {
        "project": os.path.basename(inv_data.get("root") or ""),
        "commit": inv_data.get("source_commit") or "",
        "generated_at": now_iso(),
        "inventory_hash": inv_data.get("_inventory_hash") or "",
        "status": "draft",
        "langs": ",".join(inv_data.get("langs", {})) or "?",
        "module_rows": arch_module_rows(inv_data),
        "entry_rows": "\n".join(entries),
        "summary": "<!-- TODO AI：一句话定位 -->",
    }


# ---------------- 骨架拆分（人工区保护） ----------------

def split_doc(text):
    """拆 frontmatter + pre + (BEGIN..END) + post。无 marker 视为整段人工。"""
    meta, rest = parse_frontmatter(text)
    bi = rest.find(BEG)
    ei = rest.find(END)
    if bi == -1 or ei == -1 or ei < bi:
        return meta, rest, None, ""
    pre = rest[:bi]
    inside = rest[bi + len(BEG):ei]
    post = rest[ei + len(END):]
    return meta, pre, inside, post


def compose(meta, pre, inside, post):
    parts = []
    if meta:
        parts.append(render_frontmatter(meta))
    parts.append(pre or "")
    if inside is not None:
        parts.append(BEG + "\n")
        parts.append(inside)
        parts.append("\n" + END)
    parts.append(post or "")
    return "".join(parts)


# ---------------- inventory 命令 ----------------

def cmd_inventory(root, out, extra_exclude, quiet=False, project_type="auto"):
    data = inv.build_inventory(root, extra_exclude, project_type=project_type)
    data["_inventory_hash"] = inv.canon_hash(
        {k: v for k, v in data.items() if k != "_inventory_hash"})
    inv_dir = os.path.join(out, "inventory.json")
    json_save(inv_dir, data)
    if not quiet:
        print("inventory -> %s" % os.path.relpath(inv_dir, root))
        print("  project_type: %s | files(全集): %d" %
              (data.get("project_type", "?"), len(data.get("files", []))))
        print("  modules: %d, symbols: %d, endpoints: %d, tests: %d, langs: %s"
              % (len(data["modules"]), len(data["symbols"]),
                 len(data["endpoints"]), len(data["tests"]),
                 ",".join(data.get("langs", {})) or "?"))
        if data["confidence"]["notes"]:
            print("  low-confidence notes: %d (详见 inventory.json.confidence)"
                  % len(data["confidence"]["notes"]))
    return data


def load_inventory(out):
    return json_load(os.path.join(out, "inventory.json"))


# ---------------- 页面树规划（.devdocs-plan.json，DeepWiki 对齐） ----------------

PLAN_FILE = ".devdocs-plan.json"
PAGE_TYPES = ("index", "architecture", "usage", "reference", "data")

# 各页面类型的固定章节（plan.sections 默认值；check 按此校验结构完整性）
PAGE_SECTIONS = {
    "index": ["项目定位", "能力矩阵", "文档树", "阅读路径", "覆盖率摘要"],
    "architecture": ["系统上下文", "构建块视图", "运行时场景", "横切概念与决策", "术语表"],
    "usage": ["安装与启动", "配置", "常见任务", "扩展点", "故障排查"],
    "reference": ["概览", "组件与协作", "使用指南", "对外接口面", "符号索引", "详细契约"],
    "data": ["实体与字段", "关系", "存储与生命周期"],
}

PAGE_PURPOSE = {
    "index": "项目是什么、能力边界在哪、先去读哪一页",
    "architecture": "系统怎么组织、一次典型请求怎么跑",
    "usage": "怎么安装配置、常见任务怎么做、如何扩展",
}

PAGE_TPL = {"index": "index.md", "architecture": "architecture.md", "usage": "usage.md",
            "reference": "reference.md", "data": "data.md"}

SOURCES_MARK = "<!-- SOURCES:AUTO -->"
# file:line 引用：扩展名必须以字母开头（排除 `qwen3.5:4`、`127.0.0.1:11434` 这类版本号/IP 误匹配）
REF_RE = re.compile(r"([A-Za-z0-9_][A-Za-z0-9_./\-]*\.[A-Za-z][A-Za-z0-9]{0,9}):(\d+)")


def load_plan(out):
    plan = json_load(os.path.join(out, PLAN_FILE))
    return plan if isinstance(plan, dict) and plan.get("pages") else None


def save_plan(out, plan):
    json_save(os.path.join(out, PLAN_FILE), plan)


def page_by_slug(plan, slug):
    for p in (plan or {}).get("pages") or []:
        if p.get("slug") == slug:
            return p
    return None


def doc_id_for(page):
    t = page.get("type")
    if t == "index":
        return "INDEX"
    if t == "architecture":
        return "ARCH-001"
    if t == "usage":
        return "USAGE-001"
    if t == "reference":
        return page.get("module_id") or "MOD-000"
    if t == "data":
        return "DATA-%s" % (page.get("module_id") or "MOD-000")
    return "PAGE-001"


def ignored_paths(out):
    """语义地图里显式声明忽略的文件（如 .env / 截图 / 历史产物）——
    v1.4.1：不列入「相关源文件」（此前 .env 会被当成相关源文件推荐给 AI/人读）。"""
    smap = load_semantic_map(out) or {}
    paths = set()
    for m in smap.get("modules") or []:
        for it in m.get("ignored_files") or []:
            p = it.get("path") if isinstance(it, dict) else it
            if p:
                paths.add(str(p).replace("\\", "/"))
    return paths


def root_files(inv_data, out=None):
    """根级（非目录内）文件——index/usage 页的源文件提示（已排除显式 ignored）。"""
    files = [f["path"] for f in inv_data.get("files", [])]
    ign = ignored_paths(out) if out else set()
    return sorted(p for p in files if "/" not in p and p not in ign)


def module_files(inv_data, m, smap=None):
    """模块所属文件：语义地图优先，其次按模块 path 前缀在文件全集内匹配。"""
    path = (m.get("path") or "").strip().strip("/")
    if smap:
        for sm in smap.get("modules") or []:
            if (sm.get("path") or "").strip().strip("/") == path:
                fs = [str(x).replace("\\", "/") for x in (sm.get("files") or [])]
                if fs:
                    return sorted(fs)
    files = [f["path"] for f in inv_data.get("files", [])]
    if path in ("", "."):
        return sorted(p for p in files if "/" not in p)
    pref = path + "/"
    return sorted(p for p in files if p.startswith(pref))


def module_by_id(inv_data, mid):
    for m in inv_data.get("modules", []):
        if m["id"] == mid:
            return m
    return None


def smap_for_module(out, m):
    """语义地图中对应模块条目（purpose/responsibility 来源）。"""
    smap = load_semantic_map(out)
    if not smap:
        return None
    path = (m.get("path") or "").strip().strip("/")
    for sm in smap.get("modules") or []:
        if (sm.get("path") or "").strip().strip("/") == path:
            return sm
    return None


def build_plan(inv_data, out, existing=None):
    """由 inventory + 语义地图生成页面树；existing 存在时保留人工编辑。"""
    proj = os.path.basename(inv_data.get("root") or "") or "project"
    smap = load_semantic_map(out)
    pages = [
        {"slug": "index", "type": "index", "title": "%s — 文档总览" % proj,
         "parent": None, "purpose": PAGE_PURPOSE["index"],
         "sections": list(PAGE_SECTIONS["index"]),
         "source_files": root_files(inv_data, out), "status": "planned"},
        {"slug": "architecture", "type": "architecture", "title": "架构总览",
         "parent": "index", "purpose": PAGE_PURPOSE["architecture"],
         "sections": list(PAGE_SECTIONS["architecture"]),
         "source_files": root_files(inv_data, out), "status": "planned"},
        {"slug": "usage", "type": "usage", "title": "上手与扩展",
         "parent": "index", "purpose": PAGE_PURPOSE["usage"],
         "sections": list(PAGE_SECTIONS["usage"]),
         "source_files": root_files(inv_data, out), "status": "planned"},
    ]
    for m in inv_data.get("modules", []):
        sm = smap_for_module(out, m)
        pages.append({
            "slug": "reference/%s" % module_slug(m), "type": "reference",
            "module_id": m["id"], "title": m.get("name") or m["id"],
            "parent": "index",
            "purpose": (sm or {}).get("responsibility") or "（待补：本模块职责一句话）",
            "sections": list(PAGE_SECTIONS["reference"]),
            "source_files": module_files(inv_data, m, smap),
            "status": "planned",
        })
    if existing:
        old = {}
        for p in existing.get("pages") or []:
            if p.get("slug"):
                old[p["slug"]] = p
        merged, known = [], set()
        for p in pages:
            o = old.get(p["slug"])
            if o:
                # source_files 是机器字段（随扫描/语义地图刷新），不纳入人工编辑保护
                for k in ("title", "purpose", "parent", "sections", "status"):
                    v = o.get(k)
                    if v not in (None, "", []):
                        p[k] = v
            merged.append(p)
            known.add(p["slug"])
        for slug, o in old.items():
            if slug not in known:
                kept = dict(o)
                kept["status"] = kept.get("status") or "planned"
                merged.append(kept)
        pages = merged
    return {"version": 1, "generated_at": now_iso(),
            "source": "semantic-map" if smap else "inventory",
            "pages": pages}


def cmd_plan(root, out, write=False, force=False, extra_exclude=None):
    data = ensure_inventory(out, root, extra_exclude or [])
    if data is None:
        raise SystemExit("plan：无法读取/生成 inventory.json")
    existing = None if force else load_plan(out)
    plan = build_plan(data, out, existing)
    errs = validate_plan(plan)
    if errs:
        for e in errs:
            eprint("  ! %s" % e)
        raise SystemExit("plan 校验失败（%d 项）" % len(errs))
    print("页面树（%d 页；来源 %s%s）:" % (len(plan["pages"]), plan.get("source"),
                                        "；已保留人工编辑" if existing else ""))
    for ln in plan_tree_md(plan).splitlines():
        print("  %s" % ln)
    if write:
        save_plan(out, plan)
        print("已写入 %s" % os.path.relpath(os.path.join(out, PLAN_FILE), root))
    else:
        print("（dry-run：加 --write 落盘 .devdocs-plan.json）")
    return 0


def validate_plan(plan):
    pages = plan.get("pages") or []
    slugs = [p.get("slug") for p in pages if p.get("slug")]
    errs, seen = [], set()
    for p in pages:
        s = p.get("slug")
        if not s:
            errs.append("存在缺 slug 的页")
            continue
        if s in seen:
            errs.append("slug 重复：%s" % s)
        seen.add(s)
        if p.get("type") not in PAGE_TYPES:
            errs.append("%s：type 非法（%s）" % (s, p.get("type")))
        if p.get("type") in ("reference", "data") and not p.get("module_id"):
            errs.append("%s：缺 module_id" % s)
        par = p.get("parent")
        if par and par not in slugs:
            errs.append("%s：parent 不存在（%s）" % (s, par))
    by = {p.get("slug"): p for p in pages if p.get("slug")}
    for s in slugs:
        chain, cur = set(), s
        while cur:
            if cur in chain:
                errs.append("parent 链存在环：%s" % s)
                break
            chain.add(cur)
            cur = (by.get(cur) or {}).get("parent")
    return errs


def nav_line(plan, page):
    """页面导航行（v1.5.5）：父链 › 当前页，附同级页链接——
    大文档集里人类跳转全靠 URL 手改，plan 的 parent 链数据齐全，纯渲染零成本。"""
    if not plan:
        return "（页面树未生成）"
    pages = (plan.get("pages") or [])
    by_slug = {p.get("slug"): p for p in pages if p.get("slug")}
    chain, cur = [], page
    while cur and cur.get("parent"):
        par = by_slug.get(cur["parent"])
        if not par:
            break
        chain.append(par)
        cur = par
    parts = ["[%s](%s.md)" % (p.get("title") or p["slug"], p["slug"])
             for p in reversed(chain)]
    parts.append("**%s**" % (page.get("title") or page.get("slug") or ""))
    line = " › ".join(parts)
    sibs = [p for p in pages
            if p.get("parent") == page.get("parent") and p.get("slug") != page.get("slug")]
    if sibs:
        sib = " ｜ ".join("[%s](%s.md)" % (p.get("title") or p["slug"], p["slug"])
                          for p in sibs[:8])
        if len(sibs) > 8:
            sib += " 等 %d 页" % len(sibs)
        line += "　·　同级：" + sib
    if not (page.get("parent") or sibs):
        line = "文档首页（共 %d 页）" % len(pages)
    return line


def plan_tree_md(plan, with_status=True):
    """页面树 → markdown 嵌套列表（index 文档树 / plan dry-run 共用）。"""
    if not plan:
        return "<!-- 页面树未生成：先运行 plan --write -->"
    pages = plan.get("pages") or []
    kids = {}
    for p in pages:
        kids.setdefault(p.get("parent"), []).append(p)
    lines, seen = [], set()

    def walk(parent, depth):
        for p in kids.get(parent, []):
            s = p.get("slug")
            if not s or s in seen:
                continue
            seen.add(s)
            icon = {"filled": "✅", "generated": "🟡"}.get(p.get("status"), "⬜") if with_status else "•"
            lines.append("%s- %s [%s](%s.md) — %s" % ("    " * depth, icon,
                                                      p.get("title") or s, s,
                                                      p.get("purpose") or ""))
            walk(s, depth + 1)
    walk(None, 0)
    for p in pages:
        if p.get("slug") not in seen:
            seen.add(p.get("slug"))
            lines.append("- ⬜ [%s](%s.md)（parent 未解析）" % (p.get("title") or p["slug"], p["slug"]))
    return "\n".join(lines)


def set_page_status(out, slug, status):
    """更新页面树中某页进度（planned→generated→filled）。"""
    plan = load_plan(out)
    if not plan:
        return
    p = page_by_slug(plan, slug)
    if not p:
        return
    order = {"planned": 0, "generated": 1, "filled": 2}
    if order.get(status, 0) > order.get(p.get("status") or "planned", 0):
        p["status"] = status
        save_plan(out, plan)


def format_source_files(files, limit=12):
    """相关源文件渲染：按目录分组（目录前缀只出现一次，组内列文件名），
    每组最多展示 6 个文件名，超出以「等 n 个」收尾——一长串全路径人类扫读困难。"""
    files = [str(f).replace("\\", "/") for f in (files or [])]
    if not files:
        return "（待补：本页相关源文件）"
    groups = {}   # py3.7+ dict 保序：目录按首次出现顺序
    for f in files:
        groups.setdefault(os.path.dirname(f) or "（根目录）", []).append(f)
    parts = []
    for d, fs in groups.items():
        shown = "、".join("`%s`" % os.path.basename(x) for x in fs[:6])
        more = " 等 %d 个" % len(fs) if len(fs) > 6 else ""
        parts.append("%s（%d）：%s%s" % (d, len(fs), shown, more))
    return "；".join(parts)


def resolve_ref(p, all_files):
    """引用路径解析：精确命中 / 唯一后缀命中（如 `DroneController.h:99` 短写）→ 全路径；
    多义或不存在 → None（调用方按缺/歧义分别处置）。"""
    p = p.replace("\\", "/").lstrip("./")
    if p in all_files:
        return p
    matches = [f for f in all_files if f.endswith("/" + p)]
    return matches[0] if len(matches) == 1 else None


def collect_page_refs(text, inv_data):
    """抽取页内 file:line 引用（解析为项目文件全集路径，含唯一短名），去重排序 → Sources 列表。"""
    all_files = {f["path"] for f in inv_data.get("files", [])}
    hits = {}
    for path, line in REF_RE.findall(text):
        p = resolve_ref(path, all_files)
        if p:
            hits.setdefault(p, set()).add(int(line))
    rows = []
    for p in sorted(hits):
        ls = sorted(hits[p])
        # v1.4.1：不再截断（此前超 20 行会显示 `…`，导致 Sources 不完整）
        rows.append("- `%s`：%s" % (p, "、".join(str(x) for x in ls)))
    return rows


def fill_sources(text, inv_data):
    """把 <!-- SOURCES:AUTO --> 占位替换为本页引用清单。"""
    rows = collect_page_refs(text, inv_data)
    if not rows:
        rows = ["（本页尚无 file:line 引用；填写语义时请引用源码原文——格式为 文件路径:行号）"]
    return text.replace(SOURCES_MARK, "\n".join(rows))


def page_values(inv_data, out, page):
    """统一模板变量（各类型共用；_Safe 缺省为空串）。"""
    typ = page.get("type")
    mid = page.get("module_id") or ""
    m = module_by_id(inv_data, mid) if mid else None
    langs = inv_data.get("langs") or {}
    vals = {
        "title": page.get("title") or page.get("slug"),
        "purpose": page.get("purpose") or "（待补：本页回答什么）",
        "doc_id": doc_id_for(page), "type": typ or "", "module_id": mid,
        "plan_slug": page.get("slug") or "",
        "commit": inv_data.get("source_commit") or "",
        "generated_at": now_iso(),
        "inventory_hash": inv_data.get("_inventory_hash") or "",
        "status": "draft",
        "source_files": format_source_files(
            [p for p in (page.get("source_files") or []) if p not in ignored_paths(out)]),
        "project": os.path.basename(inv_data.get("root") or "") or "project",
        "langs": ",".join(langs) or "?",
        "sections_list": " / ".join(page.get("sections") or []),
        "doc_tree": plan_tree_md(load_plan(out)),
        "nav": nav_line(load_plan(out), page),
        "n_modules": len(inv_data.get("modules") or []),
        "n_symbols": len(inv_data.get("symbols") or []),
        "n_endpoints": len(inv_data.get("endpoints") or []),
        "n_tests": len(inv_data.get("tests") or []),
        "n_files": len(inv_data.get("files") or []),
        "n_page_symbols": len([s for s in inv_data.get("symbols", []) if s.get("module") == mid]) +
                          len([e for e in inv_data.get("endpoints", []) if e.get("module") == mid]),
        "module_rows": arch_module_rows(inv_data, out),
    }
    if typ == "reference" and m:
        vals.update(reference_tpl_values(inv_data, m))
        vals["module_name"] = m.get("name") or m["id"]
    if typ == "architecture":
        vals.update(arch_values(inv_data))
        vals["module_rows"] = arch_module_rows(inv_data, out)
    if typ == "index":
        vals.update(coverage_values(inv_data, out))
    return vals


def coverage_values(inv_data, out, rep=None):
    """覆盖率机器字段（index 页由 extract 与 report 共用，勿手写）。"""
    rep = rep or analyze(inv_data, out)
    ps = rep.get("page_stat") or {}
    ok = not (rep["orphan_syms"] or rep["orphan_eps"] or rep["phantom"] or rep["stale"]
              or rep["section_missing"] or rep["ref_errors"])
    return {
        "registered": rep["registered_count"], "want_count": rep["want_count"],
        "orphan": len(rep["orphan_syms"]) + len(rep["orphan_eps"]),
        "phantom": len(rep["phantom"]), "stale": len(rep["stale"]),
        "doc_count": rep["doc_count"],
        "file_total": rep["file_total"], "file_covered": rep["file_covered"],
        "file_ignored": rep["file_ignored"], "file_uncovered_n": len(rep["file_uncovered"]),
        "page_total": ps.get("total", 0), "page_filled": ps.get("filled", 0),
        "page_generated": ps.get("generated", 0), "page_planned": ps.get("planned", 0),
        "check_status": "PASS（ERROR=0）" if ok else "有未处理项",
        "ai_fill": len(rep.get("ai_fill") or []),
        "semantic_todo": rep.get("semantic_todo", 0),
    }


# ---------------- 文档生成（extract） ----------------

def ensure_inventory(out, root, extra_exclude, project_type="auto"):
    data = load_inventory(out)
    if data is None:
        data = cmd_inventory(root, out, extra_exclude, project_type=project_type)
    return data


def _merge_doc(old_text, new_text, keep_status=False):
    """合并新旧文档：frontmatter 元数据与 AI-GEN 机器区取新；
    区外内容（标题/AI 填写正文/人工补充）保留——旧区外为空时才采用新版模板内容。
    v1.4 关键约定：AI 要填的叙事节位于 AI-GEN 区**外**，因此重生成永不冲掉已填语义。"""
    meta_new, rest_new = parse_frontmatter(new_text)
    _, new_pre, new_inside, new_post = split_doc(rest_new)
    meta_old, pre_old, inside_old, post_old = split_doc(old_text)
    fm = dict(meta_old or {})
    for k in ("type", "module_id", "plan_slug", "source_commit", "inventory_hash",
              "generated_at"):
        v = (meta_new or {}).get(k)
        if v:
            fm[k] = v
    if not fm.get("doc_id"):
        fm["doc_id"] = (meta_new or {}).get("doc_id", "")
    if not (keep_status and fm.get("status")):
        fm["status"] = (meta_new or {}).get("status") or "draft"
    pre = pre_old if (pre_old or "").strip() else (new_pre or "")
    post = post_old if (post_old or "").strip() else (new_post or "")
    # 「相关源文件」行：机器维护，位置不变（即使该行在 AI-GEN 区外也刷新）
    pre = _sync_source_line(pre, new_pre)
    post = _sync_source_line(post, new_post)
    inside = new_inside if new_inside is not None else (inside_old or "")
    # 符号卡片语义（机器区内的"用途/参数/返回/错误"已填内容）随机器区刷新而保留
    if new_inside is not None and inside_old:
        inside = _preserve_card_semantics(inside_old, new_inside)
    return compose(fm, pre, inside, post)


def _sync_source_line(old, new):
    """机器维护行（相关源文件 / 页面导航）同步：已有则替换为新渲染值；
    旧文档缺失（如 v1.5.5 新增的导航行）则**注入**到「相关源文件」行之前——
    否则存量文档永远拿不到新增的机器行（pre 区整体保留原则会挡住它）。"""
    for rx in _MACHINE_LINE_RES:
        m = rx.search(new or "")
        if not m:
            continue
        if rx.search(old or ""):
            old = rx.sub(lambda _m: m.group(0), old, count=1)
        elif rx is _NAV_LINE_RE:
            anchor = _SRC_LINE_RE.search(old or "")
            if anchor:
                old = old[:anchor.start()] + m.group(0) + "\n\n" + old[anchor.start():]
    return old


# 卡片语义行前缀：符号卡（用途/参数/返回/错误）与接口卡（用途/语义）两类
# （漏掉接口卡前缀会导致重生成时 msg/srv/topic/service/node 卡片语义被冲掉）
_SEM_LINE_PREFIX = ("- 用途 / 参数 / 返回 / 错误：", "- 用途 / 语义：")


def _preserve_card_semantics(old_inside, new_inside):
    """保留已填的符号卡片语义（v1.4.1）：卡片位于 AI-GEN 机器区内，
    机器区整体刷新会把已填的「用途 / 参数 / 返回 / 错误」冲掉——这里按锚点 ID
    把旧文本里**非 TODO** 的语义行回填到新文本，避免重生成丢语义。"""
    if not old_inside or not new_inside:
        return new_inside
    kept, cur = {}, None
    for ln in old_inside.split("\n"):
        m = ANCHOR.search(ln)
        if m:
            cur = m.group(1)
            continue
        if cur and ln.startswith(_SEM_LINE_PREFIX):
            if MARK not in ln:
                kept[cur] = ln
            cur = None
    if not kept:
        return new_inside
    out, cur = [], None
    for ln in new_inside.split("\n"):
        m = ANCHOR.search(ln)
        if m:
            cur = m.group(1)
            out.append(ln)
            continue
        if cur and ln.startswith(_SEM_LINE_PREFIX) and cur in kept:
            out.append(kept[cur])
            cur = None
            continue
        out.append(ln)
    return "\n".join(out)


def _regen_or_new(out, rel_path, new_text):
    """目标存在则保留区外（标题/AI 填写/人工补充）只刷新 AI-GEN 机器区，输出 draft。"""
    final = os.path.join(out, rel_path)
    draft = final + ".draft"
    if os.path.exists(final):
        write(draft, _merge_doc(read(final), new_text))
    else:
        write(draft, new_text)
    return os.path.relpath(draft, out)


def _refresh_generated(out, rel_path, new_text):
    """直接刷新正式文件的机器区（report 用；同样保留区外人工/AI 内容与 status）。"""
    final = os.path.join(out, rel_path)
    text = _merge_doc(read(final), new_text, keep_status=True) if os.path.exists(final) else new_text
    write(final, text)
    return os.path.relpath(final, out)


def select_pages(plan, layer, module_id=None, page_slug=None):
    pages = (plan or {}).get("pages") or []
    if page_slug:
        p = page_by_slug(plan, page_slug)
        return [p] if p else []
    if layer == "all":
        return [p for p in pages if p.get("type") != "data"]
    if layer == "reference" and module_id:
        return [p for p in pages if p.get("type") == "reference" and p.get("module_id") == module_id]
    return [p for p in pages if p.get("type") == layer]


def _extract_legacy(inv_data, root, out, layer, module_id):
    """无页面树时的旧行为（向后兼容 v1.3 存量库）。"""
    modules = inv_data["modules"]
    if module_id:
        modules = [m for m in modules if m["id"] == module_id]
    written = []
    if layer == "architecture":
        written.append(_regen_or_new(out, "architecture.md",
                                     render("architecture.md", page_values(inv_data, out, {
                                         "slug": "architecture", "type": "architecture",
                                         "title": "架构总览",
                                         "purpose": PAGE_PURPOSE["architecture"],
                                         "source_files": root_files(inv_data, out)}))))
    elif layer == "reference":
        for m in modules:
            written.append(_regen_or_new(out, "reference/%s.md" % module_slug(m),
                                         render("reference.md", page_values(inv_data, out, {
                                             "slug": "reference/%s" % module_slug(m),
                                             "type": "reference", "module_id": m["id"],
                                             "title": m.get("name") or m["id"],
                                             "purpose": "（待补：本模块职责一句话）",
                                             "source_files": module_files(inv_data, m)}))))
    elif layer == "data":
        for m in modules:
            vals = page_values(inv_data, out, {
                "slug": "data/%s" % module_slug(m), "type": "data",
                "module_id": m["id"], "title": m.get("name") or m["id"],
                "purpose": "（待补：本模块数据模型）", "source_files": module_files(inv_data, m)})
            written.append(_regen_or_new(out, "data/%s.md" % module_slug(m),
                                         render("data.md", vals)))
    else:
        raise SystemExit("未知 layer: %s（可选 architecture|reference|data|index|usage|all）" % layer)
    return written


def extract_layer(inv_data, root, out, layer, module_id=None, page_slug=None):
    if load_plan(out) is None:
        written = _extract_legacy(inv_data, root, out, layer, module_id)
    else:
        plan = load_plan(out)
        targets = select_pages(plan, layer, module_id, page_slug)
        if not targets:
            raise SystemExit("extract：页面树中没有匹配页（layer=%s module=%s page=%s）"
                             % (layer, module_id, page_slug))
        written = []
        for p in targets:
            tpl = PAGE_TPL.get(p.get("type"))
            if not tpl:
                continue
            txt = fill_sources(render(tpl, page_values(inv_data, out, p)), inv_data)
            written.append(_regen_or_new(out, "%s.md" % p["slug"], txt))
            set_page_status(out, p["slug"], "generated")
    print("生成 %d 个 draft（<target>.md.draft）：" % len(written))
    for w in written:
        print("  - %s" % w)
    print("AI 按每页 <!-- AI-FILL --> 工单填写后，经人工确认执行: promote <file.draft>")
    return 0


def cmd_promote(out, draft_path, inv_data=None):
    if not os.path.isabs(draft_path):
        cand = os.path.join(out, draft_path)
        if os.path.exists(cand):
            draft_path = cand
    draft_path = os.path.abspath(draft_path)
    if not draft_path.endswith(".draft"):
        raise SystemExit("promote 目标需为 *.draft（如 reference/demo.md.draft）")
    if not os.path.exists(draft_path):
        raise SystemExit("draft 不存在: %s" % draft_path)
    final = draft_path[:-len(".draft")]
    text = read(draft_path)
    has_marker = BEG in text and END in text
    print("promote %s -> %s" % (os.path.basename(draft_path),
                                os.path.relpath(final, out)))
    if not has_marker:
        print("  ! 警告：文件无 AI-GEN marker（纯人工文档，promote 将直接转正）")
    meta, rest = parse_frontmatter(text)
    # 转正前刷新 Sources（把 AI 新填的 file:line 引用收进本页引用清单）
    if inv_data is not None:
        text = fill_sources(text, inv_data)
        meta, rest = parse_frontmatter(text)
    if meta is None or not meta.get("doc_id"):
        print("  ! 警告：文件缺 frontmatter（doc_id），建议补上以便对账")
    else:
        # draft 转正：status current
        meta["status"] = "current"
        text = render_frontmatter(meta) + rest
    write(final, text)
    os.remove(draft_path)
    # 页面树进度：仍有 AI-FILL 工单 → generated（语义待填）；工单清空 → filled（已填充）
    slug = (meta or {}).get("plan_slug") or os.path.relpath(final, out).replace("\\", "/")[:-3]
    set_page_status(out, slug, "generated" if "<!-- AI-FILL" in text else "filled")
    print("完成。建议运行: check --dir <目标项目>")


# ---------------- 对账（check / report） ----------------

def collect_registered(out):
    reg = set()
    doc_files = []
    for fp in list_md(out):
        base = os.path.basename(fp)
        if base == "index.md":
            continue
        doc_files.append(fp)
        reg |= set(ANCHOR.findall(read(fp)))
    return reg, doc_files


def unfilled_todo_count(out):
    """统计正式文档中未填语义的 TODO 占位数（按行近似）。index 不计。"""
    total = 0
    per = {}
    for fp in list_md(out):
        base = os.path.basename(fp)
        if base == "index.md":
            continue
        c = read(fp).count(MARK)
        per[os.path.relpath(fp, out)] = c
        total += c
    return total, per


def inventory_ids(inv_data):
    ids = set()
    for s in inv_data.get("symbols", []):
        if s.get("public", True):
            ids.add(s["id"])
    for e in inv_data.get("endpoints", []):
        ids.add(e["id"])
    for i in inv_data.get("interfaces", []) or []:
        ids.add(i["id"])          # msg/srv 接口定义同样纳入对账（漏写即 orphan）
    return ids


# ---------------- 人工/低置信登记（.registered.json，register 命令维护） ----------------

REG_FILE = ".registered.json"
REG_PREFIX = {"symbol": "SYM", "endpoint": "EPT", "interface": "ITF"}


def load_registered(out):
    data = json_load(os.path.join(out, REG_FILE)) or {}
    items = data.get("items", []) if isinstance(data, dict) else []
    return items if isinstance(items, list) else []


def registered_ids(items):
    return {it["id"] for it in items if it.get("id")}


def next_reg_id(items, kind):
    prefix = REG_PREFIX.get(kind, "SYM")
    n = 0
    for it in items:
        rid = it.get("id") or ""
        if rid.startswith(prefix + "-"):
            try:
                n = max(n, int(rid.split("-", 1)[1]))
            except ValueError:
                pass
    return "%s-%03d" % (prefix, n + 1)


def cmd_register(root, out, kind, name, src, line=0, signature=None, note=None):
    """登记一个语言指纹未覆盖的符号/端点/接口（必须指向真实源文件，防编造）。
    line 命中名字 -> confidence=verified；否则 unverified（抽审优先）。"""
    if kind not in REG_PREFIX:
        raise SystemExit("register: --kind 须为 %s" % "/".join(REG_PREFIX))
    if not name or not src:
        raise SystemExit("register: 需要 --name <限定名> --src <相对项目根的源文件>")
    rel_file = src.replace("\\", "/")
    absf = os.path.join(root, rel_file)
    if not os.path.isfile(absf):
        raise SystemExit("register: 源文件不存在: %s（登记必须指向真实文件，防编造）" % rel_file)
    items = load_registered(out)
    rid = next_reg_id(items, kind)
    conf = "manual"
    if line:
        try:
            with open(absf, encoding="utf-8", errors="replace") as f:
                lines = f.read().splitlines()
            # 名字末段（支持 Foo::bar / foo.bar / bar）与源码行比对
            needle = name.replace("::", ".").split(".")[-1]
            if 0 < line <= len(lines) and needle in lines[line - 1]:
                conf = "verified"
            else:
                print("  ! line=%s 未在源文件对应行命中名字——登记为未核验，抽审时优先核对" % line)
        except OSError:
            pass
    item = {"id": rid, "kind": kind, "name": name, "file": rel_file,
            "line": int(line or 0), "confidence": conf}
    if signature:
        item["signature"] = signature
    if note:
        item["note"] = note
    items.append(item)
    json_save(os.path.join(out, REG_FILE), {"version": 1, "items": items})
    print("已登记 %s（kind=%s，file %s:%s，confidence=%s）" %
          (rid, kind, rel_file, item["line"], conf))
    print("建议运行: check --dir <目标项目>")
    return 0


# ---------------- 语义地图（LLM 提取的产物，.semantic-map.json） ----------------

SEMANTIC_MAP_FILE = ".semantic-map.json"

# ---------------- 语义地图来源分级（v1.5.4，治 P0-1"SQLite 数据库"乌龙） ----------------
# 语义地图是 LLM 产物（AI 断言），不是机器实测：渲染须标注来源，其内引用与重词须复核。
AI_ASSERT_MARK = "（语义地图·AI 断言·待核）"

# 重词 -> 源码痕迹线索（**全部**线索都搜不到才提示；对语料小写匹配，含注释文本）。
# 具体产品名（sqlite 等）term==trace：文档提了、代码里搜不到 → 即报（P0-1 的直接形态）。
_SENSITIVE_TERMS = (
    ("sqlite", ("sqlite",)),
    ("redis", ("redis",)),
    ("mysql", ("mysql",)),
    ("postgres", ("postgres", "postgresql")),
    ("mongodb", ("mongodb", "mongo")),
    ("数据库", ("sqlite", "mysql", "postgres", "mongodb", "database", ".db", "db_")),
    ("缓存", ("cache", "redis", "memcache", "lru")),
    ("调度", ("scheduler", "sched")),
)
_CODE_CORPUS_MAX = 8 * 1024 * 1024   # 源码痕迹语料拼接上限（防超大仓库拖慢）
_FILE_MAX_BYTES = 2 * 1024 * 1024    # 单文件参与语料上限（与 inventory 口径一致）


def load_semantic_map(out):
    smap = json_load(os.path.join(out, SEMANTIC_MAP_FILE))
    return smap if isinstance(smap, dict) else None


def _smap_texts(smap):
    """语义地图全部字符串值 -> [(路径, 文本)]（递归收集；路径如 .modules.responsibility）。"""
    out = []

    def _w(o, path):
        if isinstance(o, dict):
            for k, v in o.items():
                _w(v, "%s.%s" % (path, k))
        elif isinstance(o, list):
            for i, v in enumerate(o):
                _w(v, path)
        elif isinstance(o, str):
            out.append((path, o))

    _w(smap, "")
    return out


def _code_corpus(inv_data, root):
    """全部项目文件的小写拼接文本（源码痕迹语料；总量/单文件均截断）。"""
    parts, total = [], 0
    for f in inv_data.get("files") or []:
        if (f.get("bytes") or 0) > _FILE_MAX_BYTES:
            continue
        try:
            with open(os.path.join(root, f["path"]), encoding="utf-8", errors="replace") as fh:
                t = fh.read().lower()
        except OSError:
            continue
        parts.append(t)
        total += len(t)
        if total > _CODE_CORPUS_MAX:
            break
    return "\n".join(parts)


def smap_ref_errors(inv_data, out):
    """语义地图内的 file:line 引用解析不到项目文件（AI 断言产物同样不得编造引用）。提示级。"""
    smap = load_semantic_map(out)
    if not smap:
        return []
    all_files = {f["path"] for f in inv_data.get("files") or []}
    if not all_files:
        return []
    errs = set()
    for path, text in _smap_texts(smap):
        for ref, line in REF_RE.findall(text):
            if resolve_ref(ref.replace("\\", "/").lstrip("./"), all_files) is None:
                errs.add("%s -> %s:%s" % (path.lstrip("."), ref, line))
    return sorted(errs)


def smap_suspect_terms(inv_data, out, root=None):
    """地图自由文本出现重词（数据库/缓存/调度…）但源码无对应痕迹 → 疑 AI 臆造。提示级。

    口径：具体产品名（sqlite/redis…）提了就必须搜得到；泛称（数据库/缓存/调度）任一线索
    （标识符/文件名/外部依赖/注释文本）存在即放行——宁漏报不误报，只提示不拦截。"""
    smap = load_semantic_map(out)
    if not smap:
        return []
    root = root or inv_data.get("root") or ""
    if not root:
        return []
    corpus = _code_corpus(inv_data, root)
    for m in inv_data.get("modules") or []:
        corpus += " " + " ".join(str(d) for d in (m.get("external_deps") or []))
    hits = set()
    for path, text in _smap_texts(smap):
        low = text.lower()
        for term, traces in _SENSITIVE_TERMS:
            hit = term in low if term.isascii() else term in text
            if not hit or any(t in corpus for t in traces):
                continue
            hits.add("%s：出现「%s」但源码无对应痕迹（线索：%s）"
                     % (path.lstrip("."), term, "、".join(traces[:4])))
    return sorted(hits)


def file_coverage(inv_data, smap, out_rel=None):
    """文件级防漏统计：全集 files vs 语义地图归属/忽略。
    out_rel=产物目录相对项目根路径（如 docs/dev-docs），其自身文件不算待归属。
    返回 (total, covered, uncovered, ignored)。smap 缺失时 uncovered=全集（除产物）。"""
    files = [f["path"] for f in inv_data.get("files", [])]
    if out_rel and out_rel not in (".", ""):
        prefix = out_rel.replace("\\", "/") + "/"
        files = [p for p in files if not p.startswith(prefix)]
    if smap is None:
        return len(files), 0, files, 0
    covered, ignored = set(), set()
    for m in smap.get("modules", []) or []:
        for p in m.get("files", []) or []:
            covered.add(p.replace("\\", "/"))
        for it in m.get("ignored_files", []) or []:
            p = it["path"] if isinstance(it, dict) else it
            ignored.add(p.replace("\\", "/"))
    uncovered = [p for p in files if p not in covered and p not in ignored]
    return len(files), len(files) - len(uncovered), uncovered, len(ignored)


def stale_docs(inv_data, out):
    """文档 frontmatter.source_commit 落后于当前提交 -> stale。非 git 时不判。"""
    if not inv_data.get("is_git") or not inv_data.get("source_commit"):
        return []
    stale = []
    for fp in list_md(out):
        base = os.path.basename(fp)
        if base == "index.md":
            continue
        meta, _ = parse_frontmatter(read(fp))
        if not meta:
            continue
        sc = meta.get("source_commit", "")
        if sc and sc != inv_data["source_commit"]:
            stale.append(os.path.relpath(fp, out))
    return stale


def drift_diff(inv_now, out):
    """对比 baseline 与当前快照，返回代码变更/符号新增删除/受影响模块。"""
    base = json_load(os.path.join(out, ".baseline.json")) or {}
    if not base:
        return None
    changed = []
    old_hashes = dict(base.get("code_file_hashes") or {})
    old_hashes.update(base.get("files_hashes") or {})   # v1.3：全文件基线（非代码文件也参与漂移）
    now_hashes = dict(inv_now.get("code_file_hashes") or {})
    now_hashes.update(inv_now.get("files_hashes") or {})
    # 产物目录自身（docs/<out>/）的变更不算代码漂移
    out_rel = ""
    try:
        out_rel = os.path.relpath(out, inv_now.get("root") or ".").replace("\\", "/")
    except ValueError:
        out_rel = ""
    out_prefix = (out_rel + "/") if out_rel not in (".", "") else None
    for rel, h in now_hashes.items():
        if old_hashes.get(rel) != h:
            if out_prefix and rel.startswith(out_prefix):
                continue
            changed.append(rel)
    old_syms = set(base.get("symbol_ids") or [])
    now_syms = {s["id"] for s in inv_now.get("symbols", [])} | \
               {e["id"] for e in inv_now.get("endpoints", [])}
    old_eps = old_syms
    added = sorted(now_syms - old_eps)
    removed = sorted(old_syms - now_syms)
    tops = sorted({c.split("/")[0] for c in changed})
    # 精确受影响模块（最长路径前缀匹配 MOD-id）
    mods = inv_now.get("modules") or []
    affected_mods = set()
    for rel in changed:
        best = None
        for m in mods:
            p = m.get("path") or ""
            if p == ".":
                continue
            if rel == p or rel.startswith(p + "/"):
                if best is None or len(p) > len(best[0]):
                    best = (p, m["id"])
        if best:
            affected_mods.add(best[1])
        elif "/" not in rel:
            affected_mods.add("MOD-000")
    return {"changed_files": changed, "added_ids": added,
            "removed_ids": removed, "affected_tops": tops,
            "affected_mods": sorted(affected_mods)}


def _norm_head(s):
    """标题/章节名规范化（去编号、标点、空白）——用于结构匹配。"""
    return re.sub(r"[\s#*`：:（）()【】\[\]0-9.、,，\-—_]+", "", s or "")


def page_structure_errors(inv_data, out, plan):
    """页面结构门禁：①plan 标为已生成的页缺文件 ②产物页缺必需章节。"""
    missing_pages, section_missing = [], []
    for p in (plan or {}).get("pages") or []:
        rel = "%s.md" % (p.get("slug") or "")
        fp = os.path.join(out, rel)
        if not os.path.exists(fp):
            if os.path.exists(fp + ".draft"):
                continue          # 中间态：draft 已生成待 promote，不算结构错误
            if p.get("status") != "planned":
                missing_pages.append("%s（plan 标为 %s 但文件不存在）" % (rel, p.get("status")))
            continue
        heads = [_norm_head(h) for h in re.findall(r"^#+ .*$", read(fp), re.M)]
        for sec in p.get("sections") or []:
            key = _norm_head(sec)
            if key and not any(key in h for h in heads):
                section_missing.append("%s 缺节「%s」" % (rel, sec))
    return missing_pages, section_missing


def page_ref_errors(inv_data, out):
    """页内 file:line 引用解析不到项目文件（不存在或短名多义）→ ERROR（防编造）。"""
    all_files = {f["path"] for f in inv_data.get("files") or []}
    errs = []
    if not all_files:
        return errs
    for fp in list_md(out):
        rel = os.path.relpath(fp, out).replace("\\", "/")
        for path, line in REF_RE.findall(read(fp)):
            p = path.replace("\\", "/").lstrip("./")
            if resolve_ref(p, all_files) is None:
                errs.append("%s -> %s:%s" % (rel, p, line))
    return errs


def _line_kind(lines, lineno, abs_path=None):
    """源码行性质：code / import / comment / string / blank / oor（越界）。

    v1.5.2：先走**语法层判定**（`dev_langs.line_kind_ts`）——它能识别"字符串/块注释内的行"，
    即被 '''…''' / /* … */ 包住的"注释掉的代码"（文本启发式看着它就是代码，是假事实的常见来源）；
    无语法可用时（.msg/.srv 等）退化为文本启发式。"""
    if lineno < 1 or lineno > len(lines):
        return "oor"
    s = lines[lineno - 1]
    if not s.strip():
        return "blank"
    if abs_path:
        k = line_kind_ts(abs_path, lineno, s)
        if k:
            return k
    st = s.strip()
    if st.startswith("#") and not st.startswith(("#define", "#include", "#pragma", "#if", "#endif", "#else", "#elif")):
        # `#` 注释判定排除 C 预处理指令（#define/#include 等是实质代码行）
        return "comment"
    if st.startswith(("import ", "from ")):
        return "import"
    return "code"


def _is_def_line(s):
    """该行是否为"定义行"（def/class/赋值）——优先作为建议目标。"""
    t = s.strip()
    return t.startswith(("def ", "async def ", "class ")) or (" = " in t)


def _suggest_line(lines, lineno, window=8, abs_path=None):
    """在 lineno 附近找建议行：**优先定义行**（def/class/赋值），否则退化为非空非注释行；
    方向为"先向上再向下"——手写行号偏大的情形远多于偏小（常把注释块行尾/空行当锚点）。"""
    n = len(lines)
    for want_def in (True, False):
        for d in range(0, window + 1):
            for cand in (lineno - d, lineno + d):
                if not (1 <= cand <= n):
                    continue
                kind = _line_kind(lines, cand, abs_path)
                if kind not in ("code", "import"):
                    continue
                if want_def and kind == "code" and not _is_def_line(lines[cand - 1]):
                    continue
                return cand
    return None


def ref_line_issues(inv_data, out, fix=False):
    """页内 file:line 的**行号合理性**校验（v1.4.1，机制化治"手写行号偏移"）：
    - 引用的行是空行 / 注释行 / 越界 → **明确错误**，建议邻近代码行，可自动修（auto=True）
    - 引用的行是 import/from 语句、而该处上下文并未在讲"依赖/导入" → 疑似把"文件开头"当锚点，
      只提示不自动改（auto=False，避免误改正当引用）
    不含"文件不存在"的引用——那由 ref_file_missing 负责。"""
    root = inv_data.get("root") or ""
    all_files = {f["path"] for f in inv_data.get("files") or []}
    issues = []
    for fp in list_md(out):
        rel = os.path.relpath(fp, out).replace("\\", "/")
        text = read(fp)
        changed = False
        for m in REF_RE.finditer(text):
            path, line = m.group(1), m.group(2)
            p = path.replace("\\", "/").lstrip("./")
            if p not in all_files:
                continue
            try:
                with open(os.path.join(root, p), encoding="utf-8", errors="replace") as fh:
                    lines = fh.read().splitlines()
            except OSError:
                continue
            i = int(line)
            abs_path = os.path.join(root, p)
            kind = _line_kind(lines, i, abs_path)
            if kind == "code":
                continue
            # 自动修仅限"明确错误"：空行 / 越界；注释/字符串/import 行只提示（避免误改正当引用）
            auto = kind in ("blank", "oor")
            if kind == "import":
                # 取引用所在行 + 上一行作为上下文（说明词可能跨行）
                ls = text.rfind("\n", 0, m.start()) + 1
                le = text.find("\n", m.end())
                prev_ls = text.rfind("\n", 0, max(0, ls - 1)) + 1
                ctx = (text[prev_ls:ls] + text[ls: le if le != -1 else len(text)]).lower()
                if any(k in ctx for k in ("import", "导入", "依赖", "依赖项", "引入", "引用")):
                    continue          # 正当引用 import 行
            sug = _suggest_line(lines, i, abs_path=abs_path)
            issues.append({"doc": rel, "ref": "%s:%d" % (p, i), "kind": kind,
                           "file": p, "line": i, "suggest": sug, "auto": auto})
            if fix and auto and sug and sug != i:
                old, new = "%s:%d" % (path, i), "%s:%d" % (path, sug)
                if old in text:
                    text = text.replace(old, new)
                    changed = True
        if fix and changed:
            write(fp, text)
    return issues


def cmd_fixrefs(root, out, inv_data=None, write=False):
    """修正「引用行号指向空行/注释行/越界」的偏差（默认 dry-run 显示建议）。"""
    data = inv_data if inv_data is not None else load_inventory(out)
    if data is None:
        raise SystemExit("fixrefs：缺少 inventory.json（先运行 inventory）")
    issues = ref_line_issues(data, out, fix=write)
    if not issues:
        print("fixrefs：未发现行号异常引用 ✓")
        return 0
    fixable = [it for it in issues if it["auto"] and it["suggest"] and it["suggest"] != it["line"]]
    for it in issues[:40]:
        tip = ("建议 %s:%d" % (it["file"], it["suggest"])) if it["suggest"] else "未找到邻近有效行"
        flag = "自动可修" if it["auto"] else "需人工判断"
        print("  [%s|%s] %s 引用 %s → %s" % (it["kind"], flag, it["doc"], it["ref"], tip))
    if len(issues) > 40:
        print("  … 共 %d 项" % len(issues))
    if write:
        print("已自动修正 %d 处；%d 处需人工复核" % (len(fixable), len(issues) - len(fixable)))
    else:
        print("（dry-run：加 --write 应用可自动修正的 %d 处；共 %d 项）" % (len(fixable), len(issues)))
    return 0


def ai_fill_hits(out):
    """AI-FILL 工单残留（章节级未完成标记）→ 每文件计数。"""
    hits = []
    for fp in list_md(out):
        n = read(fp).count("<!-- AI-FILL")
        if n:
            hits.append("%s（%d 处）" % (os.path.relpath(fp, out).replace("\\", "/"), n))
    return hits


def zero_ref_asserts(inv_data, out):
    """refs=0 且卡片语义**已填**（非 TODO）→ 叙事在断言其用途（P0-4 的残余形态）。
    提示级（不计失败）：可能是框架回调，但叙事必须降级为「零引用，用途待证」或删除。"""
    zero_names = {z.get("name") for z in (inv_data.get("zero_refs") or [])}
    if not zero_names:
        return []
    name_of_id = {}
    for s in inv_data.get("symbols", []):
        if inv._leaf_name(s.get("qname")) in zero_names:
            name_of_id[s["id"]] = s.get("qname")
    if not name_of_id:
        return []
    hits = []
    for fp in list_md(out):
        rel = os.path.relpath(fp, out).replace("\\", "/")
        cur = None
        for ln in read(fp).split("\n"):
            m = ANCHOR.search(ln)
            if m:
                cur = m.group(1)
                continue
            if cur and ln.startswith(_SEM_LINE_PREFIX):
                if MARK not in ln and cur in name_of_id:
                    hits.append("%s（@%s %s）" % (rel, cur, name_of_id[cur]))
                cur = None
    return hits


def analyze(inv_data, out, drift=False):
    reg, doc_files = collect_registered(out)
    reg_items = load_registered(out)
    # 对账新口径（v1.3）：登记集 = 自动枚举 ∪ 人工登记；文档锚点不在登记集才算 phantom
    want = inventory_ids(inv_data) | registered_ids(reg_items)
    orphan_syms = [s["id"] for s in inv_data.get("symbols", [])
                   if s.get("public", True) and s["id"] not in reg]
    orphan_eps = [e["id"] for e in inv_data.get("endpoints", [])
                  if e["id"] not in reg]
    phantom = sorted(reg - want)
    # 登记指向不存在的源文件 = ERROR（登记腐化防线；inventory 无 files 字段时跳过）
    if inv_data.get("files") is not None:
        all_files = {f["path"] for f in inv_data["files"]}
        reg_file_errors = ["%s -> %s（文件不在项目全集）" % (it["id"], it["file"])
                           for it in reg_items
                           if it.get("file") and it["file"] not in all_files]
    else:
        reg_file_errors = []
    stale = stale_docs(inv_data, out)
    smap = load_semantic_map(out)
    out_rel = ""
    if inv_data.get("root") and out:
        try:
            out_rel = os.path.relpath(out, inv_data["root"]).replace("\\", "/")
        except ValueError:
            out_rel = ""
    f_total, f_covered, f_uncovered, f_ignored = file_coverage(inv_data, smap, out_rel)
    # v1.4 页面树结构门禁
    plan = load_plan(out)
    missing_pages, section_missing = page_structure_errors(inv_data, out, plan)
    ref_errors = page_ref_errors(inv_data, out)
    ref_lines = ref_line_issues(inv_data, out)      # v1.4.1：引用行号合理性（空行/注释行=疑似偏移）
    fill_hits = ai_fill_hits(out)
    semantic_todo = unfilled_todo_count(out)[0]   # v1.5.5：index 覆盖率人话口径（未填卡片语义处数）
    page_stat = {"total": 0, "filled": 0, "generated": 0, "planned": 0}
    for p in (plan or {}).get("pages") or []:
        page_stat["total"] += 1
        st = p.get("status") if p.get("status") in ("filled", "generated") else "planned"
        page_stat[st] += 1
    drift_info = None
    if drift:
        drift_info = drift_diff(inv_data, out)
    return {
        "orphan_syms": sorted(orphan_syms), "orphan_eps": sorted(orphan_eps),
        "phantom": phantom, "stale": stale,
        "registered_count": len(reg), "doc_count": len(doc_files),
        "want_count": len(want), "drift": drift_info,
        "reg_items": reg_items, "reg_file_errors": reg_file_errors,
        "has_semantic_map": smap is not None,
        "file_total": f_total, "file_covered": f_covered,
        "file_uncovered": f_uncovered, "file_ignored": f_ignored,
        "has_plan": plan is not None, "page_stat": page_stat,
        "plan_missing_pages": missing_pages, "section_missing": section_missing,
        "ref_errors": ref_errors, "ai_fill": fill_hits,
        "semantic_todo": semantic_todo,
        "ref_line_issues": ref_lines,
        "zero_ref_asserts": zero_ref_asserts(inv_data, out),
        "smap_ref_errors": smap_ref_errors(inv_data, out),
        "smap_suspect_terms": smap_suspect_terms(inv_data, out),
    }


def cmd_check(inv_data, out, drift, root=None, strict=False):
    if inv_data is None:
        raise SystemExit("缺少 inventory.json，先运行 inventory 或带 --drift 重扫")
    rep = analyze(inv_data, out, drift=drift)
    errors = (rep["orphan_syms"] + rep["orphan_eps"] + rep["phantom"] + rep["stale"]
              + rep["reg_file_errors"] + rep["plan_missing_pages"]
              + rep["section_missing"] + rep["ref_errors"])
    warns = list(rep["ai_fill"])
    if rep.get("ref_line_issues"):
        warns.append("引用行号疑似偏移 %d 处（运行 fixrefs 查看，--write 自动修正）"
                     % len(rep["ref_line_issues"]))
    if rep["has_semantic_map"] and rep["file_uncovered"]:
        warns.append("文件未归属 %d 个（语义地图 files/ignored_files 未覆盖）" % len(rep["file_uncovered"]))
    print("=== dev-docs check ===")
    print("登记符号/端点：%d / %d　文档文件：%d　人工登记：%d" %
          (rep["registered_count"], rep["want_count"], rep["doc_count"],
           len(rep["reg_items"])))
    ps = rep["page_stat"]
    if rep["has_plan"]:
        print("页面树：%d 页（✅ filled %d / 🟡 generated %d / ⬜ planned %d）"
              % (ps["total"], ps["filled"], ps["generated"], ps["planned"]))
    else:
        print("页面树：未生成（.devdocs-plan.json 缺失；运行 plan --write 启用结构门禁）")
    # 文件级防漏（LLM 提取的覆盖门禁素材）
    if rep["has_semantic_map"]:
        print("文件覆盖：%d / %d（ignored %d，未覆盖 %d）" %
              (rep["file_covered"], rep["file_total"],
               rep["file_ignored"], len(rep["file_uncovered"])))
        if rep["file_uncovered"]:
            for p in rep["file_uncovered"][:10]:
                print("    ? 未归属: %s" % p)
            if len(rep["file_uncovered"]) > 10:
                print("    ... 共 %d（补 .semantic-map.json 的 files/ignored_files）"
                      % len(rep["file_uncovered"]))
    else:
        print("文件覆盖：语义地图缺失（.semantic-map.json）——LLM 提取前请先建图（提示，不作为门禁）")
    unf, per = unfilled_todo_count(out)
    if unf:
        worst = max(sorted(per.items()), key=lambda kv: kv[1])
        print("语义填充：剩余未填 TODO %d 处（最多: %s %d）——%s"
              % (unf, worst[0], worst[1],
                 "--strict 下为 ERROR（交付须填尽）" if strict else
                 "普通模式仅提示；交付口径为一次填尽（--strict 门禁）"))
    else:
        print("语义填充：全部符号已填 ✓")
    if root and inv_data.get("is_git") and inv_data.get("source_commit"):
        head = inv.head_commit(root)
        if head and head != inv_data["source_commit"]:
            print("提示: 仓库 HEAD(%s) 已领先 inventory(%s)——代码可能已变更，"
                  "建议执行 check --drift 重扫" % (head, inv_data["source_commit"]))
    if drift:
        d = rep["drift"]
        if d is None:
            print("drift: 无基线（请先完成一次 extract + report 建立 baseline）")
        else:
            print("drift: 代码变更文件 %d、受影响模块 %s、新增符号/端点 %d、移除 %d" %
                  (len(d["changed_files"]), (d.get("affected_mods") or d["affected_tops"] or "无"),
                   len(d["added_ids"]), len(d["removed_ids"])))
            if not d["changed_files"] and not d["added_ids"] and not d["removed_ids"]:
                print("drift: 无漂移 ✓")
    groups = (("orphan(有码无文)", rep["orphan_syms"] + rep["orphan_eps"],
               "补文档卡片或 register 登记"),
              ("phantom(有文无码)", rep["phantom"], "删除锚点或补 register 登记"),
              ("stale(文档过期)", rep["stale"], "重跑 extract 并 promote"),
              ("reg_file_error(登记指向不存在的文件)", rep["reg_file_errors"], "修正 .registered.json 的 file"),
              ("plan_missing_page(页面树应有但未生成)", rep["plan_missing_pages"], "运行 extract --layer all"),
              ("section_missing(页缺必需章节)", rep["section_missing"], "运行 brief --page <slug> 取工单补齐"),
              ("ref_file_missing(引用文件不在项目全集)", rep["ref_errors"], "核对 file 路径或删除编造引用"),
              ("ref_line_suspect(引用行号指向空行/注释/字符串（含注释掉的代码）)",
               ["%s → %s" % (it["doc"], it["ref"]) for it in (rep.get("ref_line_issues") or [])],
               "运行 fixrefs --write 自动修正"))
    if strict:
        groups = groups + (
            ("ai_fill_left(AI 工单未填尽)", rep["ai_fill"], "填写对应章节后删除 <!-- AI-FILL --> 块"),
            # v1.5：交付口径 = 一次填尽（不再以"分批交付"留待办）
            ("semantic_todo_left(符号/接口卡片语义未填尽)",
             ["%s（%d 处）" % (k, v) for k, v in sorted(per.items()) if v],
             "按锚点补齐「用途/参数/返回/错误」"),
        )
    if strict and unf:
        # v1.5 交付口径：卡片语义未填尽 = ERROR（此前仅打印、未计入失败，门禁实际失效）
        errors = errors + ["semantic_todo_left(%s %d 处)" % (k, v)
                           for k, v in sorted(per.items()) if v]
    for label, items, fix in groups:
        if items:
            print("[%s] %d 项（%s）:" % (label, len(items), fix))
            for it in items[:20]:
                print("    - %s" % it)
            if len(items) > 20:
                print("    ... 共 %d" % len(items))
    build_issues = inv_data.get("build_issues") or []
    if build_issues:
        print("[build_decl_missing(CMake 声明但文件不存在——**项目构建风险**，非文档缺陷，不计失败)] %d 项:"
              % len(build_issues))
        for it in build_issues[:10]:
            print("    - %s（%s）：%s" % (it["name"], it["module_path"],
                                          "、".join(it["missing"][:5])))
    zero = inv_data.get("zero_refs") or []
    if zero:
        print("[zero_ref(源码零引用的名称——疑似死代码/仅供框架回调；提示，不计失败)] %d 项:" % len(zero))
        for z in zero[:20]:
            print("    - %s（%s）" % (z["name"], z["kind"]))
        if len(zero) > 20:
            print("    ... 共 %d" % len(zero))
    asserts = rep.get("zero_ref_asserts") or []
    if asserts:
        print("[zero_ref_assert(卡片已断言用途但该符号源码零引用——叙事须改写为"
              "「零引用，用途待证」或删除；提示，不计失败)] %d 项:" % len(asserts))
        for it in asserts[:10]:
            print("    - %s" % it)
        if len(asserts) > 10:
            print("    ... 共 %d" % len(asserts))
    # v1.5.4 语义地图来源分级（提示级：地图是 AI 断言产物，防其被当事实渲染）
    if rep["has_semantic_map"]:
        smap_refs = rep.get("smap_ref_errors") or []
        if smap_refs:
            print("[smap_ref_error(语义地图内 file:line 引用解析不到——AI 断言产物同样不得编造引用；"
                  "提示，不计失败)] %d 项:" % len(smap_refs))
            for it in smap_refs[:10]:
                print("    - %s" % it)
            if len(smap_refs) > 10:
                print("    ... 共 %d" % len(smap_refs))
        smap_terms = rep.get("smap_suspect_terms") or []
        if smap_terms:
            print("[smap_suspect_term(语义地图出现重词但源码无对应痕迹——疑 AI 臆造，须给出证据或删改；"
                  "提示，不计失败)] %d 项:" % len(smap_terms))
            for it in smap_terms[:10]:
                print("    - %s" % it)
            if len(smap_terms) > 10:
                print("    ... 共 %d" % len(smap_terms))
    if warns and not strict:
        print("[warn] %d 项（普通模式提示；交付须 --strict 全绿）:" % len(warns))
        for w in warns[:10]:
            print("    ~ %s" % w)
    if errors or (strict and warns):
        print("RESULT: FAIL（ERROR=%d%s）" % (len(errors),
                                             "，WARN=%d" % len(warns) if warns else ""))
        return 1
    print("RESULT: PASS（ERROR=0%s）" % ("，WARN=%d" % len(warns) if warns else ""))
    return 0


def index_page(inv_data, out):
    p = page_by_slug(load_plan(out), "index")
    if p:
        return p
    return {"slug": "index", "type": "index",
            "title": "%s — 文档总览" % (os.path.basename(inv_data.get("root") or "") or "project"),
            "purpose": PAGE_PURPOSE["index"], "sections": list(PAGE_SECTIONS["index"]),
            "source_files": root_files(inv_data, out), "parent": None, "status": "planned"}


def index_values(inv_data, out, rep):
    vals = page_values(inv_data, out, index_page(inv_data, out))
    vals.update(coverage_values(inv_data, out, rep=rep))
    return vals


def cmd_report(inv_data, root, out):
    if inv_data is None:
        inv_data = cmd_inventory(root, out, [])
    rep = analyze(inv_data, out)
    vals = index_values(inv_data, out, rep)
    _refresh_generated(out, "index.md", fill_sources(render("index.md", vals), inv_data))
    # baseline
    doc_hashes = {}
    for fp in list_md(out):
        doc_hashes[os.path.relpath(fp, out)] = inv.sha256_file(fp)
    baseline = {
        "generated_at": now_iso(),
        "source_commit": inv_data.get("source_commit") or "",
        "inventory_hash": inv_data.get("_inventory_hash") or "",
        "code_file_hashes": inv_data.get("code_file_hashes") or {},
        "files_hashes": inv_data.get("files_hashes") or {},
        "symbol_ids": sorted(({s["id"] for s in inv_data.get("symbols", [])}
                              | {e["id"] for e in inv_data.get("endpoints", [])}
                              | {i["id"] for i in inv_data.get("interfaces", []) or []})),
        "docs": doc_hashes,
    }
    json_save(os.path.join(out, ".baseline.json"), baseline)
    print("index.md 已刷新（机器区）；.baseline.json 已刷新（docs %d）" % len(doc_hashes))
    print("覆盖率：registered %d / %d；orphan %d；phantom %d；stale %d" %
          (rep["registered_count"], rep["want_count"],
           len(rep["orphan_syms"]) + len(rep["orphan_eps"]),
           len(rep["phantom"]), len(rep["stale"])))


# ---------------- 页级填写工单（brief：LLM 提取接口） ----------------

BRIEF_HINTS = {
    "index": ["先写一句话定位（是什么、给谁用），再写能力矩阵",
              "能力矩阵每行给出处（file:line 或链接）；数字不要手写，机器会渲染覆盖率",
              "文档树由 plan 渲染（勿手改结构）；purpose 与 plan 一致",
              "阅读路径按角色分（新人 / 维护者 / AI）"],
    "architecture": ["系统上下文：使用者、触发方式、外部交互清单 + 边界图（mermaid 或文本）",
                     "构建块视图：每个模块一张白盒卡（职责/对外接口/依赖/被依赖/内部结构）",
                     "运行时场景 ≥2 个：分步骤序列，每步带 file:line",
                     "横切与决策：安全/持久化/错误约定；why 标「人类待确认」",
                     "术语表 8-20 条项目专属术语"],
    "usage": ["安装与启动：命令 + 前置条件（README 摘录 + 链接，不复制原文）",
              "配置：表格（键｜含义｜默认｜来源 file:line）",
              "常见任务 ≥3 个：编号步骤，读者可照做",
              "扩展点：新增一个 X 的步骤化说明",
              "故障排查：错误信息 → 原因 → 处置"],
    "reference": ["概览四问：做什么 / 为何存在 / 依赖什么 / 谁依赖它",
                  "组件与协作：组件清单（含 file:line）+ 装配期与运行期两条协作链 + 关键设计要点",
                  "使用指南：典型用法（示例取自测试并注明 文件::用例）+ 扩展点步骤",
                  "对外接口面：名称 / 签名 / 契约 表",
                  "符号索引与详细契约在 AI-GEN 区（机器全量；卡片按批填充）"],
    "data": ["实体与字段（缺失写 unknown）", "关系（外键/引用）", "存储与生命周期"],
}

BRIEF_CHECKLIST = ["AI-FILL 残留 0", "每节 ≥1 条 file:line", "引用的文件必须在项目全集内",
                   "组件名 ⊆ 语义地图 components", "上列登记项全部出现", "示例注明来源"]


# ---------------- 卡片证据候选（v1.5.2，供 brief 工单）----------------
# 目的：把"该引用哪里"变成给定素材，避免填卡者自行推断（曾把**调用点**注释误配给定义处）。
_BANNER_RE = re.compile(r"^[=\-*/\s]*$|^(declare|definition)s?\s+for\s+\w+$", re.I)


def _src_lines(root, rel):
    try:
        with open(os.path.join(root, rel), encoding="utf-8", errors="replace") as fh:
            return fh.read().splitlines()
    except OSError:
        return []


def _leading_comments(lines, lineno, limit=2):
    """定义行紧邻上方注释块（过滤 `//****`、`//definition for functions` 之类横幅）。"""
    out, i = [], lineno - 2
    while i >= 0 and len(out) < limit:
        s = lines[i].strip()
        if s.startswith(("//", "#", "/*", "*")):
            t = s.lstrip("/*#").strip()
            if t and not _BANNER_RE.match(t):
                out.insert(0, "L%d %s" % (i + 1, t[:90]))
            i -= 1
        elif not s and out:
            i -= 1
        else:
            break
    return out


def _trailing_decl_comment(lines, lineno, abs_path):
    """定义行**行尾**注释；仅当该行确为声明/定义行（语法判定）才采纳——
    调用点行尾注释（`f(x);//说明`）不算定义处证据。"""
    if not (1 <= lineno <= len(lines)):
        return None
    s = lines[lineno - 1]
    mark = "//" if "//" in s else ("#" if "#" in s else None)
    if not mark:
        return None
    idx = s.find(mark)
    text = s[idx + len(mark):].strip()
    if not text or idx == 0 or not is_decl_line(abs_path, lineno):
        return None
    return "L%d %s" % (lineno, text[:90])


def _field_comments(lines, lineno, limit=3):
    """结构体/类字段的行尾注释（最多 3 条）。

    必须在本结构体的**闭合 `}`** 处停止——否则会越界采到后面结构体的字段注释
    （实测：`AckMsg` 拿到了 `CommandMsg` 的字段说明）。"""
    out, started = [], False
    for i in range(lineno - 1, min(lineno + 30, len(lines))):
        s = lines[i].strip()
        if "{" in s:
            started = True
        if started and s.startswith("}"):
            break
        m = re.search(r"\b(\w+)\s*;\s*//\s*(.+)$", s)
        if m:
            out.append("L%d %s — %s" % (i + 1, m.group(1), m.group(2).strip()[:70]))
        if len(out) >= limit:
            break
    return out


def evidence_hints(inv_data, out, page):
    """页内每张卡片的**候选证据**（只提示、不写入文档）。
    返回 {锚点ID: {"qname":..., "items":[证据串, ...]}}；无证据的卡片不出现。"""
    root = inv_data.get("root") or ""
    mid = page.get("module_id") or ""
    if not mid:
        return {}
    cache, res = {}, {}
    for s in inv_data.get("symbols", []):
        if s.get("module") != mid:
            continue
        rel, ln = s.get("file"), int(s.get("line") or 0)
        if not rel or not ln:
            continue
        if rel not in cache:
            cache[rel] = _src_lines(root, rel)
        lines = cache[rel]
        abs_path = os.path.join(root, rel)
        items = []
        lead = _leading_comments(lines, ln)
        if lead:
            items.append("前置注释：" + " ｜ ".join(lead))
        tc = _trailing_decl_comment(lines, ln, abs_path)
        if tc:
            items.append("声明行尾注释：" + tc)
        if rel.lower().endswith(".py"):
            doc = (docstrings_by_def_line(abs_path) or {}).get(ln)
            if doc:
                items.append("docstring：" + doc)
        if s.get("kind") == "class":
            fc = _field_comments(lines, ln)
            if fc:
                items.append("字段注释：" + " ｜ ".join(fc))
        if items:
            res[s["id"]] = {"qname": s.get("qname"), "items": items}
    return res


def page_zero_refs(inv_data, files, syms):
    """本页相关的零引用名称：refs=0 的页内符号 + 页面源文件里声明的零引用宏（P0-4 场景）。"""
    zero_all = {z.get("name") for z in (inv_data.get("zero_refs") or [])}
    if not zero_all:
        return []
    names = {s.get("qname") for s in syms
             if s.get("refs") == 0 and not inv._is_entry_like(inv._leaf_name(s.get("qname")))}
    root = inv_data.get("root") or ""
    for f in files:
        for name, _ln in macro_defs(os.path.join(root, f)):
            if name in zero_all:
                names.add(name)
    return sorted(n for n in names if n)


def brief_data(inv_data, out, page):
    typ = page.get("type") or ""
    mid = page.get("module_id") or ""
    files = [str(p).replace("\\", "/") for p in (page.get("source_files") or [])]
    fileset = set(files)
    syms = [s for s in inv_data.get("symbols", []) if mid and s.get("module") == mid]
    eps = [e for e in inv_data.get("endpoints", []) if mid and e.get("module") == mid]
    regs = [it for it in load_registered(out) if it.get("file") in fileset]
    tests = []
    for t in inv_data.get("tests", []) or []:
        f = t.get("file") if isinstance(t, dict) else str(t)
        if not f:
            continue
        d = f.rsplit("/", 1)[0] if "/" in f else ""
        if any(f == x or (d and x.startswith(d + "/")) for x in files):
            tests.append(f)
    root = inv_data.get("root") or ""
    finfo = []
    for f in files:
        n = 0
        try:
            with open(os.path.join(root, f), encoding="utf-8", errors="replace") as fh:
                n = sum(1 for _ in fh)
        except OSError:
            n = 0
        finfo.append({"file": f, "lines": n,
                      "symbols": [s.get("qname") for s in syms if s.get("file") == f]})
    return {
        "slug": page.get("slug"), "type": typ, "module_id": mid,
        "title": page.get("title") or page.get("slug"),
        "purpose": page.get("purpose") or "",
        "sections": list(page.get("sections") or []),
        "source_files": finfo,
        "symbols": [{"id": s["id"], "qname": s.get("qname"), "file": s.get("file"),
                     "line": s.get("line", 0), "refs": s.get("refs")} for s in syms],
        "endpoints": [{"id": e["id"], "path": e.get("path"), "file": e.get("file"),
                       "line": e.get("line", 0)} for e in eps],
        "registered": [{"id": it.get("id"), "name": it.get("name"), "file": it.get("file"),
                        "line": it.get("line"), "confidence": it.get("confidence")} for it in regs],
        "tests": sorted(set(tests)),
        "zero_refs": page_zero_refs(inv_data, files, syms),
        "evidence": evidence_hints(inv_data, out, page),
        "requirements": list(BRIEF_HINTS.get(typ, [])),
        "checklist": list(BRIEF_CHECKLIST),
    }


def cmd_doctor(root, out):
    """环境与能力自检（排查"为什么没符号"的第一入口）：
    Python / tree-sitter 版本、各语言适配器可用性、产物与页面树状态。"""
    import platform
    from dev_langs import EXT_LANG, available_extractors
    print("=== dev-docs doctor ===")
    print("python     : %s（%s）" % (platform.python_version(), sys.executable))
    try:
        import importlib.metadata as md
        print("tree-sitter: %s" % md.version("tree-sitter"))
    except Exception:
        print("tree-sitter: 未安装 —— 请执行 pip install -r "
              "<skill>/requirements.txt（v1.5 起为运行时硬依赖）")
    info = available_extractors()
    missing = [lang for lang, i in info.items() if i["extractor"] == "missing"]
    print("语言适配器（ext 数 %d）：" % len(EXT_LANG))
    for lang in sorted(info):
        i = info[lang]
        mark = "✓" if i["extractor"] != "missing" else "✗"
        print("  %s %-11s %-11s %s" % (mark, lang, i["extractor"], i["version"] or "-"))
    inv_path = os.path.join(out, "inventory.json")
    data = json_load(inv_path)
    print("产物       : inventory %s%s" % (
        "有" if data else "无",
        "（%d 文件 / %d 模块 / %d 符号 / %d 接口）" % (
            len(data.get("files") or []), len(data.get("modules") or []),
            len(data.get("symbols") or []), len(data.get("interfaces") or []))
        if data else ""))
    print("             plan %s | 语义地图 %s | 登记 %s | 基线 %s" % (
        "有" if os.path.exists(os.path.join(out, PLAN_FILE)) else "无",
        "有" if os.path.exists(os.path.join(out, SEMANTIC_MAP_FILE)) else "无",
        "有" if os.path.exists(os.path.join(out, REG_FILE)) else "无",
        "有" if os.path.exists(os.path.join(out, ".baseline.json")) else "无",
    ))
    if missing:
        print("⚠ 缺失语法包：%s —— 装齐后这些语言才有符号级抽取"
              % ", ".join(sorted(missing)))
        return 1
    print("RESULT: OK（全部语言适配器可用）")
    return 0


def cmd_brief(inv_data, root, out, page_slug, as_json=False):
    plan = load_plan(out)
    page = page_by_slug(plan, page_slug)
    if page is None:
        known = "、".join([p.get("slug") for p in (plan or {}).get("pages") or []][:12])
        raise SystemExit("brief：页面树中无此页（%s）。可用页：%s%s"
                         % (page_slug, known or "（无）",
                            "" if plan else "（先运行 plan --write 生成页面树）"))
    d = brief_data(inv_data, out, page)
    if as_json:
        print(json.dumps(d, ensure_ascii=False, indent=2))
        return 0
    print("=== 填写工单：%s（type=%s%s）===" %
          (d["slug"], d["type"], "，module=%s" % d["module_id"] if d["module_id"] else ""))
    print("title   : %s" % d["title"])
    print("purpose : %s" % d["purpose"])
    print("sections: %s" % " | ".join(d["sections"]))
    print("")
    print("相关源文件（读这些就够）：")
    if d["source_files"]:
        for fi in d["source_files"][:20]:
            extra = "：" + "、".join(fi["symbols"][:5]) if fi["symbols"] else ""
            print("  %s（约 %d 行，符号 %d%s）" % (fi["file"], fi["lines"], len(fi["symbols"]), extra))
        if len(d["source_files"]) > 20:
            print("  … 共 %d 个文件" % len(d["source_files"]))
    else:
        print("  （plan 未给出源文件；按模块 path 自行定位）")
    print("")
    anchors = (["@%s %s（file %s:%s）" % (s["id"], s["qname"], s["file"], s["line"]) for s in d["symbols"]]
               + ["@%s %s（file %s:%s）" % (e["id"], e.get("path") or "", e["file"], e["line"])
                  for e in d["endpoints"]]
               + ["@%s %s（file %s:%s，%s）" % (it["id"], it["name"], it["file"], it["line"],
                                                it["confidence"]) for it in d["registered"]])
    if anchors:
        print("必须覆盖的登记项（写完 check 会对账）：")
        for a in anchors[:40]:
            print("  %s" % a)
        if len(anchors) > 40:
            print("  … 共 %d 项" % len(anchors))
        print("")
    if d.get("zero_refs"):
        print("零引用名称（源码中无任何使用——疑似死代码或仅供框架回调；"
              "**勿凭命名推断用途**，叙事如实写「未被使用」或标注 refs=0）：")
        for n in d["zero_refs"]:
            print("  - %s" % n)
        print("")
    if d.get("evidence"):
        print("候选证据（写卡片「用途/参数/返回/错误」时优先引用这些；**切勿**用调用点注释解释定义处功能）：")
        for aid, ev in sorted(d["evidence"].items(), key=lambda kv: kv[0])[:25]:
            print("  @%s %s" % (aid, ev.get("qname") or ""))
            for it in ev["items"]:
                print("     - %s" % it)
        if len(d["evidence"]) > 25:
            print("  … 共 %d 张卡片有候选证据" % len(d["evidence"]))
        print("")
    else:
        print("候选证据：（本页卡片未采到注释/docstring；请读源码原文后再写，勿凭命名推断）")
        print("")
    if d["tests"]:
        print("模块测试（示例应取自这里）：%s" % "、".join(d["tests"][:8]))
        print("")
    print("撰写要求：")
    for i, h in enumerate(d["requirements"], 1):
        print("  %d. %s" % (i, h))
    print("  · 每条结论标 evidence：事实（file:line）/ 推断 / 假设 / 缺失（写 unknown）；"
          "行内证据用短式「（据 file:line）」，不比正文长")
    print("  · 签名、字段、路径必须引用源码原文；看不见的写 not visible in sources")
    print("")
    print("完成后自检（check 会查）：")
    for c in d["checklist"]:
        print("  [ ] %s" % c)
    print("")
    print("提示：叙事节在 AI-GEN 区外，重跑 extract 不会冲掉已填内容；填完 promote 转正。")
    return 0


# ---------------- 独立评估工作底稿（v1.5.4 audit 命令） ----------------
# 目的：quality-review 要求抽 ≥25 引用 / ≥15 卡片，纯手工"文档↔源码"来回翻成本高、
# 动作不标准，流程容易被跳过（4 个 P0 即由此漏入）。audit 把机器能核的全核掉
# （行性质 / 签名与盘点一致性 / 零引用断言 / 地图引用与重词），生成"文档原句 vs 源码原文"
# 并排底稿，评估者只剩一件事：判断语义对不对。

def _stride_sample(items, n):
    """确定性等距抽样（不用随机：两次运行同一结果，底稿可复现）。"""
    items = list(items)
    if len(items) <= n:
        return items
    step = len(items) / float(n)
    return [items[min(len(items) - 1, int(i * step))] for i in range(n)]


def _doc_cards(out):
    """正式文档里的卡片 -> {锚点ID: {doc, title, line, sig(反引号内), sem(语义行|None)}}。
    只收签名行式卡片（锚点行含反引号）；符号索引表行无反引号，不误收。"""
    cards = {}
    for fp in list_md(out):
        rel = os.path.relpath(fp, out).replace("\\", "/")
        title, lines = None, read(fp).split("\n")
        for i, ln in enumerate(lines):
            if ln.startswith("### "):
                title = ln[4:].strip()
            m = ANCHOR.search(ln)
            if not m:
                continue
            sm = re.search(r"`([^`]+)`", ln)
            if not sm:
                continue
            sem = None
            for j in range(i + 1, min(i + 4, len(lines))):
                if lines[j].startswith(_SEM_LINE_PREFIX):
                    sem = lines[j].strip()
                    break
            cards[m.group(1)] = {"doc": rel, "title": title or "", "line": i + 1,
                                 "sig": sm.group(1), "sem": sem}
    return cards


def audit_sheet(inv_data, root, out, n_refs=25, n_cards=15):
    """生成评估底稿。返回 (text, stats_dict)。确定性输出。"""
    rep = analyze(inv_data, out)
    all_files = {f["path"] for f in inv_data.get("files") or []}

    # 1) 引用抽样：文档原句 vs 源码原文并排，行性质机器判定
    refs = []
    for fp in list_md(out):
        rel = os.path.relpath(fp, out).replace("\\", "/")
        text = read(fp)
        for m in REF_RE.finditer(text):
            p = m.group(1).replace("\\", "/").lstrip("./")
            full = resolve_ref(p, all_files)
            if full is None:
                continue
            ls = text.rfind("\n", 0, m.start()) + 1
            le = text.find("\n", m.end())
            doc_line = " ".join(text[ls:le if le != -1 else len(text)].split())
            refs.append({"doc": rel, "ref": "%s:%s" % (m.group(1), m.group(2)),
                         "full": full, "doc_line": doc_line[:160]})
    total_refs = len(refs)
    refs = _stride_sample(sorted(refs, key=lambda r: (r["doc"], r["ref"])), n_refs)
    lines_cache = {}
    for r in refs:
        if r["full"] not in lines_cache:
            lines_cache[r["full"]] = _src_lines(root, r["full"])
        lines = lines_cache[r["full"]]
        try:
            lineno = int(r["ref"].rsplit(":", 1)[1])
        except ValueError:
            lineno = 0
        r["kind"] = _line_kind(lines, lineno, os.path.join(root, r["full"]))
        r["src"] = lines[lineno - 1].strip()[:160] if 1 <= lineno <= len(lines) else "（越界）"

    # 2) 卡片抽样：文档签名 vs 盘点签名机器比对；语义行随底稿给出
    cards = _doc_cards(out)
    syms = {s["id"]: s for s in inv_data.get("symbols", [])}
    eps = {e["id"]: e for e in inv_data.get("endpoints", [])}
    total_cards = len(cards)
    sig_mismatch = 0
    card_rows = []
    for cid in _stride_sample(sorted(cards), n_cards):
        c = cards[cid]
        s = syms.get(cid)
        mark = ""
        if s:
            dsig = " ".join(c["sig"].split())
            isig = " ".join((s.get("signature") or "").split())
            if isig and dsig != isig:
                mark = "　**[签名与盘点不一致]**"
                sig_mismatch += 1
        kind = s.get("kind") if s else ("端点" if cid in eps else "?")
        card_rows.append("@%s（%s）%s%s\n    文档签名：`%s`\n    盘点签名：`%s`\n    语义行：%s"
                         % (cid, kind, c["title"], mark, c["sig"],
                            (s or {}).get("signature") or "（无盘点记录）",
                            (c["sem"] or "（未填 TODO）")[:160]))

    # 3) 机器自动核对汇总
    machine = [
        ("引用文件不存在(ref_errors)", rep["ref_errors"]),
        ("引用行号异常(空行/注释/字符串)", [i["ref"] for i in rep.get("ref_line_issues") or []]),
        ("零引用断言(zero_ref_asserts)", rep.get("zero_ref_asserts") or []),
        ("地图引用失效(smap_ref_errors)", rep.get("smap_ref_errors") or []),
        ("地图重词无痕迹(smap_suspect_terms)", rep.get("smap_suspect_terms") or []),
        ("构建声明缺失(build_decl_missing)",
         ["%s: %s" % (b["name"], "、".join(b["missing"][:3]))
          for b in inv_data.get("build_issues") or []]),
    ]

    parts = ["=== 独立评估工作底稿（机器预核；语义判断仍须评估者完成）===",
             "",
             "## 1. 抽样引用对照（抽 %d / 共 %d）" % (len(refs), total_refs),
             ""]
    for r in refs:
        parts.append("- `%s`（%s）行性质: %s" % (r["ref"], r["doc"], r["kind"]))
        parts.append("    文档：%s" % (r["doc_line"] or "（空）"))
        parts.append("    源码：%s" % (r["src"] or "（空）"))
    parts += ["", "## 2. 抽样卡片核对（抽 %d / 共 %d；签名不一致 %d）"
              % (len(card_rows), total_cards, sig_mismatch), ""]
    parts.extend(card_rows)
    parts += ["", "## 3. 机器自动核对汇总", ""]
    for label, items in machine:
        parts.append("- %s：%s" % (label, ("共 %d 项" % len(items)) if items else "0 ✓"))
        for it in items[:5]:
            parts.append("    - %s" % it)
    parts += ["", "## 4. 抽样统计表（评估者填）", "",
              "| 项 | 抽样 | 一致 | 不一致 |", "|:---|:---|:---|:---|",
              "| 引用 | %d |  |  |" % len(refs),
              "| 卡片 | %d |  |  |" % len(card_rows),
              "| 叙事页 | 3 |  |  |", ""]
    stats = {"refs_total": total_refs, "refs_sampled": len(refs),
             "cards_total": total_cards, "cards_sampled": len(card_rows),
             "sig_mismatch": sig_mismatch}
    return "\n".join(parts), stats


def cmd_audit(inv_data, root, out, n_refs=25, n_cards=15, write_out=False):
    """生成/落盘评估工作底稿（--write 落盘 <out>/.audit-sheet.txt）。"""
    if inv_data is None:
        raise SystemExit("audit：缺少 inventory.json（先运行 inventory）")
    text, stats = audit_sheet(inv_data, root, out, n_refs, n_cards)
    if write_out:
        path = os.path.join(out, ".audit-sheet.txt")
        write(path, text)
        print("底稿 -> %s（%d 引用 / %d 卡片 / 签名不一致 %d）"
              % (os.path.relpath(path, root), stats["refs_sampled"],
                 stats["cards_sampled"], stats["sig_mismatch"]))
    else:
        print(text)
        print("统计：%s" % stats)
    print("提醒：底稿只替代机械核对；语义正确性（叙述与源码是否一致）仍须"
          "另一 agent/人按 references/quality-review.md 判定。")
    return 0


# ---------------- CLI ----------------

def _force_utf8_output():
    """Windows 控制台/管道默认 GBK：输出非 GBK 字符（✓ 等）会触发 UnicodeEncodeError。
    统一把 stdout/stderr 切到 UTF-8（Python 3.7+）；异常时静默降级，不影响主流程。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def main(argv=None):
    _force_utf8_output()
    import argparse
    ap = argparse.ArgumentParser(prog="dev_docs.py", description="dev-docs 从代码库逆向生成文档")
    ap.add_argument("command", choices=["inventory", "plan", "extract", "brief", "promote",
                                        "check", "report", "register", "fixrefs", "doctor",
                                        "audit"],
                    help="inventory 盘点 | plan 页面树 | extract 生成 draft | brief 页级填写工单 | "
                         "promote draft 转正 | check 对账/漂移 | report 索引+基线 | "
                         "register 人工登记 | fixrefs 修正引用行号偏移 | doctor 环境与能力自检 | "
                         "audit 独立评估工作底稿")
    ap.add_argument("--dir", default=".", help="目标项目目录（默认当前目录；git 仓库内自动取仓库根）")
    ap.add_argument("--out", default="dev-docs", help="输出子目录名（docs/<out>，默认 dev-docs）")
    ap.add_argument("--layer", choices=["index", "architecture", "usage", "reference", "data", "all"],
                    help="extract 的层（all=除 data 外全部页；无页面树时兼容 architecture|reference|data）")
    ap.add_argument("--module", help="extract 限定单个 MOD-id")
    ap.add_argument("--page", help="extract/brief 限定的页面 slug（如 reference/demo-providers）")
    ap.add_argument("--write", action="store_true",
                    help="plan：落盘 .devdocs-plan.json | fixrefs：落盘行号修正 | "
                         "audit：落盘 .audit-sheet.txt（均默认 dry-run/打印）")
    ap.add_argument("--force", action="store_true", help="plan：忽略已有 plan（放弃人工编辑保护）")
    ap.add_argument("--json", action="store_true", help="brief：输出 JSON（供 Agent 消费）")
    ap.add_argument("--strict", action="store_true", help="check：把 warn（AI-FILL 残留/文件未归属）升级为 ERROR")
    ap.add_argument("--exclude", action="append", default=[], help="额外排除模式（可多次）")
    ap.add_argument("--drift", action="store_true", help="check 时重扫与基线对比漂移")
    ap.add_argument("--file", help="promote 的 draft 文件路径（相对 docs/<out>/ 或绝对路径）")
    ap.add_argument("--project-type", choices=["auto", "catkin", "generic"], default="auto",
                    help="项目类型（auto=自动嗅探 package.xml；catkin 追加 build/devel/install/log 排除）")
    ap.add_argument("--kind", help="register 的登记类型 symbol|endpoint|interface")
    ap.add_argument("--name", help="register 的限定名（如 RosNode::spin 或 /cmd_vel）")
    ap.add_argument("--src", help="register 的源文件（相对项目根；必须真实存在）")
    ap.add_argument("--line", type=int, default=0, help="register 的源码行号（用于核验）")
    ap.add_argument("--signature", help="register 的签名/定义原文（可选）")
    ap.add_argument("--note", help="register 的备注（来源/抽审结论，可选）")
    ap.add_argument("--sample-refs", type=int, default=25,
                    help="audit 的引用抽样上限（默认 25）")
    ap.add_argument("--sample-cards", type=int, default=15,
                    help="audit 的卡片抽样上限（默认 15）")
    a = ap.parse_args(argv)

    root = resolve_root(a.dir)
    out = outdir(root, a.out)

    if a.command == "inventory":
        cmd_inventory(root, out, a.exclude, project_type=a.project_type)
    elif a.command == "plan":
        return cmd_plan(root, out, write=a.write, force=a.force, extra_exclude=a.exclude)
    elif a.command == "extract":
        if not (a.layer or a.page):
            raise SystemExit("extract 需要 --layer index|architecture|usage|reference|data|all 或 --page <slug>")
        # 复用已有 inventory.json（含当时 exclude），避免再次扫描产出漂移快照
        data = ensure_inventory(out, root, a.exclude, project_type=a.project_type)
        extract_layer(data, root, out, a.layer or "all", a.module, a.page)
    elif a.command == "brief":
        if not a.page:
            raise SystemExit("brief 需要 --page <slug>（先用 plan 查看可用页）")
        data = ensure_inventory(out, root, a.exclude, project_type=a.project_type)
        return cmd_brief(data, root, out, a.page, as_json=a.json)
    elif a.command == "promote":
        if not a.file:
            raise SystemExit("promote 需要 --file <draft 路径>（相对 docs/<out>/ 或绝对路径）")
        cmd_promote(out, a.file, load_inventory(out))
    elif a.command == "register":
        if not (a.kind and a.name and a.src):
            raise SystemExit("register 需要 --kind symbol|endpoint|interface "
                             "--name <限定名> --src <相对项目根文件> [--line N] [--signature S] [--note N]")
        return cmd_register(root, out, a.kind, a.name, a.src,
                            line=a.line, signature=a.signature, note=a.note)
    elif a.command == "check":
        if a.drift:
            data = cmd_inventory(root, out, a.exclude, quiet=True,
                                 project_type=a.project_type)
        else:
            data = load_inventory(out)
        return cmd_check(data, out, a.drift, root, strict=a.strict)
    elif a.command == "report":
        data = load_inventory(out)
        cmd_report(data, root, out)
    elif a.command == "fixrefs":
        return cmd_fixrefs(root, out, load_inventory(out), write=a.write)
    elif a.command == "doctor":
        return cmd_doctor(root, out)
    elif a.command == "audit":
        data = ensure_inventory(out, root, a.exclude, project_type=a.project_type)
        return cmd_audit(data, root, out, a.sample_refs, a.sample_cards, write_out=a.write)
    return 0


if __name__ == "__main__":
    sys.exit(main())
