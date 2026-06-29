"""Web adapter package (local operator console).

Security posture:
  * loopback bind only by default; never 0.0.0.0.
  * no unauthenticated remote control; no arbitrary-shell endpoint.
  * state-changing actions are POST-only, CSRF-protected, and pass through the
    same `ControllerService` as the CLI (no shelling out to the CLI).
  * loading a page never triggers a build/update/test/fetch/start/RF action.
"""
