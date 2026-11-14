"""A small Java declaration reader, enough to list what a Spring service exposes and stores.

Not a compiler: it strips comments, walks braces and reads declarations (types, fields, methods,
annotations with their arguments, record components, enum constants). From that it builds
inventories that can be compared between two revisions:

- endpoints: @RestController/@Controller mappings and @FeignClient calls, with headers, params,
  path variables, body and return type, and the method security annotation;
- data classes (DTO, request/response, event, message): fields with type, validation and JSON name;
- entities: table and columns (@Table, @Column, @JoinColumn, Spring's default snake_case naming);
- enums: constants; scheduled jobs (@Scheduled); Kafka listeners (@KafkaListener).

Used by ref_changes.py. Standard library only.
"""

import re

# ---------------------------------------------------------------- lexical helpers

STRING_RE = re.compile(r'"((?:[^"\\\n]|\\.)*)"')
TYPE_DECL_RE = re.compile(
    r"^(?:(?:public|protected|private|static|final|abstract|sealed|non-sealed|strictfp)\s+)*"
    r"(class|interface|enum|record|@interface)\s+(\w+)"
)
METHOD_RE = re.compile(
    r"^(?P<mods>(?:(?:public|protected|private|static|final|abstract|synchronized|default|native)\s+)*)"
    r"(?:<[^()]*?>\s*)?(?:(?P<ret>[\w$.]+(?:\s*<.*>)?(?:\s*\[\s*\])*)\s+)?(?P<name>\w+)\s*\((?P<params>.*)\)"
    r"(?:\s*\[\s*\])*\s*(?:throws\s+[\w$.,\s<>]+)?$",
    re.S,
)
FIELD_DECL_RE = re.compile(
    r"^(?P<mods>(?:(?:public|protected|private|static|final|transient|volatile)\s+)*)"
    r"(?P<type>[\w$.]+(?:\s*<.*>)?(?:\s*\[\s*\])*)\s+(?P<name>\w+)\s*(?:\[\s*\])*$",
    re.S,
)
MODIFIERS = {"public", "protected", "private", "static", "final", "abstract", "synchronized", "default",
             "native", "transient", "volatile", "strictfp", "sealed", "non-sealed"}


def strip_comments(text):
    """Remove // and /* */ comments, keep strings, char literals and line breaks."""
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c == '"' or c == "'":
            j = skip_string(text, i)
            out.append(text[i:j])
            i = j
        elif text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append("\n" * text.count("\n", i, j))
            i = j
        else:
            out.append(c)
            i += 1
    return "".join(out)


def skip_string(text, i):
    """Index after the string, text block or char literal starting at i."""
    if text.startswith('"""', i):
        j = text.find('"""', i + 3)
        return len(text) if j < 0 else j + 3
    quote, j, n = text[i], i + 1, len(text)
    while j < n:
        if text[j] == "\\":
            j += 2
            continue
        if text[j] == quote or text[j] == "\n":
            return j + 1
        j += 1
    return n


def skip_balanced(text, i):
    """i points at ( [ or {; return the index after its match (strings respected)."""
    pairs = {"(": ")", "[": "]", "{": "}"}
    stack, n = [], len(text)
    while i < n:
        c = text[i]
        if c in "\"'":
            i = skip_string(text, i)
            continue
        if c in pairs:
            stack.append(pairs[c])
        elif stack and c == stack[-1]:
            stack.pop()
            if not stack:
                return i + 1
        i += 1
    return n


def split_top(text, sep=","):
    """Split on sep outside (), [], {}, <> and strings."""
    parts, depth, start, i, n = [], 0, 0, 0, len(text)
    while i < n:
        c = text[i]
        if c in "\"'":
            i = skip_string(text, i)
            continue
        if c in "([{<":
            depth += 1
        elif c in ")]}>" and depth:
            depth -= 1
        elif c == sep and depth == 0:
            parts.append(text[start:i])
            start = i + 1
        i += 1
    parts.append(text[start:])
    return [p.strip() for p in parts if p.strip()]


