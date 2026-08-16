# loraham-daemon-fake (LHPC test lab)

A self-contained Python stand-in for the real `loraham_daemon`, speaking the v112 wire
protocol (raw + framed + CONF sockets) with scenario-driven radio state, deterministic
RX injection and TX capture. Materialized as a local git repo and adopted through the
PRODUCTION install path by `lhpc testlab reset`. Never installed on real boxes.
