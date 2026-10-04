"""ISO 4217 currencies and their minor units: the decimals a document amount rounds to (ADR-0003).

Taken from List One as published on 2026-09-17 by SIX, the ISO 4217 maintenance agency: every
current currency with a numeric minor unit. Fund codes (CLF, UYW, ...) and the X codes without one
(gold, special drawing rights, XXX) are left out, since nobody prices goods in them. An amendment to
the standard is an edit to these lists.
"""

from collections.abc import Mapping
from types import MappingProxyType

ISO_4217_PUBLISHED = "2026-09-17"

_NO_DECIMALS = "BIF CLP DJF GNF ISK JPY KMF KRW PYG RWF UGX VND VUV XAF XOF XPF"
_TWO_DECIMALS = (
    "AED AFN ALL AMD AOA ARS AUD AWG AZN BAM BBD BDT BMD BND BOB BRL BSD BTN BWP BYN BZD CAD CDF "
    "CHF CNY COP CRC CUP CVE CZK DKK DOP DZD EGP ERN ETB EUR FJD FKP GBP GEL GHS GIP GMD GTQ GYD "
    "HKD HNL HTG HUF IDR ILS INR IRR JMD KES KGS KHR KPW KYD KZT LAK LBP LKR LRD LSL MAD MDL MGA "
    "MKD MMK MNT MOP MRU MUR MVR MWK MXN MYR MZN NAD NGN NIO NOK NPR NZD PAB PEN PGK PHP PKR PLN "
    "QAR RON RSD RUB SAR SBD SCR SDG SEK SGD SHP SLE SOS SRD SSP STN SVC SYP SZL THB TJS TMT TOP "
    "TRY TTD TWD TZS UAH USD UYU UZS VED VES WST XCD XCG YER ZAR ZMW ZWG"
)
_THREE_DECIMALS = "BHD IQD JOD KWD LYD OMR TND"

MINOR_UNITS: Mapping[str, int] = MappingProxyType(
    dict.fromkeys(_NO_DECIMALS.split(), 0)
    | dict.fromkeys(_TWO_DECIMALS.split(), 2)
    | dict.fromkeys(_THREE_DECIMALS.split(), 3)
)
"""ISO 4217 code → its minor units: 0 for JPY, 2 for USD, 3 for KWD."""


def is_iso_4217(code: str) -> bool:
    """A current ISO 4217 currency that goods can be priced in."""
    return code in MINOR_UNITS
