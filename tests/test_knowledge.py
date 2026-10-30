"""The generated restaurant page and the hand-written menu.

The page is generated from restaurant.py, so these tests pin three things: the committed copy is the
current render, the render reads restaurant.py rather than fixed text, and the last seatings it
quotes are the last starts timeslots accepts. The menu is written by hand, so its diet and allergen
tags are checked for the contradictions a guest would act on. None of these tests needs a database.
"""

import codecs
import re
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pytest

from maitre_d import knowledge, restaurant, speech, timeslots

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "knowledge" / "restaurant.md"
MENU = ROOT / "knowledge" / "menu.md"

# The UK's 14 allergens, as the menu must label them. Written out here rather than imported, so that
# changing a label on the menu is also a deliberate change to this list.
ALLERGENS = frozenset(
    {
        "celery",
        "gluten",
        "crustaceans",
        "eggs",
        "fish",
        "lupin",
        "milk",
        "molluscs",
        "mustard",
        "peanuts",
        "sesame",
        "soybeans",
        "sulphites",
        "tree nuts",
    }
)
MENU_COLUMNS = ["Dish", "Price", "Diet", "Contains"]
DIETS = frozenset({"", "vegan", "vegetarian"})
PRICE = re.compile(r"£\d+\.\d{2}")
NOT_VEGAN = frozenset({"milk", "eggs", "fish", "crustaceans", "molluscs"})
NOT_VEGETARIAN = frozenset({"fish", "crustaceans", "molluscs"})

# Words in a dish name that imply an allergen. This is a floor, not a full list: a dish can contain
# an allergen its name never mentions, but a name that mentions one has to list it.
NAMED_ALLERGENS = {
    "celery": r"\b(?:celery|celeriac)\b",
    "eggs": r"\b(?:aioli|mayonnaise|eggs?)\b",
    "fish": r"\b(?:salmon|sea bass|cod|anchov(?:y|ies))\b",
    "crustaceans": r"\b(?:crabs?|prawns?|lobsters?|shrimps?)\b",
    "gluten": r"\b(?:naan|sourdough|batter|crumble|breadcrumbs?|bread|toast)\b",
    "milk": r"\b(?:yoghurt|raita|paneer|ghee|butter|beurre|cr[eè]me|cream|feta|kulfi)\b",
    "molluscs": r"\b(?:mussels?|squid|clams?|octopus|scallops?)\b",
    "mustard": r"\bmustard\b",
    "peanuts": r"\bpeanuts?\b",
    "sesame": r"\b(?:sesame|tahini)\b",
    "soybeans": r"\b(?:soy\w*|tofu)\b",
    "tree nuts": r"\b(?:almonds?|pistachios?|walnuts?|cashews?|hazelnuts?|pecans?)\b",
}

# A Wednesday with both services, in GMT and weeks away from any clock change, so that no notice,
# horizon or daylight rule can change the answer.
SEATING_DAY = date(2026, 11, 11)
SEATING_NOW = datetime(2026, 11, 2, 9, 0, tzinfo=restaurant.TIMEZONE)
SEATING_ROW = re.compile(r"^\| (\d+)(?: to (\d+))? \| \d+ minutes \| (.+) \|$", re.MULTILINE)


def test_the_committed_restaurant_page_is_the_current_render() -> None:
    """knowledge/restaurant.md must be what scripts/generate_knowledge.py writes from restaurant.py.

    A hand edit, or a change to restaurant.py that nobody regenerated the page for, fails here,
    before the agent can quote an hour the backend does not enforce.
    """
    assert PAGE.read_text(encoding="utf-8") == knowledge.render_restaurant_page()


@pytest.mark.parametrize("path", [PAGE, MENU], ids=["restaurant.md", "menu.md"])
def test_the_knowledge_files_are_utf8_without_a_bom_and_use_lf_newlines(path: Path) -> None:
    """The repo's convention is UTF-8 without a BOM, and LF line endings.

    This reads the bytes because read_text() silently turns CRLF into LF, so a file an editor had
    saved with CRLF would pass every other test in this module.
    """
    data = path.read_bytes()
    assert not data.startswith(codecs.BOM_UTF8), f"{path.name} starts with a byte order mark"
    assert b"\r" not in data, f"{path.name} has CRLF line endings"


