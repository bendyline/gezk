"""Portable gezk 0.7 point validation and sphere-6371000 radius semantics."""
import math
import unicodedata

EARTH_RADIUS_METERS = 6_371_000

def longitude(value):
    return -180.0 if value == 180 else 0.0 if value == 0 else value

def validate_radius(radius):
    for name, low, high in (("latitude", -90, 90), ("longitude", -180, 180), ("radiusMeters", 0, float("inf"))):
        value = radius.get(name)
        if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError(f"invalid {name}")
    return radius

def validate_location(point):
    validate_radius({**point, "radiusMeters": 0})
    ident = point.get("id")
    if not isinstance(ident, str) or not ident or len(ident) > 256 or ident != ident.strip() or ident != unicodedata.normalize("NFC", ident) or any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in ident):
        raise ValueError("invalid location id")
    if point.get("role") not in ("subject", "associated"):
        raise ValueError("invalid location role")
    if "provenance" in point and not isinstance(point["provenance"], dict):
        raise ValueError("location provenance must be an object")
    return point

def distance_meters(a, b):
    if a["latitude"] == b["latitude"] and (abs(a["latitude"]) == 90 or longitude(a["longitude"]) == longitude(b["longitude"])):
        return 0.0
    lat = math.radians(b["latitude"] - a["latitude"])
    lon = math.radians(longitude(b["longitude"]) - longitude(a["longitude"]))
    h = math.sin(lat / 2) ** 2 + math.cos(math.radians(a["latitude"])) * math.cos(math.radians(b["latitude"])) * math.sin(lon / 2) ** 2
    h = min(1.0, max(0.0, h))
    return 2 * EARTH_RADIUS_METERS * math.atan2(math.sqrt(h), math.sqrt(1 - h))

def radius_bounds(radius):
    validate_radius(radius)
    if radius["radiusMeters"] == 0:
        lon = longitude(radius["longitude"])
        pole = abs(radius["latitude"]) == 90
        return [{"west": -180 if pole else lon, "east": 180 if pole else lon, "south": radius["latitude"], "north": radius["latitude"]}]
    delta = min(math.pi, radius["radiusMeters"] / EARTH_RADIUS_METERS)
    lat = math.radians(radius["latitude"])
    padding = 1e-10 if radius["radiusMeters"] > 0 else 0
    south, north = max(-90, math.degrees(lat - delta) - padding), min(90, math.degrees(lat + delta) + padding)
    def box(west, east): return {"west": west, "east": east, "south": south, "north": north}
    if south <= -90 or north >= 90: return [box(-180, 180)]
    width = math.degrees(math.asin(min(1, math.sin(delta) / math.cos(lat)))) + padding
    west, east = longitude(radius["longitude"]) - width, longitude(radius["longitude"]) + width
    if west < -180: return [box(west + 360, 180), box(-180, east)]
    if east >= 180: return [box(west, 180), box(-180, east - 360)]
    return [box(west, east)]

def spatial_manifest(rows):
    subjects = [point for _, point in rows if point["role"] == "subject"]
    coverage = []
    if subjects:
        longs = sorted(set(longitude(p["longitude"]) for p in subjects))
        gaps = [(longs[(i + 1) % len(longs)] + (360 if i == len(longs) - 1 else 0) - v, (i + 1) % len(longs)) for i, v in enumerate(longs)]
        start = max(range(len(gaps)), key=lambda i: gaps[i][0])
        index = gaps[start][1]
        west, east = longs[index], longs[(index - 1) % len(longs)]
        south, north = min(p["latitude"] for p in subjects), max(p["latitude"] for p in subjects)
        def box(w, e): return {"west": w, "east": e, "south": south, "north": north}
        coverage = [box(west, east)] if west <= east else [box(west, 180), box(-180, east)]
    return {"schema": "document-points@1", "crs": "EPSG:4326", "distanceModel": "sphere-6371000", "locationCount": len(rows), "locatedDocuments": len(set(doc for doc, _ in rows)), "coverage": coverage}
