from __future__ import annotations

import copy
from typing import Any

from demo_mcp_suite.runtime import DemoMcpServer, object_schema, text_result, tool

_INITIAL_PLACES: dict[str, dict[str, Any]] = {
    "sensoji-temple": {
        "id": "sensoji-temple",
        "name": "Sensō-ji Temple",
        "city": "Tokyo",
        "country": "Japan",
        "neighborhood": "Asakusa",
        "tags": ["culture", "history", "temple", "first-time"],
        "duration_hours": 2.0,
        "cost_level": "free",
        "best_time": "morning",
        "access_notes": "Step-free routes are available around the main approach.",
    },
    "kappabashi-street": {
        "id": "kappabashi-street",
        "name": "Kappabashi Kitchen Street",
        "city": "Tokyo",
        "country": "Japan",
        "neighborhood": "Taito",
        "tags": ["shopping", "food", "design", "rainy-day"],
        "duration_hours": 1.5,
        "cost_level": "low",
        "best_time": "afternoon",
        "access_notes": "Flat sidewalks; several small shops have narrow entrances.",
    },
    "kiyosumi-garden": {
        "id": "kiyosumi-garden",
        "name": "Kiyosumi Garden",
        "city": "Tokyo",
        "country": "Japan",
        "neighborhood": "Koto",
        "tags": ["garden", "quiet", "walk", "photography"],
        "duration_hours": 1.5,
        "cost_level": "low",
        "best_time": "late afternoon",
        "access_notes": "Mostly flat garden paths with some uneven stone areas.",
    },
    "teamlab-borderless": {
        "id": "teamlab-borderless",
        "name": "teamLab Borderless",
        "city": "Tokyo",
        "country": "Japan",
        "neighborhood": "Azabudai Hills",
        "tags": ["art", "immersive", "ticketed", "rainy-day"],
        "duration_hours": 2.5,
        "cost_level": "high",
        "best_time": "weekday morning",
        "access_notes": "Timed ticket recommended in the fixture itinerary.",
    },
    "yanaka-ginza": {
        "id": "yanaka-ginza",
        "name": "Yanaka Ginza",
        "city": "Tokyo",
        "country": "Japan",
        "neighborhood": "Yanaka",
        "tags": ["walk", "local", "snacks", "retro"],
        "duration_hours": 2.0,
        "cost_level": "low",
        "best_time": "late afternoon",
        "access_notes": "Gentle hills and stairs nearby; choose Nippori approach for easier access.",
    },
    "hongdae-record-shops": {
        "id": "hongdae-record-shops",
        "name": "Hongdae Record Shops",
        "city": "Seoul",
        "country": "South Korea",
        "neighborhood": "Hongdae",
        "tags": ["music", "shopping", "nightlife", "local"],
        "duration_hours": 2.0,
        "cost_level": "medium",
        "best_time": "evening",
        "access_notes": "Subway access is direct; some basement shops use stairs.",
    },
    "seoul-forest": {
        "id": "seoul-forest",
        "name": "Seoul Forest",
        "city": "Seoul",
        "country": "South Korea",
        "neighborhood": "Seongsu",
        "tags": ["park", "walk", "coffee", "family"],
        "duration_hours": 2.0,
        "cost_level": "free",
        "best_time": "morning",
        "access_notes": "Wide paths and nearby cafes make this a low-friction stop.",
    },
}

_INITIAL_ITINERARIES: dict[str, dict[str, Any]] = {
    "tokyo-spring-2026": {
        "id": "tokyo-spring-2026",
        "title": "Tokyo Spring Demo Trip",
        "destination": "Tokyo, Japan",
        "dates": {"start": "2026-06-08", "end": "2026-06-12"},
        "traveler_profile": "solo traveler, moderate budget, transit-first",
        "places": [
            {
                "place_id": "sensoji-temple",
                "day": 1,
                "note": "Start early before the Nakamise-dori crowds.",
                "status": "planned",
            },
            {
                "place_id": "kiyosumi-garden",
                "day": 2,
                "note": "Pair with a quiet coffee stop in Kiyosumi-Shirakawa.",
                "status": "planned",
            },
            {
                "place_id": "teamlab-borderless",
                "day": 3,
                "note": "Hold this slot until the timed ticket is confirmed.",
                "status": "hold",
            },
        ],
    },
    "seoul-weekend-2026": {
        "id": "seoul-weekend-2026",
        "title": "Seoul Weekend Demo Trip",
        "destination": "Seoul, South Korea",
        "dates": {"start": "2026-07-18", "end": "2026-07-20"},
        "traveler_profile": "two friends, cafes, records, easy transit",
        "places": [
            {
                "place_id": "seoul-forest",
                "day": 1,
                "note": "Morning walk before Seongsu cafes get busy.",
                "status": "planned",
            },
            {
                "place_id": "hongdae-record-shops",
                "day": 2,
                "note": "Browse after dinner; keep luggage light.",
                "status": "planned",
            },
        ],
    },
}

