"""Analysis engines.

`traditional` implements rule-based technical analysis (moving averages and the
四大買賣點 signal) computed from the prices in our own database.

AI-assisted analysis will land alongside it as a sibling module, exposed under
`/api/stocks/{sid}/analysis/ai`, so the two approaches stay independent and can
be compared side by side.
"""

from app.services.analysis import traditional

__all__ = ["traditional"]
