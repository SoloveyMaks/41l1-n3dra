"""Evidence graph: routes, ordered rules, handlers, sources and sinks.

Every edge carries an evidence coordinate and an explicit resolution status.
An edge is ``established`` only when the parser found the concrete declaration
or call that justifies it.  A link that cannot be resolved is recorded as
``unknown`` with the reason instead of being drawn as if it existed.
"""

from __future__ import annotations

from typing import Any

from .kotlin_lexer import Token
from .policy import OBJECT_ACCESS_POLICY
from .project import Project
from .sast.common import JDBC_METHODS, source_params, sql_arguments

CREDENTIAL_SINKS = {
    "setBearerAuth",
    "setBasicAuth",
    "setRequestProperty",
    "setHeader",
    "addHeader",
    "header",
    "mapOf",
}

CREDENTIAL_HEADERS = {
    "authorization",
    "proxy-authorization",
    "x-api-key",
    "api-key",
    "x-auth-token",
    "x-import-password",
    "x-access-token",
}


def _node(
    identifier: str,
    kind: str,
    label: str,
    path: str | None,
    line: int | None,
    end_line: int | None,
    attributes: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": identifier,
        "kind": kind,
        "label": label,
    }
    if path is not None:
        data["path"] = path
    if line is not None:
        data["line"] = line
    if end_line is not None:
        data["endLine"] = end_line
    if attributes:
        data["attributes"] = attributes
    return data


def _edge(
    source: str,
    target: str,
    kind: str,
    basis: str,
    path: str | None = None,
    line: int | None = None,
    end_line: int | None = None,
    status: str = "established",
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "from": source,
        "to": target,
        "kind": kind,
        "status": status,
        "basis": basis,
    }
    if path is not None:
        data["evidence"] = {"path": path, "line": line, "endLine": end_line if end_line is not None else line}
    return data


def _ctor_types(project: Project, class_name: str | None) -> dict[str, str]:
    if class_name is None:
        return {}
    cls = project.find_class(class_name)
    if cls is None:
        return {}
    result: dict[str, str] = {}
    for param in cls.ctor_params:
        base = param.type_text.split("<")[0].rstrip("?").strip()
        if base:
            result[param.name] = base
    return result


def _function_id(function) -> str:
    return f"function:{function.file}:{function.owner or '-'}.{function.name}:{function.line}"


def _handler_id(function) -> str:
    return f"handler:{function.file}:{function.owner or '-'}.{function.name}:{function.line}"


def _call_summary(function) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for call in function.calls:
        calls.append(
            {
                "name": call.name,
                "receiver": call.receiver,
                "line": call.line,
                "endLine": call.end_line,
                "arguments": [call.arg_text(index) for index in range(len(call.args))],
            }
        )
    return calls


def _sql_sinks(project: Project, function) -> list[tuple[str, dict[str, Any]]]:
    sinks: list[tuple[str, dict[str, Any]]] = []
    for sql_arg in sql_arguments(function):
        token: Token = sql_arg.string_token
        interpolated = bool(token.interpolations)
        concatenated = any(item.is_op("+") for item in sql_arg.argument_tokens)
        sink_id = f"sink:sql:{function.file}:{token.line}:{sql_arg.call.name}"
        sinks.append(
            (
                sink_id,
                _node(
                    sink_id,
                    "sink",
                    f"{sql_arg.call.name} SQL",
                    function.file,
                    token.line,
                    token.line,
                    {
                        "sinkType": "sql",
                        "call": sql_arg.call.name,
                        "argumentIndex": sql_arg.argument_index,
                        "interpolated": interpolated,
                        "concatenated": concatenated,
                        "parameterized": not interpolated and not concatenated,
                    },
                ),
            )
        )
    return sinks


