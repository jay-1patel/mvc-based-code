"""Deep merge for tenant profile layers.

Rules, in order of importance:
  * dicts merge key-by-key
  * ``keyed`` lists of named objects merge BY NAME, not by position — a tenant
    that ships one flow replaces that flow and keeps every other default flow
    (flows, intents, menu buttons)
  * other lists REPLACE, never append
  * scalars and ``None`` on the right-hand side never clobber the left

The left layer is always lower precedence (vertical defaults), the right layer
is higher (file, then DB).

Why keyed merging for named objects: "lists replace" is right for a list of
phrases but wrong for a list of definitions. If a tenant publishes a single
changed flow, wholesale replacement would silently delete the other five
default flows. Names give us per-item overrides plus a ``__remove__`` escape
hatch for genuinely deleting a default:

    {"flows": [{"name": "get_quote", "success_message": "New text"}]}
    {"intents": [{"name": "place_order", "__remove__": true}]}
    {"menu": {"buttons": [{"id": "menu_view_cart", "__remove__": true}]}}
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping

# path (tuple of keys) -> the field that names each item
KEYED_PATHS: Dict[tuple, str] = {
    ("flows",): "name",
    ("intents",): "name",
    ("menu", "buttons"): "id",
}

REMOVE_KEY = "__remove__"


def deep_merge(base: Any, override: Any, path: tuple = ()) -> Any:
    """Merge ``override`` onto ``base``. Returns a new structure."""
    if isinstance(base, Mapping) and isinstance(override, Mapping):
        out: Dict[str, Any] = dict(base)
        for key, val in override.items():
            child_path = path + (key,)
            if key in out:
                out[key] = deep_merge(out[key], val, child_path)
            else:
                out[key] = _copy(val, child_path, draft=False)
        return out
    if isinstance(base, list) and isinstance(override, list):
        key_field = KEYED_PATHS.get(path)
        if key_field and _is_named(base, key_field, path) and _is_named(override, key_field, path):
            return _merge_named(base, override, key_field, path)
        # Lists replace wholesale.
        return _copy(override, path, draft=False)
    if override is None:
        return _copy(base, path)
    return _copy(override, path, draft=False)


def _is_named(items: List[Any], key_field: str, path: tuple = ()) -> bool:
    if not items:
        return True
    # Menu buttons may arrive without an id (older LLM bifurcator writes). They
    # still carry a title, so treat them as keyed — _canonicalize_keyed_item
    # synthesizes the id — instead of letting the list replace defaults
    # wholesale (which both duplicated and then invalidated the whole profile).
    menu_titles = path == ("menu", "buttons")
    for i in items:
        if not isinstance(i, Mapping):
            return False
        if key_field in i:
            continue
        if menu_titles and str(i.get("title") or "").strip():
            continue
        return False
    return True


def _normalize_keyed_name(name: str, path: tuple, key_field: str) -> str:
    """Match keyed-list names. Menu button ids equate hyphen and underscore.

    PDF/kit slugify historically emitted ``menu-products`` while vertical
    defaults use ``menu_products``. Without normalizing, both survive merge
    and the menu editor / WhatsApp list show Cards, Loans, etc. twice.
    """
    raw = str(name or "").strip()
    if path == ("menu", "buttons") and key_field == "id":
        return re.sub(r"[-_\s]+", "_", raw.casefold()).strip("_")
    return raw


def _canonicalize_keyed_item(item: Mapping, path: tuple, key_field: str) -> Dict[str, Any]:
    """Rewrite menu button ids to underscore form so hyphen clones cannot stick.

    A button with no id at all gets one synthesized from its title, so it can
    still merge/dedupe by key instead of breaking the keyed merge entirely.
    """
    out = dict(item)
    if path == ("menu", "buttons") and key_field == "id":
        raw = str(out.get("id") or "").strip()
        if not raw:
            raw = str(out.get("title") or "").strip()
        canon = re.sub(r"[-_\s]+", "_", raw.lower()).strip("_")
        if not canon:
            canon = re.sub(r"[^a-z0-9]+", "_", raw.lower()).strip("_")
        if canon:
            out["id"] = canon
    return out


def _menu_title_key(item: Mapping) -> str:
    """Normalized menu title used to collapse PDF clones of vertical defaults."""
    return re.sub(
        r"[-_\s]+",
        "_",
        str(item.get("title") or "").strip().casefold(),
    ).strip("_")


def _merge_named(base: List[Any], override: List[Any], key_field: str, path: tuple) -> List[Any]:
    """Per-item override by name. Base order is preserved, new items appended."""
    merged: List[Any] = []
    index: Dict[str, int] = {}
    title_index: Dict[str, int] = {}
    match_titles = path == ("menu", "buttons") and key_field == "id"

    def _remember(name: str, title_key: str, pos: int) -> None:
        index[name] = pos
        if match_titles and title_key:
            title_index[title_key] = pos

    def _lookup(name: str, title_key: str) -> int | None:
        if name in index:
            return index[name]
        if match_titles and title_key and title_key in title_index:
            return title_index[title_key]
        return None

    for item in base:
        if item.get(REMOVE_KEY):
            continue
        cleaned = _canonicalize_keyed_item(_strip_remove(item), path, key_field)
        name = _normalize_keyed_name(str(cleaned.get(key_field, "")), path, key_field)
        title_key = _menu_title_key(cleaned) if match_titles else ""
        if _lookup(name, title_key) is not None:
            # Same layer listed menu_products and menu-products / same title twice.
            continue
        _remember(name, title_key, len(merged))
        merged.append(cleaned)

    for item in override:
        title_key = _menu_title_key(item) if match_titles else ""
        if item.get(REMOVE_KEY):
            name = _normalize_keyed_name(str(item.get(key_field, "")), path, key_field)
            pos = _lookup(name, title_key)
            if pos is not None:
                merged[pos] = None  # tombstone
            continue
        cleaned = _canonicalize_keyed_item(_strip_remove(item), path, key_field)
        # Name from the canonicalized item: an id-less button gets its
        # synthesized id here, so two such buttons never collide on "".
        name = _normalize_keyed_name(str(cleaned.get(key_field, "")), path, key_field)
        title_key = _menu_title_key(cleaned) if match_titles else title_key
        pos = _lookup(name, title_key)
        if pos is not None:
            current = merged[pos]
            if current:
                combined = deep_merge(current, cleaned, path)
                # Keep the surviving slot's id (defaults win over PDF slug clones).
                combined[key_field] = current.get(key_field) or cleaned.get(key_field)
                merged[pos] = combined
                _remember(
                    _normalize_keyed_name(str(combined.get(key_field, "")), path, key_field),
                    _menu_title_key(combined) if match_titles else "",
                    pos,
                )
            else:
                merged[pos] = cleaned
                _remember(name, title_key, pos)
        else:
            _remember(name, title_key, len(merged))
            merged.append(cleaned)

    return [i for i in merged if i is not None]


def _strip_remove(item: Mapping) -> Dict[str, Any]:
    """Drop the __remove__ marker so it never reaches pydantic validation."""
    return {k: v for k, v in item.items() if k != REMOVE_KEY}


def merge_layers(*layers: Any) -> Dict[str, Any]:
    """Left-to-right merge of profile layers, lowest precedence first."""
    result: Dict[str, Any] = {}
    for layer in layers:
        if not layer:
            continue
        if not isinstance(layer, Mapping):
            raise TypeError(f"profile layer must be a mapping, got {type(layer).__name__}")
        result = deep_merge(result, layer, ())
    return result


def merge_drafts(base: Any, override: Any, path: tuple = ()) -> Any:
    """Merge a new draft save over the pending draft, KEEPING ``__remove__`` markers.

    The draft is a partial layer: keyed lists (intents, flows, menu buttons)
    override lower layers by name, and a deletion is a tombstone that only the
    final live merge (``deep_merge`` in build_profile) may consume. Using
    ``deep_merge`` here would consume the tombstone the moment it is saved —
    the marker would never reach the published layer, and the deleted default
    would resurrect on publish. So this variant keeps markers in the result,
    drops the matching pending item, and lets a re-added id replace its marker.
    """
    if isinstance(base, Mapping) and isinstance(override, Mapping):
        out: Dict[str, Any] = dict(base)
        for key, val in override.items():
            child_path = path + (key,)
            if key in out:
                out[key] = merge_drafts(out[key], val, child_path)
            else:
                out[key] = _copy(val, child_path, draft=True)
        return out
    if isinstance(base, list) and isinstance(override, list):
        key_field = KEYED_PATHS.get(path)
        if key_field and _is_named(base, key_field) and _is_named(override, key_field):
            return _merge_named_drafts(base, override, key_field, path)
        # Unkeyed lists still replace wholesale.
        return _copy(override, path, draft=True)
    if override is None:
        return _copy(base)
    return _copy(override, path, draft=True)


def _merge_named_drafts(base: List[Any], override: List[Any], key_field: str, path: tuple) -> List[Any]:
    """Keyed merge for the draft layer, with tombstone preservation."""
    out: List[Any] = []
    index: Dict[str, int] = {}
    title_index: Dict[str, int] = {}
    match_titles = path == ("menu", "buttons") and key_field == "id"

    def _remember(name: str, title_key: str, pos: int) -> None:
        index[name] = pos
        if match_titles and title_key:
            title_index[title_key] = pos

    def _lookup(name: str, title_key: str):
        if name in index:
            return index[name]
        if match_titles and title_key and title_key in title_index:
            return title_index[title_key]
        return None

    for item in base:
        cleaned = _canonicalize_keyed_item(dict(item), path, key_field)
        name = _normalize_keyed_name(str(cleaned.get(key_field, "")), path, key_field)
        title_key = _menu_title_key(cleaned) if match_titles else ""
        if _lookup(name, title_key) is not None:
            continue
        _remember(name, title_key, len(out))
        out.append(cleaned)

    for item in override:
        cleaned = _canonicalize_keyed_item(dict(item), path, key_field)
        name = _normalize_keyed_name(str(cleaned.get(key_field, "")), path, key_field)
        title_key = _menu_title_key(cleaned) if match_titles else ""
        marker = bool(cleaned.get(REMOVE_KEY))
        pos = _lookup(name, title_key)
        if pos is not None:
            current = out[pos]
            # A marker replaces the pending item; re-adding a tombstoned id
            # replaces the marker. Neither case can deep-merge the two.
            if marker or current.get(REMOVE_KEY):
                out[pos] = cleaned
            else:
                combined = merge_drafts(current, cleaned, path)
                combined[key_field] = current.get(key_field) or cleaned.get(key_field)
                out[pos] = combined
            _remember(
                _normalize_keyed_name(str(out[pos].get(key_field, "")), path, key_field),
                _menu_title_key(out[pos]) if match_titles else "",
                pos,
            )
        else:
            # A tombstone for a name the draft never carried must persist too —
            # it is what deletes the *default* at publish time.
            _remember(name, title_key, len(out))
            out.append(cleaned)
    return out


def prune_nulls(payload: Mapping[str, Any]) -> Dict[str, Any]:
    """Drop ``None`` values so an override can't blank out a default by omission."""
    out: Dict[str, Any] = {}
    for key, val in payload.items():
        if val is None:
            continue
        if isinstance(val, Mapping):
            out[key] = prune_nulls(val)
        elif isinstance(val, list):
            out[key] = [prune_nulls(v) if isinstance(v, Mapping) else v for v in val]
        else:
            out[key] = val
    return out


def _copy(value: Any, path: tuple = (), *, draft: bool = False) -> Any:
    """Deep-copy a layer value.

    Keyed ``menu.buttons`` lists are canonicalized (hyphen → underscore) and
    de-duplicated even when the list is assigned fresh rather than merged onto
    an existing base — otherwise a PDF draft can ship both ``menu_cards`` and
    ``menu-cards`` and both survive into the editor.
    """
    if isinstance(value, Mapping):
        return {k: _copy(v, path + (k,), draft=draft) for k, v in value.items()}
    if isinstance(value, list):
        items = [_copy(v, path, draft=draft) for v in value]
        key_field = KEYED_PATHS.get(path)
        if key_field and _is_named(items, key_field, path):
            if draft:
                return _merge_named_drafts([], items, key_field, path)
            return _merge_named([], items, key_field, path)
        return items
    return value


def collect_strings(payload: Any) -> List[str]:
    """Every string leaf in a payload — used by the eval/forbidden-term linter."""
    out: List[str] = []
    if isinstance(payload, str):
        out.append(payload)
    elif isinstance(payload, Mapping):
        for v in payload.values():
            out.extend(collect_strings(v))
    elif isinstance(payload, list):
        for v in payload:
            out.extend(collect_strings(v))
    return out

