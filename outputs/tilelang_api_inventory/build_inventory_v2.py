from __future__ import annotations

import ast
import html
import json
import re
import zipfile
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path


ROOTS = {"A3": Path("/tmp/tilelang_api_main_v2"), "A5": Path("/tmp/tilelang_api_a5_v2")}
OUT_DIR = Path("/Users/zuoming/Projects/Compiler/tilelang-mlir-ascend/outputs/tilelang_api_inventory")
OUT_XLSX = OUT_DIR / "tilelang_api_inventory_A3_main_A5_a5_branch_v2.xlsx"
OUT_JSON = OUT_DIR / "tilelang_api_inventory_A3_A5_raw_v2.json"

SCAN_PREFIXES = ("tilelang/", "src/", "examples/", "testing/", "unittest/", "benchmark/")
SCAN_SUFFIXES = (".py", ".cc", ".h", ".hh", ".hpp", ".mlir")
EXCLUDE = {".git", "build", "dist", "node_modules", "__pycache__", ".pytest_cache"}

T_RE = re.compile(r"(?<![A-Za-z0-9_])T\.([A-Za-z_]\w*)")
TL_RE = re.compile(r"tl\.npuir_([A-Za-z_]\w*)")
DEF_RE = re.compile(r"^(\s*)def\s+([A-Za-z_]\w*)\s*\(")
CLASS_RE = re.compile(r"^(\s*)class\s+([A-Za-z_]\w*)\b")
ASSIGN_DEF_RE = re.compile(
    r"^([A-Za-z_]\w*)\s*=\s*(?:_op_wrapper|_tvm_op|tir\.|T\.|Layout|BufferProxy|TensorProxy|BaseTensorProxy)"
)
SIMPLE_ALIAS_RE = re.compile(r"^([A-Za-z_]\w*)\s*=\s*([A-Za-z_]\w*)\s*(?:#.*)?$")

EXTERNAL = {
    "serial",
    "parallel",
    "grid",
    "vectorized",
    "unroll",
    "thread_binding",
    "block",
    "block_attr",
    "attr",
    "assume",
    "evaluate",
    "const",
    "cast",
    "reinterpret",
    "call_extern",
    "access_ptr",
    "address_of",
    "floordiv",
    "floormod",
    "ceildiv",
    "min",
    "max",
    "exp",
    "exp2",
    "log",
    "log2",
    "sin",
    "cos",
    "sqrt",
    "rsqrt",
    "if_then_else",
    "index_to_coordinates",
    "shift_left",
    "Ramp",
    "Broadcast",
    "Select",
    "bool",
    "int8",
    "int16",
    "int32",
    "int64",
    "uint8",
    "uint16",
    "uint32",
    "uint64",
    "float",
    "float16",
    "float32",
    "float64",
    "float8_e4m3fn",
    "float8_e4m3fnuz",
    "float8_e5m2",
    "float8_e5m2fnuz",
    "bfloat16",
    "half",
    "handle",
    "ptr",
    "Ref",
    "Buffer",
    "Tensor",
    "var",
    "prim_func",
    "ir_module",
    "match_buffer",
    "alloc_buffer",
    "alloc_fragment",
    "alloc_local",
    "alloc_shared",
    "reads",
    "writes",
    "where",
    "axis",
    "init",
    "func_attr",
    "env_thread",
    "launch_thread",
    "LetStmt",
    "Let",
    "BufferLoad",
    "BufferStore",
    "Layout",
    "Persistent",
    "contiguous",
    "replace",
    "rng_init",
    "rng_rand",
    "vscale",
}

SPECIAL_INTRIN_PUBLIC = {
    "dot": ["gemm", "npuir_dot"],
    "brc": ["vbrc", "npuir_brc"],
    "cast": ["vcast", "npuir_cast"],
    "cumsum": ["cumsum", "npuir_cumsum"],
    "bitcast": ["vbitcast", "npuir_bitcast"],
    "vcos": ["vcos", "npuir_vcos"],
    "vsin": ["vsin", "npuir_vsin"],
    "verf": ["verf", "npuir_verf"],
    "vtanh": ["vtanh", "npuir_vtanh"],
    "pipe_barrier": ["pipe_barrier", "npuir_pipe_barrier"],
    "set_flag": ["set_flag", "npuir_set_flag"],
    "wait_flag": ["wait_flag", "npuir_wait_flag"],
    "sync_block": ["block_barrier", "subblock_barrier", "npuir_sync_block"],
    "sync_block_set": ["sync_block_set", "npuir_sync_block_set"],
    "sync_block_wait": ["sync_block_wait", "npuir_sync_block_wait"],
    "debug_print_var": ["print", "npuir_debug_print_var"],
    "debug_print_buffer_value": ["print", "npuir_debug_print_buffer_value"],
}