_places: dict[str, dict[str, Any]] = {}
_itineraries: dict[str, dict[str, Any]] = {}
_change_sequence = 1


def _reset_state() -> None:
    global _places, _itineraries, _change_sequence
    _places = copy.deepcopy(_INITIAL_PLACES)
    _itineraries = copy.deepcopy(_INITIAL_ITINERARIES)
    _change_sequence = 1


def _error_result(message: str, **structured: Any) -> dict[str, Any]:
    payload = {"error": message, **structured}
    result = text_result(message, payload)
    result["isError"] = True
    return result


def _required_text(arguments: dict[str, Any], name: str) -> str | None:
    value = arguments.get(name)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _optional_limit(arguments: dict[str, Any], *, default: int, minimum: int = 1, maximum: int = 25) -> int:
    value = arguments.get("limit", default)
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return min(max(value, minimum), maximum)
    return default


def _optional_bool(arguments: dict[str, Any], name: str, default: bool) -> bool:
    value = arguments.get(name, default)
    if isinstance(value, bool):
        return value
    return default


def _optional_day(arguments: dict[str, Any], itinerary: dict[str, Any]) -> int:
    value = arguments.get("day")
    if isinstance(value, bool):
        value = None
    if isinstance(value, int) and value > 0:
        return value
    existing_days = [item["day"] for item in itinerary["places"] if isinstance(item.get("day"), int)]
    return max(existing_days, default=0) + 1


def _place_matches(place: dict[str, Any], query: str, city: str | None, tag: str | None) -> bool:
    if city and place["city"].lower() != city.lower():
        return False
    if tag and tag.lower() not in {item.lower() for item in place["tags"]}:
        return False
    if not query:
        return True
    searchable = " ".join(
        [
            place["name"],
            place["city"],
            place["country"],
            place["neighborhood"],
            " ".join(place["tags"]),
            place["best_time"],
        ]
    ).lower()
    return query.lower() in searchable


def _expanded_stop(stop: dict[str, Any]) -> dict[str, Any]:
    place = _places.get(stop["place_id"])
    return {
        **copy.deepcopy(stop),
        "place": copy.deepcopy(place) if place else None,
    }


def _public_itinerary(itinerary: dict[str, Any], *, include_places: bool) -> dict[str, Any]:
    item = copy.deepcopy(itinerary)
    stops = item.pop("places")
    item["place_count"] = len(stops)
    item["days"] = sorted({stop["day"] for stop in stops})
    if include_places:
        item["places"] = [_expanded_stop(stop) for stop in sorted(stops, key=lambda entry: (entry["day"], entry["place_id"]))]
    return item


def _itinerary_list(arguments: dict[str, Any]) -> dict[str, Any]:
    include_places = _optional_bool(arguments, "include_places", False)
    destination = _required_text(arguments, "destination")
    itineraries = list(_itineraries.values())
    if destination:
        itineraries = [item for item in itineraries if destination.lower() in item["destination"].lower()]
    items = [_public_itinerary(item, include_places=include_places) for item in itineraries]
    return text_result(
        f"Returned {len(items)} itinerary fixture(s).",
        {"itineraries": items, "total": len(items)},
    )


def _place_search(arguments: dict[str, Any]) -> dict[str, Any]:
    query = _required_text(arguments, "query") or ""
    city = _required_text(arguments, "city")
    tag = _required_text(arguments, "tag")
    limit = _optional_limit(arguments, default=10)
    matches = [
        copy.deepcopy(place)
        for place in _places.values()
        if _place_matches(place, query=query, city=city, tag=tag)
    ][:limit]
    return text_result(
        f"Found {len(matches)} place(s) in the local travel fixture.",
        {
            "places": matches,
            "query": {"query": query or None, "city": city, "tag": tag, "limit": limit},
        },
    )


def _itinerary_add_place(arguments: dict[str, Any]) -> dict[str, Any]:
    global _change_sequence
    itinerary_id = _required_text(arguments, "itinerary_id")
    place_id = _required_text(arguments, "place_id")
    if itinerary_id is None:
        return _error_result("itinerary_id is required", required=["itinerary_id", "place_id"])
    if place_id is None:
        return _error_result("place_id is required", required=["itinerary_id", "place_id"])
    itinerary = _itineraries.get(itinerary_id)
    if itinerary is None:
        return _error_result("Unknown itinerary", itinerary_id=itinerary_id, known_itinerary_ids=sorted(_itineraries))
    if place_id not in _places:
        return _error_result("Unknown place", place_id=place_id, known_place_ids=sorted(_places))
    if any(stop["place_id"] == place_id for stop in itinerary["places"]):
        return _error_result("Place is already on this itinerary", itinerary_id=itinerary_id, place_id=place_id)

    note = _required_text(arguments, "note") or "Added from local demo place catalog."
    stop = {
        "place_id": place_id,
        "day": _optional_day(arguments, itinerary),
        "note": note,
        "status": "planned",
        "added_by": "demo-mcp-suite",
        "change_id": f"travel-change-{_change_sequence:03d}",
    }
    _change_sequence += 1
    itinerary["places"].append(stop)
    return text_result(
        f"Added {_places[place_id]['name']} to {itinerary['title']}.",
        {
            "itinerary": _public_itinerary(itinerary, include_places=True),
            "added_stop": _expanded_stop(stop),
        },
    )


