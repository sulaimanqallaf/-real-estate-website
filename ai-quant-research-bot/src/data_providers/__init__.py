"""Big Money / institutional-context data providers.

Every provider in this package returns a `base.ProviderResult` - never a raw
value - so callers always see `status`/`error`/`available_at` alongside
whatever data made it through. None of these providers place trades, connect
to a broker, or feed anything that bypasses the existing signal/risk/regime/
portfolio gates; see `src/big_money.py` for how their output is aggregated and
`README.md`'s "Big Money Data Engine" section for the point-in-time rules that
apply throughout.
"""
