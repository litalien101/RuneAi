#!/usr/bin/env python3
"""
project_exporter.py

Production-oriented project architecture exporter.

Features:
- Recursive project inventory with configurable ignores
- SHA-256 hashing and file metadata
- Python AST analysis
- JavaScript / TypeScript regex-based static analysis
- package.json / requirements / pyproject / lockfile discovery
- Framework detection
- HTTP route and WebSocket detection
- Internal dependency resolution
- Dependency graph + cycle detection
- React component / Three.js / R3F hints
- MMO subsystem classification
- Architecture metrics and risk signals
- Mermaid graph generation
- Compact deterministic AI-oriented JSON
- gzip-compressed AI export
- Optional source excerpts (disabled by default)
- No third-party dependencies

Usage:
    python project_exporter.py /path/to/project
    python project_exporter.py /path/to/project --output ./project_export
    python project_exporter.py . --include-source --max-source-bytes 12000
"""

from __future__ import annotations

import argparse
import ast
import gzip
import hashlib
import json
import math
import os
import re
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = "project-architecture-v1"

IGNORE_DIRS = {
    ".git", ".hg", ".svn", ".idea", ".vscode",
    "node_modules", "__pycache__", ".venv", "venv", "env",
    "dist", "build", ".next", ".nuxt", ".turbo", ".cache",
    "coverage", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".tox", ".eggs", "*.egg-info", ".gradle", ".terraform",
    "target", "bin", "obj", ".expo", ".parcel-cache",
}

SOURCE_EXTENSIONS = {
    ".py", ".pyi", ".js", ".jsx", ".mjs", ".cjs",
    ".ts", ".tsx", ".mts", ".cts",
}

CONFIG_FILENAMES = {
    "package.json", "requirements.txt", "requirements-dev.txt",
    "pyproject.toml", "poetry.lock", "Pipfile", "Pipfile.lock",
    "package-lock.json", "npm-shrinkwrap.json", "yarn.lock",
    "pnpm-lock.yaml", "bun.lockb", "bun.lock",
    "tsconfig.json", "jsconfig.json",
    "vite.config.js", "vite.config.ts",
    "next.config.js", "next.config.mjs", "next.config.ts",
    "astro.config.js", "astro.config.ts",
    "tailwind.config.js", "tailwind.config.ts",
    "docker-compose.yml", "docker-compose.yaml",
    "Dockerfile",
}

LANGUAGE_BY_EXT = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "jsx",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".mts": "typescript",
    ".cts": "typescript",
}

FRAMEWORK_RULES = {
    "react": ["react", "react-dom"],
    "nextjs": ["next"],
    "vue": ["vue"],
    "nuxt": ["nuxt"],
    "svelte": ["svelte"],
    "angular": ["@angular/core"],
    "threejs": ["three"],
    "r3f": ["@react-three/fiber"],
    "react-three-drei": ["@react-three/drei"],
    "fastapi": ["fastapi"],
    "flask": ["flask"],
    "django": ["django"],
    "express": ["express"],
    "nestjs": ["@nestjs/common"],
    "socketio": ["socket.io", "python-socketio"],
    "websocket": ["websocket", "websockets", "ws"],
    "prisma": ["prisma", "@prisma/client"],
    "drizzle": ["drizzle-orm"],
    "tailwind": ["tailwindcss"],
    "vite": ["vite"],
    "electron": ["electron"],
    "playwright": ["playwright"],
    "pytest": ["pytest"],
}

