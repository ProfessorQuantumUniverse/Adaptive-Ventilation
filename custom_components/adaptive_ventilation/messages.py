"""Human readable explanations for every recommendation.

Principle 3 of the specification: no recommendation without a reason and the
numbers behind it. These templates are what the notification, the entity
attribute and the panel all render.

They deliberately live in Python rather than in ``strings.json``: the texts are
formatted with live numbers and are consumed by notifications and by the panel,
neither of which can use the entity translation machinery.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping

_LOGGER = logging.getLogger(__name__)

DEFAULT_LANGUAGE = "en"

#: ``reason_key`` -> language -> template. Format placeholders come straight
#: from ``Recommendation.reason_data``.
TEMPLATES: dict[str, dict[str, str]] = {
    # -- safety ----------------------------------------------------------
    "storm_warning": {
        "en": "Storm warning ({event}) - close every window. Gusts {wind_kmh} km/h.",
        "de": "Sturmwarnung ({event}) - alle Fenster schliessen. Boeen {wind_kmh} km/h.",
    },
    "storm_warning_cover": {
        "en": "Hail expected - lower the shutter to protect the glass.",
        "de": "Hagel erwartet - Rollladen runter, das schuetzt die Scheibe.",
    },
    "rain_incoming": {
        "en": "Rain in about {minutes} min ({probability} %) - close {window}.",
        "de": "Regen in ca. {minutes} min ({probability} %) - {window} schliessen.",
    },
    "frost_and_heating": {
        "en": "{outdoor} C outside and the heating is on - {window} has been open {minutes} min.",
        "de": "{outdoor} C draussen bei laufender Heizung - {window} steht seit {minutes} min offen.",
    },
    "away_and_open": {
        "en": "Nobody home and {window} is open.",
        "de": "Niemand zu Hause und {window} steht offen.",
    },
    "away_keep_closed": {
        "en": "Nobody home - keep it closed.",
        "de": "Niemand zu Hause - geschlossen lassen.",
    },
    "dark_and_open": {
        "en": "{window} is open on the ground floor after dark.",
        "de": "{window} steht im Erdgeschoss offen, und es ist dunkel.",
    },
    "outdoor_pm_spike": {
        "en": "Particulates outside at {outdoor_pm25} ug/m3 - keep the windows shut.",
        "de": "Feinstaub draussen bei {outdoor_pm25} ug/m3 - Fenster zu lassen.",
    },
    "outdoor_pm_spike_close": {
        "en": "Outside {outdoor_pm25} ug/m3 against {indoor_pm25} inside - close now.",
        "de": "Draussen {outdoor_pm25} ug/m3 gegen {indoor_pm25} drinnen - jetzt schliessen.",
    },
    "outdoor_sensor_implausible": {
        "en": (
            "The outdoor sensor reads {sensor} C, the weather service {forecast} C "
            "({delta} K apart) - it is probably in the sun."
        ),
        "de": (
            "Der Aussensensor meldet {sensor} C, der Wetterdienst {forecast} C "
            "({delta} K Unterschied) - er steht vermutlich in der Sonne."
        ),
    },
    "data_stale": {
        "en": "Sensor data is stale - no recommendations until it updates.",
        "de": "Sensordaten sind veraltet - bis zur Aktualisierung keine Empfehlungen.",
    },
    "basement_summer_veto": {
        "en": (
            "{room}: {outdoor_ah} g/m3 outside against {indoor_ah} g/m3 inside - "
            "airing out would condense water on the cold walls."
        ),
        "de": (
            "{room}: {outdoor_ah} g/m3 draussen gegen {indoor_ah} g/m3 drinnen - "
            "Lueften wuerde an den kalten Waenden Wasser niederschlagen."
        ),
    },
    # -- air quality -----------------------------------------------------
    "co2_high": {
        "en": "{room}: {co2} ppm CO2 - purge for {minutes} min.",
        "de": "{room}: {co2} ppm CO2 - {minutes} min stosslueften.",
    },
    "co2_high_urgent": {
        "en": "{room}: {co2} ppm CO2, that is a lot - purge for {minutes} min now.",
        "de": "{room}: {co2} ppm CO2, das ist viel - jetzt {minutes} min stosslueften.",
    },
    "voc_high": {
        "en": "{room}: VOC index {voc} - purge for {minutes} min.",
        "de": "{room}: VOC-Index {voc} - {minutes} min stosslueften.",
    },
    "indoor_pm_high": {
        "en": "{room}: {indoor_pm25} ug/m3 indoors - purge for {minutes} min.",
        "de": "{room}: {indoor_pm25} ug/m3 drinnen - {minutes} min stosslueften.",
    },
    "pm_both_high": {
        "en": (
            "{room}: {indoor_pm25} ug/m3 inside, {outdoor_pm25} outside - "
            "short and hard, {minutes} min."
        ),
        "de": (
            "{room}: {indoor_pm25} ug/m3 drinnen, {outdoor_pm25} draussen - "
            "kurz und kraeftig, {minutes} min."
        ),
    },
    "inversion_pm": {
        "en": "Inversion: {outdoor_pm25} ug/m3 and no wind - postpone ventilation.",
        "de": "Inversionslage: {outdoor_pm25} ug/m3 und kein Wind - Lueften verschieben.",
    },
    # -- moisture --------------------------------------------------------
    "humidity_spike": {
        "en": (
            "{room} at {humidity} % RH ({indoor_ah} g/m3 against {outdoor_ah} outside) - "
            "purge {minutes} min and keep the door shut."
        ),
        "de": (
            "{room} bei {humidity} % rF ({indoor_ah} g/m3 gegen {outdoor_ah} draussen) - "
            "{minutes} min stosslueften, Tuere zu."
        ),
    },
    "humidity_spike_blocked": {
        "en": (
            "{room} is humid, but at {outdoor_ah} g/m3 the air outside is no drier - "
            "ventilation would not help."
        ),
        "de": (
            "{room} ist feucht, aber mit {outdoor_ah} g/m3 ist die Aussenluft nicht "
            "trockener - Lueften bringt hier nichts."
        ),
    },
    "mold_risk": {
        "en": (
            "{room}: {surface_humidity} % RH on the coldest wall ({surface_temperature} C) - "
            "purge {minutes} min."
        ),
        "de": (
            "{room}: {surface_humidity} % rF an der kaeltesten Wand "
            "({surface_temperature} C) - {minutes} min stosslueften."
        ),
    },
    "mold_risk_heat": {
        "en": (
            "{room}: {surface_humidity} % RH at the wall surface. Ventilation will not "
            "help right now - heat the room instead."
        ),
        "de": (
            "{room}: {surface_humidity} % rF an der Wandoberflaeche. Lueften hilft gerade "
            "nicht - stattdessen heizen."
        ),
    },
    "laundry_drying": {
        "en": "{room}: laundry drying, {outdoor_ah} g/m3 outside against {indoor_ah} inside.",
        "de": "{room}: Waesche trocknet, {outdoor_ah} g/m3 draussen gegen {indoor_ah} drinnen.",
    },
    "dry_air": {
        "en": "{room} at {humidity} % RH ({indoor_ah} g/m3) - ventilate less, not more.",
        "de": "{room} bei {humidity} % rF ({indoor_ah} g/m3) - weniger lueften, nicht mehr.",
    },
    "dry_air_close": {
        "en": "{room} is already very dry at {humidity} % RH - close the window.",
        "de": "{room} ist mit {humidity} % rF schon sehr trocken - Fenster schliessen.",
    },
    "unheated_room": {
        "en": (
            "{room} is at {temperature} C while {warm_room} is at {warm_temperature} C - "
            "keep the door closed or moisture will condense in there."
        ),
        "de": (
            "{room} hat {temperature} C, {warm_room} dagegen {warm_temperature} C - "
            "Tuere zu, sonst kondensiert dort Feuchte."
        ),
    },
    # -- summer ----------------------------------------------------------
    "night_flush": {
        "en": (
            "{room}: open {window} - {outdoor} C outside against {indoor} C inside, "
            "about {expected_k} K by morning."
        ),
        "de": (
            "{room}: {window} auf - draussen {outdoor} C, drinnen {indoor} C, "
            "bis morgen ca. {expected_k} K."
        ),
    },
    "night_flush_keep": {
        "en": "{room}: leave {window} open - still {delta_t} K cooler outside.",
        "de": "{room}: {window} offen lassen - draussen immer noch {delta_t} K kuehler.",
    },
    "morning_close": {
        "en": (
            "Close the windows in {minutes} min - it gets warmer outside than inside, "
            "peak {peak} C today."
        ),
        "de": (
            "In {minutes} min Fenster zu - dann ist es draussen waermer als drinnen, "
            "Spitze heute {peak} C."
        ),
    },
    "morning_close_now": {
        "en": "Close the windows now - outside is overtaking inside, peak {peak} C today.",
        "de": "Jetzt Fenster zu - draussen ueberholt drinnen, Spitze heute {peak} C.",
    },
    "keep_closed_hot": {
        "en": "{room}: keep closed, {delta_t} K warmer outside ({outdoor} C).",
        "de": "{room}: geschlossen halten, draussen {delta_t} K waermer ({outdoor} C).",
    },
    "precool_heatwave": {
        "en": (
            "{peak} C ahead - pre-cool {room} tonight while it is {outdoor} C outside, "
            "the thermal store has to be empty before the wave."
        ),
        "de": (
            "{peak} C stehen bevor - {room} heute Nacht bei {outdoor} C vorkuehlen, "
            "der Speicher muss vor der Welle leer sein."
        ),
    },
    "tropical_night": {
        "en": "Tropical night: it stays at {night_min} C. Ventilate all night.",
        "de": "Tropennacht: es bleibt bei {night_min} C. Die ganze Nacht lueften.",
    },
    "tropical_night_insufficient": {
        "en": (
            "Tropical night at {night_min} C: about {achievable} K obtainable, "
            "{required} K needed. Night flushing alone will not be enough."
        ),
        "de": (
            "Tropennacht mit {night_min} C: rund {achievable} K holbar, noetig waeren "
            "{required} K. Nachtlueften allein reicht nicht mehr."
        ),
    },
    "tropical_night_fan": {
        "en": "{room} at {temperature} C - a fan is worth 2-3 K of perceived temperature.",
        "de": "{room} bei {temperature} C - ein Ventilator bringt gefuehlt 2-3 K.",
    },
    "thunderstorm_window": {
        "en": (
            "Cold front: {drop} K in a short window. Open {window} now, "
            "close again before the rain."
        ),
        "de": (
            "Frontdurchgang: {drop} K in kurzer Zeit. {window} jetzt auf, "
            "vor dem Regen wieder zu."
        ),
    },
    "fan_instead": {
        "en": (
            "{room} at {indoor} C and {outdoor} C outside - opening will not help, "
            "moving air will."
        ),
        "de": (
            "{room} bei {indoor} C, draussen {outdoor} C - Oeffnen bringt nichts, "
            "Luftbewegung schon."
        ),
    },
    "away_prepare": {
        "en": "Leaving: lower {window} - that keeps about {saving_w} W of heat out.",
        "de": "Beim Gehen: {window} runter - haelt rund {saving_w} W Waerme drausssen.",
    },
    # -- winter ----------------------------------------------------------
    "winter_purge": {
        "en": "{room}: purge for {minutes} min, wide open ({outdoor} C outside).",
        "de": "{room}: {minutes} min stosslueften, weit offen ({outdoor} C draussen).",
    },
    "winter_purge_cross": {
        "en": (
            "{room}: cross ventilate with {partner} - {minutes} min is enough that way."
        ),
        "de": (
            "{room}: quer lueften zusammen mit {partner} - so reichen {minutes} min."
        ),
    },
    "avoid_tilt_winter": {
        "en": (
            "{window} has been tilted for {minutes_open} min at {outdoor} C - "
            "that chills the reveal. Wide open for {purge_minutes} min instead."
        ),
        "de": (
            "{window} steht seit {minutes_open} min auf Kipp bei {outdoor} C - "
            "das kuehlt die Laibung aus. Besser {purge_minutes} min weit offen."
        ),
    },
    "passive_solar_gain": {
        "en": "{room} at {indoor} C - open {window} for {gain_w} W of free sunshine.",
        "de": "{room} bei {indoor} C - {window} hoch, {gain_w} W Sonne geschenkt.",
    },
    "cover_night_insulation": {
        "en": "Lower {window} for the night - saves roughly {saving_w} W.",
        "de": "{window} fuer die Nacht runter - spart rund {saving_w} W.",
    },
    "preheat_before_purge": {
        "en": "{room}: turn the radiator down before the purge, up again afterwards.",
        "de": "{room}: Heizkoerper vor dem Lueften runter, danach wieder hoch.",
    },
    # -- shading ---------------------------------------------------------
    "solar_shading": {
        "en": "{window}: {load_w} W on the glass - shading keeps {saving_w} W out.",
        "de": "{window}: {load_w} W auf der Scheibe - Verschattung haelt {saving_w} W ab.",
    },
    "solar_shading_ahead": {
        "en": "Sun reaches {window} in {minutes} min - shade it before it arrives.",
        "de": "In {minutes} min trifft die Sonne {window} - vorher verschatten.",
    },
    "shading_gap": {
        "en": (
            "{window} takes {load_w} W with no shading at all - about {daily_k} K a day. "
            "This is the biggest gap in the setup."
        ),
        "de": (
            "{window} bekommt {load_w} W voellig ungeschuetzt ab - rund {daily_k} K pro Tag. "
            "Das ist die groesste Luecke im System."
        ),
    },
    # -- general ---------------------------------------------------------
    "window_forgotten": {
        "en": "{window} has been open for {hours} h without doing anything useful.",
        "de": "{window} steht seit {hours} h offen, ohne dass es etwas bringt.",
    },
    "internal_load": {
        "en": "{load_w} W running in {room} - open {window} before the heat spreads.",
        "de": "{load_w} W laufen in {room} - {window} auf, bevor sich die Waerme verteilt.",
    },
    "hold_min_duration": {
        "en": "Holding {held_action} for another {minutes_left} min to avoid flapping.",
        "de": "Bleibt noch {minutes_left} min bei {held_action}, damit nichts flattert.",
    },
    "nothing_to_do": {
        "en": "Nothing to do.",
        "de": "Nichts zu tun.",
    },
}

#: Short notification titles per priority.
TITLES: dict[str, dict[str, str]] = {
    "SAFETY": {"en": "Ventilation - act now", "de": "Lueftung - jetzt handeln"},
    "HEALTH": {"en": "Air quality", "de": "Luftqualitaet"},
    "COMFORT": {"en": "Ventilation", "de": "Lueftung"},
    "OPTIMIZATION": {"en": "Ventilation tip", "de": "Lueftungstipp"},
}


def render(reason_key: str, data: Mapping[str, Any], language: str = DEFAULT_LANGUAGE) -> str:
    """Render a reason into a sentence, tolerating missing placeholders."""
    template_set = TEMPLATES.get(reason_key)
    if template_set is None:
        return reason_key.replace("_", " ")
    template = template_set.get(language) or template_set[DEFAULT_LANGUAGE]
    try:
        return template.format_map(_Defaults(data))
    except Exception:  # pragma: no cover - a broken template must not break a push
        _LOGGER.debug("could not render reason %s with %s", reason_key, dict(data))
        return reason_key.replace("_", " ")


def title(priority_name: str, language: str = DEFAULT_LANGUAGE) -> str:
    entry = TITLES.get(priority_name, TITLES["COMFORT"])
    return entry.get(language) or entry[DEFAULT_LANGUAGE]


def resolve_language(raw: str | None) -> str:
    """Map a Home Assistant language code onto a supported message language."""
    if not raw:
        return DEFAULT_LANGUAGE
    short = raw.split("-")[0].lower()
    return short if short in ("en", "de") else DEFAULT_LANGUAGE


class _Defaults(dict):
    """``format_map`` helper that leaves unknown placeholders visible but harmless."""

    def __missing__(self, key: str) -> str:
        return "?"
