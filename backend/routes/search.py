from datetime import datetime

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt, get_jwt_identity, jwt_required

from app import SEARCH_ROUTE_PRESETS, _build_live_route_estimate, _build_route_catalog, _match_route_suggestions
from utils.blueprints import bind_route


search_bp = Blueprint("search_bp", __name__)


def _build_curated_routes(limit):
    curated_routes = []
    for item in SEARCH_ROUTE_PRESETS:
        pickup_location = str(item.get("pickup_location") or "").strip()
        drop_location = str(item.get("drop_location") or "").strip()
        if not pickup_location or not drop_location:
            continue

        curated_routes.append(
            {
                "label": f"{pickup_location} → {drop_location}",
                "value": f"{pickup_location} → {drop_location}",
                "kind": "featured_route",
                "route_key": f"{pickup_location.lower()}||{drop_location.lower()}",
                "pickup_location": pickup_location,
                "drop_location": drop_location,
                "truck_type": "14 tyre",
                "estimated_price": None,
                "eta_hours": None,
                "count": 0,
                "source": "curated",
                "last_seen": "",
            }
        )

    return curated_routes[:limit]


def _matches_query(item, query_text):
    if not query_text:
        return True

    haystack = " ".join(
        [
            str(item.get("label") or ""),
            str(item.get("value") or ""),
            str(item.get("pickup_location") or ""),
            str(item.get("drop_location") or ""),
            str(item.get("route_key") or ""),
        ]
    ).lower()
    return query_text.lower() in haystack


def _get_search_identity_context():
    try:
        identity = get_jwt_identity()
    except Exception:
        identity = None

    try:
        claims = get_jwt()
    except Exception:
        claims = {}

    phone = str(claims.get("phone") or claims.get("contact") or "").strip() or None
    return identity, phone


def _build_search_sections(catalog, limit):
    history_rows = sorted(catalog.get("history_rows") or [], key=lambda item: item.get("created_at_raw") or datetime.min, reverse=True)
    recent_searches = []
    recent_route_keys = set()

    for item in history_rows:
      route_key = item.get("route_key")
      if not route_key or route_key in recent_route_keys:
          continue
      recent_route_keys.add(route_key)
      recent_searches.append({
          "label": item.get("route_label") or "",
          "value": item.get("route_label") or "",
          "kind": "recent_search",
          "route_key": route_key,
          "pickup_location": item.get("pickup_location") or "",
          "drop_location": item.get("drop_location") or "",
          "truck_type": item.get("truck_type") or "12 tyre",
          "estimated_price": item.get("estimated_price"),
          "eta_hours": item.get("eta_hours"),
          "count": 1,
          "source": item.get("source") or "history",
          "last_seen": item.get("created_at") or "",
      })
      if len(recent_searches) >= limit:
          break

    route_cards = catalog.get("route_cards") or []
    popular_routes = [
        {
            "label": item.get("route_label") or "",
            "value": item.get("route_label") or "",
            "kind": "popular_route",
            "route_key": item.get("route_key") or "",
            "pickup_location": item.get("pickup_location") or "",
            "drop_location": item.get("drop_location") or "",
            "truck_type": item.get("suggested_truck_type") or "12 tyre",
            "estimated_price": item.get("estimated_price"),
            "eta_hours": item.get("eta_hours"),
            "count": item.get("count") or 0,
            "source": item.get("source") or "history",
            "last_seen": item.get("last_seen") or "",
        }
        for item in route_cards[:limit]
    ]

    curated_routes = _build_curated_routes(limit)
    for item in curated_routes:
        if item.get("route_key") not in {route.get("route_key") for route in popular_routes}:
            popular_routes.append(item)

    frequently_booked_routes = [item for item in popular_routes if int(item.get("count") or 0) >= 2][:limit]

    return {
        "recent_searches": recent_searches,
        "popular_routes": popular_routes,
        "frequently_booked_routes": frequently_booked_routes,
    }