MMO_RULES = {
    "combat": [
        "combat", "attack", "damage", "weapon", "ability", "skill",
        "health", "hp", "hitbox", "hitpoint", "critical", "cooldown",
    ],
    "movement": [
        "player", "movement", "move", "velocity", "position", "transform",
        "locomotion", "character", "controller", "input", "acceleration",
        "physics", "pathfind",
    ],
    "networking": [
        "network", "socket", "websocket", "ws", "replication", "packet",
        "protocol", "server", "client", "netcode", "connection",
    ],
    "prediction": [
        "prediction", "predict", "clientprediction", "rollback",
        "interpolation", "extrapolation",
    ],
    "reconciliation": [
        "reconciliation", "reconcile", "authoritative", "snapshot",
        "tick", "serverstate",
    ],
    "inventory": [
        "inventory", "item", "equipment", "loot", "stash", "backpack",
        "crafting", "slot",
    ],
    "quest": [
        "quest", "mission", "objective", "dialogue", "npc",
    ],
    "world": [
        "world", "zone", "map", "region", "terrain", "chunk", "scene",
        "dungeon", "instance",
    ],
    "entities": [
        "entity", "component", "ecs", "spawn", "despawn", "entitymanager",
    ],
    "chat": [
        "chat", "whisper", "guildchat", "partychat", "messagechannel",
    ],
    "persistence": [
        "save", "load", "database", "db", "persistence", "postgres",
        "mysql", "mongodb", "redis",
    ],
}

IMPORT_RE = re.compile(
    r"""(?:import\s+(?:[^'"]+?\s+from\s+)?|export\s+[^'"]*?\s+from\s+|require\s*\(\s*)['"]([^'"]+)['"]""",
    re.MULTILINE,
)
DYNAMIC_IMPORT_RE = re.compile(r"""import\s*\(\s*['"]([^'"]+)['"]\s*\)""")
EXPORT_RE = re.compile(
    r"""\bexport\s+(?:default\s+)?(?:async\s+)?(?:function|class|const|let|var|interface|type|enum)\s+([A-Za-z_$][\w$]*)"""
)
NAMED_EXPORT_RE = re.compile(r"""\bexport\s*\{([^}]+)\}""", re.MULTILINE)
COMPONENT_RE = re.compile(
    r"""\b(?:function|const|class)\s+([A-Z][A-Za-z0-9_$]*)\b"""
)
ROUTE_PATTERNS = [
    re.compile(r"""@\s*(?:app|router)\.(get|post|put|patch|delete|options|head)\s*\(\s*["']([^"']+)["']"""),
    re.compile(r"""(?:app|router)\.(get|post|put|patch|delete|options|head)\s*\(\s*["']([^"']+)["']"""),
    re.compile(r"""@(get|post|put|patch|delete)\s*\(\s*["']([^"']+)["']"""),
]
WS_PATTERNS = [
    re.compile(r"""@\s*(?:app|router)\.websocket\s*\(\s*["']([^"']+)["']"""),
    re.compile(r"""(?:app|router)\.ws\s*\(\s*["']([^"']+)["']"""),
    re.compile(r"""(?:WebSocket|websocket|io)\s*\("""),
    re.compile(r"""new\s+WebSocket\s*\(\s*["']([^"']+)["']"""),
]

SECRET_RE = re.compile(
    r"""(?i)\b(?:api[_-]?key|secret|token|password|passwd|private[_-]?key)\b\s*[:=]\s*["'][^"']{6,}["']"""
)


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def safe_read_text(path: Path, max_bytes: int | None = None) -> str:
    with path.open("rb") as f:
        data = f.read() if max_bytes is None else f.read(max_bytes)
    return data.decode("utf-8", errors="ignore")


