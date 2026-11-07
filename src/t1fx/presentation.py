"""Display aliases only: never model features, real issuer names, or hidden outcomes."""
import re


def trade_labels(trade: dict) -> dict[str, str]:
    match = re.search(r'-T(\d{6})$', trade['trade_id'])
    trade_label = (f"Trade {int(match[1])+1} · {trade['executed_at'][:10]}"
                   if match else trade['trade_id'])
    security = trade['security_id']
    counterparty = trade['counterparty_id']
    security_match = re.fullmatch(r'EQ-(\d+)', security)
    cp_match = re.fullmatch(r'CP-(\d{4})', counterparty)
    return {'trade_label': trade_label,
            'security_label': f'Sample equity {int(security_match[1])+1}' if security_match else security,
            'counterparty_label': f'Sample counterparty {int(cp_match[1])+1}' if cp_match else counterparty}