def squash(text):
    return re.sub(r"\s+", " ", text or "").strip()


# ---------------------------------------------------------------- annotations

def take_annotations(text):
    """Pull every annotation out of a declaration head: ([(name, args)], text without them)."""
    annots, rest, i, n = [], [], 0, len(text)
    while i < n:
        if text[i] in "\"'":
            j = skip_string(text, i)
            rest.append(text[i:j])
            i = j
            continue
        if text[i] == "(":   # a parameter list keeps its own annotations
            j = skip_balanced(text, i)
            rest.append(text[i:j])
            i = j
            continue
        if text[i] == "@" and not text.startswith("@interface", i):
            m = re.match(r"@([\w$.]+)", text[i:])
            if m:
                name = m.group(1).split(".")[-1]
                j = i + m.end()
                k = j
                while k < n and text[k].isspace():
                    k += 1
                args = None
                if k < n and text[k] == "(":
                    end = skip_balanced(text, k)
                    args = text[k + 1:end - 1]
                    j = end
                annots.append((name, args))
                rest.append(" ")
                i = j
                continue
        rest.append(text[i])
        i += 1
    return annots, "".join(rest)


def ann_args(args):
    """'value' / named attributes of one annotation -> {name: raw expression}; a bare value is 'value'."""
    out = {}
    if not args or not args.strip():
        return out
    for part in split_top(args):
        m = re.match(r"^(\w+)\s*=(?!=)\s*(.*)$", part, re.S)
        if m:
            out[m.group(1)] = m.group(2).strip()
        else:
            out.setdefault("value", part.strip())
    return out


def ann_strings(expr, consts=None):
    """String literals of an attribute. A constant declared in the same file is resolved; any other
    constant reference is kept as {NAME}."""
    if expr is None:
        return []
    found = STRING_RE.findall(expr)
    if found:
        return found
    expr = expr.strip().strip("{}").strip()
    out = []
    for part in split_top(expr) if expr else []:
        name = part.strip().split(".")[-1]
        out.append((consts or {}).get(name, f"{{{part.strip()}}}"))
    return out


def string_constants(types):
    """static String NAME = "literal" fields of a file -> {NAME: literal}."""
    out = {}
    for t in types:
        for f in t["fields"]:
            if f.get("static") and f.get("initializer"):
                m = re.fullmatch(r'\s*"((?:[^"\\\n]|\\.)*)"\s*', f["initializer"])
                if m:
                    out.setdefault(f["name"], m.group(1))
    return out


def ann(annots, *names):
    for name, args in annots:
        if name in names:
            return name, args
    return None


def ann_names(annots):
    return {name for name, _ in annots}


# ---------------------------------------------------------------- declarations

def parse_java(text):
    """-> list of type declarations: {name, kind, annotations, fields, methods, constants, components}."""
    src = strip_comments(text)
    types = []
    _members(src, 0, len(src), None, types)
    return types


def _members(src, start, end, owner, types):
    i = start
    while i < end:
        while i < end and (src[i].isspace() or src[i] == ";"):
            i += 1
        if i >= end:
            break
        j, stop = i, None
        while j < end:
            c = src[j]
            if c in "\"'":
                j = skip_string(src, j)
                continue
            if c == "(" or c == "[":
                j = skip_balanced(src, j)
                continue
            if c in ";{=":
                stop = c
                break
            if c == "}":   # stray closing brace: give up on this body
                return
            j += 1
        head = src[i:j]
        if stop is None:
            break
        if stop == "=":
            k = j
            while k < end and src[k] != ";":
                if src[k] in "\"'":
                    k = skip_string(src, k)
                    continue
                if src[k] in "([{":
                    k = skip_balanced(src, k)
                    continue
                k += 1
            _declare(head, owner, types, initializer=src[j + 1:k].strip())
            i = k + 1
            continue
        if stop == ";":
            _declare(head, owner, types)
            i = j + 1
            continue
        body_end = skip_balanced(src, j)
        decl = _declare(head, owner, types, has_body=True)
        if decl is not None:
            if decl["kind"] == "enum":
                inner_start = _enum_constants(src, j + 1, body_end - 1, decl)
                _members(src, inner_start, body_end - 1, decl, types)
            else:
                _members(src, j + 1, body_end - 1, decl, types)
        i = body_end