LEGACY_NOTES = {
    "vcumsum": "文档文件名出现过，但当前签名/测试多使用 T.cumsum。",
    "vinterleave": "未见 T.vinterleave 调用；当前公开别名通常是 T.interleave。",
    "vdeinterleave": "未见 T.vdeinterleave 调用；当前公开别名通常是 T.deinterleave。",
    "vtranspose": "未见 T.vtranspose 调用；当前公开别名通常是 T.transpose。",
    "vpad": "未见 T.vpad 调用；当前公开别名通常是 T.pad。",
    "vgather": "未见 T.vgather 调用；当前公开别名通常是 T.gather。",
}

EXPERT_TOKENS = (
    "T.Scope(",
    "T.alloc_L1",
    "T.alloc_L0",
    "T.alloc_ub",
    "T.load_nd2nz",
    "T.store_fixpipe",
    "T.npuir_load_nd2nz",
    "T.npuir_store_fixpipe",
    "T.set_flag",
    "T.wait_flag",
    "T.pipe_barrier",
    "T.sync_block_set",
    "T.sync_block_wait",
    "T.block_barrier",
    "T.subblock_barrier",
)


def rel(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="replace")


def iter_files(root: Path):
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if set(path.relative_to(root).parts) & EXCLUDE:
            continue
        path_rel = rel(root, path)
        if path_rel.startswith(SCAN_PREFIXES) and path_rel.endswith(SCAN_SUFFIXES):
            yield path


def kind_of(path_rel: str) -> str:
    if path_rel.startswith(("testing/", "unittest/")):
        return "test"
    if path_rel.startswith(("examples/", "benchmark/")):
        return "example"
    if path_rel.startswith("tilelang/language/"):
        return "python-def"
    if path_rel.startswith("src/target/") and "codegen_npuir_api" in path_rel:
        return "codegen-expert"
    if path_rel.startswith("src/target/") and "codegen_npuir_dev" in path_rel:
        return "codegen-developer"
    if path_rel.startswith("src/target/") and "codegen_npuir.cc" in path_rel:
        return "codegen-legacy"
    if path_rel.startswith("src/"):
        return "src"
    if path_rel.startswith("tilelang/"):
        return "tilelang"
    return "other"


def api(name: str) -> str:
    if name in {"cid", "subid"} or name.startswith("T."):
        return name
    return "T." + name


def aname(api_name: str) -> str:
    return api_name[2:] if api_name.startswith("T.") else api_name


def doc_modes(line: str) -> set[str]:
    lower = line.lower()
    modes = set()
    if "developer" in lower or "dev op" in lower:
        modes.add("Developer")
    if "expert" in lower or "exp op" in lower:
        modes.add("Expert")
    return modes


def file_modes(path_rel: str, text: str) -> set[str]:
    lower_path = path_rel.lower()
    lower_text = text[:3000].lower()
    modes = set()
    explicit_modes = set()
    if re.search(r"pytest\.mark\.mode\([\"']developer[\"']\)", lower_text):
        explicit_modes.add("Developer")
    if re.search(r"pytest\.mark\.mode\([\"']expert[\"']\)", lower_text):
        explicit_modes.add("Expert")
    if len(explicit_modes) == 1:
        return explicit_modes
    if re.search(r"(^|[/_.-])dev([_.-]|$)", lower_path) or (
        "tilelang_ascend_mode" in lower_text and "developer" in lower_text
    ):
        modes.add("Developer")
    if re.search(r"(^|[/_.-])exp([_.-]|$)", lower_path):
        modes.add("Expert")
    return modes