def relpath(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def is_ignored_dir(name: str, ignored: set[str]) -> bool:
    return name in ignored or any(
        token.startswith("*") and name.endswith(token[1:])
        for token in ignored
    )


def iter_files(root: Path, ignored: set[str]) -> Iterable[Path]:
    for current, dirs, files in os.walk(root):
        dirs[:] = sorted(
            d for d in dirs
            if not is_ignored_dir(d, ignored)
        )
        for filename in sorted(files):
            path = Path(current) / filename
            try:
                if path.is_symlink():
                    continue
                yield path
            except OSError:
                continue


def node_name(node: ast.AST) -> str:
    return getattr(node, "name", node.__class__.__name__)


def analyze_python(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {
        "imports": [],
        "from_imports": [],
        "classes": [],
        "functions": [],
        "async_functions": [],
        "decorators": [],
        "routes": [],
        "websockets": [],
        "calls": [],
        "loc": 0,
        "syntax_error": None,
    }
    try:
        text = safe_read_text(path)
        result["loc"] = text.count("\n") + (1 if text else 0)
        tree = ast.parse(text, filename=str(path))
    except SyntaxError as exc:
        result["syntax_error"] = f"{exc.msg} at line {exc.lineno}"
        return result
    except Exception as exc:
        result["syntax_error"] = type(exc).__name__
        return result

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                result["imports"].append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                result["from_imports"].append(node.module)
        elif isinstance(node, ast.ClassDef):
            result["classes"].append(node.name)
        elif isinstance(node, ast.FunctionDef):
            result["functions"].append(node.name)
            for dec in node.decorator_list:
                d = ast.unparse(dec) if hasattr(ast, "unparse") else node_name(dec)
                result["decorators"].append(d)
                m = re.search(r"\b(get|post|put|patch|delete|options|head|websocket)\s*\(\s*['\"]([^'\"]+)", d)
                if m:
                    if m.group(1) == "websocket":
                        result["websockets"].append(m.group(2))
                    else:
                        result["routes"].append({
                            "method": m.group(1).upper(),
                            "path": m.group(2),
                        })
        elif isinstance(node, ast.AsyncFunctionDef):
            result["async_functions"].append(node.name)
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                result["calls"].append(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                result["calls"].append(node.func.attr)

    result["imports"] = sorted(set(result["imports"]))
    result["from_imports"] = sorted(set(result["from_imports"]))
    result["classes"] = sorted(set(result["classes"]))
    result["functions"] = sorted(set(result["functions"]))
    result["async_functions"] = sorted(set(result["async_functions"]))
    result["decorators"] = sorted(set(result["decorators"]))
    result["calls"] = sorted(set(result["calls"]))[:300]
    return result


def analyze_js_ts(path: Path) -> dict[str, Any]:
    text = safe_read_text(path)
    imports = sorted(set(
        IMPORT_RE.findall(text) +
        DYNAMIC_IMPORT_RE.findall(text)
    ))
    exports = sorted(set(EXPORT_RE.findall(text)))
    for group in NAMED_EXPORT_RE.findall(text):
        for item in group.split(","):
            name = item.strip().split(" as ")[-1].strip()
            if re.match(r"^[A-Za-z_$][\w$]*$", name):
                exports.append(name)

    components = sorted(set(COMPONENT_RE.findall(text)))
    routes: list[dict[str, str]] = []
    websockets: list[str] = []

    for pattern in ROUTE_PATTERNS:
        for match in pattern.finditer(text):
            if len(match.groups()) == 2:
                routes.append({
                    "method": match.group(1).upper(),
                    "path": match.group(2),
                })

    for pattern in WS_PATTERNS:
        for match in pattern.finditer(text):
            if match.groups():
                websockets.append(match.group(1))
            else:
                websockets.append("detected")

    return {
        "imports": imports,
        "exports": sorted(set(exports)),
        "components": components,
        "routes": sorted(
            {json.dumps(x, sort_keys=True): x for x in routes}.values(),
            key=lambda x: (x["method"], x["path"]),
        ),
        "websockets": sorted(set(websockets)),
        "loc": text.count("\n") + (1 if text else 0),
        "has_three": bool(re.search(r"\b(?:THREE|three|@react-three)\b", text)),
        "has_react": bool(re.search(r"\b(?:React|useState|useEffect|jsx|tsx)\b", text)),
    }


def parse_package_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(safe_read_text(path))
        deps = {}
        deps.update(data.get("dependencies") or {})
        deps.update(data.get("devDependencies") or {})
        return {
            "name": data.get("name"),
            "version": data.get("version"),
            "dependencies": dict(sorted(deps.items())),
            "scripts": sorted((data.get("scripts") or {}).keys()),
        }
    except Exception as exc:
        return {"error": type(exc).__name__}


def parse_requirements(path: Path) -> list[str]:
    result = []
    try:
        for raw in safe_read_text(path).splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or line.startswith(("-r", "--")):
                continue
            line = line.split("#", 1)[0].strip()
            result.append(line)
    except Exception:
        pass
    return sorted(set(result))


def parse_pyproject(path: Path) -> dict[str, Any]:
    text = safe_read_text(path)
    deps = []
    for match in re.finditer(r"""["']([A-Za-z0-9_.-]+)(?:[<>=!~].*)?["']""", text):
        candidate = match.group(1)
        if candidate.lower() not in {"python", "setuptools", "wheel"}:
            deps.append(candidate)
    return {
        "dependencies": sorted(set(deps)),
        "tool_sections": sorted(set(
            re.findall(r"^\[tool\.([^\]]+)\]", text, re.MULTILINE)
        )),
    }


def detect_mmo(tags_source: str) -> list[str]:
    lower = tags_source.lower()
    scores = {}
    for subsystem, terms in MMO_RULES.items():
        hits = sum(1 for term in terms if term in lower)
        if hits:
            scores[subsystem] = hits
    return [
        name for name, _ in sorted(
            scores.items(), key=lambda item: (-item[1], item[0])
        )
    ]


def module_candidates(import_name: str) -> list[str]:
    normalized = import_name.replace("\\", "/")
    if normalized.startswith((".", "/")):
        return [normalized]
    return []


def strip_extension(p: str) -> str:
    for ext in (".tsx", ".ts", ".jsx", ".js", ".mjs", ".cjs", ".py", ".pyi"):
        if p.endswith(ext):
            return p[:-len(ext)]
    return p


def resolve_internal_import(source: str, import_name: str, known: set[str]) -> str | None:
    if not import_name.startswith("."):
        return None

    src = Path(source)
    base = src.parent
    parts = Path(import_name).parts
    candidate = (base.joinpath(*parts)).as_posix()
    candidate = str(Path(candidate))

    attempts = [
        candidate,
        strip_extension(candidate),
        strip_extension(candidate) + "/index",
        candidate + ".py",
        candidate + ".js",
        candidate + ".jsx",
        candidate + ".ts",
        candidate + ".tsx",
    ]

    for item in attempts:
        item = Path(item).as_posix()
        if item in known:
            return item
    return None


def find_cycles(graph: dict[str, list[str]]) -> list[list[str]]:
    visited: set[str] = set()
    active: list[str] = []
    active_set: set[str] = set()
    cycles: set[tuple[str, ...]] = set()

    def canonical_cycle(cycle: list[str]) -> tuple[str, ...]:
        if not cycle:
            return ()
        rotations = [tuple(cycle[i:] + cycle[:i]) for i in range(len(cycle))]
        rev = list(reversed(cycle))
        rotations += [tuple(rev[i:] + rev[:i]) for i in range(len(rev))]
        return min(rotations)

    def dfs(node: str) -> None:
        visited.add(node)
        active.append(node)
        active_set.add(node)

        for dep in graph.get(node, []):
            if dep in active_set:
                idx = active.index(dep)
                cycle = active[idx:]
                cycles.add(canonical_cycle(cycle))
            elif dep not in visited:
                dfs(dep)

        active.pop()
        active_set.remove(node)

    for node in sorted(graph):
        if node not in visited:
            dfs(node)

    return [list(x) for x in sorted(cycles)]


def percentile(values: list[int], p: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    idx = (len(values) - 1) * p
    lo, hi = math.floor(idx), math.ceil(idx)
    if lo == hi:
        return float(values[lo])
    return values[lo] + (values[hi] - values[lo]) * (idx - lo)


def mermaid_id(path: str) -> str:
    return "N" + hashlib.sha1(path.encode()).hexdigest()[:10]


@dataclass
class ExportConfig:
    root: Path
    output: Path
    ignored: set[str]
    include_source: bool
    max_source_bytes: int
    max_file_bytes: int
    max_graph_edges: int


class ProjectExporter:
    def __init__(self, config: ExportConfig):
        self.cfg = config
        self.root = config.root.resolve()
        self.output = config.output.resolve()

    def scan(self) -> dict[str, Any]:
        started = time.time()
        all_paths = list(iter_files(self.root, self.cfg.ignored))
        source_paths = [
            p for p in all_paths
            if p.suffix.lower() in SOURCE_EXTENSIONS
        ]

        file_records: dict[str, dict[str, Any]] = {}
        package_data: dict[str, Any] = {}
        requirements: list[str] = []
        pyproject: dict[str, Any] = {}

        known_source = {
            relpath(p, self.root) for p in source_paths
        }

        for path in all_paths:
            relative = relpath(path, self.root)
            try:
                stat = path.stat()
                size = stat.st_size
                record: dict[str, Any] = {
                    "path": relative,
                    "name": path.name,
                    "extension": path.suffix.lower(),
                    "size": size,
                    "language": LANGUAGE_BY_EXT.get(path.suffix.lower()),
                    "sha256": sha256_file(path),
                }

                if path.suffix.lower() in SOURCE_EXTENSIONS and size <= self.cfg.max_file_bytes:
                    if path.suffix.lower() in {".py", ".pyi"}:
                        analysis = analyze_python(path)
                    else:
                        analysis = analyze_js_ts(path)

                    imports = (
                        analysis.get("imports", []) +
                        analysis.get("from_imports", [])
                    )
                    record["analysis"] = analysis
                    record["imports"] = sorted(set(imports))
                    record["mmo"] = detect_mmo(
                        relative + " " + " ".join(
                            str(v) for v in analysis.values()
                            if isinstance(v, list)
                        )
                    )

                    if self.cfg.include_source:
                        record["source"] = safe_read_text(
                            path, self.cfg.max_source_bytes
                        )

                file_records[relative] = record

                if path.name == "package.json":
                    package_data[relative] = parse_package_json(path)
                elif path.name in {"requirements.txt", "requirements-dev.txt"}:
                    requirements.extend(parse_requirements(path))
                elif path.name == "pyproject.toml":
                    pyproject[relative] = parse_pyproject(path)
            except (OSError, PermissionError):
                file_records[relative] = {
                    "path": relative,
                    "error": "unreadable",
                }

        graph: dict[str, list[str]] = defaultdict(list)
        external_imports: dict[str, list[str]] = {}
        routes = []
        websockets = []

        for path, record in file_records.items():
            imports = record.get("imports", [])
            internal = []
            external = []
            for imp in imports:
                resolved = resolve_internal_import(path, imp, known_source)
                if resolved:
                    internal.append(resolved)
                else:
                    external.append(imp)
            graph[path] = sorted(set(internal))
            external_imports[path] = sorted(set(external))

            analysis = record.get("analysis", {})
            for route in analysis.get("routes", []):
                routes.append({"file": path, **route})
            for ws in analysis.get("websockets", []):
                websockets.append({"file": path, "path": ws})

            if internal:
                record["internal_dependencies"] = sorted(set(internal))
            if external:
                record["external_dependencies"] = sorted(set(external))

        cycles = find_cycles(graph)

        dependency_in = Counter()
        dependency_out = Counter()
        for src, deps in graph.items():
            dependency_out[src] = len(deps)
            for dep in deps:
                dependency_in[dep] += 1

        source_files = [
            r for r in file_records.values()
            if r.get("language")
        ]

        languages = Counter(r["language"] for r in source_files)
        extensions = Counter(
            r.get("extension", "") for r in file_records.values()
            if r.get("extension")
        )

        frameworks = self._detect_frameworks(
            file_records, package_data, requirements, pyproject
        )

        mmo_counts = Counter()
        for record in source_files:
            for tag in record.get("mmo", []):
                mmo_counts[tag] += 1

        risks = self._architecture_risks(
            file_records, graph, cycles, dependency_in, dependency_out
        )

        architecture = {
            "source_files": len(source_files),
            "total_files": len(file_records),
            "total_bytes": sum(
                int(r.get("size", 0)) for r in file_records.values()
            ),
            "total_loc": sum(
                int(r.get("analysis", {}).get("loc", 0))
                for r in source_files
            ),
            "languages": dict(languages.most_common()),
            "extensions": dict(extensions.most_common()),
            "dependency_edges": sum(len(v) for v in graph.values()),
            "cycles": len(cycles),
            "routes": len(routes),
            "websockets": len(websockets),
            "frameworks": frameworks,
            "mmo_subsystems": dict(mmo_counts.most_common()),
            "fan_in": {
                "max": max(dependency_in.values(), default=0),
                "p95": percentile(list(dependency_in.values()), 0.95),
            },
            "fan_out": {
                "max": max(dependency_out.values(), default=0),
                "p95": percentile(list(dependency_out.values()), 0.95),
            },
            "risks": risks,
        }

        tree = self._build_tree(file_records)
        generated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        result = {
            "schema": SCHEMA_VERSION,
            "project": {
                "name": self.root.name,
                "root_name": self.root.name,
                "generated_at": generated_at,
                "scan_seconds": round(time.time() - started, 3),
                "source_included": self.cfg.include_source,
            },
            "tree": tree,
            "files": file_records,
            "packages": {
                "package_json": package_data,
                "requirements": sorted(set(requirements)),
                "pyproject": pyproject,
            },
            "frameworks": frameworks,
            "routes": sorted(routes, key=lambda x: (x["file"], x["path"])),
            "websockets": sorted(
                websockets, key=lambda x: (x["file"], x["path"])
            ),
            "dependencies": {
                "internal": dict(sorted(graph.items())),
                "external": dict(sorted(external_imports.items())),
            },
            "cycles": cycles,
            "architecture": architecture,
            "mmo": {
                "subsystems": dict(mmo_counts.most_common()),
                "files_by_subsystem": self._files_by_mmo(file_records),
            },
            "hash_index": {
                path: record.get("sha256")
                for path, record in sorted(file_records.items())
                if record.get("sha256")
            },
        }
        result["mermaid"] = self._mermaid(graph)
        return result

    def _detect_frameworks(
        self,
        files: dict[str, dict[str, Any]],
        packages: dict[str, Any],
        requirements: list[str],
        pyproject: dict[str, Any],
    ) -> list[str]:
        evidence = set()

        package_names = set()
        for package in packages.values():
            package_names.update((package.get("dependencies") or {}).keys())

        requirement_text = " ".join(requirements).lower()
        source_text = []
        for record in files.values():
            analysis = record.get("analysis", {})
            source_text.extend(record.get("imports", []))
            source_text.extend(analysis.get("decorators", []))

        corpus = (
            " ".join(package_names) + " " +
            requirement_text + " " +
            " ".join(source_text)
        ).lower()

        for framework, terms in FRAMEWORK_RULES.items():
            if any(term.lower() in corpus for term in terms):
                evidence.add(framework)

        return sorted(evidence)

    def _files_by_mmo(self, files: dict[str, dict[str, Any]]) -> dict[str, list[str]]:
        out: dict[str, list[str]] = defaultdict(list)
        for path, record in files.items():
            for tag in record.get("mmo", []):
                out[tag].append(path)
        return {k: sorted(v) for k, v in sorted(out.items())}

    def _architecture_risks(
        self,
        files: dict[str, dict[str, Any]],
        graph: dict[str, list[str]],
        cycles: list[list[str]],
        fan_in: Counter,
        fan_out: Counter,
    ) -> list[dict[str, Any]]:
        risks = []

        if cycles:
            risks.append({
                "type": "circular_dependencies",
                "severity": "high",
                "count": len(cycles),
                "evidence": cycles[:20],
            })

        high_fan_out = [
            p for p, n in fan_out.items() if n >= max(10, math.ceil(
                percentile(list(fan_out.values()), 0.95)
            ))
        ]
        if high_fan_out:
            risks.append({
                "type": "high_fan_out",
                "severity": "medium",
                "count": len(high_fan_out),
                "files": sorted(high_fan_out)[:50],
            })

        high_fan_in = [
            p for p, n in fan_in.items() if n >= max(10, math.ceil(
                percentile(list(fan_in.values()), 0.95)
            ))
        ]
        if high_fan_in:
            risks.append({
                "type": "high_fan_in",
                "severity": "medium",
                "count": len(high_fan_in),
                "files": sorted(high_fan_in)[:50],
            })

        orphans = []
        reverse = Counter(dep for deps in graph.values() for dep in deps)
        for path, record in files.items():
            if not record.get("language"):
                continue
            if path not in reverse and not graph.get(path):
                orphans.append(path)
        if orphans:
            risks.append({
                "type": "isolated_source_files",
                "severity": "low",
                "count": len(orphans),
                "files": sorted(orphans)[:100],
            })

        secrets = []
        for path, record in files.items():
            if record.get("source"):
                if SECRET_RE.search(record["source"]):
                    secrets.append(path)
        if secrets:
            risks.append({
                "type": "possible_hardcoded_secrets",
                "severity": "high",
                "count": len(secrets),
                "files": sorted(secrets)[:50],
            })

        syntax_errors = [
            p for p, r in files.items()
            if r.get("analysis", {}).get("syntax_error")
        ]
        if syntax_errors:
            risks.append({
                "type": "syntax_parse_failures",
                "severity": "medium",
                "count": len(syntax_errors),
                "files": sorted(syntax_errors)[:50],
            })

        return risks

    def _build_tree(self, files: dict[str, dict[str, Any]]) -> dict[str, Any]:
        root: dict[str, Any] = {"type": "directory", "children": {}}
        for path in sorted(files):
            current = root
            parts = Path(path).parts
            for i, part in enumerate(parts):
                children = current["children"]
                if part not in children:
                    is_file = i == len(parts) - 1
                    children[part] = {
                        "type": "file" if is_file else "directory",
                        "children": {} if not is_file else None,
                    }
                current = children[part]
        return root

    def _mermaid(self, graph: dict[str, list[str]]) -> str:
        lines = ["graph TD"]
        edge_count = 0
        for src, deps in sorted(graph.items()):
            for dep in deps:
                if edge_count >= self.cfg.max_graph_edges:
                    break
                lines.append(
                    f'    {mermaid_id(src)}["{src}"] --> {mermaid_id(dep)}["{dep}"]'
                )
                edge_count += 1
        if edge_count >= self.cfg.max_graph_edges:
            lines.append(
                f'    LIMIT["Graph truncated at {self.cfg.max_graph_edges} edges"]'
            )
        return "\n".join(lines) + "\n"

    def write(self, data: dict[str, Any]) -> dict[str, Path]:
        self.output.mkdir(parents=True, exist_ok=True)

        compact_path = self.output / "architecture.ai.json.gz"
        json_path = self.output / "architecture.json"
        md_path = self.output / "architecture.md"
        mmd_path = self.output / "dependency_graph.mmd"
        manifest_path = self.output / "manifest.json"

        compact_bytes = json.dumps(
            data,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        with gzip.open(compact_path, "wb", compresslevel=9) as f:
            f.write(compact_bytes)

        with json_path.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        mmd_path.write_text(data["mermaid"], encoding="utf-8")
        md_path.write_text(self._markdown_summary(data), encoding="utf-8")

        manifest = {
            "schema": SCHEMA_VERSION,
            "compressed_export": compact_path.name,
            "uncompressed_export": json_path.name,
            "markdown_summary": md_path.name,
            "mermaid_graph": mmd_path.name,
            "json_bytes": len(compact_bytes),
            "gzip_bytes": compact_path.stat().st_size,
            "compression_ratio": round(
                compact_path.stat().st_size / max(1, len(compact_bytes)), 4
            ),
        }
        manifest_path.write_text(
            json.dumps(manifest, indent=2), encoding="utf-8"
        )

        return {
            "compressed": compact_path,
            "json": json_path,
            "markdown": md_path,
            "mermaid": mmd_path,
            "manifest": manifest_path,
        }

    def _markdown_summary(self, data: dict[str, Any]) -> str:
        a = data["architecture"]
        lines = [
            f"# Project Architecture — {data['project']['name']}",
            "",
            f"- Schema: `{data['schema']}`",
            f"- Generated: `{data['project']['generated_at']}`",
            f"- Total files: **{a['total_files']}**",
            f"- Source files: **{a['source_files']}**",
            f"- Source LOC: **{a['total_loc']}**",
            f"- Dependency edges: **{a['dependency_edges']}**",
            f"- Circular dependency groups: **{a['cycles']}**",
            "",
            "## Languages",
        ]
        for lang, count in a["languages"].items():
            lines.append(f"- {lang}: {count}")

        lines += ["", "## Frameworks"]
        lines += [f"- {x}" for x in data["frameworks"]] or ["- None detected"]

        lines += ["", "## Routes"]
        lines += [
            f"- `{r['method']} {r['path']}` — `{r['file']}`"
            for r in data["routes"][:200]
        ] or ["- None detected"]

        lines += ["", "## WebSockets"]
        lines += [
            f"- `{w['path']}` — `{w['file']}"
            for w in data["websockets"][:200]
        ] or ["- None detected"]

        lines += ["", "## MMO Subsystems"]
        lines += [
            f"- {name}: {count} files"
            for name, count in data["mmo"]["subsystems"].items()
        ] or ["- None detected"]

        lines += ["", "## Architecture Risks"]
        for risk in a["risks"]:
            lines.append(
                f"- **{risk['severity']}** `{risk['type']}` — "
                f"{risk.get('count', 0)} affected"
            )
        if not a["risks"]:
            lines.append("- No automated risk signals detected.")

        lines += ["", "## Cycles"]
        for cycle in data["cycles"][:50]:
            lines.append("- " + " → ".join(f"`{x}`" for x in cycle))
        if not data["cycles"]:
            lines.append("- None detected.")

        lines += [
            "",
            "## Important",
            "",
            "This export is static analysis, not proof of runtime behavior. "
            "Detected frameworks, routes, MMO subsystems, dependencies, "
            "and architecture risks are evidence-based heuristics and "
            "should be validated against runtime behavior and tests.",
            "",
        ]
        return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export a project into an AI-readable architecture package."
    )
    parser.add_argument(
        "project",
        nargs="?",
        default=".",
        help="Project root to scan.",
    )
    parser.add_argument(
        "--output", "-o",
        default="./project_export",
        help="Output directory.",
    )
    parser.add_argument(
        "--include-source",
        action="store_true",
        help="Include bounded source excerpts in the AI export.",
    )
    parser.add_argument(
        "--max-source-bytes",
        type=int,
        default=12000,
        help="Maximum source bytes per file when --include-source is used.",
    )
    parser.add_argument(
        "--max-file-bytes",
        type=int,
        default=2_000_000,
        help="Skip detailed source analysis above this size.",
    )
    parser.add_argument(
        "--max-graph-edges",
        type=int,
        default=10000,
        help="Maximum Mermaid edges.",
    )
    parser.add_argument(
        "--ignore",
        action="append",
        default=[],
        help="Additional directory/file-name pattern to ignore. Repeatable.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(args.project).expanduser().resolve()

    if not root.exists():
        print(f"ERROR: project does not exist: {root}", file=sys.stderr)
        return 2
    if not root.is_dir():
        print(f"ERROR: project is not a directory: {root}", file=sys.stderr)
        return 2

    ignored = set(IGNORE_DIRS)
    ignored.update(args.ignore)

    cfg = ExportConfig(
        root=root,
        output=Path(args.output).expanduser(),
        ignored=ignored,
        include_source=args.include_source,
        max_source_bytes=max(0, args.max_source_bytes),
        max_file_bytes=max(1, args.max_file_bytes),
        max_graph_edges=max(1, args.max_graph_edges),
    )

    print(f"[1/3] Scanning project: {root}")
    exporter = ProjectExporter(cfg)
    data = exporter.scan()

    print(
        f"[2/3] Analyzed {data['architecture']['total_files']} files, "
        f"{data['architecture']['source_files']} source files"
    )

    outputs = exporter.write(data)

    print("[3/3] Export complete")
    for name, path in outputs.items():
        print(f"  {name:12} {path}")

    print()
    print("AI export:")
    print(f"  {outputs['compressed']}")
    print(
        f"  compressed to {outputs['compressed'].stat().st_size:,} bytes"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
