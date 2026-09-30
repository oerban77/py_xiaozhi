"""Indonesia holiday MCP tools.

- ``indonesia_holiday_query``: query national holidays (SKB 3 Menteri).
  Three actions: list (current year), year (specific year), check (is date a holiday?).

Data source: https://api.kemendesa.link/libur-nasional
"""

from .register import register_indonesia_holiday_tools

__all__ = ["register_indonesia_holiday_tools"]
