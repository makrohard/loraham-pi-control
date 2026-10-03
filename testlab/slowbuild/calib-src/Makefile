# Calibration workload: the fixed C sources, built with `make -j$(nproc)` by calibrate.sh.
CC ?= cc
CFLAGS ?= -O2
OBJS := $(patsubst %.c,%.o,$(sort $(wildcard *.c)))

calib: $(OBJS)
	$(CC) -o $@ $(OBJS)

%.o: %.c
	$(CC) $(CFLAGS) -c -o $@ $<