def _declare(head, owner, types, has_body=False, initializer=None):
    annots, rest = take_annotations(head)
    rest = squash(rest)
    if not rest or rest.startswith(("package ", "import ")) or rest in ("static", "default"):
        return None
    m = TYPE_DECL_RE.match(rest)
    if m:
        name = m.group(2) if owner is None else f"{owner['name']}.{m.group(2)}"
        decl = {"name": name, "simple": m.group(2), "kind": m.group(1), "annotations": annots,
                "modifiers": rest[:m.start(1)].split(), "fields": [], "methods": [], "constants": [],
                "components": []}
        if m.group(1) == "record":
            p = rest.find("(", m.end())
            if p >= 0:
                comps = rest[p + 1:skip_balanced(rest, p) - 1]
                decl["components"] = [x for x in (_param(c) for c in split_top(comps)) if x]
        types.append(decl)
        return decl
    if owner is None:
        return None
    if "(" in rest:
        mm = METHOD_RE.match(rest)
        if mm and mm.group("name") not in ("if", "for", "while", "switch", "catch", "return", "new"):
            params = [x for x in (_param(p) for p in split_top(mm.group("params"))) if x]
            owner["methods"].append({"name": mm.group("name"), "returns": squash(mm.group("ret") or ""),
                                     "annotations": annots, "params": params,
                                     "modifiers": mm.group("mods").split()})
        return None
    mf = FIELD_DECL_RE.match(rest)
    if mf and not has_body:
        mods = mf.group("mods").split()
        owner["fields"].append({"name": mf.group("name"), "type": squash(mf.group("type")), "annotations": annots,
                                "static": "static" in mods, "transient": "transient" in mods,
                                "initializer": initializer})
    return None


def _enum_constants(src, start, end, decl):
    """Read the constants of an enum body; return where the rest of the body starts."""
    i = start
    while i < end:
        c = src[i]
        if c in "\"'":
            i = skip_string(src, i)
            continue
        if c in "([{":
            i = skip_balanced(src, i)
            continue
        if c == ";":
            break
        i += 1
    for part in split_top(src[start:i]):
        _, rest = take_annotations(part)
        m = re.match(r"\s*([A-Za-z_$][\w$]*)", rest)
        if m:
            decl["constants"].append(m.group(1))
    return min(i + 1, end)


def _param(text):
    annots, rest = take_annotations(text)
    words = squash(rest).replace("final ", "").strip()
    m = re.match(r"^(.*\S)\s+(\w+)$", words, re.S)
    if not m:
        return None
    return {"name": m.group(2), "type": squash(m.group(1)), "annotations": annots}


# ---------------------------------------------------------------- endpoints

HTTP_MAPPINGS = {"GetMapping": "GET", "PostMapping": "POST", "PutMapping": "PUT", "DeleteMapping": "DELETE",
                 "PatchMapping": "PATCH", "RequestMapping": None}
SECURITY_ANNOTATIONS = ("PreAuthorize", "Secured", "RolesAllowed", "PostAuthorize")


def _paths(attrs, consts=None):
    found = ann_strings(attrs.get("value"), consts) + ann_strings(attrs.get("path"), consts)
    return found or [""]


def _join(base, path):
    full = "/" + "/".join(p.strip("/") for p in (base, path) if p and p.strip("/"))
    return full if full != "//" else "/"


