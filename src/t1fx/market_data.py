"""Versioned official calendar snapshots. Cutoffs remain explicit scenario assumptions."""
from datetime import date, time

NYSE_SOURCE = 'https://www.nyse.com/trade/hours-calendars'
USD_SOURCE = 'https://www.frbservices.org/about/holiday-schedules/'
EUR_SOURCE = 'https://www.ecb.europa.eu/ecb/contacts/working-hours/html/index.en.html'
NYSE = {
    2026: '01-01 01-19 02-16 04-03 05-25 06-19 07-03 09-07 11-26 12-25',
    2027: '01-01 01-18 02-15 03-26 05-31 06-18 07-05 09-06 11-25 12-24',
    2028: '01-17 02-21 04-14 05-29 06-19 07-04 09-04 11-23 12-25',
}
USD = {
    2026: '01-01 01-19 02-16 05-25 06-19 07-04 09-07 10-12 11-11 11-26 12-25',
    2027: '01-01 01-18 02-15 05-31 06-19 07-05 09-06 10-11 11-11 11-25 12-25',
    2028: '01-01 01-17 02-21 05-29 06-19 07-04 09-04 10-09 11-11 11-23 12-25',
}
EUR = {2026: '01-01 04-03 04-06 05-01 12-25 12-26',
       2027: '01-01 03-26 03-29 05-01 12-25 12-26',
       2028: '01-01 04-14 04-17 05-01 12-25 12-26'}
EARLY = {2026: '11-27 12-24', 2027: '11-26', 2028: '07-03 11-24'}


def dates(table):
    return frozenset(date.fromisoformat(f'{year}-{day}') for year, values in table.items() for day in values.split())


def apply_calendar_bundle(config):
    """Pinned 2026–2028 scope; no national-holiday API masquerades as a payment calendar."""
    if config.calendar_bundle != 'us-2026-2028':
        return config
    coverage = {'valid_from': date(2026, 1, 1), 'valid_to': date(2028, 12, 31)}
    market = config.market.model_copy(update={**coverage, 'holidays': dates(NYSE),
        'settlement_holidays': dates(USD), 'source_url': NYSE_SOURCE,
        'settlement_source_url': USD_SOURCE,
        'trading_early_closes': {d: time(13) for d in dates(EARLY)}})
    routes = []
    for route in config.fx_routes:
        calendars = {}
        for side, currency in [('sell', route.sell_currency), ('buy', route.buy_currency)]:
            calendar = getattr(route, side + '_calendar')
            if currency in ('USD', 'EUR'):
                calendar = calendar.model_copy(update={**coverage,
                    'holidays': dates(USD if currency == 'USD' else EUR),
                    'classification': 'sourced', 'source_url': USD_SOURCE if currency == 'USD' else EUR_SOURCE})
            calendars[side + '_calendar'] = calendar
        routes.append(route.model_copy(update=calendars))
    return type(config).model_validate(config.model_dump() | {'market': market.model_dump(),
        'fx_routes': [route.model_dump() for route in routes]})


def coverage_report(config):
    return {'bundle': config.calendar_bundle, 'trading_source': config.market.source_url,
            'settlement_source': config.market.settlement_source_url,
            'routes': [{'pair': f'{r.sell_currency}/{r.buy_currency}',
                        'sell_calendar': r.sell_calendar.classification,
                        'buy_calendar': r.buy_calendar.classification,
                        'cutoff': 'scenario assumption'} for r in config.fx_routes]}