def test_a_changed_service_in_restaurant_py_changes_the_hours_on_the_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hours come from SERVICES_BY_WEEKDAY when the page is rendered, not from text in the renderer.

    Patching restaurant.DINNER would prove nothing: the dict keeps the Service objects it was built
    with, so the renderer would still see the old dinner. This patches the dict the renderer reads.
    """
    later_dinner = restaurant.Service("dinner", time(18, 0), time(23, 0))
    later_week = {
        weekday: (restaurant.LUNCH, later_dinner) if services else ()
        for weekday, services in restaurant.SERVICES_BY_WEEKDAY.items()
    }
    monkeypatch.setattr(restaurant, "SERVICES_BY_WEEKDAY", later_week)

    page = knowledge.render_restaurant_page()

    assert "- Tuesday to Sunday: lunch 12:00-15:00, dinner 18:00-23:00" in page
    assert "17:30-22:30" not in page
    assert "lunch 13:30, dinner 21:30" in page


def test_a_changed_closed_date_reaches_the_page(monkeypatch: pytest.MonkeyPatch) -> None:
    """A closure added to CLOSED_DAYS_OF_YEAR in restaurant.py appears on the page, and one removed
    disappears, with no edit to the renderer."""
    monkeypatch.setattr(restaurant, "CLOSED_DAYS_OF_YEAR", frozenset({(7, 4)}))

    page = knowledge.render_restaurant_page()

    assert "4 July" in page
    assert "25 December" not in page


def test_a_changed_table_plan_and_booking_window_reach_the_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The name, the tables, the party limit, the notice, the horizon and the slot grid all come
    from restaurant.py, so the agent's rules follow the backend's without a second edit."""
    monkeypatch.setattr(restaurant, "NAME", "Test Table")
    monkeypatch.setattr(
        restaurant,
        "TABLES",
        (restaurant.DiningTable(1, "T1", 2), restaurant.DiningTable(2, "T2", 2)),
    )
    monkeypatch.setattr(restaurant, "MAX_PARTY_SIZE", 2)
    monkeypatch.setattr(restaurant, "MIN_NOTICE", timedelta(minutes=30))
    monkeypatch.setattr(restaurant, "BOOKING_HORIZON", timedelta(days=90))
    monkeypatch.setattr(restaurant, "SLOT_MINUTES", 30)

    page = knowledge.render_restaurant_page()

    assert page.count("# Test Table:") == 1
    assert "- 2 tables seating 2" in page
    assert "a party of more than 2 cannot be booked by phone" in page
    assert "Bookings can be made from 30 minutes from now, up to 90 days ahead." in page
    assert "Start times fall every 30 minutes." in page


def _accepts(at: time, party_size: int) -> bool:
    weekday = speech.WEEKDAYS[SEATING_DAY.weekday()]
    result = timeslots.validate_start(SEATING_DAY, weekday, at, party_size, SEATING_NOW)
    return isinstance(result, datetime)


def _quarter_hours(service: restaurant.Service) -> list[time]:
    """Every grid start inside the service's window, including the ones a sitting cannot fit."""
    starts: list[time] = []
    at = datetime.combine(SEATING_DAY, service.opens)
    while at.time() < service.closes:
        starts.append(at.time())
        at += timedelta(minutes=restaurant.SLOT_MINUTES)
    return starts


def _seatings_on_page(page: str, party_size: int) -> list[str]:
    """The "lunch 13:30, dinner 21:00" cell of the page row that covers this party size."""
    for row in SEATING_ROW.finditer(page):
        smallest, largest = int(row[1]), int(row[2] or row[1])
        if smallest <= party_size <= largest:
            return row[3].split(", ")
    raise AssertionError(f"the page has no last-seating row for a party of {party_size}")


@pytest.mark.parametrize("party_size", range(1, restaurant.MAX_PARTY_SIZE + 1))
def test_each_last_seating_on_the_page_is_the_last_start_timeslots_accepts(party_size: int) -> None:
    """The page gives the agent the latest time it may offer, so it has to match the backend.

    For every service, the last quarter hour that timeslots accepts must be the time the page
    quotes, and the quarter hour after it must be rejected. Otherwise the agent offers a time the
    backend refuses, or never offers the last one that would have worked.

    last_seating() re-derives closing time minus sitting length, which timeslots does not expose,
    so this is the test that ties the two together.
    """
    listed = _seatings_on_page(knowledge.render_restaurant_page(), party_size)
    services = restaurant.SERVICES_BY_WEEKDAY[SEATING_DAY.weekday()]
    assert services, "the chosen day has no services, so the test would check nothing"

    for service in services:
        accepted = [at for at in _quarter_hours(service) if _accepts(at, party_size)]
        assert accepted, f"timeslots accepts no {service.name} start for {party_size} guests"
        last = accepted[-1]
        after = (datetime.combine(SEATING_DAY, last) + timedelta(minutes=15)).time()

        assert knowledge.last_seating(service, party_size) == last
        assert not _accepts(after, party_size), (
            f"timeslots accepts {after:%H:%M} after {last:%H:%M}"
        )
        assert f"{service.name} {last:%H:%M}" in listed