def line_modes(path_rel: str, text: str, line: str, kind: str, *, tdot: bool = False, context: str = "") -> set[str]:
    if tdot and kind.startswith("codegen"):
        return set()
    modes = file_modes(path_rel, text)
    if not modes:
        if re.search(r"def\s+[A-Za-z_]\w*(?:_dev|Dev)\w*\s*\(", context):
            modes.add("Developer")
        if re.search(r"def\s+[A-Za-z_]\w*(?:_exp|Exp)\w*\s*\(", context):
            modes.add("Expert")
        if any(token in line for token in EXPERT_TOKENS):
            modes.add("Expert")
    if kind == "codegen-expert":
        modes.add("Expert")
    if kind == "codegen-developer":
        modes.add("Developer")
    modes |= doc_modes(line)
    return modes


def sig_at(lines: list[str], idx: int) -> str:
    signature = lines[idx].strip()
    balance = signature.count("(") - signature.count(")")
    j = idx + 1
    while balance > 0 and j < len(lines) and j < idx + 14:
        part = lines[j].strip()
        if part and not part.startswith("#"):
            signature += " " + part
            balance += part.count("(") - part.count(")")
        j += 1
    return re.sub(r"\s+", " ", signature).rstrip(":")


def empty_branch():
    return {
        "defs": set(),
        "uses": set(),
        "sigs": set(),
        "notes": set(),
        "evidence": set(),
        "mode_evidence": defaultdict(set),
        "mode_paths": defaultdict(set),
        "definition_paths": set(),
        "use_paths": set(),
        "comment_only_uses": set(),
        "internal": set(),
    }


def empty_record():
    return {"branches": {"A3": empty_branch(), "A5": empty_branch()}}


apis = defaultdict(empty_record)


def add(
    branch,
    api_name,
    loc,
    evidence,
    *,
    definition=False,
    use=False,
    signature=None,
    note=None,
    modes=None,
    internal=None,
    comment=False,
):
    data = apis[api_name]["branches"][branch]
    data["evidence"].add(evidence)
    if definition:
        data["defs"].add(loc)
        data["definition_paths"].add(loc)
    if use:
        data["uses"].add(loc)
        data["use_paths"].add(loc)
        if comment:
            data["comment_only_uses"].add(loc)
    if signature:
        data["sigs"].add(signature)
    if note:
        data["notes"].add(note)
    if internal:
        data["internal"].add(internal)
    for mode in modes or []:
        data["mode_evidence"][mode].add(evidence)
        data["mode_paths"][mode].add(loc)


def truncate(items, max_items=14, max_chars=2600):
    values = sorted(items)
    if not values:
        return ""
    output = []
    total = 0
    for value in values:
        if len(output) >= max_items or total + len(value) + 2 > max_chars:
            break
        output.append(value)
        total += len(value) + 2
    return "；".join(output) + (f"；...（共 {len(values)} 处）" if len(output) < len(values) else "")