def norm_path(path):
    return re.sub(r"\{[^}]*\}", "{}", path.rstrip("/") or "/")


def _request_methods(attrs):
    raw = attrs.get("method")
    if not raw:
        return ["ANY"]
    return sorted({m for m in re.findall(r"\b(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\b", raw)}) or ["ANY"]


def _binding(param, annotation, consts=None):
    hit = ann(param["annotations"], annotation)
    if not hit:
        return None
    attrs = ann_args(hit[1])
    name = (ann_strings(attrs.get("value"), consts) or ann_strings(attrs.get("name"), consts)
            or [param["name"]])[0]
    optional = ("required" in attrs and attrs["required"].strip() == "false") or "defaultValue" in attrs \
        or param["type"].startswith("Optional")
    return name + ("?" if optional else "")


def endpoints(types, path):
    """Endpoints served (kind http) and called through Feign (kind feign) by the given declarations."""
    out = []
    consts = string_constants(types)
    for t in types:
        names = ann_names(t["annotations"])
        feign = ann(t["annotations"], "FeignClient")
        if not (names & {"RestController", "Controller"} or feign or ann(t["annotations"], "RequestMapping")):
            continue
        kind, client = "http", None
        bases = [""]
        if feign:
            kind = "feign"
            attrs = ann_args(feign[1])
            client = (ann_strings(attrs.get("name"), consts) or ann_strings(attrs.get("value"), consts)
                      or ann_strings(attrs.get("contextId"), consts) or [t["simple"]])[0]
            bases = ann_strings(attrs.get("path"), consts) or [""]
        cls_map = ann(t["annotations"], "RequestMapping")
        if cls_map:
            bases = [_join(b, p) for b in bases for p in _paths(ann_args(cls_map[1]), consts)]
        class_security = [squash(f"@{n}({a or ''})") for n, a in t["annotations"] if n in SECURITY_ANNOTATIONS]
        for m in t["methods"]:
            for name, args in m["annotations"]:
                if name not in HTTP_MAPPINGS:
                    continue
                attrs = ann_args(args)
                verbs = [HTTP_MAPPINGS[name]] if HTTP_MAPPINGS[name] else _request_methods(attrs)
                headers = sorted(x for x in (_binding(p, "RequestHeader", consts) for p in m["params"]) if x)
                headers += [f"{h}=" for h in ann_strings(attrs.get("headers"), consts)]
                params = sorted(x for x in (_binding(p, "RequestParam", consts) for p in m["params"]) if x)
                params += [f"{p}=" for p in ann_strings(attrs.get("params"), consts)]
                body = next((p["type"] for p in m["params"] if ann(p["annotations"], "RequestBody")), None)
                valid_body = any(ann(p["annotations"], "RequestBody") and ann(p["annotations"], "Valid", "Validated")
                                 for p in m["params"])
                security = [squash(f"@{n}({a or ''})") for n, a in m["annotations"] if n in SECURITY_ANNOTATIONS]
                for base in bases:
                    for p in _paths(attrs, consts):
                        full = _join(base, p)
                        for verb in verbs:
                            out.append({
                                "kind": kind, "client": client, "method": verb, "path": full,
                                "key": f"{kind}:{client or ''}:{verb}:{norm_path(full)}",
                                "handler": f"{t['name']}#{m['name']}", "file": path,
                                "headers": headers, "params": params,
                                "path_vars": [x for x in (_binding(q, "PathVariable", consts) for q in m["params"])
                                              if x],
                                "body": body, "valid_body": valid_body, "returns": m["returns"],
                                "security": security or class_security,
                                "consumes": ann_strings(attrs.get("consumes")),
                                "produces": ann_strings(attrs.get("produces")),
                            })
    return out


ENDPOINT_FIELDS = ("headers", "params", "path_vars", "body", "valid_body", "returns", "security", "consumes",
                   "produces", "path", "handler")


