"""Sdílený generátor odkazů na Google Flights (parametr ``?tfs=``).

Google Flights NEUMÍ spolehlivě předvyplnit vyhledávání z textového dotazu
(``?q=flights from X to Y on …``) – funguje jen někdy, open-jaw („and from …")
nikdy. Spolehlivé je předat binární parametr ``tfs``: base64url-kódovaný
protobuf s typem cesty, legy, termíny a cestujícími – tentýž, který generuje
samotné UI Google Flights při sdílení odkazu.

Schéma (reverzně dokumentované, používají ho např. knihovny fast-flights
a google-flights-url):

    message Airport    { string airport = 2; }
    message FlightData { string date = 2; Airport from = 13; Airport to = 14; }
    enum    Trip       { ROUND_TRIP = 1; ONE_WAY = 2; MULTI_CITY = 3; }
    message Info {
        repeated FlightData data = 3;
        repeated int32 passengers = 8;   // 1 = dospělý
        int32 seat = 9;                  // 1 = economy
        Trip trip = 19;
    }

Serializace je pár bajtů varint + length-delimited polí – kóduje se ručně,
závislost na protobuf knihovně by byla zbytečná.
"""
from __future__ import annotations

import base64
from datetime import date
from typing import Optional

_TRIP_ROUNDTRIP = 1
_TRIP_ONEWAY = 2
_TRIP_MULTICITY = 3


def _varint(value: int) -> bytes:
    out = bytearray()
    while True:
        bits = value & 0x7F
        value >>= 7
        if value:
            out.append(bits | 0x80)
        else:
            out.append(bits)
            return bytes(out)


def _tag(field: int, wire_type: int) -> bytes:
    return _varint((field << 3) | wire_type)


def _len_delimited(field: int, payload: bytes) -> bytes:
    return _tag(field, 2) + _varint(len(payload)) + payload


def _airport(code: str) -> bytes:
    return _len_delimited(2, code.encode("ascii"))


def _leg(day: date, origin: str, destination: str) -> bytes:
    body = _len_delimited(2, day.isoformat().encode("ascii"))
    body += _len_delimited(13, _airport(origin))
    body += _len_delimited(14, _airport(destination))
    return _len_delimited(3, body)


def google_flights_url(origin: str, destination: str,
                       depart: Optional[date], ret: Optional[date] = None,
                       return_origin: str = "",
                       return_destination: str = "") -> str:
    """Odkaz na Google Flights s předvyplněným vyhledáváním (1 dospělý, economy).

    - zpáteční: ``ret`` vyplněné, návratová letiště prázdná nebo zrcadlová,
    - open-jaw: návratový pár odlišný → multi-city se dvěma legy,
    - one-way: bez ``ret``.

    Vrací "" při chybějících povinných údajích – odkaz bez termínu by otevřel
    vyhledávání s výchozím (jiným) datem a matoucí cenou.
    """
    if not (origin and destination and depart):
        return ""
    openjaw = bool(ret and return_origin and return_destination and (
        return_origin != destination or return_destination != origin
    ))
    payload = _leg(depart, origin, destination)
    if ret and openjaw:
        payload += _leg(ret, return_origin, return_destination)
        trip = _TRIP_MULTICITY
    elif ret:
        payload += _leg(ret, destination, origin)
        trip = _TRIP_ROUNDTRIP
    else:
        trip = _TRIP_ONEWAY
    payload += _tag(8, 0) + _varint(1)   # passengers: 1 dospělý
    payload += _tag(9, 0) + _varint(1)   # seat: economy
    payload += _tag(19, 0) + _varint(trip)
    tfs = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    # curr=EUR: ceny v EUR jako celá aplikace, ať je srovnání 1:1.
    return f"https://www.google.com/travel/flights/search?tfs={tfs}&hl=en&curr=EUR"
