import inspect


def unwrap_view(view_func):
    return inspect.unwrap(view_func)


def bind_route(blueprint, rule, view_func, endpoint, methods, decorators=None):
    wrapped = unwrap_view(view_func)
    for decorator in decorators or []:
        wrapped = decorator(wrapped)

    blueprint.add_url_rule(rule, endpoint=endpoint, view_func=wrapped, methods=list(methods))


def bind_routes(blueprint, route_specs):
    for spec in route_specs:
        bind_route(
            blueprint,
            spec["rule"],
            spec["view_func"],
            spec["endpoint"],
            spec.get("methods", ["GET"]),
            spec.get("decorators"),
        )
