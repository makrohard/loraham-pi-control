"""Shared application/service core.

Both the CLI adapter and the web adapter call into this package and ONLY this
package for behaviour. Adapters are responsible for input parsing and rendering;
they must never re-implement validation, resource-conflict handling, TX-safety
checks, status interpretation or job control. That logic lives here so the two
interfaces stay identical.
"""