def _http_sinks(function) -> list[tuple[str, dict[str, Any]]]:
    sinks: list[tuple[str, dict[str, Any]]] = []
    for call in function.calls:
        if call.name not in CREDENTIAL_SINKS:
            continue
        if call.name in {"setBearerAuth", "setBasicAuth"}:
            header = call.string_arg(0) or "Authorization"
        elif call.name == "mapOf":
            header = call.string_arg(0)
        else:
            header = call.string_arg(0)
        if not header or header.lower() not in CREDENTIAL_HEADERS:
            continue
        sink_id = f"sink:http:{function.file}:{call.line}:{call.name}"
        sinks.append(
            (
                sink_id,
                _node(
                    sink_id,
                    "sink",
                    f"HTTP {header}",
                    function.file,
                    call.line,
                    call.end_line,
                    {"sinkType": "http-header", "header": header, "call": call.name},
                ),
            )
        )
    return sinks


def _tls_sinks(function) -> list[tuple[str, dict[str, Any]]]:
    sinks: list[tuple[str, dict[str, Any]]] = []
    for token in function.body:
        if token.kind != "IDENT" or token.text not in {"hostnameVerifier", "HostnameVerifier"}:
            continue
        # A verifier is a sink only when the function also opens a connection.
        if not any(
            item.kind == "IDENT" and item.text in {"HttpsURLConnection", "openConnection", "SSLContext"}
            for item in function.body
        ):
            continue
        sink_id = f"sink:tls:{function.file}:{token.line}"
        sinks.append(
            (
                sink_id,
                _node(
                    sink_id,
                    "sink",
                    "TLS hostname verification",
                    function.file,
                    token.line,
                    token.line,
                    {"sinkType": "tls-verifier", "setting": token.text},
                ),
            )
        )
        break
    return sinks