def scan_branch(branch: str, root: Path):
    init_path = root / "tilelang/language/__init__.py"
    init_text = read(init_path)
    has_tvm_star = "from tvm.script.parser.tir import *" in init_text
    source_by_public = {}
    publics_by_source = defaultdict(set)
    pydefs = defaultdict(list)
    alias_assignments = []

    tree = ast.parse(init_text)
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        for alias in node.names:
            if alias.name == "*":
                continue
            public = alias.asname or alias.name
            source = alias.name
            source_by_public[public] = source
            publics_by_source[source].add(public)
            loc = f"tilelang/language/__init__.py:{getattr(alias, 'lineno', getattr(node, 'lineno', 1))}"
            add(
                branch,
                api(public),
                loc,
                "python-export",
                definition=True,
                signature="export " + source + (f" as {public}" if public != source else ""),
                note="由 tilelang.language.__init__ 导出到 T 命名空间",
            )

    for path in (root / "tilelang/language").rglob("*.py"):
        path_rel = rel(root, path)
        lines = read(path).splitlines()
        for i, line in enumerate(lines):
            match = DEF_RE.match(line)
            if match and not match.group(2).startswith("_"):
                pydefs[match.group(2)].append((f"{path_rel}:{i + 1}", sig_at(lines, i)))
            match = CLASS_RE.match(line)
            if match and not match.group(2).startswith("_"):
                pydefs[match.group(2)].append((f"{path_rel}:{i + 1}", sig_at(lines, i)))
            stripped = line.strip()
            match = ASSIGN_DEF_RE.match(stripped)
            if match:
                pydefs[match.group(1)].append((f"{path_rel}:{i + 1}", stripped))
            match = SIMPLE_ALIAS_RE.match(stripped)
            if match:
                alias_assignments.append((match.group(1), match.group(2), f"{path_rel}:{i + 1}", stripped))

    for alias, target, loc, signature in alias_assignments:
        if target in pydefs and not alias.startswith("_"):
            pydefs[alias].append((loc, signature))

    for public, source in source_by_public.items():
        for loc, signature in pydefs.get(source, []):
            add(branch, api(public), loc, "python-def", definition=True, signature=signature)
        if public != source:
            for loc, signature in pydefs.get(public, []):
                add(branch, api(public), loc, "python-def", definition=True, signature=signature)
    for source, publics in publics_by_source.items():
        for loc, signature in pydefs.get(source, []):
            for public in publics | {source}:
                add(branch, api(public), loc, "python-def", definition=True, signature=signature)

    docs_root = root / "docs/Tilelang.language"
    for path in docs_root.rglob("T.*.md"):
        path_rel = rel(root, path)
        fname = path.name[2:-3]
        lines = read(path).splitlines()
        add(branch, api(fname), f"{path_rel}:1", "doc-file", definition=True, note="docs/Tilelang.language API 文档文件")
        sig_names = []
        for i, line in enumerate(lines[:70], 1):
            if re.match(r"^##\s*2", line.strip()):
                break
            stripped = line.strip().strip("`")
            if "T." not in stripped:
                continue
            for match in T_RE.finditer(stripped):
                name = match.group(1)
                if re.search(rf"T\.{re.escape(name)}\s*\(", stripped):
                    sig_names.append(name)
                    add(
                        branch,
                        api(name),
                        f"{path_rel}:{i}",
                        "doc-signature",
                        definition=True,
                        signature=stripped,
                        modes=doc_modes(stripped),
                        note="docs/Tilelang.language OP 概述签名",
                    )
        if sig_names and fname not in sig_names:
            add(
                branch,
                api(fname),
                f"{path_rel}:1",
                "doc-file",
                definition=True,
                note=f"文档文件名 T.{fname} 与概述签名 {', '.join('T.' + x for x in sorted(set(sig_names)))} 不一致",
            )

    observed_t = set()
    for path in iter_files(root):
        path_rel = rel(root, path)
        kind = kind_of(path_rel)
        text = read(path)
        file_mode_set = file_modes(path_rel, text)
        lines = text.splitlines()
        for i, line in enumerate(lines, 1):
            loc = f"{path_rel}:{i}"
            comment = line.strip().startswith(("#", "//"))
            context = "\n".join(lines[max(0, i - 21) : min(len(lines), i + 5)])
            for match in T_RE.finditer(line):
                name = match.group(1)
                observed_t.add(name)
                if comment:
                    evidence = "use-comment"
                elif kind == "test":
                    evidence = "use-test"
                elif kind == "example":
                    evidence = "use-example"
                elif kind == "src":
                    evidence = "use-src"
                else:
                    evidence = "use"
                add(
                    branch,
                    api(name),
                    loc,
                    evidence,
                    use=True,
                    modes=line_modes(path_rel, text, line, kind, tdot=True, context=context),
                    note="注释中的出现" if comment else None,
                    comment=comment,
                )
            for match in TL_RE.finditer(line):
                intr = match.group(1)
                wrapper = "npuir_" + intr
                if kind == "codegen-expert":
                    evidence, modes, definition = "codegen-expert", {"Expert"}, True
                elif kind == "codegen-developer":
                    evidence, modes, definition = "codegen-developer", {"Developer"}, True
                elif kind == "codegen-legacy":
                    evidence, modes, definition = "codegen-legacy", set(), True
                else:
                    evidence, modes, definition = "internal-intrinsic-ref", file_mode_set, False
                internal = "tl.npuir_" + intr
                publics = set(publics_by_source.get(wrapper, set())) | {wrapper} | set(SPECIAL_INTRIN_PUBLIC.get(intr, []))
                for public in publics:
                    add(
                        branch,
                        api(public),
                        loc,
                        evidence,
                        definition=definition,
                        use=not definition,
                        modes=modes,
                        internal=internal,
                        note=f"映射/引用内部 {internal}",
                    )
            if re.search(r"\bcid\b", line):
                add(branch, "cid", loc, "kernel-binding", use=True, modes=file_mode_set, note="Kernel launch block id 变量名出现")
            if re.search(r"\bsubid\b", line):
                add(branch, "subid", loc, "kernel-binding", use=True, modes=file_mode_set, note="Kernel launch sub-block id 变量名出现")

    for name in observed_t:
        for loc, signature in pydefs.get(name, []):
            add(branch, api(name), loc, "python-def", definition=True, signature=signature)
        source = source_by_public.get(name)
        for loc, signature in pydefs.get(source, []):
            add(branch, api(name), loc, "python-def", definition=True, signature=signature)
        if name in EXTERNAL and has_tvm_star:
            add(
                branch,
                api(name),
                "tilelang/language/__init__.py:11",
                "external-import",
                definition=True,
                signature="from tvm.script.parser.tir import *",
                note="通过 TVM TIR script star import 暴露；本仓库内主要是转导/使用",
            )

    for name, definitions in pydefs.items():
        if name in source_by_public or name in publics_by_source or name in observed_t:
            for loc, signature in definitions:
                add(branch, api(name), loc, "python-def", definition=True, signature=signature)

    if api("cumsum") in apis:
        add(branch, api("cumsum"), "docs/Tilelang.language/排序操作/T.vcumsum.md:1", "doc-note", definition=True, note=LEGACY_NOTES["vcumsum"])