def _itinerary_remove_place(arguments: dict[str, Any]) -> dict[str, Any]:
    itinerary_id = _required_text(arguments, "itinerary_id")
    place_id = _required_text(arguments, "place_id")
    if itinerary_id is None:
        return _error_result("itinerary_id is required", required=["itinerary_id", "place_id"])
    if place_id is None:
        return _error_result("place_id is required", required=["itinerary_id", "place_id"])
    itinerary = _itineraries.get(itinerary_id)
    if itinerary is None:
        return _error_result("Unknown itinerary", itinerary_id=itinerary_id, known_itinerary_ids=sorted(_itineraries))

    for index, stop in enumerate(itinerary["places"]):
        if stop["place_id"] == place_id:
            removed = itinerary["places"].pop(index)
            return text_result(
                f"Removed {_places.get(place_id, {'name': place_id})['name']} from {itinerary['title']}.",
                {
                    "itinerary": _public_itinerary(itinerary, include_places=True),
                    "removed_stop": _expanded_stop(removed),
                    "destructive_change": True,
                },
            )
    return _error_result("Place is not on this itinerary", itinerary_id=itinerary_id, place_id=place_id)


_TOOLS = [
    tool(
        name="itinerary_list",
        title="List travel itineraries",
        description="Return local demo itineraries, optionally expanded with planned places.",
        input_schema=object_schema(
            {
                "destination": {
                    "type": "string",
                    "description": "Optional case-insensitive destination filter.",
                },
                "include_places": {
                    "type": "boolean",
                    "description": "Include expanded place details for each itinerary stop.",
                    "default": False,
                },
            }
        ),
        read_only=True,
    ),
    tool(
        name="place_search",
        title="Search places",
        description="Search the in-memory place catalog by text, city, and tag.",
        input_schema=object_schema(
            {
                "query": {
                    "type": "string",
                    "description": "Case-insensitive text search across place metadata.",
                },
                "city": {
                    "type": "string",
                    "description": "Optional exact city filter, case-insensitive.",
                },
                "tag": {
                    "type": "string",
                    "description": "Optional exact tag filter, case-insensitive.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum places to return; clamped to 1..25.",
                    "minimum": 1,
                    "maximum": 25,
                    "default": 10,
                },
            }
        ),
        read_only=True,
    ),
    tool(
        name="itinerary_add_place",
        title="Add place to itinerary",
        description="Add a catalog place to a local fixture itinerary.",
        input_schema=object_schema(
            {
                "itinerary_id": {
                    "type": "string",
                    "description": "Fixture itinerary id.",
                    "enum": sorted(_INITIAL_ITINERARIES),
                },
                "place_id": {
                    "type": "string",
                    "description": "Fixture place id.",
                    "enum": sorted(_INITIAL_PLACES),
                },
                "day": {
                    "type": "integer",
                    "description": "Positive trip day number; defaults to the next open day.",
                    "minimum": 1,
                },
                "note": {
                    "type": "string",
                    "description": "Optional planning note for this stop.",
                },
            },
            required=["itinerary_id", "place_id"],
        ),
        read_only=False,
        idempotent=False,
    ),
    tool(
        name="itinerary_remove_place",
        title="Remove place from itinerary",
        description="Remove a planned place from a local fixture itinerary.",
        input_schema=object_schema(
            {
                "itinerary_id": {
                    "type": "string",
                    "description": "Fixture itinerary id.",
                    "enum": sorted(_INITIAL_ITINERARIES),
                },
                "place_id": {
                    "type": "string",
                    "description": "Fixture place id currently on the itinerary.",
                    "enum": sorted(_INITIAL_PLACES),
                },
            },
            required=["itinerary_id", "place_id"],
        ),
        read_only=False,
        destructive=True,
        idempotent=False,
    ),
]

_HANDLERS = {
    "itinerary_list": _itinerary_list,
    "place_search": _place_search,
    "itinerary_add_place": _itinerary_add_place,
    "itinerary_remove_place": _itinerary_remove_place,
}

_reset_state()

SERVER = DemoMcpServer(
    slug="travel-planner",
    service_slug="demo_travel",
    title="Travel Planner MCP",
    description="가상의 여행 플래너 MCP",
    tools=_TOOLS,
    handlers=_HANDLERS,
    reset=_reset_state,
)