def diff_endpoints(old, new):
    """Compare two endpoint lists by key -> [{change, endpoint, differences}]."""
    a = {e["key"]: e for e in old}
    b = {e["key"]: e for e in new}
    out = []
    for key in sorted(set(a) | set(b)):
        if key not in a:
            out.append({"change": "added", **_brief(b[key])})
        elif key not in b:
            out.append({"change": "removed", **_brief(a[key])})
        else:
            diffs = []
            for f in ENDPOINT_FIELDS:
                if a[key][f] != b[key][f]:
                    diffs.append({"field": f, "old": a[key][f], "new": b[key][f]})
            if diffs:
                out.append({"change": "changed", **_brief(b[key]), "differences": diffs,
                            "breaking": _breaking(diffs)})
    return out


def _brief(e):
    return {k: e[k] for k in ("kind", "client", "method", "path", "handler", "file", "headers", "params", "body",
                              "returns", "security")}


def _required(values):
    return {v for v in values or [] if not v.endswith("?")}


def _breaking(diffs):
    """Changes an existing caller notices: new required header/param, other body/return type, path, security."""
    reasons = []
    for d in diffs:
        f = d["field"]
        if f in ("headers", "params"):
            added = _required(d["new"]) - _required(d["old"])
            if added:
                reasons.append(f"{f} bắt buộc mới: {', '.join(sorted(added))}")
        elif f in ("body", "returns", "consumes"):
            reasons.append(f"{f} đổi")
        elif f == "path_vars" and len(d["old"]) != len(d["new"]):
            reasons.append("path variable đổi")    # a renamed variable is the same URL for the caller
        elif f == "security":
            reasons.append("quyền truy cập đổi")
        elif f == "valid_body" and d["new"]:
            reasons.append("body bắt đầu được @Valid kiểm tra")
    return reasons


# ---------------------------------------------------------------- data classes and entities

VALIDATION = {"NotNull", "NotBlank", "NotEmpty", "Size", "Min", "Max", "Pattern", "Email", "Positive",
              "PositiveOrZero", "Negative", "Past", "Future", "Digits", "DecimalMin", "DecimalMax", "Valid",
              "AssertTrue", "AssertFalse"}
REQUIRED_MARKS = {"NotNull", "NotBlank", "NotEmpty"}
JSON_ANNOTATIONS = {"JsonProperty", "JsonAlias", "JsonIgnore", "JsonFormat", "JsonInclude", "SerializedName"}
DATA_PATH_RE = re.compile(r"(^|/)(dtos?|requests?|responses?|payloads?|models?|events?|messages?|commands?|vo|"
                          r"contracts?)/", re.I)
DATA_NAME_RE = re.compile(r"(Request|Response|Dto|DTO|Payload|Event|Message|Command|Result|Body|Req|Res|Item|"
                          r"Detail|Info)$")
NOT_DATA_RE = re.compile(r"(Publisher|Producer|Consumer|Listener|Handler|Service|Config|Configuration|Mapper|"
                         r"Adapter|Factory|Utils?|Test|Controller|Repository|Client|Exception|Builder)$")


def _ann_text(annots, wanted):
    return sorted(squash(f"@{n}" + (f"({a})" if a else "")) for n, a in annots if n in wanted)


def data_fields(t):
    """Instance fields (and record components) of a data class -> {name: {type, validation, json}}."""
    rows = {}
    for f in t["components"] + [f for f in t["fields"] if not f.get("static")]:
        json_name = ann(f["annotations"], "JsonProperty", "SerializedName")
        rows[f["name"]] = {
            "type": f["type"],
            "validation": _ann_text(f["annotations"], VALIDATION),
            "json": ann_strings(ann_args(json_name[1]).get("value"))[:1] if json_name and json_name[1] else [],
            "ignored": bool(ann(f["annotations"], "JsonIgnore", "Transient")),
            "required": bool(ann_names(f["annotations"]) & REQUIRED_MARKS),
        }
    return rows