def has_any(data, keys) -> bool:
    return bool(data["evidence"] & set(keys))


def branch_overall(data) -> str:
    has_codegen = has_any(data, ["codegen-expert", "codegen-developer"])
    has_legacy_codegen = "codegen-legacy" in data["evidence"]
    has_def = has_any(data, ["python-def", "python-export"])
    has_doc = has_any(data, ["doc-signature", "doc-file"])
    has_test_example = has_any(data, ["use-test", "use-example"])
    has_real_use = bool(data["uses"] - data["comment_only_uses"])
    has_external = "external-import" in data["evidence"]
    if has_codegen:
        return "明确支持(codegen)"
    if has_def and has_doc and has_test_example:
        return "明确支持(文档+定义+用例)"
    if has_def and has_test_example:
        return "明确支持(定义+用例)"
    if has_def and has_doc:
        return "支持(文档+定义)"
    if has_def:
        return "有公开定义"
    if has_doc:
        return "有API文档"
    if has_external:
        return "TVM/TIR转导"
    if has_legacy_codegen:
        return "仅旧codegen映射"
    if has_real_use:
        return "仅用例/引用"
    if data["uses"]:
        return "仅注释引用"
    return "无证据"


def mode_status(data, mode: str) -> str:
    evidence = data["mode_evidence"].get(mode, set())
    has_def = has_any(data, ["python-def", "python-export"])
    has_doc = has_any(data, ["doc-signature", "doc-file"])
    has_mode_doc = "doc-signature" in evidence
    has_mode_use = bool(evidence & {"use-test", "use-example", "use", "use-src", "kernel-binding"})
    if (mode == "Expert" and "codegen-expert" in evidence) or (mode == "Developer" and "codegen-developer" in evidence):
        return "支持(codegen)"
    if has_mode_doc and has_def and has_mode_use:
        return "支持(文档+定义+用例)"
    if has_mode_doc and has_def:
        return "支持(文档+定义)"
    if has_def and has_mode_use:
        return "支持(定义+用例)"
    if has_doc and has_mode_use:
        return "支持(文档+用例)"
    if has_mode_use:
        return "模式用例出现"
    overall = branch_overall(data)
    if overall.startswith(("明确支持", "支持")) or overall in {"有公开定义", "有API文档", "TVM/TIR转导"}:
        return "未分模式(" + overall + ")"
    if overall == "仅旧codegen映射":
        return "未分模式(仅旧codegen映射)"
    if overall in {"仅用例/引用", "仅注释引用"}:
        return overall
    return "无"


