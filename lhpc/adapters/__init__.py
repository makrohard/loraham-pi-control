"""Interface adapters.

Adapters translate user input (argv / HTTP request) into calls on the shared
`lhpc.core.services.ControllerService` and render its `ActionResult`. They hold
NO business logic: no validation, no resource-conflict checks, no TX gating, no
status interpretation. The web adapter in particular must call the service layer
directly — never shell out to the CLI.
"""