def search_suggestions():
    args = request.args
    field = str(args.get("field") or "pickup").strip().lower() or "pickup"
    query_text = str(args.get("q") or "").strip()
    limit = max(3, min(int(args.get("limit") or 8), 20))
    pickup_location = args.get("pickup_location") or args.get("pickupLocation")
    drop_location = args.get("drop_location") or args.get("dropLocation")
    load_weight = args.get("load_weight") or args.get("loadWeight")
    truck_type = args.get("truck_type") or args.get("truckType")
    identity, phone = _get_search_identity_context()

    catalog = _build_route_catalog(user_identifier=identity, phone=phone, limit=max(limit * 4, 40))
    sections = _build_search_sections(catalog, limit)
    suggestions = _match_route_suggestions(catalog, query_text, field, limit=limit)

    if not query_text:
        if field == "drop":
            suggestions = (catalog.get("drop_locations") or [])[:limit]
        else:
            suggestions = (catalog.get("pickup_locations") or [])[:limit]

        for curated_route in _build_curated_routes(limit):
            if not any((item.get("route_key") or "") == curated_route["route_key"] for item in suggestions):
                suggestions.append(curated_route)

        suggestions = suggestions[:limit]
    else:
        for curated_route in _build_curated_routes(limit):
            if _matches_query(curated_route, query_text) and not any((item.get("route_key") or "") == curated_route["route_key"] for item in suggestions):
                suggestions.append(curated_route)

        suggestions.sort(key=lambda item: (item.get("kind") != "featured_route", item.get("label") or item.get("value") or ""))
        suggestions = suggestions[:limit]

    payload = {
        "status": "success",
        "field": field,
        "query": query_text,
        "suggestions": suggestions,
        "pickup_suggestions": (catalog.get("pickup_locations") or [])[:limit],
        "drop_suggestions": (catalog.get("drop_locations") or [])[:limit],
        "recent_searches": sections["recent_searches"],
        "popular_routes": sections["popular_routes"],
        "frequently_booked_routes": sections["frequently_booked_routes"],
        "personalized_suggestions": sections["recent_searches"][:limit] if identity else [],
    }

    live_estimate = _build_live_route_estimate(pickup_location, drop_location, load_weight, truck_type)
    if live_estimate:
        payload["live_estimate"] = live_estimate

    return jsonify(payload)


def search_routes():
    args = request.args
    pickup_location = args.get("pickup_location") or args.get("pickupLocation")
    drop_location = args.get("drop_location") or args.get("dropLocation")
    load_weight = args.get("load_weight") or args.get("loadWeight")
    truck_type = args.get("truck_type") or args.get("truckType")
    limit = max(3, min(int(args.get("limit") or 8), 20))
    identity, phone = _get_search_identity_context()

    catalog = _build_route_catalog(user_identifier=identity, phone=phone, limit=max(limit * 4, 40))
    sections = _build_search_sections(catalog, limit)
    live_estimate = _build_live_route_estimate(pickup_location, drop_location, load_weight, truck_type)

    route_cards = []
    for item in catalog.get("route_cards") or []:
        route_cards.append(
            {
                "label": item.get("route_label") or "",
                "value": item.get("route_label") or "",
                "kind": "route",
                "route_key": item.get("route_key") or "",
                "pickup_location": item.get("pickup_location") or "",
                "drop_location": item.get("drop_location") or "",
                "truck_type": item.get("suggested_truck_type") or "12 tyre",
                "estimated_price": item.get("estimated_price"),
                "eta_hours": item.get("eta_hours"),
                "count": item.get("count") or 0,
                "source": item.get("source") or "history",
                "last_seen": item.get("last_seen") or "",
            }
        )

    response = {
        "status": "success",
        "query": {
            "pickup_location": str(pickup_location or "").strip(),
            "drop_location": str(drop_location or "").strip(),
            "load_weight": load_weight,
            "truck_type": truck_type,
        },
        "routes": route_cards[:limit],
        "recent_searches": sections["recent_searches"],
        "popular_routes": sections["popular_routes"],
        "frequently_booked_routes": sections["frequently_booked_routes"],
        "personalized_suggestions": sections["recent_searches"][:limit] if identity else [],
        "pickup_suggestions": (catalog.get("pickup_locations") or [])[:limit],
        "drop_suggestions": (catalog.get("drop_locations") or [])[:limit],
    }

    for curated_route in _build_curated_routes(limit):
        if not any((item.get("route_key") or "") == curated_route["route_key"] for item in response["routes"]):
            response["routes"].append(curated_route)

    response["routes"] = response["routes"][:limit]

    if live_estimate:
        response["live_estimate"] = live_estimate

    return jsonify(response)


bind_route(search_bp, "/api/search/suggestions", search_suggestions, "api_search_suggestions", ["GET"], [jwt_required(optional=True)])
bind_route(search_bp, "/api/search/routes", search_routes, "api_search_routes", ["GET"], [jwt_required(optional=True)])