def group_of(api_name):
    name = aname(api_name)
    if api_name in {"cid", "subid"}:
        return "Kernel变量"
    if name.startswith("npuir_"):
        return "Legacy/Internal NPUIR"
    if name.startswith("v") and name not in {"var", "vectorized", "vscale", "view"}:
        return "v-prefix/向量"
    if name in {
        "gemm",
        "load_nd2nz",
        "store_fixpipe",
        "store_nz2nd",
        "alloc_L1",
        "alloc_L0A",
        "alloc_L0B",
        "alloc_L0C",
        "alloc_ub",
        "Scope",
        "set_flag",
        "wait_flag",
        "pipe_barrier",
        "sync_block_set",
        "sync_block_wait",
        "block_barrier",
        "subblock_barrier",
        "rs",
    }:
        return "NPUIR Expert/同步/内存"
    if name in {
        "alloc_shared",
        "copy",
        "clear",
        "fill",
        "atomic_add",
        "atomic_addx4",
        "reduce",
        "reduce_max",
        "reduce_min",
        "reduce_sum",
        "cumsum",
        "reshape",
        "view",
        "arange",
        "concat",
        "pad",
        "flip",
        "gather",
        "interleave",
        "deinterleave",
        "transpose",
        "print",
    }:
        return "TileLang公开API"
    if name in EXTERNAL:
        return "TVM/TIR通用"
    return "其他出现"


def explain(api_name, a3, a5):
    name = aname(api_name)
    s3 = branch_overall(a3)
    s5 = branch_overall(a5)
    if s3 != "无证据" and s5 != "无证据":
        parts = [f"A3={s3}；A5={s5}"]
    elif s5 != "无证据":
        parts = [f"仅 A5：{s5}"]
    elif s3 != "无证据":
        parts = [f"仅 A3：{s3}"]
    else:
        parts = ["未见证据"]
    codegen = []
    for label, data in (("A3", a3), ("A5", a5)):
        modes = []
        if "codegen-expert" in data["evidence"]:
            modes.append("Expert")
        if "codegen-developer" in data["evidence"]:
            modes.append("Developer")
        if modes:
            codegen.append(label + ":" + "/".join(modes))
    if codegen:
        parts.append("backend codegen=" + "，".join(codegen))
    if name.startswith("npuir_"):
        parts.append("legacy/internal NPUIR 名称；建议新示例优先 v-prefix 或公开别名")
    if name in LEGACY_NOTES:
        parts.append(LEGACY_NOTES[name])
    mismatch = [note for note in sorted(a3["notes"] | a5["notes"]) if "不一致" in note]
    if mismatch:
        parts.append(mismatch[0])
    return "；".join(dict.fromkeys(parts))


def build_rows():
    for branch, root in ROOTS.items():
        scan_branch(branch, root)

    rows = []
    for name in sorted(apis.keys(), key=lambda item: (group_of(item), item.lower())):
        a3 = apis[name]["branches"]["A3"]
        a5 = apis[name]["branches"]["A5"]
        signatures = sorted(a3["sigs"] | a5["sigs"], key=lambda s: (s.startswith("export "), len(s), s))
        rows.append(
            {
                "API": name,
                "分组": group_of(name),
                "A3 总体": branch_overall(a3),
                "A3 Expert": mode_status(a3, "Expert"),
                "A3 Developer": mode_status(a3, "Developer"),
                "A5 总体": branch_overall(a5),
                "A5 Expert": mode_status(a5, "Expert"),
                "A5 Developer": mode_status(a5, "Developer"),
                "定义/签名摘要": truncate(signatures, 6, 1200),
                "A3 定义/映射路径": truncate(a3["definition_paths"], 14, 2800),
                "A5 定义/映射路径": truncate(a5["definition_paths"], 14, 2800),
                "A3 使用路径": truncate(a3["use_paths"], 14, 2800),
                "A5 使用路径": truncate(a5["use_paths"], 14, 2800),
                "A3 Expert依据": truncate(a3["mode_paths"].get("Expert", set()), 8, 1600),
                "A3 Developer依据": truncate(a3["mode_paths"].get("Developer", set()), 8, 1600),
                "A5 Expert依据": truncate(a5["mode_paths"].get("Expert", set()), 8, 1600),
                "A5 Developer依据": truncate(a5["mode_paths"].get("Developer", set()), 8, 1600),
                "简单说明": explain(name, a3, a5),
                "证据类型": ",".join(sorted(a3["evidence"] | a5["evidence"])),
            }
        )
    return rows