def build_graph(project: Project) -> dict[str, Any]:
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []

    rules = [
        _node(
            f"rule:{rule.index}",
            "authorization-rule",
            f"{rule.index}. {rule.effect or 'matcher'}",
            rule.source_file,
            rule.line,
            rule.end_line,
            {
                "order": rule.index,
                "effect": rule.effect,
                "effectArgs": rule.effect_args,
                "matchers": [{"method": method, "pattern": pattern} for method, pattern in rule.matchers],
                "anyRequest": rule.any_request,
            },
        )
        for rule in project.security_rules
    ]
    for node in rules:
        nodes[node["id"]] = node

    routes_by_path = {route.path: route for route in project.routes}
    reached_functions: set[str] = set()

    for route in project.routes:
        route_node_id = f"route:{route.method or 'ANY'} {route.path}"
        handler = route.handler
        handler_id = _handler_id(handler)
        nodes[route_node_id] = _node(
            route_node_id,
            "route",
            route.route_id,
            handler.file,
            handler.line,
            handler.end_line,
            {
                "method": route.method,
                "path": route.path,
                "controller": route.class_name,
                "handler": handler.name,
            },
        )
        if handler_id not in nodes:
            nodes[handler_id] = _node(
                handler_id,
                "handler",
                f"{handler.owner or '-'}.{handler.name}",
                handler.file,
                handler.line,
                handler.end_line,
                {
                    "owner": handler.owner,
                    "parameters": [
                        {"name": param.name, "type": param.type_text, "line": param.line}
                        for param in handler.params
                    ],
                    "calls": _call_summary(handler),
                    "bodyKind": handler.body_kind,
                },
            )
        edges.append(
            _edge(
                route_node_id,
                handler_id,
                "handled-by",
                "Аннотация маршрута объявлена непосредственно над функцией-обработчиком.",
                handler.file,
                handler.line,
                handler.end_line,
            )
        )

        if route.applied_rule is not None:
            edges.append(
                _edge(
                    f"rule:{route.applied_rule.index}",
                    route_node_id,
                    "authorizes",
                    "Первое правило в порядке объявления, чей метод и шаблон совпали с маршрутом.",
                    route.applied_rule.source_file,
                    route.applied_rule.line,
                    route.applied_rule.end_line,
                )
            )
        else:
            unresolved.append(
                {
                    "kind": "route-rule",
                    "route": route.route_id,
                    "path": handler.file,
                    "line": handler.line,
                    "status": "unknown",
                    "reason": "Ни одно правило authorizeHttpRequests не сопоставлено с маршрутом.",
                }
            )

        for source in source_params(handler):
            source_id = f"source:{handler.file}:{handler.name}:{source.name}:{source.line}"
            nodes[source_id] = _node(
                source_id,
                "parameter-source",
                source.name,
                handler.file,
                source.line,
                source.end_line,
                {"sourceKind": source.kind, "handler": handler.name},
            )
            edges.append(
                _edge(
                    handler_id,
                    source_id,
                    "reads-source",
                    f"Параметр `{source.name}` объявлен в сигнатуре обработчика как {source.kind}.",
                    handler.file,
                    source.line,
                    source.end_line,
                )
            )

        ctor_types = _ctor_types(project, handler.owner)
        functions = [(handler, handler_id)]
        for call in handler.calls:
            receiver_type = ctor_types.get(call.receiver)
            if receiver_type is None:
                continue
            target_class = project.find_class(receiver_type)
            if target_class is None:
                continue
            for method in target_class.functions:
                if method.name != call.name:
                    continue
                target_id = _function_id(method)
                if target_id not in nodes:
                    nodes[target_id] = _node(
                        target_id,
                        "function",
                        f"{method.owner or '-'}.{method.name}",
                        method.file,
                        method.line,
                        method.end_line,
                        {
                            "owner": method.owner,
                            "parameters": [
                                {"name": param.name, "type": param.type_text, "line": param.line}
                                for param in method.params
                            ],
                            "calls": _call_summary(method),
                        },
                    )
                edges.append(
                    _edge(
                        handler_id,
                        target_id,
                        "calls",
                        f"`{call.receiver}.{call.name}(...)` разрешён по типу конструктора `{receiver_type}`.",
                        handler.file,
                        call.line,
                        call.end_line,
                    )
                )
                functions.append((method, target_id))

        for function, function_node_id in functions:
            if function_node_id in reached_functions and function is not handler:
                continue
            reached_functions.add(function_node_id)
            sinks = _sql_sinks(project, function) + _http_sinks(function) + _tls_sinks(function)
            for sink_id, sink_node in sinks:
                if sink_id not in nodes:
                    nodes[sink_id] = sink_node
                edges.append(
                    _edge(
                        function_node_id,
                        sink_id,
                        "contains-sink",
                        f"Sink объявлен в теле `{function.name}`.",
                        function.file,
                        sink_node.get("line"),
                        sink_node.get("endLine"),
                    )
                )

    # CORS configurations and the routes they are registered for.
    for function in project.functions:
        tokens = function.body
        if not tokens:
            continue
        has_cors = any(
            token.kind == "IDENT" and token.text in {"allowedOriginPatterns", "allowedOrigins"}
            for token in tokens
        ) or any(call.name == "registerCorsConfiguration" for call in function.calls)
        if not has_cors:
            continue
        function_id = f"function:{function.file}:{function.owner or '-'}.{function.name}:{function.line}"
        if function_id not in nodes:
            nodes[function_id] = _node(
                function_id,
                "function",
                f"{function.owner or '-'}.{function.name}",
                function.file,
                function.line,
                function.end_line,
                {
                    "owner": function.owner,
                    "parameters": [
                        {"name": param.name, "type": param.type_text, "line": param.line}
                        for param in function.params
                    ],
                    "calls": _call_summary(function),
                },
            )
        for index, token in enumerate(tokens):
            if not (token.kind == "IDENT" and token.text in {"allowedOriginPatterns", "allowedOrigins"}):
                continue
            sink_id = f"sink:cors:{function.file}:{token.line}"
            if sink_id in nodes:
                continue
            nodes[sink_id] = _node(
                sink_id,
                "sink",
                "CORS origin policy",
                function.file,
                token.line,
                token.line,
                {"sinkType": "cors", "setting": token.text},
            )
            edges.append(
                _edge(
                    function_id,
                    sink_id,
                    "contains-sink",
                    "Настройка CORS объявлена в теле функции конфигурации.",
                    function.file,
                    token.line,
                    token.line,
                )
            )
        for call in function.calls:
            if call.name != "registerCorsConfiguration":
                continue
            path = call.string_arg(0)
            if not path:
                continue
            route = routes_by_path.get(path)
            target = f"route:{route.method or 'ANY'} {path}" if route is not None else f"cors-registration:{path}"
            if route is None:
                nodes[target] = _node(
                    target,
                    "cors-registration",
                    path,
                    function.file,
                    call.line,
                    call.end_line,
                    {"registeredPath": path},
                )
                unresolved.append(
                    {
                        "kind": "cors-route",
                        "path": function.file,
                        "line": call.line,
                        "status": "unknown",
                        "reason": f"Маршрут `{path}` не найден среди обработчиков контроллеров.",
                    }
                )
            edges.append(
                _edge(
                    function_id,
                    target,
                    "registers-cors-for",
                    "Вызов `registerCorsConfiguration` сопоставлен с путём маршрута.",
                    function.file,
                    call.line,
                    call.end_line,
                )
            )

    # Every remaining sink is still a real declaration: keep it in the graph.
    # Reachability from a route is computed afterwards instead of guessed.
    edge_keys = {(edge["from"], edge["to"], edge["kind"]) for edge in edges}
    for function in project.functions:
        sinks = _sql_sinks(project, function) + _http_sinks(function) + _tls_sinks(function)
        if not sinks:
            continue
        function_id = _function_id(function)
        if function_id not in nodes:
            nodes[function_id] = _node(
                function_id,
                "function",
                f"{function.owner or '-'}.{function.name}",
                function.file,
                function.line,
                function.end_line,
                {
                    "owner": function.owner,
                    "parameters": [
                        {"name": param.name, "type": param.type_text, "line": param.line}
                        for param in function.params
                    ],
                    "calls": _call_summary(function),
                },
            )
        for sink_id, sink_node in sinks:
            if sink_id not in nodes:
                nodes[sink_id] = sink_node
            key = (function_id, sink_id, "contains-sink")
            if key in edge_keys:
                continue
            edges.append(
                _edge(
                    function_id,
                    sink_id,
                    "contains-sink",
                    f"Sink объявлен в теле `{function.name}`.",
                    function.file,
                    sink_node.get("line"),
                    sink_node.get("endLine"),
                )
            )
            edge_keys.add(key)

    # Route reachability: BFS over established edges only.
    adjacency: dict[str, list[str]] = {}
    for edge in edges:
        if edge["status"] != "established":
            continue
        adjacency.setdefault(edge["from"], []).append(edge["to"])
    reachable: set[str] = {node["id"] for node in nodes.values() if node["kind"] == "route"}
    queue = list(reachable)
    while queue:
        current = queue.pop()
        for target in adjacency.get(current, []):
            if target not in reachable:
                reachable.add(target)
                queue.append(target)
    for node in nodes.values():
        if node["kind"] in {"function", "sink", "handler"}:
            node.setdefault("attributes", {})["routeReachable"] = node["id"] in reachable

    ordered_rules = [
        {
            "order": rule.index,
            "effect": rule.effect,
            "effectArgs": rule.effect_args,
            "matchers": [{"method": method, "pattern": pattern} for method, pattern in rule.matchers],
            "anyRequest": rule.any_request,
            "path": rule.source_file,
            "line": rule.line,
            "endLine": rule.end_line,
        }
        for rule in project.security_rules
    ]

    return {
        "policy": {"objectAccess": OBJECT_ACCESS_POLICY},
        "securityRules": ordered_rules,
        "nodes": sorted(nodes.values(), key=lambda item: (item["kind"], item["id"])),
        "edges": edges,
        "unresolved": unresolved,
        "summary": {
            "nodes": len(nodes),
            "edges": len(edges),
            "routes": len(project.routes),
            "rules": len(project.security_rules),
            "established": sum(1 for edge in edges if edge["status"] == "established"),
            "unknown": sum(1 for edge in edges if edge["status"] == "unknown") + len(unresolved),
        },
    }