def _menu_rows() -> list[dict[str, str]]:
    """Every dish row on the menu, each keyed by its table's header."""
    rows: list[dict[str, str]] = []
    header: list[str] | None = None
    for line in MENU.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|"):
            header = None
            continue
        cells = [cell.strip() for cell in line.split("|")[1:-1]]
        if header is None:
            assert cells == MENU_COLUMNS, f"a menu table has the header {cells}"
            header = cells
        elif set("".join(cells)) <= set("-:"):
            continue  # the |---| row under the header
        else:
            assert len(cells) == len(header), f"a menu row has {len(cells)} cells: {line}"
            rows.append(dict(zip(header, cells, strict=True)))
    return rows


def _allergens(cell: str) -> set[str]:
    """The labels in a Contains cell.

    A label is the text before any parenthesis, so "gluten (wheat)" is "gluten". Anything that is
    not one of the 14 fails, which is what catches a typo such as "nuts".
    """
    if cell == "none":
        return set()
    labels: set[str] = set()
    for item in cell.split(","):
        label = item.split("(")[0].strip()
        assert label in ALLERGENS, f"{item.strip()!r} is not one of the 14 UK allergen labels"
        labels.add(label)
    return labels


def test_every_dish_has_a_price_in_pounds_and_a_known_diet_tag() -> None:
    """Prices are in GBP, written like £9.50. The Diet column is only vegan, vegetarian or blank.

    A blank Diet means the dish contains meat or fish, so any other word there, such as "veggie",
    would leave the guest with a tag the agent cannot interpret.
    """
    rows = _menu_rows()
    assert rows, "the menu has no dish rows"
    for row in rows:
        assert PRICE.fullmatch(row["Price"]), f"{row['Dish']}: price {row['Price']!r}"
        assert row["Diet"] in DIETS, f"{row['Dish']}: diet {row['Diet']!r}"


def test_a_dish_tagged_vegan_lists_no_milk_eggs_fish_crustaceans_or_molluscs() -> None:
    """A dish tagged vegan must not list an animal allergen, because the tag is what a vegan guest
    relies on."""
    for row in _menu_rows():
        if row["Diet"] == "vegan":
            listed = _allergens(row["Contains"]) & NOT_VEGAN
            assert not listed, f"{row['Dish']} is tagged vegan but lists {sorted(listed)}"


def test_a_dish_tagged_vegetarian_lists_no_fish_crustaceans_or_molluscs() -> None:
    """A dish tagged vegetarian must not list a sea-food allergen. Dairy and eggs are allowed, so
    the check is narrower than the vegan one."""
    for row in _menu_rows():
        if row["Diet"] == "vegetarian":
            listed = _allergens(row["Contains"]) & NOT_VEGETARIAN
            assert not listed, f"{row['Dish']} is tagged vegetarian but lists {sorted(listed)}"


def test_every_allergen_label_on_the_menu_is_one_of_the_fourteen() -> None:
    """Labels are matched exactly. A label the page does not define, such as "nuts" or
    "tree-nuts", would never match the allergen a guest asks about."""
    for row in _menu_rows():
        _allergens(row["Contains"])


def test_an_allergen_a_dish_name_implies_is_listed_for_that_dish() -> None:
    """The dish name and the Contains column must agree.

    A dish that names yoghurt lists milk, and a dish that names naan lists gluten. This only knows
    the words in NAMED_ALLERGENS, so it cannot prove a list is complete, but it catches the omission
    that matters most: a name the guest reads that says something the allergen column does not.
    """
    for row in _menu_rows():
        implied = {
            allergen
            for allergen, pattern in NAMED_ALLERGENS.items()
            if re.search(pattern, row["Dish"], re.IGNORECASE)
        }
        missing = implied - _allergens(row["Contains"])
        assert not missing, f"{row['Dish']} names {sorted(missing)} but does not list it"


def test_the_menu_has_the_dishes_and_allergens_guests_ask_about() -> None:
    """Coverage the agent depends on: 18 to 22 dishes, at least three vegan, several with neither
    peanuts nor tree nuts, and at least one dish with each of peanuts, tree nuts, sesame, mustard,
    fish and crustaceans.

    Removing the only peanut dish would still pass every consistency test, so coverage is checked
    on its own.
    """
    rows = _menu_rows()
    assert 18 <= len(rows) <= 22, f"the menu has {len(rows)} dishes"
    assert sum(row["Diet"] == "vegan" for row in rows) >= 3
    nut_free = [row for row in rows if not (_allergens(row["Contains"]) & {"peanuts", "tree nuts"})]
    assert len(nut_free) >= 3
    listed = {label for row in rows for label in _allergens(row["Contains"])}
    missing = {"peanuts", "tree nuts", "sesame", "mustard", "fish", "crustaceans"} - listed
    assert not missing, f"no dish lists {sorted(missing)}"