def stats_and_notes(rows):
    def active(status):
        return status not in {"无", "无证据"}

    stats = [
        ["生成时间", datetime.now().strftime("%Y-%m-%d %H:%M:%S")],
        ["A3 分支", "main (本地 git archive)"],
        ["A5 分支", "a5-branch (本地 git archive)"],
        ["API 行数", len(rows)],
    ]
    for col_name in ["A3 总体", "A3 Expert", "A3 Developer", "A5 总体", "A5 Expert", "A5 Developer"]:
        stats.append([col_name + " 非无", sum(1 for row in rows if active(row[col_name]))])
    for group, count in Counter(row["分组"] for row in rows).most_common():
        stats.append(["分组: " + group, count])
    a5_only = [row["API"] for row in rows if row["A3 总体"] == "无证据" and row["A5 总体"] != "无证据"]
    a3_only = [row["API"] for row in rows if row["A5 总体"] == "无证据" and row["A3 总体"] != "无证据"]
    stats.append(["仅 A5 有证据 API", ", ".join(a5_only[:100]) + (f" ... 共{len(a5_only)}" if len(a5_only) > 100 else f" (共{len(a5_only)})")])
    stats.append(["仅 A3 有证据 API", ", ".join(a3_only[:100]) + (f" ... 共{len(a3_only)}" if len(a3_only) > 100 else f" (共{len(a3_only)})")])

    notes = [
        ["说明项", "内容"],
        ["本版变化", "v2 不再把没有 codegen 的 API 简单标成“通用/出现”。Python export/def、docs OP 概述签名、模式化测试/示例都参与支持判定。"],
        ["明确支持(codegen)", "在 codegen_npuir_api.cc 或 codegen_npuir_dev.cc 看到 tl.npuir_* backend 映射。"],
        ["明确支持(文档+定义+用例)", "同一分支有 API 文档、Python 公开定义/导出，并且测试或示例中有实际调用。"],
        ["支持(文档+定义)", "有 docs/Tilelang.language API 文档和 Python 公开定义/导出，但没找到对应测试/示例或无法归模式。"],
        ["支持(定义+用例)", "有 Python 公开定义/导出，并且有测试/示例实际调用；不要求 codegen 直接出现。"],
        ["未分模式(...)", "分支总体证据明确，但没有足够证据把它归到 Expert 或 Developer 某一侧；这不是否定支持。"],
        ["仅旧codegen映射", "只在已标为 deprecated 的 src/target/codegen_npuir.cc 看到映射，未在 api/dev backend 中确认。"],
        ["模式判定", "Expert/Developer 优先来自 codegen_npuir_api/dev；其次来自文档 [Expert/Developer]、文件名 _exp/_dev、TILELANG_ASCEND_MODE、以及 Expert 专属语法如 Scope/alloc_L1/load_nd2nz/sync。"],
        ["扫描范围", "tilelang/, src/, docs/, examples/, testing/, unittest/, benchmark/。docs 只使用 OP 概述签名，不把示例代码整页传播为模式依据。"],
        ["限制", "没有逐个验证运行正确性、参数边界、硬件限制和 pass 依赖；结论是基于仓库证据的支持度整理。"],
    ]
    return stats, notes


NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
RNS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def col(index):
    name = ""
    while index:
        index, rem = divmod(index - 1, 26)
        name = chr(65 + rem) + name
    return name