def is_data_class(t, path):
    if t["kind"] not in ("class", "record") or ann(t["annotations"], "Entity", "Embeddable", "MappedSuperclass"):
        return False
    if NOT_DATA_RE.search(t["simple"]):
        return False
    return bool(DATA_PATH_RE.search(path) or DATA_NAME_RE.search(t["simple"]))


def diff_fields(old, new):
    added, removed, changed = [], [], []
    for name in sorted(set(old) | set(new)):
        if name not in old:
            added.append({"field": name, **new[name]})
        elif name not in new:
            removed.append({"field": name, **old[name]})
        elif old[name] != new[name]:
            what = [k for k in ("type", "validation", "json", "ignored") if old[name][k] != new[name][k]]
            changed.append({"field": name, "what": what, "old": old[name], "new": new[name]})
    return added, removed, changed


def snake(name):
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
    return re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", s).lower()


def entity_columns(t):
    """Table name and columns of an @Entity (Spring's default physical naming: snake_case)."""
    table_ann = ann(t["annotations"], "Table")
    table = None
    if table_ann:
        table = (ann_strings(ann_args(table_ann[1]).get("name")) or [None])[0]
    table = table or snake(t["simple"])
    cols = {}
    for f in t["fields"]:
        names = ann_names(f["annotations"])
        if f.get("static") or f.get("transient") or "Transient" in names:
            continue
        if names & {"OneToMany", "ManyToMany"}:
            continue
        rel = ann(f["annotations"], "OneToOne", "ManyToOne")
        if rel and "mappedBy" in ann_args(rel[1]):
            continue
        col_ann = ann(f["annotations"], "JoinColumn" if rel else "Column")
        attrs = ann_args(col_ann[1]) if col_ann and col_ann[1] else {}
        name = (ann_strings(attrs.get("name")) or [None])[0]
        if not name:
            name = snake(f["name"]) + ("_id" if rel else "")
        if "Embedded" in names or "EmbeddedId" in names:
            name = f"<embedded {f['name']}>"
        info = {"field": f["name"], "type": f["type"],
                "attributes": sorted(f"{k}={squash(v)}" for k, v in attrs.items() if k != "name")}
        if "Id" in names or "EmbeddedId" in names:
            info["attributes"].append("id")
        enum = ann(f["annotations"], "Enumerated")
        if enum:
            info["attributes"].append("enum=" + ("STRING" if enum[1] and "STRING" in enum[1] else "ORDINAL"))
        cols[name.lower()] = info
    return table, cols


# ---------------------------------------------------------------- whole-file inventory

def inventory(text, path):
    """Everything this module knows how to read in one Java file."""
    try:
        types = parse_java(text)
    except RecursionError:
        types = []
    inv = {"endpoints": endpoints(types, path), "data": {}, "entities": {}, "enums": {}, "scheduled": {},
           "listeners": {}}
    for t in types:
        if t["kind"] == "enum":
            inv["enums"][t["name"]] = t["constants"]
        if ann(t["annotations"], "Entity"):
            table, cols = entity_columns(t)
            inv["entities"][t["name"]] = {"table": table, "columns": cols, "file": path}
        elif is_data_class(t, path):
            inv["data"][t["name"]] = {"fields": data_fields(t), "file": path}
        for m in t["methods"]:
            key = f"{t['name']}#{m['name']}"
            sched = [squash(f"@{n}({a or ''})") for n, a in m["annotations"] if n in ("Scheduled", "SchedulerLock")]
            if sched:
                inv["scheduled"][key] = sched
            listen = ann(m["annotations"], "KafkaListener")
            if listen:
                attrs = ann_args(listen[1])
                inv["listeners"][key] = {k: squash(v) for k, v in sorted(attrs.items())
                                         if k in ("topics", "topicPattern", "groupId", "id", "containerFactory",
                                                  "concurrency", "value", "properties", "autoStartup")}
    return inv