def sheet_xml(data, widths, freeze=True, autofilter=True):
    max_cols = max(len(row) for row in data)
    max_rows = len(data)
    parts = [f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="{NS}" xmlns:r="{RNS}">']
    if freeze:
        parts.append('<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/><selection pane="bottomLeft" activeCell="A2" sqref="A2"/></sheetView></sheetViews>')
    parts.append("<cols>")
    for i in range(1, max_cols + 1):
        width = widths[i - 1] if i - 1 < len(widths) else 18
        parts.append(f'<col min="{i}" max="{i}" width="{width}" customWidth="1"/>')
    parts.append("</cols><sheetData>")
    for row_idx, row in enumerate(data, 1):
        max_len = max((len(str(value)) for value in row), default=0)
        height = 28 if row_idx == 1 else 62 if max_len > 140 else 22
        parts.append(f'<row r="{row_idx}" ht="{height}" customHeight="1">')
        for col_idx in range(1, max_cols + 1):
            value = row[col_idx - 1] if col_idx - 1 < len(row) else ""
            style = 1 if row_idx == 1 else 2
            ref = f"{col(col_idx)}{row_idx}"
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                parts.append(f'<c r="{ref}" s="{style}"><v>{value}</v></c>')
            elif value is None:
                parts.append(f'<c r="{ref}" s="{style}"/>')
            else:
                parts.append(
                    f'<c r="{ref}" t="inlineStr" s="{style}"><is><t xml:space="preserve">{html.escape(str(value), quote=True)}</t></is></c>'
                )
        parts.append("</row>")
    parts.append("</sheetData>")
    if autofilter:
        parts.append(f'<autoFilter ref="A1:{col(max_cols)}{max_rows}"/>')
    parts.append("</worksheet>")
    return "".join(parts)


def write_xlsx(rows, stats, notes):
    headers = [
        "API",
        "分组",
        "A3 总体",
        "A3 Expert",
        "A3 Developer",
        "A5 总体",
        "A5 Expert",
        "A5 Developer",
        "定义/签名摘要",
        "A3 定义/映射路径",
        "A5 定义/映射路径",
        "A3 使用路径",
        "A5 使用路径",
        "A3 Expert依据",
        "A3 Developer依据",
        "A5 Expert依据",
        "A5 Developer依据",
        "简单说明",
        "证据类型",
    ]
    api_sheet = [headers] + [[row[header] for header in headers] for row in rows]
    stats_sheet = [["指标", "值"]] + stats
    notes_sheet = notes

    styles = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?><styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><fonts count="2"><font><sz val="11"/><name val="Aptos"/></font><font><b/><color rgb="FFFFFFFF"/><sz val="11"/><name val="Aptos"/></font></fonts><fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF1F4E78"/><bgColor indexed="64"/></patternFill></fill></fills><borders count="2"><border><left/><right/><top/><bottom/><diagonal/></border><border><left style="thin"><color rgb="FFD9E2EC"/></left><right style="thin"><color rgb="FFD9E2EC"/></right><top style="thin"><color rgb="FFD9E2EC"/></top><bottom style="thin"><color rgb="FFD9E2EC"/></bottom><diagonal/></border></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="3"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf><xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles><dxfs count="0"/><tableStyles count="0" defaultTableStyle="TableStyleMedium2" defaultPivotStyle="PivotStyleLight16"/></styleSheet>"""
    content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/worksheets/sheet3.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/><Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/></Types>"""
    rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/><Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/></Relationships>"""
    workbook = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="{NS}" xmlns:r="{RNS}"><sheets><sheet name="API清单" sheetId="1" r:id="rId1"/><sheet name="统计" sheetId="2" r:id="rId2"/><sheet name="说明" sheetId="3" r:id="rId3"/></sheets></workbook>"""
    workbook_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/><Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet3.xml"/><Relationship Id="rId4" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>"""
    now = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    core = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?><cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:dcmitype="http://purl.org/dc/dcmitype/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><dc:title>TileLang NPUIR API Inventory v2</dc:title><dc:creator>Codex</dc:creator><cp:lastModifiedBy>Codex</cp:lastModifiedBy><dcterms:created xsi:type="dcterms:W3CDTF">{now}</dcterms:created><dcterms:modified xsi:type="dcterms:W3CDTF">{now}</dcterms:modified></cp:coreProperties>"""
    app = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"><Application>Codex</Application></Properties>"""

    with zipfile.ZipFile(OUT_XLSX, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", rels)
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        archive.writestr("xl/styles.xml", styles)
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            sheet_xml(api_sheet, [24, 20, 26, 30, 30, 26, 30, 30, 46, 58, 58, 58, 58, 42, 42, 42, 42, 70, 36]),
        )
        archive.writestr("xl/worksheets/sheet2.xml", sheet_xml(stats_sheet, [30, 130]))
        archive.writestr("xl/worksheets/sheet3.xml", sheet_xml(notes_sheet, [30, 130]))
        archive.writestr("docProps/core.xml", core)
        archive.writestr("docProps/app.xml", app)


def main():
    rows = build_rows()
    stats, notes = stats_and_notes(rows)
    OUT_JSON.write_text(json.dumps({"rows": rows, "stats": stats, "notes": notes}, ensure_ascii=False, indent=2), encoding="utf-8")
    write_xlsx(rows, stats, notes)
    print(f"WROTE {OUT_XLSX}")
    print(f"ROWS {len(rows)}")
    print("GROUPS", dict(Counter(row["分组"] for row in rows)))
    print("A5_DEV", Counter(row["A5 Developer"] for row in rows).most_common(12))


if __name__ == "__main__":
    main()
